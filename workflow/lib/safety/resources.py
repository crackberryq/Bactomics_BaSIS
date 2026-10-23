"""Resource preparation inside one already activated Snakemake job.

Snakemake owns environment deployment, dependencies, retries and completed
outputs. This module only prepares or verifies one resource and writes its
checksum inventory; it never starts Conda or another workflow.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time


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
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".writing-", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
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
        elif name == "genomad":
            common += ["download_genomad.py"]
    paths.extend(vendor / name for name in common)
    return sorted(map(str, set(paths)))


def _inventory(path, *, owned_root=None, full=True):
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
            entries[str(item)] = {"bytes": item.stat().st_size}
            if full:
                entries[str(item)]["sha256"] = sha256(item)
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
    # New receipts also track directory membership. A new file or retargeted
    # symlink must not be silently admitted into a previously verified snapshot.
    if receipt.get("inventory_roots"):
        observed, links = _scan_roots(receipt["inventory_roots"], full=full)
        expected = artifacts if full else {
            name: {"bytes": info["bytes"]} for name, info in artifacts.items()}
        if observed != expected:
            raise ValueError("Resource checksum mismatch or changed inventory: " + str(path))
        if links != receipt.get("symlinks", {}):
            raise ValueError("Resource symlink targets changed: " + str(path))
        return receipt
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


def _roots(paths, *, owned=None):
    """Collapse nested declared paths so large payloads are hashed only once."""
    owned = Path(owned).resolve() if owned is not None else None
    resolved = {Path(path).resolve(strict=True) for path in paths}
    if owned is not None and any(path != owned and owned.is_relative_to(path) for path in resolved):
        raise ValueError("Declared resource path encloses its prepared snapshot")
    roots = []
    for path in sorted(resolved, key=lambda p: (len(p.parts), str(p))):
        if any(Path(entry["path"]).is_dir() and path.is_relative_to(entry["path"])
               for entry in roots):
            continue
        roots.append({"path": str(path), "owned": path == owned})
    return roots


def _scan_roots(roots, *, full=True):
    artifacts, links = {}, {}
    for entry in roots:
        path = Path(entry["path"])
        artifacts.update(_inventory(path, owned_root=path if entry.get("owned") else None,
                                    full=full))
        if path.is_dir():
            for item in path.rglob("*"):
                if item.is_symlink():
                    links[str(item)] = os.readlink(item)
    return artifacts, links


def _select_inventory(roots, artifacts, links):
    """Reuse bytes just hashed from the superset of declared input paths."""
    paths = [Path(entry["path"]) for entry in roots]
    def selected(filename):
        return any(Path(filename).is_relative_to(path) for path in paths)
    return ({name: value for name, value in artifacts.items() if selected(name)},
            {name: value for name, value in links.items() if selected(name)})


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _environment_identity(path):
    if path is None:
        return None
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    if record.get("schema") != "basis-snake-environment-v1" or record.get("status") != "completed":
        raise ValueError("Resource environment has no completed receipt: " + str(path))
    # Capture time and diagnostic-file timestamps do not change an installation.
    # Prefix matters for providers whose resources live inside the environment.
    identity = {key: record.get(key) for key in (
        "prefix", "recipe_sha256", "post_deploy_sha256", "packages",
        "conda_managed", "platform", "machine")}
    identity["packages"] = sorted(identity["packages"] or [], key=lambda item: json.dumps(item, sort_keys=True))
    return identity


def _semantic_result(value):
    """Retain source declarations consumed by dependent providers, without clocks."""
    if isinstance(value, dict):
        return {key: _semantic_result(item) for key, item in value.items()
                if key not in {"created_at", "created_at_utc", "started_at", "finished_at"}}
    if isinstance(value, list):
        return [_semantic_result(item) for item in value]
    return value


def _preparation_identity(spec, dependencies, environment_receipt, source_artifacts):
    workflow = Path(__file__).resolve().parents[3]
    sources = source_inputs(spec, workflow)
    # These modules orchestrate execution and publication, not resource content.
    sources = [Path(path) for path in sources if Path(path).parent != Path(__file__).parent]
    return {
        "schema": "basis-resource-inputs-v2",
        "spec": preparation_spec(spec),
        "environment": _environment_identity(environment_receipt),
        "provider_sources": {str(path.relative_to(workflow)): sha256(path) for path in sources},
        "source_artifacts": source_artifacts,
        "dependencies": [{"artifacts": row["artifacts"],
                          "result": _semantic_result(row["result"])}
                         for row in dependencies],
    }


@contextmanager
def _resource_lock(root, name, *, timeout=15):
    folder = root / "locks"
    if folder.is_symlink():
        raise ValueError("Resource lock directory must not be a symlink")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + ".lock")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Another resource preparation still holds " + str(path))
                time.sleep(0.1)
        yield
    finally:
        os.close(descriptor)


def _cache_directory(root, name, key):
    path = root
    for component in ("snapshots", name, key):
        path = path / component
        if path.is_symlink():
            raise ValueError("Resource snapshot directory must not be a symlink: " + str(path))
        path.mkdir(exist_ok=True)
    return path


def _publish(path, receipt):
    # Preserve a completed record byte-for-byte when a job only revalidates it.
    # Snakemake may have removed its declared receipt before executing the job.
    if path.is_file() and json.loads(path.read_text(encoding="utf-8")) == receipt:
        return
    atomic_json(path, receipt)


def _check_data_pointer(root, name):
    """Accept only our own already-published snapshot alias, never an arbitrary link."""
    alias = root / name / "data"
    if not alias.is_symlink():
        return
    try:
        target = alias.resolve(strict=True)
    except OSError as exc:
        raise ValueError("Resource data symlink has no valid snapshot target: " + str(alias)) from exc
    namespace = root / "snapshots" / name
    if not target.is_dir() or not target.is_relative_to(namespace) or target.name != "data":
        raise ValueError("Resource data symlink is not a managed snapshot: " + str(alias))
    manifest = target.parent.parent / "completed.json"
    if not manifest.is_file():
        raise ValueError("Resource data symlink has no completed snapshot: " + str(alias))
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if (record.get("schema") != SCHEMA or record.get("status") != "completed"
            or record.get("resource") != name or record.get("mode") != "native"
            or record.get("payload_root") != str(target)
            or record.get("cache_key") != target.parent.parent.name):
        raise ValueError("Resource data symlink does not match its completed snapshot: " + str(alias))


def _publish_data_pointer(root, name, receipt):
    """Keep the stable path used by DAG-time command construction without copying data."""
    payload = receipt.get("payload_root")
    if not payload:
        return
    alias = root / name / "data"
    _check_data_pointer(root, name)
    if alias.is_symlink() and alias.resolve() == Path(payload):
        return
    if alias.exists() and not alias.is_symlink():
        raise ValueError("Existing resource data directory will not be replaced: " + str(alias))
    descriptor, temporary = tempfile.mkstemp(prefix="data-link-", dir=alias.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    temporary.unlink()
    try:
        temporary.symlink_to(os.path.relpath(payload, alias.parent), target_is_directory=True)
        temporary.replace(alias)
    finally:
        temporary.unlink(missing_ok=True)


def _completed_receipt(name, spec, result, roots, identity, key, dependencies, *, payload=None,
                       inventory=None):
    artifacts, links = inventory if inventory is not None else _scan_roots(roots)
    return {
        "schema": SCHEMA, "status": "completed", "resource": name,
        "mode": spec.get("mode", "native"), "provider": spec["provider"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifacts": artifacts, "inventory_roots": roots, "symlinks": links,
        "result": result, "module_updates": result.get("module_updates", {}),
        "deep_updates": result.get("deep_updates", {}),
        "provenance": result.get("provenance", {}),
        "dependencies": {str(Path(path).resolve()): sha256(path) for path in dependencies},
        "preparation_identity": identity, "cache_key": key,
        "payload_root": str(payload) if payload is not None else None,
        "execution_owner": "snakemake", "analyses_run": False,
    }


def _protect_legacy(destination, receipt_path):
    """Old receipts cannot prove their original preparation settings/environment."""
    if receipt_path.is_file():
        previous = json.loads(receipt_path.read_text(encoding="utf-8"))
        if previous.get("schema") == SCHEMA and not previous.get("cache_key"):
            archive = destination / "legacy-receipts" / (sha256(receipt_path) + ".json")
            if not archive.exists():
                atomic_json(archive, previous)
            raise ValueError(
                "Legacy resource receipt and data were preserved at " + str(destination)
                + ". The old receipt does not record preparation inputs. Configure its verified "
                  "local paths explicitly with resources.policy: manual and a separate resource "
                  "receipt directory before resuming; no download was started.")
    legacy = destination / "data"
    if not legacy.is_symlink() and legacy.exists():
        raise ValueError(
            "Existing legacy resource data were preserved at " + str(legacy)
            + ". Select verified local paths explicitly with a separate resource receipt "
              "directory before resuming; no download was started.")


def prepare(name, spec, resource_root, logfile, *, threads=1, dependency_receipts=(),
            environment_receipt=None, runner=None):
    """Publish a small receipt; payloads and failed attempts outlive workflow jobs.

    Completed snapshots are reusable only for matching preparation inputs and
    after full verification. Native attempt paths never change: provider metadata
    can safely contain absolute paths. Neither old data nor failed attempts are
    removed here, including when a new resource version fails to prepare.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        raise ValueError("Invalid resource name")
    spec = deepcopy(spec)
    provider = spec.setdefault("provider", name)
    if provider not in PROVIDERS:
        raise ValueError("Unsupported resource provider: " + str(provider))
    if int(threads) < 1:
        raise ValueError("Resource threads must be positive")
    root = Path(resource_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / name
    if destination.is_symlink():
        raise ValueError("Resource output directory must not be a symlink")
    destination.mkdir(parents=True, exist_ok=True)
    receipt_path = destination / "provenance.json"
    dependency_receipts = tuple(dependency_receipts)
    with _resource_lock(root, name):
        _check_data_pointer(root, name)
        dependencies = [verify_receipt(path) for path in dependency_receipts]
        inputs = external_inputs(spec)
        for filename in inputs:
            path = Path(filename).resolve(strict=True)
            if path.is_dir() and root.is_relative_to(path):
                raise ValueError("Resource input directory encloses its generated cache: " + str(path))
            if spec.get("mode") == "external" and path.is_relative_to(destination):
                raise ValueError("External resource must be outside its generated receipt directory")
        source_artifacts, source_links = _scan_roots(_roots(inputs)) if inputs else ({}, {})
        external = spec.get("mode") == "external"
        result = external_result(spec) if external else None
        declared = list(_named_paths(result)) if external else []
        if external and not declared:
            raise ValueError("No external resource paths were declared")
        declared_roots = _roots(declared) if external else []
        external_inventory = _select_inventory(declared_roots, source_artifacts, source_links) if external else None
        identity = _preparation_identity(spec, dependencies, environment_receipt, source_artifacts)
        key = _digest(identity)
        cache = _cache_directory(root, name, key)
        completed = cache / "completed.json"
        if completed.is_file():
            if external:
                receipt = json.loads(completed.read_text(encoding="utf-8"))
                if (receipt.get("schema") != SCHEMA or receipt.get("status") != "completed"
                        or receipt.get("artifacts") != external_inventory[0]
                        or receipt.get("symlinks") != external_inventory[1]
                        or receipt.get("inventory_roots") != declared_roots
                        or receipt.get("result") != result):
                    raise ValueError("External resource snapshot differs from verified inputs: " + str(completed))
            else:
                receipt = verify_receipt(completed)
            if receipt.get("preparation_identity") != identity or receipt.get("cache_key") != key:
                raise ValueError("Resource snapshot preparation identity differs: " + str(completed))
            _publish_data_pointer(root, name, receipt)
            _publish(receipt_path, receipt)
            print("Verified existing resource snapshot: " + str(completed), flush=True)
            return receipt

        if external:
            receipt = _completed_receipt(name, spec, result, declared_roots, identity, key,
                                         dependency_receipts, inventory=external_inventory)
        else:
            _protect_legacy(destination, receipt_path)
            attempt = Path(tempfile.mkdtemp(prefix="attempt-", dir=cache))
            data = attempt / "data"
            data.mkdir()
            atomic_json(attempt / "request.json", {
                "status": "started", "resource": name, "cache_key": key,
                "preparation_identity": identity,
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "logfile": str(Path(logfile).resolve()), "payload_root": str(data)})
            spec["dependency_resources"] = [entry["result"] for entry in dependencies]
            spec["threads"] = int(threads)
            spec["cache_directory"] = str(root)
            spec.pop("command_prefix", None)
            spec["module_config"] = {**spec.get("module_config", {}), "command_prefix": []}
            try:
                if runner is None:
                    from .runtime import DirectRunner
                    runner = DirectRunner({**spec.get("module_config", {}), "threads": threads,
                        "command_prefix": [], "timeout_seconds": spec.get("timeout_seconds", 14400)}, logfile)
                result = _provider(spec, data, runner)
                roots = _roots([data, *_named_paths(result)], owned=data)
                receipt = _completed_receipt(name, spec, result, roots, identity, key,
                                             dependency_receipts, payload=data)
                atomic_json(attempt / "receipt.json", receipt)
            except BaseException as exc:
                atomic_json(attempt / "failure.json", {
                    "status": "failed", "resource": name, "cache_key": key,
                    "failed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "error_type": type(exc).__name__, "logfile": str(Path(logfile).resolve()),
                    "payload_preserved": str(data), "success_receipt_published": False})
                raise
        # The cache manifest commits a verified resource before the disposable
        # workflow receipt is published. An interrupted publication is reusable.
        atomic_json(completed, receipt)
        _publish_data_pointer(root, name, receipt)
        _publish(receipt_path, receipt)
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
    command.add_argument("--environment")
    args = parser.parse_args(argv)
    receipt = prepare(args.name, json.loads(args.spec_json), args.resource_root, args.logfile,
                      threads=args.threads, dependency_receipts=args.dependency,
                      environment_receipt=args.environment)
    print(f"Resource ready: {receipt['resource']} | {len(receipt['artifacts'])} recorded files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
