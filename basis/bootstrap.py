"""First-use resource preparation inside the analysis DAG.

Planning is read-only. Resource jobs publish immutable, content-verified
snapshots under a shared cache and attest those snapshots in the current run.
Existing externally configured resources are never replaced or refreshed.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import uuid

from .config import ALL_MODULES, PATH_KEYS
from .execution import CommandRunner
from .progress import emit, stage
from .util import atomic_json, hash_object, now, read_json, sha256_file

POLICY = "basis-resource-bootstrap-v1"
PROVIDERS = {
    "amrfinder": ("core-amr", "amrfinder"), "card": ("rgi", "rgi"),
    "vfdb": ("diamond", "vfdb"), "antismash": ("antismash", "antismash"),
    "genomad": ("context", "genomad"), "mobsuite": ("mobsuite", "mobsuite"),
    "bacmet": ("diamond", "bacmet"), "integronfinder": ("context", "integronfinder"),
    "isescan": ("isescan", "isescan"), "icescreen": ("icescreen", "icescreen"),
    "mlst": ("mlst", "mlst"), "crisprcasfinder": ("crisprcasfinder", "crisprcasfinder"),
    "prosite": ("deepsafe", "deepsafe"), "prott5": ("pathogenfinder2", "pathogenfinder2"),
    "deep_catalogue": ("deepsafe", "deepsafe"),
}
CORE_RESOURCE = {module: provider for provider, (_, module) in PROVIDERS.items()
                 if module != "deepsafe" and provider != "prott5"}
MANIFEST = "bootstrap-manifest.json"


def _present(value):
    return value is not None and value != ""


def _provider_module(provider):
    if provider in {"prosite", "prott5", "deep_catalogue"}:
        return "bootstrap_deep"
    if provider in {"mlst", "crisprcasfinder"}:
        return "bootstrap_typing_defense"
    return "bootstrap_providers"


def _implementation(provider):
    path = Path(__file__).with_name(_provider_module(provider) + ".py")
    return sha256_file(path) if path.is_file() else "provider-not-installed"


def _packaged_inputs(provider):
    if provider != "deep_catalogue":
        return {}
    root = Path(__file__).parent / "resources"
    paths = sorted((root / "comparators/reviewed-v1").rglob("*"))
    paths += [root / "motifs/rules-v1.json", root / "placement/registry-v2.json"]
    return {str(path.relative_to(root)): sha256_file(path) for path in paths if path.is_file()}


def plan_auto_resources(config, deep=False, extend=False):
    """Return (copied configuration, deterministic resource specs), without I/O writes."""
    resolved = copy.deepcopy(config)
    resources = resolved.get("resources") or {}
    policy = resources.get("policy", "manual")
    if policy not in {"manual", "auto"}:
        raise ValueError("resources.policy must be manual or auto")
    if policy != "auto" or resolved.get("validation_mode") == "synthetic_fixture":
        return resolved, []
    directory = Path(resources.get("directory") or Path.home() / ".cache/bactromics-basis").expanduser().resolve()
    resolved.setdefault("resources", {})["directory"] = str(directory)
    modules = resolved["modules"]
    deep_cfg = resolved["deep"]
    requests = {}

    def request(provider, target, deps=(), options=None):
        recipe, owner = PROVIDERS[provider]
        cfg = deep_cfg if owner == "deepsafe" else modules[owner]
        entry = requests.setdefault(provider, {"id": provider, "provider": provider,
            "recipe": recipe, "targets": [], "dependencies": [], "options": {},
            "preset": resources.get("preset", "core"), "module_config": copy.deepcopy(cfg),
            "command_prefix": list(cfg.get("command_prefix") or []),
            "environment_backend": cfg.get("environment_backend", resolved.get("execution", {}).get("environment_backend", "auto")),
            "environment_storage": str(Path(resolved["execution"].get("conda_prefix") or
                                            Path(resolved["outdir"]).parent / ".basis-envs").expanduser().resolve()),
            "threads": min(int(cfg.get("threads", 1)), int(resolved["execution"]["cores"])),
            "memory_mb": max(1024, min(int(cfg.get("memory_mb", 4000)), int(resolved["execution"]["memory_mb"]))),
            "timeout_seconds": max(14400, int(cfg.get("timeout_seconds", 14400))),
            "cache_directory": str(directory), "provider_source_sha256": _implementation(provider),
            "packaged_inputs_sha256": _packaged_inputs(provider)})
        if target not in entry["targets"]:
            entry["targets"].append(target)
        entry["dependencies"] = sorted(set(entry["dependencies"]) | set(deps))
        entry["options"].update(copy.deepcopy(options or {}))
        return provider

    def source_resource(module, target):
        cfg = modules[module]
        if not _present(cfg.get("database")):
            return request(CORE_RESOURCE[module], target)
        return None

    for module, provider in CORE_RESOURCE.items():
        cfg = modules[module]
        if cfg.get("enabled") and cfg.get("mode", "native") == "native" and not _present(cfg.get("database")):
            request(provider, module)
    pf2 = modules["pathogenfinder2"]
    if extend and pf2.get("enabled") and pf2.get("mode", "native") == "native":
        if not all(_present(pf2.get(k)) for k in ("prot_t5_path", "weights_model")):
            request("prott5", "pathogenfinder2", options={"external_modules": {"pathogenfinder2": copy.deepcopy(pf2)}})
    if deep:
        if bool(deep_cfg.get("reference_fasta")) != bool(deep_cfg.get("reference_metadata")):
            raise ValueError("DeepSafe reference_fasta and reference_metadata must be supplied together")
        if not _present(deep_cfg.get("prosite_snapshot")):
            request("prosite", "deepsafe")
        dependencies = []
        if not _present(deep_cfg.get("domain_database")):
            dep = source_resource("antismash", "deepsafe")
            if dep:
                dependencies.append(dep)
        if not _present(deep_cfg.get("reference_fasta")):
            external = {}
            for module in ("amrfinder", "rgi", "vfdb", "antismash"):
                dep = source_resource(module, "deepsafe")
                if dep:
                    dependencies.append(dep)
                else:
                    external[module] = {k: copy.deepcopy(v) for k, v in modules[module].items()
                                        if k in PATH_KEYS or k in {"database_version", "version"}}
            request("deep_catalogue", "deepsafe", dependencies,
                    {"external_modules": external, "domain_database": deep_cfg.get("domain_database")})
        elif (not _present(deep_cfg.get("domain_database"))
              or not _present(deep_cfg.get("profiles_dir"))
              or (deep_cfg.get("motifs", True) and not _present(deep_cfg.get("motif_rules")))
              or (deep_cfg.get("family_placement") and not _present(deep_cfg.get("placement_registry")))):
            # An external antiSMASH database may supply Pfam without rebuilding
            # or replacing the caller's already configured reference pair.
            request("deep_catalogue", "deepsafe", dependencies, {
                "external_modules": ({"antismash": copy.deepcopy(modules["antismash"])}
                                     if modules["antismash"].get("database") else {}),
                "reference_fasta": deep_cfg["reference_fasta"],
                "reference_metadata": deep_cfg["reference_metadata"],
                "domain_database": deep_cfg.get("domain_database"), "preserve_references": True})
    from .workflow_environments import environment_for_recipe
    ordered, remaining = [], set(requests)
    while remaining:
        ready = sorted(name for name in remaining if set(requests[name]["dependencies"]) <= {s["id"] for s in ordered})
        if not ready:
            raise ValueError("Automatic resource dependency cycle")
        for name in ready:
            spec = requests[name]
            recipe = Path(environment_for_recipe(spec["recipe"]))
            spec["recipe_sha256"] = sha256_file(recipe)
            dependency_keys = {s["id"]: s["cache_key"] for s in ordered if s["id"] in spec["dependencies"]}
            spec["cache_key"] = hash_object({"policy": POLICY, "provider": name,
                "provider_source_sha256": spec["provider_source_sha256"], "recipe_sha256": spec["recipe_sha256"],
                "options": spec["options"], "preset": spec["preset"],
                "runtime_binding": ({k: spec[k] for k in ("command_prefix", "environment_backend", "environment_storage")}
                                    if name in {"isescan", "icescreen", "integronfinder", "antismash"} else {}),
                "packaged_inputs_sha256": spec["packaged_inputs_sha256"], "dependencies": dependency_keys})
            spec["snapshot"] = str(directory / "snapshots" / name / spec["cache_key"])
            ordered.append(spec)
            remaining.remove(name)
    return resolved, ordered


def _specs(plan):
    return {spec["id"]: spec for spec in plan.get("bootstrap_resources", [])}


def receipt_path(root, resource_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", resource_id):
        raise ValueError("Invalid bootstrap resource identifier")
    return Path(root) / "bootstrap" / (resource_id + ".json")


def ready_path(root, resource_id):
    """Success-only DAG output; diagnostic receipts survive a failed job."""
    return receipt_path(root, resource_id).with_suffix(".ready.json")


def bootstrap_dependencies(plan, sid, module):
    from .sample_policy import module_decision
    if module == "deepsafe":
        requested = plan.get("options", {}).get("deep", False)
    else:
        requested = module_decision(plan, sid, module)["action"] == "run"
        requested = requested and plan["config"]["modules"][module].get("mode", "native") == "native"
    if not requested:
        return []
    run_id = os.environ.get("BASIS_SCHEDULER_RUN_ID", plan["plan_hash"])
    root = Path(plan["outdir"]) / "provenance/scheduler" / run_id
    return [str(ready_path(root, spec["id"])) for spec in plan.get("bootstrap_resources", []) if module in spec["targets"]]


def bootstrap_environment(plan, resource_id):
    from .workflow_environments import environment_for_recipe
    spec = _specs(plan)[resource_id]
    return None if spec.get("command_prefix") or spec.get("environment_backend") == "external" else environment_for_recipe(spec["recipe"])


def bootstrap_uses_conda(plan):
    return any(bootstrap_environment(plan, name) is not None for name in _specs(plan))


def _files(root):
    root = Path(root)
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() and not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Resource snapshot has an external symbolic link: " + str(path))
        if path.is_file() and path != root / MANIFEST:
            files[str(path.relative_to(root))] = path
    return files


def _hash_files(paths, label):
    checks = {}
    total = len(paths)
    with stage(label):
        for index, (name, path) in enumerate(paths.items(), 1):
            if index == 1 or index % 100 == 0 or index == total:
                emit(f"{label}: file {index}/{total}")
            checks[name] = {"sha256": sha256_file(path), "size": path.stat().st_size}
    return checks


def _remap(value, source, destination):
    if isinstance(value, dict):
        return {k: _remap(v, source, destination) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_remap(v, source, destination) for v in value]
    if isinstance(value, str) and (value == str(source) or value.startswith(str(source) + os.sep)):
        return str(destination) + value[len(str(source)):]
    return value


def _links(root, normalize=False):
    root = Path(root).resolve()
    links = {}
    for path in sorted(root.rglob("*")):
        if not path.is_symlink():
            continue
        target = path.resolve(strict=True)
        if not target.is_relative_to(root):
            raise ValueError("Resource snapshot has an external symbolic link: " + str(path))
        relative = os.path.relpath(target, path.parent)
        if normalize and os.readlink(path) != relative:
            path.unlink()
            path.symlink_to(relative, target_is_directory=target.is_dir())
        links[str(path.relative_to(root))] = os.readlink(path)
    return links


def _declared_external_hashes(result):
    """Exact provider input identities, including external paths not in updates."""
    provenance = result.get("provenance", {})
    expected = {}
    def record(path, digest):
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError("External provenance requires an absolute file path")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("External provenance requires a SHA256 digest: " + path)
        if path in expected and expected[path] != digest:
            raise ValueError("Conflicting external resource identities: " + path)
        expected[path] = digest
    for row in provenance.get("source_files", []):
        record(row["path"], row["sha256"])
    pfam = provenance.get("pfam")
    if pfam:
        record(pfam["path"], pfam["sha256"])
    for row in provenance.get("external_resources", {}).values():
        base = Path(row["path"])
        for relative, digest in row["sha256"].items():
            path = base / relative
            if not path.resolve().is_relative_to(base.resolve()):
                raise ValueError("External inventory escapes resource root")
            record(str(path), digest)
    for path, digest in provenance.get("details", {}).get("source_sha256", {}).items():
        record(path, digest)
    return expected


def _external_paths(result, snapshot):
    paths = {}
    snapshot = Path(snapshot)
    def add(value):
        path = Path(value)
        if not path.is_absolute() or path.is_relative_to(snapshot):
            return
        if not path.exists():
            raise ValueError("Prepared external resource is absent: " + str(path))
        if path.is_file():
            paths[str(path)] = path
        elif path.is_dir():
            paths.update({str(p): p for p in sorted(path.rglob("*"))
                          if p.is_file() and ".cache" not in p.relative_to(path).parts})
    sections = list(result.get("module_updates", {}).values()) + [result.get("deep_updates", {})]
    for cfg in sections:
        for key, value in cfg.items():
            if key not in PATH_KEYS or not isinstance(value, str) or not value:
                continue
            add(value)
            if key == "domain_database":
                for suffix in (".h3f", ".h3i", ".h3m", ".h3p"):
                    add(value + suffix)
    for path in _declared_external_hashes(result):
        add(path)
    for row in result.get("provenance", {}).get("external_resources", {}).values():
        add(row["path"])
    return paths


def _validate_declared_hashes(result, external, snapshot, files):
    for name, expected in _declared_external_hashes(result).items():
        path = Path(name)
        observed = (files.get(str(path.relative_to(snapshot)), {}) if path.is_relative_to(snapshot)
                    else external.get(name, {}))
        if observed.get("sha256") != expected:
            raise ValueError("Provider input changed before publication: " + name)


def verify_snapshot(spec):
    root = Path(spec["snapshot"])
    if root.is_symlink():
        raise ValueError("Resource snapshot root cannot be a symbolic link")
    manifest = read_json(root / MANIFEST)
    if (manifest.get("schema") != POLICY or manifest.get("resource_id") != spec["id"]
            or manifest.get("cache_key") != spec["cache_key"]):
        raise ValueError("Resource snapshot identity mismatch: " + str(root))
    if _links(root) != manifest.get("symlinks", {}):
        raise ValueError("Resource snapshot symbolic links changed: " + str(root))
    paths = _files(root)
    if set(paths) != set(manifest.get("files", {})):
        raise ValueError("Resource snapshot file inventory changed: " + str(root))
    checks = _hash_files(paths, "Verify " + spec["id"] + " snapshot")
    if checks != manifest["files"]:
        raise ValueError("Resource snapshot checksum mismatch: " + str(root))
    external = _external_paths(manifest["result"], root)
    if set(external) != set(manifest.get("external_files", {})):
        raise ValueError("External resource file inventory changed: " + spec["id"])
    if _hash_files(external, "Verify " + spec["id"] + " external resources") != manifest.get("external_files", {}):
        raise ValueError("External bundled resources changed: " + spec["id"])
    return manifest


def _read_receipt(plan, root, spec):
    from .workflow_runtime import read_document
    receipt = read_document(receipt_path(root, spec["id"]))
    expected = {"schema": "basis-bootstrap-receipt-v1", "run_id": Path(root).name,
                "plan_hash": plan["plan_hash"], "source_hash": plan["source_hash"],
                "resource_id": spec["id"], "cache_key": spec["cache_key"]}
    if any(receipt.get(k) != v for k, v in expected.items()):
        raise ValueError("Resource receipt identity mismatch: " + spec["id"])
    return receipt


def _load_receipt(plan, root, spec):
    receipt = _read_receipt(plan, root, spec)
    if receipt.get("status") != "ready":
        raise ValueError("Resource preparation failed: " + spec["id"] + ": " + receipt.get("error", "unknown failure"))
    manifest_path = Path(spec["snapshot"]) / MANIFEST
    if receipt.get("manifest") != str(manifest_path) or sha256_file(manifest_path) != receipt.get("manifest_sha256"):
        raise ValueError("Resource manifest changed after preparation: " + spec["id"])
    return receipt, read_json(manifest_path)


def apply_resources(plan, root, module, sample_id=None):
    """Overlay attested resource fields in a private plan; the saved plan stays immutable."""
    result = copy.deepcopy(plan)
    for spec in plan.get("bootstrap_resources", []):
        if module not in spec["targets"]:
            continue
        _, manifest = _load_receipt(plan, root, spec)
        updates = manifest["result"]
        section = result["config"]["deep"] if module == "deepsafe" else result["config"]["modules"][module]
        values = updates.get("deep_updates", {}) if module == "deepsafe" else updates.get("module_updates", {}).get(module, {})
        for key, value in values.items():
            if key not in section or not _present(section[key]):
                section[key] = copy.deepcopy(value)
        section.setdefault("resource_bootstrap", []).append({"resource_id": spec["id"],
            "cache_key": spec["cache_key"], "manifest": str(Path(spec["snapshot"]) / MANIFEST),
            "manifest_sha256": sha256_file(Path(spec["snapshot"]) / MANIFEST)})
    return result


def bootstrap_audit(plan, root):
    """Explain current-run failures and unscheduled dependants without executing them."""
    specs, found = _specs(plan), {}
    needed = set(specs)
    if plan.get("samples"):
        from .sample_policy import module_decision
        needed = {name for name, spec in specs.items() if any(
            bool(plan.get("options", {}).get("deep")) if module == "deepsafe" else
            plan["config"]["modules"][module].get("mode", "native") == "native" and any(
                module_decision(plan, sample, module)["action"] == "run" for sample in plan["samples"])
            for module in spec["targets"])}
        pending = list(needed)
        while pending:
            for dependency in specs[pending.pop()].get("dependencies", []):
                if dependency not in needed:
                    needed.add(dependency)
                    pending.append(dependency)

    def visit(name, active=()):
        if name in found:
            return found[name]
        if name in active:
            raise ValueError("Cyclic resource preparation dependencies")
        spec = specs[name]
        dependencies = [visit(dep, (*active, name)) for dep in spec.get("dependencies", [])]
        path = receipt_path(root, name)
        item = {"resource_id": name, "status": "not_started" if name in needed else "not_requested", "receipt": str(path),
                "dependencies": list(spec.get("dependencies", [])), "root_causes": []}
        if path.is_file():
            try:
                receipt = _read_receipt(plan, root, spec)
                if receipt.get("status") not in {"ready", "failed"}:
                    raise ValueError("Unrecognized resource receipt status: " + name)
                item.update({k: receipt.get(k) for k in (
                    "status", "reused", "manifest", "manifest_sha256", "error", "command_log")})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                item.update(status="failed", error="Invalid resource receipt: " + str(exc))
        blocked = [dep for dep in dependencies if dep["status"] in {"failed", "blocked"}]
        if blocked and item["status"] != "ready":
            item["blocked_by"] = [dep["resource_id"] for dep in blocked]
            item["root_causes"] = list({cause["resource_id"]: cause for dep in blocked
                                       for cause in dep["root_causes"]}.values())
            if item["status"] == "not_started":
                item.update(status="blocked", error="Not started because required resource preparation failed")
        if item["status"] == "failed" and not item["root_causes"]:
            item["root_causes"] = [{"resource_id": name, "error": item.get("error") or "Unknown preparation failure",
                                    "command_log": item.get("command_log"), "receipt": str(path)}]
        found[name] = item
        return item

    return [visit(spec["id"]) for spec in plan.get("bootstrap_resources", [])]


def module_resource_blockers(plan, module, audit):
    required = {spec["id"] for spec in plan.get("bootstrap_resources", []) if module in spec["targets"]}
    return [copy.deepcopy(item) for item in audit if item["resource_id"] in required and item["status"] != "ready"]


def resource_failure_message(blockers):
    causes = {cause["resource_id"]: cause for item in blockers for cause in item.get("root_causes", [])}
    detail = "; ".join(name + ": " + str(cause["error"]) for name, cause in causes.items())
    if not detail:
        detail = "required preparation did not finish; inspect the scheduler log"
    return "Resource preparation failed: " + detail + ". Blocked resources: " + ", ".join(
        item["resource_id"] + " (" + item["status"] + ")" for item in blockers)


def _runner(plan, spec, logfile, allocated_threads=None):
    cfg = {"command_prefix": list(spec.get("command_prefix") or []),
           "threads": min(spec["threads"], allocated_threads or spec["threads"]),
           "timeout_seconds": spec["timeout_seconds"]}
    environment = {"backend": "external", "command_prefix": cfg["command_prefix"]}
    if bootstrap_environment(plan, spec["id"]) is not None:
        import yaml
        from .workflow_environments import active_environment, conda_storage
        from .environment_workflow import verify
        environment = active_environment(spec["recipe"], conda_storage(plan))
        verification = verify(Path(environment["prefix"]), yaml.safe_load(Path(environment["recipe"]).read_text()),
                              spec["recipe"], {"manager_executable": environment["command_prefix"][0]}, logfile)
        environment["metadata_sha256"] = hash_object(verification["metadata_sha256"])
        cfg["command_prefix"] = environment["command_prefix"]
    return CommandRunner(cfg, logfile), environment


def _prepare(spec, destination, runner):
    import importlib
    module = importlib.import_module("basis." + _provider_module(spec["provider"]))
    function = "prepare_deep_resource" if spec["provider"] in {"prosite", "prott5", "deep_catalogue"} else "prepare_resource"
    return getattr(module, function)(spec, destination, runner)


def run_resource(plan, root, resource_id, allocated_threads=None):
    """Publish/reuse one snapshot, recording failures as explicit current-run data."""
    from .workflow_runtime import _write
    spec = _specs(plan)[resource_id]
    root = Path(root)
    # A retry cannot inherit an earlier readiness marker for this same run.
    ready_path(root, resource_id).unlink(missing_ok=True)
    value = {"schema": "basis-bootstrap-receipt-v1", "run_id": root.name,
             "plan_hash": plan["plan_hash"], "source_hash": plan["source_hash"],
             "resource_id": spec["id"], "provider": spec["provider"], "cache_key": spec["cache_key"],
             "started_at": now(), "status": "failed", "reused": False}
    logfile = root / "bootstrap" / (resource_id + ".commands.log")
    cache = Path(spec["cache_directory"])
    snapshot = Path(spec["snapshot"])
    runner = None
    try:
        if spec.get("packaged_inputs_sha256", {}) != _packaged_inputs(spec["provider"]):
            raise ValueError("Packaged resource inputs changed after planning")
        if spec.get("provider_source_sha256") != _implementation(spec["provider"]):
            raise ValueError("Resource provider source changed after planning")
        dependencies = []
        for dependency in spec["dependencies"]:
            _, manifest = _load_receipt(plan, root, _specs(plan)[dependency])
            dependencies.append({"provider": manifest["provider"], "id": dependency, **manifest["result"]})
        cache.mkdir(parents=True, exist_ok=True)
        locks = cache / "locks"
        locks.mkdir(exist_ok=True)
        with (locks / (resource_id + "-" + spec["cache_key"] + ".lock")).open("a+") as lock:
            with stage("Waiting for resource cache lock: " + resource_id):
                fcntl.flock(lock, fcntl.LOCK_EX)
            if snapshot.exists():
                manifest = verify_snapshot(spec)
                value["reused"] = True
            else:
                runner, environment = _runner(plan, spec, logfile, allocated_threads)
                staging = cache / "staging" / (resource_id + "-" + uuid.uuid4().hex)
                staging.mkdir(parents=True, exist_ok=False)
                value["staging_directory"] = str(staging)
                provider_spec = copy.deepcopy(spec)
                provider_spec["threads"] = runner.threads
                provider_spec["dependency_resources"] = dependencies
                provider_spec["dependencies_resolved"] = {d["provider"]: d for d in dependencies}
                prepared = _prepare(provider_spec, staging, runner)
                if not isinstance(prepared, dict) or not isinstance(prepared.get("provenance"), dict):
                    raise ValueError("Resource provider did not return provenance")
                prepared.setdefault("module_updates", {})
                prepared.setdefault("deep_updates", {})
                if not isinstance(prepared["module_updates"], dict) or not isinstance(prepared["deep_updates"], dict):
                    raise ValueError("Resource provider updates must be mappings")
                links = _links(staging, normalize=True)
                files = _files(staging)
                if not files:
                    raise ValueError("Resource provider produced an empty snapshot")
                checks = _hash_files(files, "Seal " + resource_id + " snapshot")
                prepared = _remap(prepared, staging, snapshot)
                external = _hash_files(_external_paths(prepared, snapshot), "Seal " + resource_id + " external resources")
                _validate_declared_hashes(prepared, external, snapshot, checks)
                manifest = {"schema": POLICY, "resource_id": spec["id"], "provider": spec["provider"],
                    "cache_key": spec["cache_key"], "created_at": now(), "spec": spec,
                    "result": prepared, "files": checks, "symlinks": links, "external_files": external,
                    "environment": environment, "commands": runner.commands,
                    "refresh_policy": "frozen_first_use; existing snapshots are verified and never implicitly refreshed"}
                atomic_json(staging / MANIFEST, manifest)
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                staging.rename(snapshot)
                value.pop("staging_directory", None)
            value.update(status="ready", manifest=str(snapshot / MANIFEST),
                         manifest_sha256=sha256_file(snapshot / MANIFEST),
                         file_count=len(manifest["files"]), snapshot=str(snapshot))
    except Exception as exc:
        value.update(status="failed", error=str(exc))
    value["finished_at"] = now()
    value["command_log"] = str(logfile) if logfile.exists() else None
    if runner is not None:
        value["commands"] = runner.commands
    receipt = _write(receipt_path(root, resource_id), value)
    if receipt["status"] == "ready":
        _write(ready_path(root, resource_id), {"schema": "basis-bootstrap-ready-v1",
               "run_id": root.name, "plan_hash": plan["plan_hash"], "resource_id": resource_id,
               "receipt": str(receipt_path(root, resource_id)), "receipt_sha256": sha256_file(receipt_path(root, resource_id))})
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--resource", required=True)
    parser.add_argument("--threads", type=int)
    args = parser.parse_args(argv)
    from .workflow_runtime import _load
    plan, root, _ = _load(args.plan, args.run_dir)
    receipt = run_resource(plan, root, args.resource, args.threads)
    if receipt["status"] != "ready":
        print("Resource preparation failed: " + args.resource + ": " + str(receipt.get("error")), file=sys.stderr, flush=True)
        print("Failure receipt: " + str(receipt_path(root, args.resource)), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
