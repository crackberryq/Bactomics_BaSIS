"""Lossless, content-addressed JSON storage for workflow checkpoints.

This module only serializes data. It never executes an analysis, changes a
result, marks a job complete, or edits Snakemake's metadata.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

LEGACY_SCHEMA = "basis-snakemake-deep-state-v1"
COMPACT_SCHEMA = "basis-snakemake-deep-state-v2"
LOCATIONS = (("context",), ("state", "internal"),
             ("state", "family_inventory"), ("budget",))
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def logical_digest(value):
    """Preserve the v1 digest exactly without a second full JSON string."""
    result = hashlib.sha256()
    encoder = json.JSONEncoder(sort_keys=True, default=str, allow_nan=False)
    for chunk in encoder.iterencode(value):
        result.update(chunk.encode("utf-8"))
    return result.hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def _invalid_number(value):
    raise ValueError("Invalid JSON number: " + value)


def read_raw(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=_unique,
                         parse_constant=_invalid_number)


def store_directory(path):
    """Blobs are outside all job-owned native directories."""
    path = Path(path).absolute()
    for parent in path.parents:
        if parent.name == "deepsafe":
            return parent / "state-objects"
    return path.parent / "state-objects"


def _reference_path(path, reference):
    if not isinstance(reference, dict) or set(reference) != {"location", "sha256", "path"}:
        raise ValueError("Malformed shared state reference")
    location = reference["location"]
    if not isinstance(location, list) or tuple(location) not in LOCATIONS:
        raise ValueError("Unknown shared state location")
    digest = reference["sha256"]
    if not isinstance(digest, str) or not _HEX.fullmatch(digest):
        raise ValueError("Malformed shared state checksum")
    path = Path(path).absolute()
    expected = store_directory(path) / (digest + ".json")
    if reference["path"] != os.path.relpath(expected, path.parent):
        raise ValueError("Shared state reference is outside its object store")
    if expected.is_symlink() or expected.parent.is_symlink():
        raise ValueError("Shared state object/store must not be a symlink")
    return expected


def _references(path, raw):
    references = raw.get("shared_objects")
    if not isinstance(references, list):
        raise ValueError("Compact state lacks shared object references")
    seen = set()
    result = []
    for reference in references:
        target = _reference_path(path, reference)
        location = tuple(reference["location"])
        if location in seen:
            raise ValueError("Duplicate shared state location")
        seen.add(location)
        result.append((reference, target))
    if ("context",) not in seen:
        raise ValueError("Compact state lacks its shared context")
    return result


def object_paths(path):
    """Read only the compact envelope for dependency discovery."""
    raw = read_raw(path)
    if not isinstance(raw, dict):
        raise ValueError("Workflow state must be an object")
    if raw.get("schema") == LEGACY_SCHEMA:
        return []
    if raw.get("schema") != COMPACT_SCHEMA:
        raise ValueError("Unrecognized workflow state: " + str(path))
    return [target for _, target in _references(path, raw)]


def _signature(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_document(path, shared_cache=None, raw=None):
    """Hydrate either storage version to the exact logical v1 document."""
    if raw is None:
        raw = read_raw(path)
    if not isinstance(raw, dict):
        raise ValueError("Workflow state must be an object")
    if raw.get("schema") == LEGACY_SCHEMA:
        if "shared_objects" in raw:
            raise ValueError("Legacy state contains reserved shared_objects field")
        return raw
    if raw.get("schema") != COMPACT_SCHEMA:
        raise ValueError("Unrecognized workflow state: " + str(path))
    for reference, target in _references(path, raw):
        before = _signature(target)
        cache_key = (str(target), reference["sha256"], before)
        value = shared_cache.get(cache_key) if shared_cache is not None else None
        if value is None:
            value = read_raw(target)
            if logical_digest(value) != reference["sha256"]:
                raise ValueError("Shared state checksum mismatch: " + str(target))
            if _signature(target) != before:
                raise ValueError("Shared state changed while reading: " + str(target))
            if shared_cache is not None:
                # A small per-invocation cache; no cross-run trust of a digest.
                if len(shared_cache) >= 8:
                    shared_cache.pop(next(iter(shared_cache)))
                shared_cache[cache_key] = value
        container = raw
        for part in reference["location"][:-1]:
            container = container.get(part)
            if not isinstance(container, dict):
                raise ValueError("Shared state parent is missing or malformed")
        name = reference["location"][-1]
        if name in container:
            raise ValueError("Compact state contains both inline and shared values")
        container[name] = value
    del raw["shared_objects"]
    raw["schema"] = LEGACY_SCHEMA
    return raw


def _dump(stream, value, *, indent=None):
    json.dump(value, stream, sort_keys=True, default=str, allow_nan=False, indent=indent)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())


def sync_directory(path):
    """Persist directory entries before publishing references to their files."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _grant_read_access(path, source_stat):
    if source_stat is None:
        return
    current = path.stat()
    needed = stat.S_IMODE(source_stat.st_mode) & 0o444
    if needed & 0o040 and current.st_gid != source_stat.st_gid:
        if current.st_mode & 0o040:
            raise ValueError("Shared state object has a conflicting reader group: " + str(path))
        os.chown(path, -1, source_stat.st_gid)
    os.chmod(path, stat.S_IMODE(current.st_mode) | needed)
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _publish_object(directory, value, source_stat=None):
    digest = logical_digest(value)
    target = directory / (digest + ".json")
    if directory.is_symlink() or target.is_symlink():
        raise ValueError("Shared state object/store must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    sync_directory(directory.parent)
    if target.exists():
        if logical_digest(read_raw(target)) != digest:
            raise ValueError("Existing shared state checksum mismatch: " + str(target))
        _grant_read_access(target, source_stat)
        sync_directory(directory)
        return target, digest
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".object-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            _dump(stream, value)
        _grant_read_access(temporary, source_stat)
        # Publish complete bytes without ever replacing an immutable object.
        try:
            os.link(temporary, target)
        except FileExistsError:
            if logical_digest(read_raw(target)) != digest:
                raise ValueError("Concurrent shared state checksum mismatch: " + str(target))
            _grant_read_access(target, source_stat)
        sync_directory(directory)
        return target, digest
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_document(path, document, *, preserve_stat=None, before_replace=None):
    """Publish shared data, verify roundtrip, then atomically replace one state.

    Optional stat preservation is for offline, lossless migration only. This
    does not modify scheduler receipts or claim native work was performed.
    """
    path = Path(path).absolute()
    if path.is_symlink():
        raise ValueError("Workflow state must not be a symlink")
    if document.get("schema") != LEGACY_SCHEMA or "shared_objects" in document:
        raise ValueError("Expected a hydrated v1 workflow state")
    if logical_digest(document["context"]) != document.get("context_sha256"):
        raise ValueError("DeepSafe context changed: " + str(path))
    original_digest = logical_digest(document)
    compact = dict(document)
    compact["state"] = dict(document["state"])
    compact["schema"] = COMPACT_SCHEMA
    compact["shared_objects"] = []
    directory = store_directory(path)
    for location in LOCATIONS:
        container = compact
        for part in location[:-1]:
            container = container[part]
        if location[-1] not in container:
            continue
        value = container.pop(location[-1])
        target, digest = _publish_object(directory, value, preserve_stat)
        compact["shared_objects"].append({"location": list(location), "sha256": digest,
            "path": os.path.relpath(target, path.parent)})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".state-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            _dump(stream, compact, indent=2)
        # Includes every unknown metadata field and all original results.
        if logical_digest(read_document(temporary)) != original_digest:
            raise ValueError("Compact workflow state failed lossless roundtrip")
        if preserve_stat is not None:
            if (temporary.stat().st_uid, temporary.stat().st_gid) != (preserve_stat.st_uid, preserve_stat.st_gid):
                os.chown(temporary, preserve_stat.st_uid, preserve_stat.st_gid)
            os.chmod(temporary, stat.S_IMODE(preserve_stat.st_mode))
            os.utime(temporary, ns=(preserve_stat.st_atime_ns, preserve_stat.st_mtime_ns))
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
        if before_replace is not None:
            before_replace()
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

