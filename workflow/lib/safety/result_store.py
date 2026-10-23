"""Verified native results retained independently of scheduler output cleanup.

The caller owns command construction and scientific verification. This module
only binds attempts to execution identities, inventories their outputs, and
publishes a small completion receipt after the caller's verifier succeeds.
Resource receipts are identities of already verified snapshots; database bytes
are not read again for every analysis job.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time
import uuid

from .resources import SCHEMA as RESOURCE_SCHEMA, _environment_identity, _semantic_result
from .runtime import write_json

SCHEMA = "basis-native-result-v1"
INPUT_SCHEMA = "basis-native-execution-v1"
_INPUT_KEYS = ("assembly", "proteins", "gff", "genbank")
_OPERATIONAL = {"timeout_seconds", "heartbeat_seconds", "cache", "failed_retention",
                "failed_attempts_to_keep"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _read(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def execution_identity(module, sample, full_config, *, sources=(), extra_inputs=(), executable=None,
                       require_sample_inputs=True):
    """Bind an attempt to actual inputs, its executable and scoped declarations.

    Callers provide the parser/command source files relevant to this module.
    Input files and the executable are hashed here; the resource preparation
    barrier is responsible for verifying database inventories. Allocation
    settings such as threads are retained conservatively in the identity.
    """
    settings = dict(full_config.get("modules", {}).get(module, {}))
    if not settings:
        raise ValueError(f"Missing module settings: {module}")
    inputs = {}
    for key in _INPUT_KEYS:
        if sample.get(key):
            path = Path(sample[key]).resolve(strict=True)
            if not path.is_file():
                raise ValueError(f"Input is not a regular file: {path}")
            inputs[key] = {"path": str(path), "sha256": sha256(path)}
    if require_sample_inputs and not inputs:
        raise ValueError("Native result identity requires declared input files")
    expected = sample.get("input_validation", {}).get("input_hashes", {})
    for key, row in inputs.items():
        if key in expected and expected[key] != row["sha256"]:
            raise ValueError(f"Input changed after validation: {row['path']}")
    program = str(executable or settings.get("executable") or "")
    resolved = shutil.which(program) if program else None
    if resolved is None:
        raise ValueError(f"Cannot verify native executable identity: {program or module}")
    program_path = Path(resolved).resolve(strict=True)
    resources = []
    for entry in full_config.get("resource_receipts", []):
        path = Path(entry["path"] if isinstance(entry, dict) else entry)
        row = _read(path)
        if (row.get("schema") != RESOURCE_SCHEMA or row.get("status") != "completed"
                or not isinstance(row.get("artifacts"), dict) or not row["artifacts"]
                or not isinstance(row.get("resource"), str) or not row["resource"]):
            raise ValueError(f"Resource receipt is not verified and complete: {path}")
        if isinstance(entry, dict) and entry.get("sha256") and entry["sha256"] != sha256(path):
            raise ValueError(f"Resource receipt changed after setup: {path}")
        resources.append({"resource": row.get("resource"), "artifacts": row["artifacts"],
                          "result": _semantic_result(row.get("result", {}))})
    environments = [_environment_identity(path) for path in full_config.get("environment_receipts", [])]
    if not environments:
        raise ValueError("Native result identity requires a verified environment receipt")
    source_rows = {str(Path(path).resolve(strict=True)): sha256(path) for path in sources}
    if not source_rows:
        raise ValueError("Native result identity requires command/parser sources")
    extras = {str(Path(path).resolve(strict=True)): sha256(path) for path in extra_inputs}
    if not inputs and not resources and not extras:
        raise ValueError("Native result identity requires sample, resource or explicit input identities")
    # The full input-validation map and unrelated output paths are not settings.
    sample_context = {key: value for key, value in sample.items()
                      if key not in {*_INPUT_KEYS, "input_validation", "safety_dir", "isolate_dir"}}
    return {"schema": INPUT_SCHEMA, "module": module, "inputs": inputs,
            "sample": sample_context, "settings": {key: value for key, value in settings.items()
                                                     if key not in _OPERATIONAL},
            "decision": full_config.get("module_decisions", {}).get(module, {}),
            "execution": full_config.get("execution", {}),
            "executable": {"path": str(program_path), "sha256": sha256(program_path)},
            "resources": sorted(resources, key=digest),
            "environments": sorted(environments, key=digest), "sources": source_rows,
            "extra_inputs": extras}


def _verify_execution_inputs(identity):
    if identity.get("schema") != INPUT_SCHEMA:
        return
    checks = {value["path"]: value["sha256"] for value in identity.get("inputs", {}).values()}
    checks.update(identity.get("sources", {}))
    checks.update(identity.get("extra_inputs", {}))
    executable = identity.get("executable", {})
    if executable.get("path"):
        checks[executable["path"]] = executable["sha256"]
    for path, expected in checks.items():
        if not Path(path).is_file() or sha256(path) != expected:
            raise ValueError(f"Native execution input changed during the attempt: {path}")


def output_inventory(directory):
    """Inventory all native output files without following directory symlinks.

    Linked files are bound to their target contents. Internal directory links
    are covered by the same inventory; external directory links are rejected
    because their mutable contents cannot be represented by a link alone.
    """
    directory = Path(directory).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError(f"Native result payload is not a directory: {directory}")
    rows = {}
    files = 0
    for parent, directories, names in os.walk(directory, followlinks=False):
        for name in sorted(directories + names):
            path = Path(parent) / name
            relative = path.relative_to(directory).as_posix()
            if relative == ".snakemake_timestamp":
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                target = path.resolve(strict=True)
                row = {"kind": "link", "target": os.readlink(path)}
                if target.is_file():
                    row.update(bytes=target.stat().st_size, sha256=sha256(target))
                    files += 1
                elif target.is_dir() and target.is_relative_to(directory):
                    row["directory"] = True
                else:
                    raise ValueError(f"Unsupported native output symlink: {path}")
                rows[relative] = row
            elif stat.S_ISREG(mode):
                rows[relative] = {"kind": "file", "bytes": path.stat().st_size, "sha256": sha256(path)}
                files += 1
            elif stat.S_ISDIR(mode):
                rows[relative] = {"kind": "directory"}
            else:
                raise ValueError(f"Unsupported native output type: {path}")
    if not files:
        raise ValueError("Verified native result contains no output files")
    return dict(sorted(rows.items()))


def _record(path, *, expected_identity=None, destination=None):
    row = _read(path)
    if (row.get("schema") != SCHEMA or row.get("status") != "completed"
            or not isinstance(row.get("identity"), dict)
            or row.get("key") != digest(row["identity"]) or not row.get("artifacts")):
        raise ValueError(f"Invalid native completion record: {path}")
    if expected_identity is not None and row["identity"] != expected_identity:
        raise ValueError(f"Native completion identity mismatch: {path}")
    payload = Path(row["payload_root"])
    if not payload.is_absolute() or payload.name != "data" or payload.is_symlink():
        raise ValueError(f"Invalid native payload path: {payload}")
    if payload.parent.parent.name != row["key"] or not payload.parent.name.startswith("attempt-"):
        raise ValueError(f"Native payload is outside its identity directory: {payload}")
    manifest = payload.parent / "completed.json"
    if not manifest.is_file() or _read(manifest) != row:
        raise ValueError(f"Native completion manifest is missing or changed: {manifest}")
    if destination is not None and str(Path(destination).absolute()) != row.get("destination"):
        raise ValueError(f"Native completion destination mismatch: {destination}")
    for relative in row["artifacts"]:
        name = Path(relative)
        if name.is_absolute() or ".." in name.parts or str(name) in {"", "."}:
            raise ValueError(f"Unsafe native inventory path: {relative}")
    return row


def _check_link(row, *, repair=False):
    destination, payload = Path(row["destination"]), Path(row["payload_root"])
    if destination.is_symlink():
        if destination.resolve() != payload:
            raise ValueError(f"Native result link points to a different payload: {destination}")
    elif destination.exists():
        raise ValueError(f"Native result link has been replaced: {destination}")
    elif repair:
        _publish_link(destination, payload)
    else:
        raise ValueError(f"Native result link is missing: {destination}. Restore it from its completion "
                         "receipt with result_store.verify_receipt(..., repair_link=True), or rerun "
                         "only this module; the verified payload is retained.")


def verify_receipt(receipt, destination=None, expected_identity=None, *, repair_link=False):
    """Verify the immutable completion identity and all payload contents."""
    row = _record(receipt, expected_identity=expected_identity, destination=destination)
    payload = Path(row["payload_root"])
    if not payload.is_dir():
        raise ValueError(f"Native payload is missing: {payload}. Restore this snapshot or remove only "
                         f"the scheduler receipt {receipt} and its matching cache.json before recomputing.")
    if output_inventory(payload) != row["artifacts"]:
        raise ValueError(f"Native payload integrity check failed: {payload}. Restore the recorded files; "
                         "corrupted results are never silently reused.")
    _check_link(row, repair=repair_link)
    return row


def receipt_inputs(receipt, destination):
    """Cheap DAG-time existence check; full hashes are verified on reuse/parse."""
    receipt = Path(receipt)
    if not receipt.is_file():
        return []
    row = _record(receipt, destination=destination)
    payload = Path(row["payload_root"])
    _check_link(row)
    paths = [payload.parent / "completed.json"]
    for relative, item in row["artifacts"].items():
        path = payload / relative
        if not path.exists():
            raise ValueError(f"Native completion references missing output: {path}. Restore the file "
                             f"or remove only {receipt} and the matching cache.json before recomputing.")
        if item.get("kind") != "directory":
            paths.append(path)
    return list(map(str, paths))


@contextmanager
def _lock(root, *, timeout=15):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a+") as stream:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Another process owns the native result store: {root}")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _publish_link(destination, payload):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".link-" + uuid.uuid4().hex)
    temporary.symlink_to(payload, target_is_directory=True)
    try:
        if destination.exists() and not destination.is_symlink():
            # Earlier outputs have no complete identity. Preserve them without
            # claiming they can be reused, and never remove their contents.
            archive = destination.parent / ".previous-results" / (destination.name + "-" + uuid.uuid4().hex)
            archive.parent.mkdir(parents=True, exist_ok=True)
            destination.rename(archive)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _prune_failed(root, keep):
    failures = []
    for failure in root.glob("*/attempt-*/failure.json"):
        attempt = failure.parent
        if ((attempt / "completed.json").exists() or attempt.is_symlink() or failure.is_symlink()
                or attempt.parent.is_symlink() or not re.fullmatch(r"[0-9a-f]{64}", attempt.parent.name)
                or not re.fullmatch(r"attempt-[0-9a-f]{32}", attempt.name)
                or not attempt.resolve().is_relative_to(root.resolve())):
            continue
        row = _read(failure)
        if row.get("schema") == SCHEMA and row.get("status") == "failed":
            failures.append((failure.stat().st_mtime_ns, attempt))
    for _, attempt in sorted(failures, reverse=True)[keep:]:
        shutil.rmtree(attempt)


def run_cached(name, identity, destination, receipt, store_root, execute, verify, *,
               failed_retention=3, allow_reuse=True):
    """Execute and verify into a durable attempt, returning ``(record, reused)``.

    ``execute(payload)`` and ``verify(payload)`` are supplied by the owning
    native module. Only the receipt is a Snakemake output. Successful snapshots
    and existing legacy output directories are never automatically pruned.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        raise ValueError("Invalid native result name")
    if (not isinstance(identity, dict) or identity.get("schema") != INPUT_SCHEMA
            or not identity.get("sources") or not identity.get("environments")
            or not identity.get("executable", {}).get("sha256")
            or not any(identity.get(key) for key in ("inputs", "resources", "extra_inputs"))):
        raise ValueError("Native result reuse requires a complete verified execution identity")
    if type(failed_retention) is not int or not 1 <= failed_retention <= 100:
        raise ValueError("failed_retention must be an integer from 1 to 100")
    if not isinstance(allow_reuse, bool):
        raise ValueError("allow_reuse must be a boolean")
    destination, receipt = Path(destination).absolute(), Path(receipt).absolute()
    root = Path(store_root).resolve()
    if root == destination or root.is_relative_to(destination) or receipt.is_relative_to(destination):
        raise ValueError("Native result store and completion receipt must be outside the output link")
    key = digest(identity)
    folder = root / key
    cache = folder / "cache.json"
    with _lock(root):
        if allow_reuse and cache.is_file():
            row = _record(cache, expected_identity=identity, destination=destination)
            if output_inventory(row["payload_root"]) != row["artifacts"]:
                raise ValueError(f"Native cached payload integrity check failed: {row['payload_root']}")
            _publish_link(destination, Path(row["payload_root"]))
            write_json(receipt, row)
            return row, True
        folder.mkdir(parents=True, exist_ok=True)
        attempt = folder / ("attempt-" + uuid.uuid4().hex)
        payload = attempt / "data"
        payload.mkdir(parents=True)
        started = datetime.now(timezone.utc).isoformat()
        write_json(attempt / "started.json", {"schema": SCHEMA, "status": "started", "name": name,
                   "key": key, "identity": identity, "started_at": started})
        try:
            execute(payload)
            _verify_execution_inputs(identity)
            verify(payload)
            row = {"schema": SCHEMA, "status": "completed", "name": name, "key": key,
                   "identity": identity, "payload_root": str(payload), "destination": str(destination),
                   "artifacts": output_inventory(payload), "started_at": started,
                   "finished_at": datetime.now(timezone.utc).isoformat()}
            write_json(attempt / "completed.json", row)
            write_json(cache, row)
            _publish_link(destination, payload)
            write_json(receipt, row)
            return row, False
        except BaseException as error:
            write_json(attempt / "failure.json", {"schema": SCHEMA, "status": "failed", "name": name,
                       "key": key, "identity": identity, "payload_root": str(payload),
                       "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
                       "error": str(error) or type(error).__name__})
            _prune_failed(root, failed_retention)
            raise
