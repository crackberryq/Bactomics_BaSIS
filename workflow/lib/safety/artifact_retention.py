"""Retain local products before Snakemake prepares a replacement job.

The engine removes output files and directory() trees *before* the rule body.
This small, version-gated adapter runs before that removal, including retries.
Only the installed workflow is affected. No analysis parameters are changed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import re
import uuid


SUPPORTED_SNAKEMAKE = {"9.6.2", "9.27.1"}


def verified_snakemake_version():
    """Check the running engine, accommodating Conda's 0.0.0 metadata stub.

    The imported runtime supplies the scheduler API we wrap. Distribution
    metadata is a consistency check when it contains a real version, not an
    alternative authority for an unknown or unsupported runtime.
    """
    import snakemake

    version = getattr(snakemake, "__version__", None)
    if version not in SUPPORTED_SNAKEMAKE:
        raise RuntimeError(f"Output retention has not been verified for Snakemake runtime {version!r}; use one of {sorted(SUPPORTED_SNAKEMAKE)}")
    try:
        distribution = importlib.metadata.version("snakemake")
    except importlib.metadata.PackageNotFoundError:
        distribution = None
    if distribution not in {None, "0.0.0", version}:
        raise RuntimeError(f"Snakemake runtime version {version} conflicts with distribution metadata {distribution!r}; repair the controller environment before running")
    return version


def _write_manifest(path, value):
    temporary = path.with_suffix(".pending")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _absolute(path):
    # Do not follow the final symlink: retain the link, never move its target.
    return Path(os.path.abspath(os.fspath(path)))


def archive_paths(base_dir, paths, *, rule, wildcards=None, inputs=(), reason="before-rerun"):
    """Move owned local products to a unique archive; fail before any deletion.

    Moves preserve bytes without hardlinks or a second full-size copy. The
    manifest is written before the first move and updated after every move, so
    interrupted preservation remains inspectable. Cross-filesystem moves fail
    closed; there is deliberately no copy/delete fallback.
    """
    base = _absolute(base_dir)
    archive_root = base / "retained"
    candidates = []
    for value in paths:
        path = _absolute(value)
        if not os.path.lexists(path):
            continue
        if path == base or not path.is_relative_to(base) or path.is_relative_to(archive_root):
            raise ValueError(f"Refusing to retain an unowned output path: {path}")
        # A symlinked ancestor could turn a lexical child into external data.
        if path.parent.resolve() != path.parent or base.resolve() != base:
            raise ValueError(f"Retention requires output directories without symlinked ancestors: {path}")
        for value_in in inputs:
            source = _absolute(value_in)
            if source == path or source.is_relative_to(path):
                raise ValueError(f"Refusing to move a product containing a current input: {path}")
        candidates.append(path)
    # Retaining a directory already retains all of its listed children.
    selected = []
    for path in sorted(set(candidates), key=lambda item: (len(item.parts), str(item))):
        if not any(path.is_relative_to(parent) for parent in selected):
            selected.append(path)
    if not selected:
        return None
    if archive_root.is_symlink():
        raise ValueError(f"Retention archive must not be a symlink: {archive_root}")
    label = re.sub(r"[^A-Za-z0-9_.-]", "_", str(rule))[:80]
    identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + label + "-" + uuid.uuid4().hex
    destination = archive_root / identifier
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"schema": "workflow-output-retention-v1", "state": "archiving",
        "reason": reason, "rule": str(rule), "wildcards": dict(wildcards or {}),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prior_execution_status": "not_inferred", "entries": []}
    for path in selected:
        relative = path.relative_to(base).as_posix()
        entry = {"original": str(path), "archive": "files/" + relative, "moved": False}
        if path.is_symlink():
            entry.update(kind="symlink", target=os.readlink(path))
        else:
            entry["kind"] = "directory" if path.is_dir() else "file"
        manifest["entries"].append(entry)
    manifest_path = destination / "manifest.json"
    _write_manifest(manifest_path, manifest)
    try:
        for path, entry in zip(selected, manifest["entries"]):
            target = destination / entry["archive"]
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)
            entry["moved"] = True
            _write_manifest(manifest_path, manifest)
        manifest["state"] = "complete"
        _write_manifest(manifest_path, manifest)
    except BaseException as error:
        manifest.update(state="interrupted", error=str(error))
        _write_manifest(manifest_path, manifest)
        raise RuntimeError(f"Output preservation failed; no replacement job may run. Inspect {manifest_path}") from error
    return destination


def _job_paths(job):
    products = list(job.output) + list(job.log)
    if job.benchmark:
        products.append(job.benchmark)
    # Native tool sidecars are not necessarily individual Snakemake outputs.
    products.extend(getattr(job.params, "retention_paths", []))
    return products


def install(workflow, base_dirs):
    """Install before scheduling, including native standalone invocation.

    ``params.retention_paths`` lists directories/files exclusively owned by a
    rule. Do not list a shared work directory or a rule's inputs there.
    """
    verified_snakemake_version()
    from snakemake.jobs import Job
    original = Job.remove_existing_output
    if not inspect.iscoroutinefunction(original) or list(inspect.signature(original).parameters) != ["self"]:
        raise RuntimeError("Snakemake output preparation changed; refusing to run without output retention")
    if workflow.execution_settings is not None:
        workflow.execution_settings.keep_incomplete = True
    if workflow.storage_settings is not None:
        workflow.storage_settings.notemp = True
    workflow._basis_retention_roots = tuple(str(_absolute(path)) for path in base_dirs)
    if getattr(original, "_basis_artifact_retention", False):
        return

    @wraps(original)
    async def preserve_then_remove(self):
        owner = self.rule.workflow
        bases = getattr(owner, "_basis_retention_roots", None)
        if bases is not None:
            owner.execution_settings.keep_incomplete = True
            owner.storage_settings.notemp = True
            # Fail closed rather than accidentally delete a remote or in-place
            # product; shipped workflows use ordinary local output paths.
            from snakemake.io import is_flagged
            if any(getattr(item, "is_storage", False) or is_flagged(item, "update") for item in self.output):
                raise RuntimeError("Output retention requires local, non-in-place products")
            grouped = {}
            for product in _job_paths(self):
                path = _absolute(product)
                matches = [Path(base) for base in bases if path.is_relative_to(base)]
                if not matches:
                    raise RuntimeError(f"Output is outside configured BaSIS retention roots: {path}")
                base = max(matches, key=lambda item: len(item.parts))
                grouped.setdefault(base, []).append(path)
            for base, paths in grouped.items():
                archive_paths(base, paths, rule=self.rule.name,
                              wildcards=self.wildcards_dict, inputs=self.input)
        await original(self)

    preserve_then_remove._basis_artifact_retention = True
    Job.remove_existing_output = preserve_then_remove
