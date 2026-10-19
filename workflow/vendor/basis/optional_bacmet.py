"""Pinned BacMet experimental reference setup; never edits a study configuration.

The source database contains experimentally described reference proteins. A
homology hit in a new isolate does not inherit that experimental validation.
"""
from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path



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
