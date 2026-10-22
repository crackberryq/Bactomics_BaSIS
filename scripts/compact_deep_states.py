#!/usr/bin/env python3
"""Review or compact persisted state storage without running workflow jobs.

The default is a dry run. Stop writers before using --apply. Each replacement
is atomic, but a batch is not a transaction: interrupted batches remain readable
and can be reviewed and resumed. No native output, evidence, workflow completion
flag, or Snakemake provenance metadata is changed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety import state_storage as storage

LEGACY_SCHEMA = "basis-snakemake-deep-state-v1"
COMPACT_SCHEMA = "basis-snakemake-deep-state-v2"
DISCOVERY_SCHEMA = "basis-snakemake-deep-family-inputs-v1"


class CompactionError(RuntimeError):
    def __init__(self, message, audit_paths=()):
        super().__init__(message)
        self.audit_paths = list(audit_paths)


@dataclass
class Original:
    path: Path
    stat: os.stat_result
    byte_sha256: str
    logical_sha256: str
    schema: str
    audit: dict


def validate_document(path, raw=None):
    # This loader checks the original input identity and resource provenance.
    # It does not initialize outputs, construct a runner, or execute a stage.
    from safety.deep import load_document
    return load_document(path, raw=raw)


def byte_digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def fingerprint(value):
    # Reads may update atime; all other relevant identity/change fields must
    # remain unchanged between validation and replacement.
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def assert_unchanged(path, original_stat, original_sha256):
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode)
            or fingerprint(before) != fingerprint(original_stat)
            or byte_digest(path) != original_sha256
            or fingerprint(path.lstat()) != fingerprint(original_stat)):
        raise ValueError("State changed during storage review: " + str(path))


def state_paths(root):
    paths = set(root.glob("states/*.json"))
    paths.update(root.glob("candidates/*/*.json"))
    return sorted(paths)


def storage_bytes(root, paths):
    objects = list((root / "state-objects").glob("*.json"))
    state_size = sum(path.stat().st_size for path in paths if path.is_file())
    object_size = sum(path.stat().st_size for path in objects if path.is_file())
    return {"state_bytes": state_size, "object_bytes": object_size,
            "total_bytes": state_size + object_size, "object_count": len(objects)}


def write_audit(path, value):
    if path.parent.is_symlink() or not path.parent.resolve().is_relative_to(path.parent.parent.resolve()):
        raise ValueError("Audit directory must not be a symlink or escape the state root: " + str(path.parent))
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".review-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        storage.sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def isolate_batch(isolates, *, apply=False, progress=None):
    """Validate all requested isolates before replacing any original state."""
    roots = []
    for isolate in isolates:
        root = (Path(isolate).resolve() / "safety/deepsafe").resolve()
        if not root.is_dir():
            raise CompactionError("Missing state directory: " + str(root))
        if root not in roots:
            roots.append(root)
    if not roots:
        raise CompactionError("At least one isolate is required")

    now = datetime.now(timezone.utc)
    review_id = now.strftime("%Y%m%dT%H%M%S.%fZ-") + uuid.uuid4().hex
    reviews = {}
    originals = []
    for root in roots:
        reviews[root] = {
            "schema": "basis-deep-storage-review-v1", "review_id": review_id,
            "started_at": now.isoformat(), "mode": "apply" if apply else "dry-run",
            "status": "preflight", "root": str(root), "files": [], "skipped": [],
            "warnings": [
                "Storage validation does not certify workflow completion.",
                "Replacements are atomic per file; a batch is resumable, not transactional.",
                "No native jobs run and no Snakemake provenance is changed.",
                "Unreferenced immutable objects are retained.",
            ],
        }
    audit_paths = {root: root / "storage-reviews" / (review_id + ".json") for root in roots}

    def save_reviews():
        for root, review in reviews.items():
            write_audit(audit_paths[root], review)

    def report(message):
        if progress is not None:
            progress(message)

    try:
        discovered = {root: state_paths(root) for root in roots}
        total = sum(len(paths) for paths in discovered.values())
        scanned = 0
        report(f"Preflight: checking {total} JSON files; no disk space is reclaimed during validation.")
        for root in roots:
            review = reviews[root]
            candidates = discovered[root]
            review["before_storage"] = storage_bytes(root, candidates)
            for path in candidates:
                original_stat = path.lstat()
                scanned += 1
                report(f"[preflight {scanned}/{total}] {original_stat.st_size / 1024 ** 2:.2f} MiB {path}")
                if not stat.S_ISREG(original_stat.st_mode) or not path.resolve().is_relative_to(root):
                    raise ValueError("Refusing non-regular or escaped state path: " + str(path))
                original_sha256 = byte_digest(path)
                raw = storage.read_raw(path)
                if not isinstance(raw, dict):
                    raise ValueError("State JSON is not an object: " + str(path))
                schema = raw.get("schema")
                if schema not in {LEGACY_SCHEMA, COMPACT_SCHEMA}:
                    if schema != DISCOVERY_SCHEMA:
                        raise ValueError("Unrecognized or incomplete state schema: " + str(path))
                    review["skipped"].append({"path": str(path), "schema": schema,
                                               "reason": "Family input discovery document"})
                    del raw
                    continue
                document = validate_document(path, raw=raw)
                del raw
                logical_sha256 = storage.logical_digest(document)
                assert_unchanged(path, original_stat, original_sha256)
                entry = {
                    "path": str(path), "schema_before": schema,
                    "before_sha256": original_sha256, "before_bytes": original_stat.st_size,
                    "mtime_ns": original_stat.st_mtime_ns,
                    "atime_ns": original_stat.st_atime_ns,
                    "mode": stat.S_IMODE(original_stat.st_mode),
                    "logical_sha256": logical_sha256,
                    "context_sha256": document.get("context_sha256"),
                    "state_identity_sha256": storage.logical_digest(document["state"].get("identity")),
                    "action": "already-compact" if schema == COMPACT_SCHEMA else "would-compact",
                }
                del document
                review["files"].append(entry)
                originals.append(Original(path, original_stat, original_sha256,
                                          logical_sha256, schema, entry))
        for review in reviews.values():
            review["status"] = "applying" if apply else "dry-run-complete"
        save_reviews()

        if apply:
            report(f"Preflight passed for {len(originals)} states; beginning atomic replacements.")
            for position, original in enumerate(originals, 1):
                path, entry = original.path, original.audit
                label = "already compact" if original.schema == COMPACT_SCHEMA else "input"
                report(f"[compact {position}/{len(originals)}] {label} "
                       f"{original.stat.st_size / 1024 ** 2:.2f} MiB {path}")
                assert_unchanged(path, original.stat, original.byte_sha256)
                if original.schema != COMPACT_SCHEMA:
                    document = validate_document(path)
                    if storage.logical_digest(document) != original.logical_sha256:
                        raise ValueError("Logical state changed during storage review: " + str(path))
                    try:
                        storage.write_document(
                            path, document, preserve_stat=original.stat,
                            before_replace=lambda original=original: assert_unchanged(
                                original.path, original.stat, original.byte_sha256))
                    except Exception as exc:
                        if isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, errno.EDQUOT}:
                            raise RuntimeError(
                                f"Insufficient disk space to compact {path}. "
                                "Preflight does not reclaim disk space. New shared objects and a "
                                "temporary state must be written before the original is replaced. "
                                "Previously compacted files remain valid; free space and rerun. "
                                f"Original error: {exc}") from exc
                        raise RuntimeError(f"Cannot compact state {path}: {exc}") from exc
                    del document
                    entry["action"] = "compacted"
                # The storage writer verifies the hydrated temporary document
                # before replacement. Record the published bytes as an audit.
                entry["after_sha256"] = byte_digest(path)
                entry["after_bytes"] = path.stat().st_size
                # Hashing/validation can update atime; restore both original
                # timestamps after the final read, including unchanged v2 files.
                os.utime(path, ns=(original.stat.st_atime_ns, original.stat.st_mtime_ns))
                save_reviews()
        else:
            for original in originals:
                original.audit.update(after_sha256=original.byte_sha256,
                                      after_bytes=original.stat.st_size)

        for root, review in reviews.items():
            review["status"] = "apply-complete" if apply else "dry-run-complete"
            review["finished_at"] = datetime.now(timezone.utc).isoformat()
            review["after_storage"] = storage_bytes(root, state_paths(root))
        save_reviews()
        return [str(audit_paths[root]) for root in roots]
    except Exception as exc:
        for root, review in reviews.items():
            review.update(status="failed", error=f"{type(exc).__name__}: {exc}",
                          finished_at=datetime.now(timezone.utc).isoformat())
            try:
                review["after_storage"] = storage_bytes(root, state_paths(root))
            except Exception as sizing_error:
                review["after_storage"] = None
                review["storage_measurement_error"] = f"{type(sizing_error).__name__}: {sizing_error}"
        try:
            save_reviews()
        except Exception as audit_error:
            raise CompactionError(f"{exc}; could not save audit: {audit_error}",
                                  [str(p) for p in audit_paths.values() if p.exists()]) from exc
        raise CompactionError(str(exc), [str(p) for p in audit_paths.values()]) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolate", action="append", required=True, type=Path,
                        help="Isolate directory; may be repeated")
    parser.add_argument("--apply", action="store_true",
                        help="Replace validated legacy states; default only reviews storage")
    args = parser.parse_args(argv)
    try:
        audits = isolate_batch(args.isolate, apply=args.apply,
                               progress=lambda message: print(message, flush=True))
    except CompactionError as exc:
        print("Storage review failed: " + str(exc), file=sys.stderr)
        for path in exc.audit_paths:
            print("Audit: " + path, file=sys.stderr)
        return 1
    print("Compaction complete." if args.apply else "Dry run complete; no state files replaced.")
    reviews = [storage.read_raw(path) for path in audits]
    files = [entry for review in reviews for entry in review["files"]]
    compacted = sum(entry["action"] == "compacted" for entry in files)
    already = sum(entry["action"] == "already-compact" for entry in files)
    eligible = sum(entry["action"] == "would-compact" for entry in files)
    skipped = sum(len(review["skipped"]) for review in reviews)
    if args.apply:
        print(f"States: {compacted} compacted, {already} already compact; {skipped} discovery files skipped.")
    else:
        print(f"States: {len(files)} reviewed, {eligible} eligible, {already} already compact; "
              f"{skipped} discovery files skipped.")
    before = {key: sum(review["before_storage"][key] for review in reviews)
              for key in ("state_bytes", "object_bytes", "total_bytes")}
    after = {key: sum(review["after_storage"][key] for review in reviews)
             for key in before}
    gib = 1024 ** 3
    print(f"State files: {before['state_bytes'] / gib:.3f} -> {after['state_bytes'] / gib:.3f} GiB; "
          f"shared objects: {before['object_bytes'] / gib:.3f} -> {after['object_bytes'] / gib:.3f} GiB.")
    print(f"Observed total: {before['total_bytes'] / gib:.3f} -> {after['total_bytes'] / gib:.3f} GiB; "
          f"reclaimed: {(before['total_bytes'] - after['total_bytes']) / gib:.3f} GiB.")
    if not args.apply:
        print("Dry-run totals are measured storage, not a prediction of compacted size.")
    for path in audits:
        print("Audit: " + path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
