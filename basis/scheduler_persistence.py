"""Integrity checks for stable Snakemake targets; never schedules or runs analyses.

A successful marker is a claim about immutable evidence. Before constructing the
DAG, revoke claims whose evidence/resources changed. Snakemake alone decides
which missing/incomplete targets and descendants need to run.
"""
from __future__ import annotations

import os
from pathlib import Path

from .util import atomic_json, hash_object, now, read_json, sha256_file


def state_name(plan):
    return "state-" + plan["plan_hash"]


def resource_fingerprints(config, require_present=False):
    """Fingerprint the configured resource closure, including profile overrides."""
    import json
    import yaml
    from .config import PATH_KEYS
    from .pipeline import path_fingerprint
    paths = {}
    untracked = []
    def add(key, path):
        value = path_fingerprint(path)
        if require_present and value.get("missing"):
            raise ValueError("Configured resource does not exist: " + key + "=" + str(path))
        paths[key] = value
    def domain(key, path):
        add(key, path)
        for suffix in (".h3f", ".h3i", ".h3m", ".h3p"):
            add(key + suffix, str(path) + suffix)
    for key in sorted(PATH_KEYS - {"placement_cache_dir"}):
        if config.get(key):
            add(key, config[key])
    if config.get("domain_database"):
        domain("domain_database", config["domain_database"])
    if config.get("profiles_dir"):
        directory = Path(config["profiles_dir"])
        for source in sorted(set(directory.glob("*.yaml")) | set(directory.glob("*.yml"))):
            profile = yaml.safe_load(source.read_text(encoding="utf-8"))
            if not isinstance(profile, dict):
                raise ValueError("Profile must be a mapping: " + str(source))
            resources = profile.get("resources") or {}
            if not isinstance(resources, dict):
                untracked.append(str(source))
                continue
            if set(resources) - {"domains"}:
                untracked.append(str(source))
            resource = resources.get("domains") or {}
            if isinstance(resource, str):
                resource = {"database": resource}
            if not isinstance(resource, dict) or set(resource) - {"database"}:
                untracked.append(str(source))
            if isinstance(resource, dict) and resource.get("database"):
                database = Path(resource["database"]).expanduser()
                if not database.is_absolute():
                    database = source.parent / database
                domain("profile:" + str(source.resolve()), database.resolve())
    if config.get("tree_review_registry"):
        registry = Path(config["tree_review_registry"])
        if registry.is_file():
            data = json.loads(registry.read_text())
            for index, row in enumerate(data.get("records", [])):
                for raw in row.get("artifacts", {}):
                    path = Path(raw)
                    # Native review reuse interprets these against worker cwd;
                    # unknown relative-path scope must never authorize reuse.
                    if not path.is_absolute():
                        untracked.append("tree_review_registry:" + str(index) + ":" + raw)
                        continue
                    add("review_artifact:" + str(path), path)
    if untracked:
        paths["untracked_resource_scopes"] = sorted(set(untracked))
    return paths


def file_inventory(root):
    """Match the scientific DeepSafe engine identity exclusions."""
    root = Path(root).resolve()
    return {str(path.relative_to(root)): sha256_file(path)
            for path in sorted(root.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
            and not (path.name in {"history", ".conda_lock"} and "conda-meta" in path.parts)}


TOOL_PROBE = r'''
import hashlib,json,shutil,sys
from pathlib import Path
root=Path(sys.prefix).resolve()
names=json.loads(sys.argv[1]); binaries={}
for name in names:
    raw=shutil.which(name)
    if not raw: raise RuntimeError('Missing required executable: '+name)
    path=Path(raw).resolve()
    with path.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
    binaries[str(path)]=digest
print(json.dumps({'prefix':str(root),'executables':binaries,'conda':(root/'conda-meta').is_dir()}))
'''


def capture_tool_identity(module, config, runner):
    """Verify startup plus tool/environment bytes before the setup barrier."""
    import json
    from .pipeline import path_fingerprint
    if module == "deepsafe":
        names = ["hmmscan", "mafft", "iqtree"]
        mode = config.get("reference_search_tool", "blastp")
        if mode in {"blastp", "diamond"}:
            names.append(mode)
        if config.get("family_placement"):
            names.append("epa-ng")
        if config.get("prosite_snapshot"):
            names.append("perl")
        names = [config.get("executables", {}).get(name, name) for name in names]
        if config.get("prosite_profiles"):
            names.append(config.get("executables", {}).get("pfscan", "pfscanV3"))
    else:
        names = [config["executable"]]
    identity = json.loads(runner.run([config.get("python_executable", "python"), "-c", TOOL_PROBE,
                                      json.dumps(names)]).stdout)
    if not identity.get("executables"):
        raise ValueError("Tool identity probe returned no verified executables")
    if identity.get("conda"):
        identity["environment_sha256"] = hash_object(file_inventory(identity["prefix"]))
    prefix = Path(identity["prefix"])
    identity["reuse_supported"] = bool(identity.get("conda")) and all(
        Path(path).is_relative_to(prefix) for path in identity["executables"])
    return identity


def _verify_artifacts(value, digest):
    for path in value.get("artifacts", []):
        if isinstance(path, str) and not Path(path).exists():
            raise ValueError("Recorded artifact is missing: " + path)
    maps = [value.get("artifact_checksums", {})]
    if value.get("deep_cache"):
        maps.append(value["deep_cache"].get("checksums", {}))
    for checks in maps:
        for path, expected in checks.items():
            if digest(path) != expected:
                raise ValueError("Artifact checksum changed: " + str(path))


def prepare_scheduler_state(plan, root, launch_root, force=False):
    """Audit existing success markers; preserve diagnostics and native files."""
    from .pipeline import _read_scheduler_result
    from .progress import emit
    emit("Verifying saved Snakemake completion records and resource fingerprints")
    from .workflow_runtime import _identity, _write, read_document
    root, launch_root = Path(root), Path(launch_root)
    root.mkdir(parents=True, exist_ok=True)
    stable_context = {"schema": "basis-stable-scheduler-context-v1", **_identity(plan, root),
                      "config_hash": hash_object(plan["config"])}
    context = root / "state-context.json"
    if context.exists():
        previous = read_document(context)
        previous.pop("content_sha256", None)
        if previous != stable_context:
            raise ValueError("Stable scheduler namespace identity mismatch")
    else:
        _write(context, stable_context)
    audit = {"schema": "basis-scheduler-integrity-audit-v1", "plan_hash": plan["plan_hash"],
             "state_root": str(root), "launch_id": launch_root.name, "checked_at": now(),
             "force_requested": bool(force), "retained": [], "revoked": [],
             "scope": "Verify completed targets only; scheduling and retries belong to Snakemake."}
    digests, inventories, resources = {}, {}, {}
    def digest(path):
        path = str(path)
        if path not in digests:
            digests[path] = sha256_file(path)
        return digests[path]
    for marker in sorted(root.rglob("*.ready.json")):
        try:
            ready = read_document(marker)
            if ready.get("run_id") != root.name or ready.get("plan_hash") != plan["plan_hash"]:
                raise ValueError("Ready marker identity mismatch")
            for dependency, expected in ready.get("dependency_checksums", {}).items():
                if not Path(dependency).is_file() or sha256_file(dependency) != expected:
                    raise ValueError("Upstream completion claim changed: " + dependency)
            if ready.get("schema") == "basis-bootstrap-ready-v1":
                from .bootstrap import _load_receipt, verify_snapshot
                resource_id = ready["resource_id"]
                spec = next(s for s in plan.get("bootstrap_resources", []) if s["id"] == resource_id)
                _load_receipt(plan, root, spec)
                verify_snapshot(spec)
            else:
                receipt = Path(ready["receipt"])
                if not receipt.resolve().is_relative_to(root.resolve()):
                    raise ValueError("Ready receipt is outside scheduler state")
                if digest(receipt) != ready["receipt_sha256"]:
                    raise ValueError("Ready receipt checksum mismatch")
                value = read_json(receipt)
                if value.get("schema") == "basis-scheduler-receipt-v1":
                    result = _read_scheduler_result(plan, value["sample_id"], value["module"], root)
                    if result.get("execution_status") in {"failed", "blocked"}:
                        raise ValueError("Ready marker points at unsuccessful execution")
                    _verify_artifacts(result, digest)
                else:
                    value = read_document(receipt)
                    _verify_artifacts(value, digest)
                    if value.get("schema") == "basis-workflow-resource-check-v1" and value.get("status") == "ready":
                        cfg = value.get("verified_config")
                        if cfg is None:
                            raise ValueError("Resource gate predates resource integrity contract")
                        key = hash_object(cfg)
                        if key not in resources:
                            resources[key] = resource_fingerprints(cfg)
                        if resources[key].get("untracked_resource_scopes"):
                            raise ValueError("Resource closure is not fully tracked; require a fresh setup check")
                        if resources[key] != value.get("resource_fingerprints"):
                            raise ValueError("Configured resource content changed")
                        identity = value.get("tool_identity") or {}
                        for path, expected in identity.get("executables", {}).items():
                            if digest(path) != expected:
                                raise ValueError("Executable content changed: " + path)
                        if identity and identity.get("reuse_supported") is False:
                            raise ValueError("External tool dependency closure is not fully tracked; require a fresh setup check")
                        if identity.get("conda"):
                            prefix = identity["prefix"]
                            if prefix not in inventories:
                                inventories[prefix] = hash_object(file_inventory(prefix))
                            if inventories[prefix] != identity.get("environment_sha256"):
                                raise ValueError("Tool environment content changed: " + prefix)
            audit["retained"].append(str(marker.relative_to(root)))
        except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
            # Remove only disposable completion claims. Failed results and all
            # original outputs remain available to reporting and inspection.
            marker.unlink(missing_ok=True)
            audit["revoked"].append({"marker": str(marker.relative_to(root)), "reason": str(exc)})
    changed = True
    while changed:
        changed = False
        for marker in sorted(root.rglob("*.ready.json")):
            ready = read_document(marker)
            for dependency, expected in ready.get("dependency_checksums", {}).items():
                if not Path(dependency).is_file() or sha256_file(dependency) != expected:
                    marker.unlink(missing_ok=True)
                    relative = str(marker.relative_to(root))
                    if relative in audit["retained"]:
                        audit["retained"].remove(relative)
                    audit["revoked"].append({"marker": relative, "reason": "Upstream completion claim changed: " + dependency})
                    changed = True
                    break
    atomic_json(launch_root / "integrity-audit.json", audit)
    return {"path": str(launch_root / "integrity-audit.json"),
            "retained_targets": len(audit["retained"]), "revoked_targets": len(audit["revoked"])}
