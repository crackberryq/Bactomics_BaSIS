"""Pinned BacMet experimental reference setup; never edits a study configuration.

The source database contains experimentally described reference proteins. A
homology hit in a new isolate does not inherit that experimental validation.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .execution import CommandRunner


EXP_SHA256 = "e62756aafc1c653cdd1408fdbf558fd396ce1ef7ad99f2af50b0a6b67d00dd45"
EXP_COUNT = 753
RELEASE = "BacMet 2.0 experimentally confirmed proteins (2018-03-11)"
SOURCE_PATH = "/download/BacMet2_EXP_database.fasta"
SOURCE_HOST = "bacmet.biomedicine.gu.se"
SOURCE_URLS = tuple(scheme + "://" + SOURCE_HOST + SOURCE_PATH for scheme in ("https", "http"))
CITATION = "Pal et al. (2014), BacMet: antibacterial biocide and metal resistance genes database. doi:10.1093/nar/gkt1252"
_SCHEMA = "basis-bacmet-experimental-snapshot-v1"


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def parse_bacmet_header(header):
    """Read a known BacMet protein header without collapsing its reference ID."""
    header = str(header).strip().removeprefix(">")
    if not header:
        raise ValueError("Empty BacMet header")
    reference_id = header.split()[0]
    parts = reference_id.split("|")
    if len(parts) not in (4, 5) or not re.fullmatch(r"BAC[0-9]+", parts[0]):
        raise ValueError("Unsupported BacMet source header")
    # Six v2 headers do not follow the common BAC|gene|sp/tr|accession|entry form.
    # Two have no gene field; four have BAC|gene|accession|description.
    if len(parts) == 5:
        gene, source_kind, accession = parts[1], parts[2], parts[3]
        if source_kind not in {"sp", "tr"}:
            raise ValueError("Unsupported BacMet accession source: " + reference_id)
    elif parts[1] in {"sp", "tr"}:
        gene, source_kind, accession = None, parts[1], parts[2]
    else:
        gene, source_kind, accession = parts[1], "as_recorded", parts[2]
    if not accession:
        raise ValueError("Missing BacMet accession: " + reference_id)
    return {"bacmet_id": parts[0], "reference_id": reference_id, "gene": gene or None,
            "accession": accession, "accession_source": source_kind, "source_header": header}


def validate_bacmet_fasta(path, *, expected_sha256=EXP_SHA256, expected_count=EXP_COUNT):
    """Validate exact source bytes, protein alphabet, IDs, and header variants."""
    path = Path(path)
    if _sha(path) != expected_sha256:
        raise ValueError("BacMet source checksum differs from the reviewed experimental snapshot")
    entries = []
    current = None
    seen = set()
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current is not None and not current["length"]:
                raise ValueError("Empty BacMet protein: " + current["bacmet_id"])
            current = parse_bacmet_header(line[1:])
            reference_id = current["reference_id"]
            # Five BAC labels are reused across different source accessions in v2.
            # Only the complete first FASTA token identifies a unique source record.
            if reference_id in seen:
                raise ValueError("Duplicate BacMet reference ID: " + reference_id)
            seen.add(reference_id)
            current["length"] = 0
            entries.append(current)
        else:
            if current is None:
                raise ValueError("Sequence before first BacMet FASTA header")
            if not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWXY]+", line):
                raise ValueError(f"Invalid BacMet protein characters at line {number}")
            current["length"] += len(line)
    if current is not None and not current["length"]:
        raise ValueError("Empty BacMet protein: " + current["bacmet_id"])
    if len(entries) != expected_count:
        raise ValueError(f"Expected {expected_count} BacMet proteins, observed {len(entries)}")
    return entries


def _allowed_url(url):
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme in {"https", "http"} and parsed.hostname == SOURCE_HOST
            and parsed.path == SOURCE_PATH and not parsed.username and not parsed.password
            and parsed.port in (None, 80, 443) and not parsed.query)


class _OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_url(newurl):
            raise ValueError("BacMet redirected outside its official source URL")
        if req.full_url.startswith("https:") and not newurl.startswith("https:"):
            raise ValueError("HTTPS downgrade rejected; the separately pinned HTTP fallback is explicit")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_source(destination, log):
    """Use the official HTTPS URL first, then its historical hash-pinned HTTP URL."""
    opener = urllib.request.build_opener(_OfficialRedirect())
    errors = []
    for url in SOURCE_URLS:
        for attempt in range(1, 3):
            log(f"BacMet download {url} (attempt {attempt}/2)")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "BaSIS-BacMet-setup/1"})
                with opener.open(req, timeout=20) as response:
                    effective = response.geturl()
                    if not _allowed_url(effective):
                        raise ValueError("Unexpected BacMet download location")
                    headers = dict(response.headers.items())
                    with Path(destination).open("wb") as handle:
                        size = 0
                        while True:
                            block = response.read(65536)
                            if not block:
                                break
                            size += len(block)
                            if size > 5_000_000:
                                raise ValueError("BacMet download exceeded the expected snapshot size limit")
                            handle.write(block)
                validate_bacmet_fasta(destination)
                log(f"BacMet checksum verified; {EXP_COUNT} experimental reference proteins")
                return {"source_url": url, "effective_url": effective, "http_headers": headers,
                        "downloaded_at_utc": _now(), "transport": urllib.parse.urlsplit(effective).scheme,
                        "content_pin_basis": "SHA256 observed from official source during source review"}
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
                log("BacMet attempt did not produce the pinned source: " + str(exc))
                # Keep the final failed snapshot directory and its log for inspection;
                # never expose a partial file as a prepared database.
                if attempt == 1:
                    time.sleep(1)
    raise RuntimeError("Unable to obtain the pinned official BacMet FASTA. " + " | ".join(errors))


def _resources(folder, metadata, reused):
    return {"database": str(folder / "bacmet_exp.dmnd"), "database_version": RELEASE,
            "reference_fasta": str(folder / "BacMet2_EXP_database.fasta"),
            "source_metadata": str(folder / "reference_headers.json"),
            "provenance": str(folder / "provenance.json"), "protein_count": EXP_COUNT,
            "snapshot": str(folder), "reused": reused,
            "executable": "diamond", "version_args": ["version"],
            "command_prefix": metadata["command_prefix"]}


def _existing(root, version, prefix):
    for manifest in sorted(root.glob("experimental-v2-*/provenance.json"), reverse=True):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if (data.get("schema") != _SCHEMA or data.get("status") != "ready"
                    or data.get("source_sha256") != EXP_SHA256
                    or data.get("diamond_version") != version or data.get("command_prefix") != prefix):
                continue
            hashes = data.get("sha256", {})
            expected = {"BacMet2_EXP_database.fasta", "bacmet_exp.dmnd", "reference_headers.json"}
            if set(hashes) != expected:
                continue
            if any(not (manifest.parent / name).is_file() or _sha(manifest.parent / name) != digest
                   for name, digest in hashes.items()):
                continue
            validate_bacmet_fasta(manifest.parent / "BacMet2_EXP_database.fasta")
            return _resources(manifest.parent, data, True)
        except (OSError, ValueError, KeyError):
            continue
    return None


def prepare_bacmet(database_root, diamond_env, *, threads=4, log=print,
                   offline_fasta=None, reuse=True, allow_download=True):
    """Prepare or verify a pinned BacMet DIAMOND resource in a unique directory.

    ``database_root`` is the BacMet database parent, e.g. ~/bactomics/db/bacmet.
    ``diamond_env`` is an existing conda prefix (the user's basis-rgi environment).
    Returns resource paths; no study files, modules, or analysis outputs are changed.
    """
    if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
        raise ValueError("threads must be a positive integer")
    root = Path(database_root).expanduser().resolve()
    env = Path(diamond_env).expanduser().resolve()
    if not (env / "conda-meta").is_dir() or not (env / "bin/diamond").is_file():
        raise ValueError("Existing DIAMOND conda environment not found: " + str(env))
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    folder = root / ("experimental-v2-" + stamp)
    folder.mkdir(exist_ok=False)
    logfile = folder / "setup.log"

    def emit(message):
        with logfile.open("a", encoding="utf-8") as handle:
            handle.write(f"[{_now()}] {message}\n")
        log(message)

    prefix = ["conda", "run", "--no-capture-output", "-p", str(env)]
    runner = CommandRunner({"command_prefix": prefix, "threads": threads,
                            "timeout_seconds": 600}, folder / "commands.log")
    state = {"schema": _SCHEMA, "started_at_utc": _now(), "status": "preparing",
             "command_prefix": prefix, "source_sha256": EXP_SHA256}
    try:
        emit("Checking DIAMOND in " + str(env))
        version = runner.run(["diamond", "version"]).stdout.strip()
        version_match = re.search(r"diamond version\s+(\d+)\.(\d+)\.(\d+)", version, flags=re.I)
        if not version_match:
            raise ValueError("DIAMOND version output was not recognized")
        if tuple(map(int, version_match.groups())) < (2, 1, 7):
            raise ValueError("BacMet source ID preservation requires DIAMOND 2.1.7 or newer")
        state["diamond_version"] = version
        existing = _existing(root, version, prefix) if reuse and offline_fasta is None else None
        if existing:
            emit("Verified existing BacMet snapshot: " + existing["snapshot"])
            state.update(status="reused", reused_snapshot=existing["snapshot"])
            (folder / "setup_status.json").write_text(json.dumps(state, indent=2) + "\n")
            return existing
        if offline_fasta is None and not allow_download:
            raise ValueError("No verified BacMet snapshot is available; rerun with --install to download and build the official reference database")
        partial = folder / "source.part"
        if offline_fasta is not None:
            source = Path(offline_fasta).expanduser().resolve()
            validate_bacmet_fasta(source)
            shutil.copyfile(source, partial)
            downloaded = {"source_url": SOURCE_URLS[1], "local_source": str(source),
                          "copied_at_utc": _now(), "transport": "local_pinned_copy",
                          "content_pin_basis": "SHA256 observed from official source during source review"}
            emit("Copied local BacMet FASTA; checking pinned source bytes")
        else:
            downloaded = _download_source(partial, emit)
        entries = validate_bacmet_fasta(partial)
        fasta = folder / "BacMet2_EXP_database.fasta"
        partial.rename(fasta)
        headers = folder / "reference_headers.json"
        headers.write_text(json.dumps({"schema": "basis-bacmet-headers-v1", "references": entries}, indent=2) + "\n",
                           encoding="utf-8")
        emit(f"Building BacMet DIAMOND database with {threads} CPU threads")
        argv = ["diamond", "makedb", "--in", str(fasta), "--db", str(folder / "bacmet_exp"),
                "--threads", str(threads), "--no-parse-seqids"]
        runner.run(argv)
        index = folder / "bacmet_exp.dmnd"
        if not index.is_file() or index.stat().st_size == 0:
            raise ValueError("DIAMOND did not create a nonempty BacMet database")
        if _sha(fasta) != EXP_SHA256:
            raise ValueError("BacMet source changed during database construction")
        state.update(status="ready", finished_at_utc=_now(), database="BacMet",
                     database_version=RELEASE, release_date="2018-03-11",
                     dataset="experimentally confirmed reference proteins", citation=CITATION,
                     protein_count=len(entries), residue_count=sum(x["length"] for x in entries),
                     repeated_bacmet_labels={key: value for key, value in Counter(x["bacmet_id"] for x in entries).items() if value > 1},
                     reference_key="complete first FASTA token; BAC numeric label alone is not unique",
                     download=downloaded, commands=runner.commands,
                     source_download_page="http://bacmet.biomedicine.gu.se/download_temporary.html",
                     license_status="not_explicitly_stated_on_source_pages",
                     license_note="Source pages provide downloads and a copyright notice; no database redistribution license was verified. The publication license is not asserted as the database license.",
                     interpretation="Sequence homology to an experimental reference does not establish biocide/metal resistance or antimicrobial resistance in the tested isolate.",
                     sha256={p.name: _sha(p) for p in (fasta, index, headers)})
        (folder / "provenance.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        emit("BacMet ready: " + str(index))
        return _resources(folder, state, False)
    except Exception as exc:
        state.update(status="failed", finished_at_utc=_now(), error=str(exc), commands=runner.commands)
        (folder / "setup_status.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        emit("BacMet preparation stopped: " + str(exc))
        raise
