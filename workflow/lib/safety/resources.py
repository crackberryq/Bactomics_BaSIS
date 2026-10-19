"""Resource preparation inside one already activated Snakemake job.

Snakemake owns environment deployment, dependencies, retries and completed
outputs. This module only prepares or verifies one resource and writes its
checksum inventory; it never starts Conda or another workflow.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil


SCHEMA = "basis-snakemake-resource-v1"
PROVIDERS = frozenset({"amrfinder", "card", "rgi", "vfdb", "bacmet", "antismash",
    "genomad", "mobsuite", "integronfinder", "isescan", "icescreen", "minced",
    "mlst", "crisprcasfinder", "prosite", "prott5", "deep_catalogue"})
PATH_FIELDS = frozenset({"database", "metadata", "reference_fasta", "reference_metadata",
    "prot_t5_path", "weights_model", "domain_database", "profiles_dir", "motif_rules",
    "placement_registry", "prosite_snapshot"})
RESOURCE_FIELDS = PATH_FIELDS | frozenset({"database_version", "prot_t5_identity",
    "pf2_source_commit", "expected_models", "version"})


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".writing")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _named_paths(value):
    """Find declared configuration paths, without treating identities as paths."""
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if key in PATH_FIELDS and isinstance(item, str) and item:
            yield Path(item).expanduser()
        elif isinstance(item, dict):
            yield from _named_paths(item)


def external_result(spec):
    result = deepcopy(spec.get("result") or {
        "module_updates": spec.get("module_updates", {}),
        "deep_updates": spec.get("deep_updates", {}),
        "provenance": spec.get("provenance", {}),
    })
    if not isinstance(result, dict):
        raise ValueError("External resource result must be a mapping")
    if not result.get("module_updates") and not result.get("deep_updates"):
        raise ValueError("External resources require explicit module_updates or deep_updates")
    return result


def preparation_spec(spec):
    """Only resource settings belong in this job's tracked parameters.

    Changing sample selection, analysis thresholds or thread allocation must not
    download a new database snapshot. Dependency files and the conda directive
    independently track resource prerequisites and the selected environment.
    """
    result = {key: deepcopy(spec[key]) for key in ("id", "provider", "mode", "version")
              if key in spec}
    options = deepcopy(spec.get("options") or {})
    if "external_modules" in options:
        options["external_modules"] = {
            module: {key: value for key, value in fields.items() if key in RESOURCE_FIELDS}
            for module, fields in options["external_modules"].items()}
    result["options"] = options
    result["module_config"] = {key: deepcopy(value) for key, value in spec.get("module_config", {}).items()
                                if key in RESOURCE_FIELDS}
    if spec.get("mode") == "external":
        external = external_result(spec)
        external["module_updates"] = {
            module: {key: value for key, value in fields.items() if key in RESOURCE_FIELDS}
            for module, fields in external.get("module_updates", {}).items()}
        result["result"] = external
    return result


def external_inputs(spec):
    """Additional real inputs to the resource rule, evaluated at DAG creation."""
    paths = []
    if spec.get("mode") == "external":
        paths.extend(_named_paths(external_result(spec)))
    options = spec.get("options") or {}
    paths.extend(_named_paths(options))
    # Explicit source paths need dependency tracking even with custom namespaces.
    for entry in options.get("reference_sources", []):
        if entry.get("path"):
            paths.append(Path(entry["path"]).expanduser())
    return sorted({str(path.resolve()) for path in paths})


def source_inputs(spec, workflow_root):
    """Declare the implementation and packaged data used to prepare a snapshot."""
    root = Path(workflow_root)
    helpers = root / "workflow/lib/safety"
    paths = [helpers / "resources.py", helpers / "runtime.py"]
    if spec.get("mode") == "external":
        return list(map(str, paths))
    vendor = root / "workflow/vendor/basis"
    name = spec.get("provider", spec.get("id"))
    common = ["inputs.py", "util.py"]
    if name in {"prosite", "prott5", "deep_catalogue"}:
        common += ["bootstrap_deep.py", "references.py"]
        if name == "prosite":
            common += ["prosite.py", "prosite_profiles.py"]
        elif name == "deep_catalogue":
            # Catalogue contents, review declarations and profiles are immutable
            # inputs to this one resource job, not inferred biological labels.
            paths.extend(path for path in (vendor / "resources/comparators/reviewed-v1").rglob("*")
                         if path.is_file())
            paths.extend([vendor / "resources/motifs/rules-v1.json",
                          vendor / "resources/placement/registry-v2.json"])
    elif name in {"mlst", "crisprcasfinder"}:
        common += ["bootstrap_typing_defense.py"]
        if name == "mlst":
            common += ["mlst_database.py"]
    elif name != "minced":
        common += ["bootstrap_providers.py"]
        if name == "bacmet":
            common += ["optional_bacmet.py"]
    paths.extend(vendor / name for name in common)
    return sorted(map(str, set(paths)))


def _inventory(path, *, owned_root=None):
    """Hash actual files, rejecting broken or escaping links in produced data."""
    path = Path(path).resolve(strict=True)
    if not path.is_file() and not path.is_dir():
        raise ValueError("Resource is not an ordinary file or directory: " + str(path))
    paths = [path] if path.is_file() else sorted(path.rglob("*"))
    entries = {}
    for item in paths:
        if item.is_symlink() and not item.exists():
            raise ValueError("Broken resource symlink: " + str(item))
        resolved = item.resolve(strict=True)
        if owned_root is not None and not resolved.is_relative_to(owned_root):
            raise ValueError("Produced resource symlink escapes its output directory: " + str(item))
        if item.is_file():
            entries[str(item)] = {"sha256": sha256(item), "bytes": item.stat().st_size}
    if not entries or not any(entry["bytes"] for entry in entries.values()):
        raise ValueError("Resource contains no nonempty files: " + str(path))
    return entries


def verify_receipt(path, *, full=True):
    """Verify a completed prerequisite when a resource depends on its contents."""
    path = Path(path)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("schema") != SCHEMA or receipt.get("status") != "completed":
        raise ValueError("Resource prerequisite has no completed receipt: " + str(path))
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("Resource receipt has no artifact inventory: " + str(path))
    for name, expected in artifacts.items():
        artifact = Path(name)
        if not artifact.is_file() or artifact.stat().st_size != expected["bytes"]:
            raise ValueError("Missing or changed resource artifact: " + name)
        if full and sha256(artifact) != expected["sha256"]:
            raise ValueError("Resource checksum mismatch: " + name)
    return receipt


def _provider(spec, data, runner):
    name = spec["provider"]
    if name in {"prosite", "prott5", "deep_catalogue"}:
        from basis.bootstrap_deep import prepare_deep_resource
        return prepare_deep_resource(spec, data, runner)
    if name in {"mlst", "crisprcasfinder"}:
        from basis.bootstrap_typing_defense import prepare_resource
        return prepare_resource(spec, data, runner)
    if name == "minced":
        version = runner.run(["minced", "--version"]).stdout.strip()
        atomic_json(data / "installed-tool.json", {
            "tool": "minced", "version": version,
            "database": "No external database required", "analyses_run": False})
        return {"module_updates": {}, "deep_updates": {},
                "provenance": {"provider": "minced", "tool_version": version}}
    from basis.bootstrap_providers import prepare_resource
    return prepare_resource(spec, data, runner)


def prepare(name, spec, resource_root, logfile, *, threads=1, dependency_receipts=(), runner=None):
    """Build one owned output. A failed invocation never writes a success receipt."""
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        raise ValueError("Invalid resource name")
    spec = deepcopy(spec)
    provider = spec.setdefault("provider", name)
    if provider not in PROVIDERS:
        raise ValueError("Unsupported resource provider: " + str(provider))
    if int(threads) < 1:
        raise ValueError("Resource threads must be positive")
    root = Path(resource_root).expanduser().resolve()
    destination = root / name
    if destination.is_symlink():
        raise ValueError("Resource output directory must not be a symlink")
    destination.mkdir(parents=True, exist_ok=True)
    data = destination / "data"
    receipt_path = destination / "provenance.json"
    # This is the single job's declared output directory. Completed sibling
    # resources and the separate resumable download cache are never touched.
    if data.is_symlink():
        raise ValueError("Resource data output must not be a symlink")
    receipt_path.unlink(missing_ok=True)
    dependencies = [verify_receipt(path) for path in dependency_receipts]
    spec["dependency_resources"] = [entry["result"] for entry in dependencies]
    spec["threads"] = int(threads)
    spec["cache_directory"] = str(root)
    spec.pop("command_prefix", None)
    spec["module_config"] = {**spec.get("module_config", {}), "command_prefix": []}
    if spec.get("mode") == "external":
        result = external_result(spec)
        declared = list(_named_paths(result))
        if not declared:
            raise ValueError("No external resource paths were declared")
        artifacts = {}
        for path in declared:
            resolved = path.resolve(strict=True)
            if resolved == destination or resolved.is_relative_to(destination):
                raise ValueError("External resource must be outside its generated receipt directory")
            artifacts.update(_inventory(resolved))
        if data.exists():
            shutil.rmtree(data)
        data.mkdir()
        atomic_json(data / "external-resource.json", {
            "mode": "external", "inputs": sorted(map(str, declared)),
            "source_files_modified": False, "downloads_performed": False})
    else:
        if data.exists():
            shutil.rmtree(data)
        data.mkdir()
        if runner is None:
            from .runtime import DirectRunner
            runner = DirectRunner({**spec.get("module_config", {}), "threads": threads,
                "command_prefix": [], "timeout_seconds": spec.get("timeout_seconds", 14400)}, logfile)
        result = _provider(spec, data, runner)
        artifacts = _inventory(data, owned_root=data.resolve())
        # Environment-bundled databases and explicitly retained resources remain
        # outside data; their bytes are also part of the resource provenance.
        for path in _named_paths(result):
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(data.resolve()):
                artifacts.update(_inventory(resolved))
    artifacts.update(_inventory(data, owned_root=data.resolve()))
    receipt = {
        "schema": SCHEMA, "status": "completed", "resource": name,
        "mode": spec.get("mode", "native"), "provider": provider,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifacts": artifacts, "result": result,
        "module_updates": result.get("module_updates", {}),
        "deep_updates": result.get("deep_updates", {}),
        "provenance": result.get("provenance", {}),
        "dependencies": {str(Path(path).resolve()): sha256(path) for path in dependency_receipts},
        "execution_owner": "snakemake", "analyses_run": False,
    }
    atomic_json(receipt_path, receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("prepare")
    command.add_argument("--name", required=True)
    command.add_argument("--spec-json", required=True)
    command.add_argument("--resource-root", required=True)
    command.add_argument("--logfile", required=True)
    command.add_argument("--threads", type=int, default=1)
    command.add_argument("--dependency", action="append", default=[])
    args = parser.parse_args(argv)
    receipt = prepare(args.name, json.loads(args.spec_json), args.resource_root, args.logfile,
                      threads=args.threads, dependency_receipts=args.dependency)
    print(f"Resource ready: {receipt['resource']} | {len(receipt['artifacts'])} recorded files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
