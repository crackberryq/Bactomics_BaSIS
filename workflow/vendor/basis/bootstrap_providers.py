"""First-install providers for frozen native screening database snapshots.

Providers use fixed official download locations or the installed tool's native
database installer. The bootstrap worker owns locks, transactional publication,
reuse and configuration merging. No provider updates an existing study.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import ssl
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

from .inputs import read_fasta
from .util import atomic_json, hash_object, sha256_file


PROVIDER_SCHEMA = "basis-native-bootstrap-providers-v1"
CARD_URL = "https://card.mcmaster.ca/latest/data"
VFDB_URL = "https://www.mgc.ac.cn/VFs/Down/VFDB_setA_pro.fas.gz"
_OFFICIAL_HOSTS = {"card.mcmaster.ca", "www.card.mcmaster.ca", "www.mgc.ac.cn", "mgc.ac.cn"}
BACKENDS = ("amrfinder", "card", "rgi", "vfdb", "antismash", "genomad", "mobsuite", "bacmet",
            "integronfinder", "isescan", "icescreen")
_MOB_AFFINITY_EXEC = """import os, sys
threads = int(sys.argv[1])
if not hasattr(os, 'sched_getaffinity') or not hasattr(os, 'sched_setaffinity'):
    raise RuntimeError('MOB initialization requires Linux CPU affinity to honor its allocated cores')
allowed = sorted(os.sched_getaffinity(0))
os.sched_setaffinity(0, allowed[:threads])
os.execvp('mob_init', ['mob_init', '--database_directory', sys.argv[2]])
"""


def _official_url(value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname not in _OFFICIAL_HOSTS or parsed.username or parsed.password:
        raise ValueError("Download redirected outside the supported official HTTPS sources")
    return value


class _OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _InterruptedDownload(OSError):
    pass


def _download_once(url, path, maximum):
    _official_url(url)
    path = Path(path)
    opener = urllib.request.build_opener(_OfficialRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "BaSIS-bootstrap/1"})
    with opener.open(request, timeout=120) as response, path.open("xb") as output:
        effective = _official_url(response.geturl())
        declared_length = response.headers.get("Content-Length")
        if declared_length is not None and not str(declared_length).isdigit():
            raise _InterruptedDownload("Official resource returned an invalid Content-Length")
        expected_length = int(declared_length) if declared_length is not None else None
        if expected_length is not None and expected_length > maximum:
            raise ValueError("Official resource exceeds the supported download size limit")
        size = 0
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > maximum:
                raise ValueError("Official resource exceeds the supported download size limit")
            output.write(block)
        headers = {key: response.headers.get(key) for key in ("ETag", "Last-Modified", "Content-Length")
                   if response.headers.get(key)}
    if not size:
        raise _InterruptedDownload("Official resource download was empty")
    if expected_length is not None and size != expected_length:
        raise _InterruptedDownload(f"Incomplete official resource download: expected {expected_length} bytes, received {size}")
    return {"source_url": url, "effective_url": effective, "downloaded_at_utc": _now(),
            "sha256": sha256_file(path), "bytes": size, "response_headers": headers,
            "integrity_basis": "observed_first_install_snapshot; no prior independent vendor content pin asserted"}


def _download(url, path, maximum=250_000_000):
    """Bounded HTTPS retries; partial bytes never become a completed snapshot."""
    _official_url(url)
    path = Path(path)
    if path.exists():
        raise ValueError("Refusing to overwrite an existing download artifact: " + str(path))
    for attempt in range(1, 4):
        if attempt > 1 and path.exists():
            path.unlink()
        print(f"Downloading {url} (attempt {attempt}/3)", flush=True)
        try:
            result = _download_once(url, path, maximum)
            result["attempts"] = attempt
            return result
        except urllib.error.HTTPError as exc:
            if exc.code not in {408, 429} and not 500 <= exc.code <= 599:
                raise
            error = exc
        except (OSError, urllib.error.URLError, ssl.SSLError, http.client.IncompleteRead) as exc:
            cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            if isinstance(cause, ssl.SSLCertVerificationError):
                host = urllib.parse.urlsplit(url).hostname
                raise RuntimeError(
                    f"TLS certificate verification failed for official source {host}: {cause}. "
                    "Check the machine clock and certificate trust chain; the server certificate may also be expired. "
                    "Certificate verification remains enabled. Retry after the certificate issue is resolved, "
                    "or explicitly configure a previously downloaded, provenance-verified local database. "
                    "No unverified mirror was substituted."
                ) from exc
            error = exc
        if attempt == 3:
            raise RuntimeError(f"Official download failed after 3 attempts: {error}") from error
        print("Download interrupted; retrying with certificate verification enabled.", flush=True)
        time.sleep(attempt * 2)
    raise AssertionError("Download retry loop did not finish")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _require(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root) or not path.is_file() or not path.stat().st_size:
        raise ValueError("Required native database artifact is missing, empty or external: " + str(path))
    return path


def _hashes(root):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if not path.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError("Prepared resource contains an external symlink: " + str(path))
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256_file(path)
    if not result:
        raise ValueError("Native resource preparation produced no files")
    return result


def _version(path):
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value or len(value) > 200 or "\n" in value or not any(c.isdigit() for c in value):
        raise ValueError("Unrecognized database version file: " + str(path))
    return value


def _check_version(spec, version):
    expected = (spec.get("options") or {}).get("expected_version")
    if expected is not None and expected != version:
        raise ValueError("Resolved resource version differs from expected_version: " + version)


def _finish(spec, root, updates, version, *, deep=None, sources=None, details=None):
    _check_version(spec, version)
    hashes = _hashes(root)
    metadata = root / "resource-provenance.json"
    provenance = {"schema": PROVIDER_SCHEMA, "provider": spec.get("provider", spec.get("backend", spec.get("name"))),
                  "resolved_version": version, "prepared_at_utc": _now(), "sha256": hashes,
                  "snapshot_content_hash": hash_object(hashes), "implicit_refresh": False,
                  "details": details or {}, "reference_sources": sources or [],
                  "comparator_review_status": "unreviewed"}
    # Snapshot-relative source paths keep the saved provenance relocatable.
    saved = dict(provenance)
    saved["reference_sources"] = [{**entry, "path": Path(entry["path"]).relative_to(root).as_posix()}
                                  for entry in (sources or [])]
    if "bundled_copy" in saved["details"]:
        saved["details"] = dict(saved["details"], bundled_copy=Path(saved["details"]["bundled_copy"]).relative_to(root).as_posix())
    atomic_json(metadata, saved)
    for config in updates.values():
        config.setdefault("database_version", version)
        config.setdefault("metadata", str(metadata))
    return {"module_updates": updates, "deep_updates": deep or {}, "provenance": provenance}


def extract_card(archive, destination):
    """Validate every member and extract exactly one ordinary card.json file."""
    destination = Path(destination)
    with tarfile.open(archive, "r:*") as handle:
        members, names, size = [], set(), 0
        for member in handle.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or "\\" in member.name or member.issym() or member.islnk():
                raise ValueError("Unsafe CARD archive member: " + member.name)
            if not (member.isfile() or member.isdir()):
                raise ValueError("Unsupported CARD archive member type")
            normalized = name.as_posix()
            if normalized in names:
                raise ValueError("Duplicate CARD archive member: " + normalized)
            names.add(normalized)
            size += member.size
            if size > 1_000_000_000 or len(names) > 10000:
                raise ValueError("CARD archive exceeds supported extraction bounds")
            if member.isfile() and name.name == "card.json":
                members.append(member)
        if len(members) != 1:
            raise ValueError("CARD archive must contain exactly one card.json")
        stream = handle.extractfile(members[0])
        if stream is None:
            raise ValueError("CARD JSON could not be read from archive")
        with stream, destination.open("xb") as output:
            shutil.copyfileobj(stream, output)
    data = json.loads(destination.read_text(encoding="utf-8"))
    version = data.get("_version") if isinstance(data, dict) else None
    models = [entry for key, entry in data.items() if str(key).isdigit() and isinstance(entry, dict)] if isinstance(data, dict) else []
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+)+", version) or not models:
        raise ValueError("Downloaded CARD JSON lacks its version or model entries")
    if not any(isinstance(model.get("model_sequences"), dict) for model in models):
        raise ValueError("Downloaded CARD JSON lacks model sequences")
    return version, len(models)


def normalize_vfdb(raw, destination):
    """Repair the known non-UTF8 NBSP in descriptions; preserve IDs and residues."""
    lines, repairs = [], []
    for number, line in enumerate(raw.splitlines(keepends=True), 1):
        if line.startswith(b">"):
            identifier = line[1:].split()[0].decode("ascii")
            header = line.decode("utf-8", errors="surrogateescape")
            count = header.count("\udca0")
            fixed = header.replace("\udca0", "\u00a0").encode("utf-8")
            if fixed.decode("utf-8")[1:].split()[0] != identifier:
                raise ValueError("VFDB header normalization changed an identifier")
            if count:
                repairs.append({"line": number, "reference_id": identifier, "nonbreaking_spaces": count})
            lines.append(fixed)
        else:
            line.decode("ascii")
            lines.append(line)
    normalized = b"".join(lines)
    sequence_bytes = lambda value: b"".join(line for line in value.splitlines(keepends=True) if not line.startswith(b">"))
    if sequence_bytes(raw) != sequence_bytes(normalized):
        raise ValueError("VFDB normalization changed sequence bytes")
    Path(destination).write_bytes(normalized)
    proteins = read_fasta(destination)
    if not proteins or any(not re.fullmatch(r"[A-Z*]+", str(sequence)) for sequence in proteins.values()):
        raise ValueError("VFDB protein FASTA is empty or contains invalid residues")
    return {"headers_repaired": repairs, "protein_count": len(proteins),
            "raw_fasta_sha256": hashlib.sha256(raw).hexdigest(), "normalized_fasta_sha256": sha256_file(destination),
            "sequence_bytes_preserved": True, "reference_ids_preserved": True}


def _diamond(runner, fasta, prefix, threads, root):
    version = runner.run(["diamond", "version"], cwd=root).stdout.strip()
    match = re.search(r"diamond version\s+(\d+)\.(\d+)\.(\d+)", version, re.I)
    if not match or tuple(map(int, match.groups())) < (2, 1, 7):
        raise ValueError("Reference ID preservation requires DIAMOND >=2.1.7")
    runner.run(["diamond", "makedb", "--in", str(fasta), "--db", str(prefix),
                "--threads", str(threads), "--no-parse-seqids"], cwd=root)
    _require(root, str(prefix.relative_to(root)) + ".dmnd")
    return version


_PYTHON_RESOURCE_LOCATION = """import importlib.util, json, sys
from pathlib import Path
name = sys.argv[1]
locations = []
if name:
    spec = importlib.util.find_spec(name)
    if spec is None or spec.submodule_search_locations is None:
        raise RuntimeError('Installed native Python package was not found: ' + name)
    locations = [str(Path(path).resolve()) for path in spec.submodule_search_locations]
print(json.dumps({'schema': 'basis-native-python-location-v1',
                  'prefix': str(Path(sys.prefix).resolve()),
                  'executable': str(Path(sys.executable).resolve()),
                  'package': name, 'locations': locations}))
"""


def _native_python_location(runner, package=""):
    """Ask the selected tool interpreter, including .pth/egg package layouts.

    The controller's Python version and ambient Conda prefix are not evidence of
    where a separately routed native tool is installed. Paths must be returned
    by that interpreter and remain inside its completed Conda environment.
    """
    response = runner.run(["python", "-I", "-c", _PYTHON_RESOURCE_LOCATION, package])
    try:
        record = json.loads(response.stdout)
        if record.get("schema") != "basis-native-python-location-v1" or record.get("package") != package:
            raise ValueError("Unexpected native Python resource-location schema")
        prefix = Path(record["prefix"])
        executable = Path(record["executable"])
        if not prefix.is_absolute() or not executable.is_absolute():
            raise ValueError("Native Python resource paths must be absolute")
        prefix, executable = prefix.resolve(strict=True), executable.resolve(strict=True)
        if not executable.is_relative_to(prefix):
            raise ValueError("Native Python executable is outside its environment")
        if not (prefix / "conda-meta/history").is_file():
            raise ValueError("Bundled resource environment lacks Conda transaction history")
        locations = record["locations"]
        if not isinstance(locations, list) or (package and len(locations) != 1) or (not package and locations):
            raise ValueError("Native Python package location is missing or ambiguous")
        location = None
        if package:
            location = Path(locations[0])
            if not location.is_absolute():
                raise ValueError("Native Python package path must be absolute")
            location = location.resolve(strict=True)
            if not location.is_dir() or not location.is_relative_to(prefix):
                raise ValueError("Native Python package is outside its environment")
        return prefix, location
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid native Python resource-location response") from exc


def _active_prefix(runner):
    return _native_python_location(runner)[0]


def _copy_bundled(provider, root, runner):
    if provider == "integronfinder":
        prefix, package = _native_python_location(runner, "integron_finder")
    else:
        prefix, package = _active_prefix(runner), None
    if provider == "isescan":
        source = prefix / "bin/pHMMs"
        required = ["clusters.single.faa", "clusters.faa.hmm"]
    elif provider == "icescreen":
        source = prefix / "bin/icescreen_detection_SP/database"
        required = []
        if not (source / "blastdb").is_dir() or not (source / "hmmdb").is_dir():
            raise ValueError("ICEscreen bundled BLAST/HMM databases missing")
    else:
        source, required = package / "data/Models", []
    if not source.is_dir() or not source.resolve().is_relative_to(prefix):
        raise ValueError("Bundled resource directory is absent or outside its environment")
    for name in required:
        _require(source, name)
    for path in source.rglob("*"):
        if not path.resolve().is_relative_to(prefix):
            raise ValueError("Bundled resource points outside the selected environment")
    copied = root / "bundled"
    shutil.copytree(source, copied, symlinks=False)
    hashes = _hashes(copied)
    if provider == "icescreen" and (not any(k.startswith("blastdb/") for k in hashes) or not any(k.startswith("hmmdb/") for k in hashes)):
        raise ValueError("ICEscreen bundled database subfolders are empty")
    if provider == "integronfinder" and not any(k.endswith((".hmm", ".cm")) for k in hashes):
        raise ValueError("IntegronFinder bundled HMM/covariance models absent")
    versions = {}
    for path in (prefix / "conda-meta").glob("*.json"):
        entry = json.loads(path.read_text())
        if entry.get("name") in {provider, "integron_finder"}:
            versions[entry["name"]] = entry.get("version")
    version = ";".join(f"{name}={version}" for name, version in sorted(versions.items())) or "bundled-content-" + hash_object(hashes)[:16]
    return source, version, {"runtime_binding": "tool_environment_bundled_resources",
                             "bundled_source": str(source), "bundled_copy": str(copied),
                             "source_sha256": {str(source / key): digest for key, digest in hashes.items()},
                             "configuration_note": "Native tools read their environment's bundled resources; the copied snapshot records their exact bytes."}


def _antismash_environment_assets(runner):
    """Fingerprint package model assets prepared by the upstream installer."""
    prefix, package = _native_python_location(runner, "antismash")
    hashes = {}
    for path in sorted(package.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".py", ".pyc", ".pyo"}:
            continue
        if not path.resolve().is_relative_to(prefix):
            raise ValueError("antiSMASH model asset points outside its native environment")
        if "data" in path.relative_to(package).parts or path.suffix in {".hmm", ".h3f", ".h3i", ".h3m", ".h3p", ".pkl", ".pickle", ".mdl"}:
            hashes[str(path)] = sha256_file(path)
    if not hashes:
        raise ValueError("antiSMASH native package model assets were not found")
    return {"runtime_binding": "native_environment_generated_and_bundled_assets",
            "source_sha256": hashes,
            "environment_asset_policy": "The upstream installer prepares model indexes/caches inside its isolated Conda environment; their resulting bytes are fingerprinted."}


def prepare_resource(spec: dict, destination: Path, runner) -> dict:
    """Prepare one supported first-install resource, returning configuration fields."""
    provider = spec.get("provider", spec.get("backend", spec.get("name")))
    if provider not in BACKENDS:
        raise ValueError("Unsupported native bootstrap provider: " + str(provider))
    if any(key in (spec.get("options") or {}) for key in ("url", "source_url", "command", "command_prefix")):
        raise ValueError("Bootstrap providers use fixed official sources and commands")
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    threads = int(getattr(runner, "threads", 0) or spec.get("threads", 1))
    if threads < 1:
        raise ValueError("Provider threads must be positive")
    if provider in {"integronfinder", "isescan", "icescreen"}:
        source, version, details = _copy_bundled(provider, root, runner)
        return _finish(spec, root, {provider: {"database": str(source)}}, version, details=details)
    if provider in {"card", "rgi"}:
        archive = root / "card-data.tar.bz2"
        download = _download(CARD_URL, archive)
        card = root / "card.json"
        version, count = extract_card(archive, card)
        return _finish(spec, root, {"rgi": {"database": str(card)}}, version,
                       sources=[{"source": "CARD", "path": str(card), "format": "card_json", "review_status": "unreviewed"}],
                       details={"download": download, "model_count": count, "indexing": "RGI adapter loads CARD into its run-local database"})
    if provider == "vfdb":
        archive = root / "VFDB_setA_pro.fas.gz"
        download = _download(VFDB_URL, archive)
        with gzip.open(archive, "rb") as handle:
            raw = handle.read(100_000_001)
        if len(raw) > 100_000_000:
            raise ValueError("Expanded VFDB FASTA exceeds expected bounds")
        fasta = root / "VFDB_setA_pro.normalized.fas"
        repairs = normalize_vfdb(raw, fasta)
        diamond = _diamond(runner, fasta, root / "vfdb_setA", threads, root)
        version = "VFDB Set A snapshot " + download["sha256"][:16]
        return _finish(spec, root, {"vfdb": {"database": str(root / "vfdb_setA.dmnd"), "reference_fasta": str(fasta)}}, version,
                       sources=[{"source": "VFDB", "path": str(fasta), "format": "fasta", "review_status": "unreviewed"}],
                       details={"download": download, "normalization": repairs, "diamond_version": diamond})
    if provider == "bacmet":
        from .optional_bacmet import _download_source, validate_bacmet_fasta, RELEASE
        fasta = root / "BacMet2_EXP_database.fasta"
        download = _download_source(fasta, lambda text: print(text, flush=True))
        entries = validate_bacmet_fasta(fasta)
        diamond = _diamond(runner, fasta, root / "bacmet_exp", threads, root)
        return _finish(spec, root, {"bacmet": {"database": str(root / "bacmet_exp.dmnd"), "reference_fasta": str(fasta)}}, RELEASE,
                       sources=[{"source": "BacMet", "path": str(fasta), "format": "fasta", "review_status": "unreviewed"}],
                       details={"download": download, "protein_count": len(entries), "diamond_version": diamond})
    if provider == "amrfinder":
        db = root / "database"
        runner.run(["amrfinder_update", "--database", str(db)], cwd=root)
        latest = db / "latest"
        if not latest.is_dir() or not latest.resolve().is_relative_to(root):
            raise ValueError("AMRFinder downloader did not produce an internal latest database directory")
        resolved = latest.resolve()
        if latest.is_symlink():
            # Some upstream versions create an absolute link. Keep the frozen
            # snapshot valid after the worker publishes/renames its staging tree.
            latest.unlink()
            latest.symlink_to(os.path.relpath(resolved, latest.parent), target_is_directory=True)
        required = ["version.txt", "AMRProt.fa", "AMRProt.fa.phr", "AMR.LIB"] + ["AMR.LIB.h3" + suffix for suffix in ("f", "i", "m", "p")]
        for name in required:
            _require(resolved, name)
        version = _version(resolved / "version.txt")
        probe = runner.run(["amrfinder", "-V", "--database", str(resolved)], cwd=root).stdout.strip()
        return _finish(spec, root, {"amrfinder": {"database": str(resolved)}}, version,
                       sources=[{"source": "AMRFinder", "path": str(resolved / "AMRProt.fa"), "format": "fasta", "review_status": "unreviewed"}],
                       details={"database_version_probe": probe, "selection": "upstream current compatible database resolved once"})
    if provider == "genomad":
        # Keep verified archives outside the attempt/output directory so an
        # extraction failure or Snakemake cleanup never forces a new transfer.
        cache = Path(spec.get("cache_directory") or root.parent) / "downloads" / "genomad"
        runner.run(["python", str(Path(__file__).with_name("download_genomad.py")),
                    "--destination", str(root), "--cache", str(cache)], cwd=root)
        db = root / "genomad_db"
        for name in ("version.txt", "genomad_marker_metadata.tsv", "names.dmp", "nodes.dmp"):
            _require(db, name)
        for prefix in ("genomad_db", "genomad_mini_db", "genomad_integrase_db"):
            for suffix in ("", ".dbtype", ".index"):
                _require(db, prefix + suffix)
        version = _version(db / "version.txt")
        download = json.loads((root / "genomad-download.json").read_text())
        return _finish(spec, root, {"genomad": {"database": str(db)}}, version,
                       details={"download": download})
    if provider == "antismash":
        runner.run(["download-antismash-databases", "--database-dir", str(root)], cwd=root)
        pfams = sorted(root.glob("pfam/*/Pfam-A.hmm"))
        if not pfams:
            raise ValueError("antiSMASH installer did not produce a Pfam database")
        pfam = max(pfams, key=lambda p: tuple(int(x) for x in p.parent.name.split(".")))
        for suffix in ("", ".h3f", ".h3i", ".h3m", ".h3p"):
            _require(root, str(pfam.relative_to(root)) + suffix)
        for directory in ("as-js", "mite", "resfam", "tigrfam", "clusterblast", "knownclusterblast", "clustercompare", "comparippson", "nrps_pks"):
            if not (root / directory).is_dir() or not any(p.is_file() and p.stat().st_size for p in (root / directory).rglob("*")):
                raise ValueError("antiSMASH database component missing: " + directory)
        tool = runner.run(["antismash", "--version"], cwd=root).stdout.strip()
        version = tool + " database bundle " + hash_object(_hashes(root))[:16]
        environment_assets = _antismash_environment_assets(runner)
        return _finish(spec, root, {"antismash": {"database": str(root)}}, version,
                       deep={"domain_database": str(pfam)},
                       details={"tool_version": tool, "pfam_version": pfam.parent.name, **environment_assets})
    if provider == "mobsuite":
        # Upstream mob_init chooses its Mash worker count from CPU affinity and
        # has no thread option. Restrict this process before executing it.
        runner.run(["python", "-c", _MOB_AFFINITY_EXEC, str(threads), str(root)], cwd=root)
        for name in ("ncbi_plasmid_full_seqs.fas", "ncbi_plasmid_full_seqs.fas.msh", "repetitive.dna.fas",
                     "rep.dna.fas", "mob.proteins.faa", "mpf.proteins.faa", "orit.fas", "clusters.txt", "taxa.sqlite"):
            _require(root, name)
        for prefix in ("ncbi_plasmid_full_seqs.fas", "repetitive.dna.fas"):
            for suffix in ("nhr", "nin", "nsq"):
                matches = list(root.glob(prefix + "." + suffix)) + list(root.glob(prefix + ".*." + suffix))
                if not matches or any(not p.stat().st_size for p in matches):
                    raise ValueError("MOB-suite BLAST index missing: " + prefix + "." + suffix)
        with sqlite3.connect((root / "taxa.sqlite").as_uri() + "?mode=ro", uri=True) as database:
            if database.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("MOB-suite taxonomy SQLite integrity check failed")
            if database.execute("SELECT count(*) FROM species").fetchone()[0] < 1:
                raise ValueError("MOB-suite taxonomy database contains no species")
        tool = runner.run(["mob_recon", "--version"], cwd=root).stdout.strip()
        version = "MOB-suite snapshot " + hash_object(_hashes(root))[:16]
        return _finish(spec, root, {"mobsuite": {"database": str(root)}}, version,
                       details={"tool_version": tool, "initialization_cpu_affinity_limit": threads})
    raise AssertionError("Provider dispatch is incomplete")
