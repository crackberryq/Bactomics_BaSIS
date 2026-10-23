#!/usr/bin/env python3
"""Revalidate retained CCF payloads with the corrected parser, without native runs.

Preview by default. --apply preserves original attempts and publishes verified
snapshots under the current parser identity. Configuration overlays are ordered;
--isolate optionally narrows a configured batch, and --store targets one sample.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import copy
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor"), str(ROOT / "scripts")]

from archive_run_results import active_workflows
from recover_ccf_results import _require_local
from safety import ccf_assembly as assembly, extend, result_store, runtime
from safety.context import SafetyContext
from safety.doctor import load_configuration
from safety.storage import native_result_store
from basis import inputs, normalize, util

MODULE = "crisprcasfinder"
SCHEMA = "basis-ccf-revalidation-v1"
OLD_ERROR = "CRISPR array bounds or region continuity disagree"
LEGACY_HASHES = {
    "workflow/lib/safety/extend.py": "b9bfcd4d34383a39381ebf66df3c83abff906f777275e31e49e6908f9a7dd11e",
    "workflow/lib/safety/ccf_assembly.py": "faf888abebcd981eab07b5a3085fc7723304da8319f1a5ec030acad789bc1e82",
    "workflow/vendor/basis/adapters/crisprcasfinder.py": "6cd1c8ad05dca2dd6a6c87e7095d98fb99f22b3e4d4bd9bfd98186dd728a4c0c",
}


def sources():
    return sorted(str(Path(module.__file__).resolve()) for module in
                  (assembly, assembly.ccf, extend, result_store, runtime, inputs, normalize, util))


def _progress(message):
    print(message, file=sys.stderr, flush=True)


def require_stopped():
    active = active_workflows()
    if active:
        raise ValueError("Stop the workflow and wait for its workers before revalidation; active PIDs: "
                         + ", ".join(str(row["pid"]) for row in active))


def _object(path):
    row = runtime.read_json(path)
    if not isinstance(row, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return row


def _identity(original, sample, config):
    config = copy.deepcopy(config)
    settings = config.get("modules", {}).get(MODULE, {})
    if (not settings or not settings.get("enabled", True) or settings.get("cache", True) is not True
            or config.get("module_decisions", {}).get(MODULE, {}).get("action", "run") != "run"):
        raise ValueError("Revalidation requires an enabled module with cache reuse allowed")
    threads = original.get("settings", {}).get("threads")
    if type(threads) is not int or threads < 1:
        raise ValueError("Saved thread allocation is invalid")
    configured_threads = settings.get("threads")
    if type(configured_threads) is not int or configured_threads < threads:
        raise ValueError("Current configured threads cannot support the saved allocation")
    # Snakemake may have capped the configured request to its available cores.
    # Preserve that actual allocation; resumption must use the same allocation.
    settings["threads"] = threads
    if set(original.get("sources", {})) != set(sources()):
        raise ValueError("Saved identity must contain exactly the eight original CCF source paths")
    executable = original.get("executable", {}).get("path")
    extras = original.get("extra_inputs", {})
    if (not isinstance(executable, str) or not Path(executable).is_absolute()
            or not isinstance(extras, dict) or len(extras) != 2
            or any(not Path(path).is_absolute() for path in extras)):
        raise ValueError("Saved absolute native executable identities are incomplete")
    current = result_store.execution_identity(MODULE, sample, config, sources=sources(),
        extra_inputs=sorted(extras), executable=executable)
    legacy = copy.deepcopy(current)
    legacy["sources"].update({str(ROOT / path): checksum for path, checksum in LEGACY_HASHES.items()})
    if original != current and original != legacy:
        changed = sorted(key for key in set(original) | set(current)
                         if original.get(key) != current.get(key) and key != "sources")
        if original.get("sources") != current["sources"] and original.get("sources") != legacy["sources"]:
            changed.append("sources")
            paths = [str(Path(path).relative_to(ROOT)) for path in sources()
                     if original["sources"][path] not in {current["sources"][path], legacy["sources"][path]}]
            changed.append("source paths: " + ", ".join(paths) if paths else "mixed parser source versions")
        raise ValueError("Saved inputs, configuration, resources, environment, executables or source identity changed; "
                         "changed fields: " + "; ".join(changed))
    return current, settings


def _configured_inputs(isolate, context):
    base = isolate / "safety"
    if context is None:
        return (_object(base / "metadata/sample.json"),
                _object(base / "metadata/configs/crisprcasfinder.json"))
    # Reconstruct the current module document without changing metadata or
    # invoking preparation. execution_identity verifies all receipt identities.
    sample = copy.deepcopy(context.samples[str(isolate)])
    config = context.module_document(str(isolate), MODULE)
    config["resource_receipts"] = []
    for path in context.resource_inputs(MODULE):
        row = _object(path)
        config["modules"][MODULE].update(row.get("result", {}).get("module_updates", {}).get(MODULE, {}))
        config["resource_receipts"].append({"path": str(path), "sha256": result_store.sha256(path)})
    config["environment_receipts"] = context.module_environment_receipts(MODULE)
    return sample, config


def _inventory(payload):
    """Full payload inventory, retaining the optional model link as a link only."""
    payload = Path(payload)
    rows = {}
    for parent, directories, files in os.walk(payload, followlinks=False):
        for name in sorted(directories + files):
            path = Path(parent) / name
            relative = path.relative_to(payload).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                target = path.resolve(strict=True)
                row = {"kind": "link", "target": os.readlink(path)}
                if target.is_file():
                    row.update(bytes=target.stat().st_size, sha256=result_store.sha256(target))
                elif target.is_dir() and (relative == "models" or target.is_relative_to(payload)):
                    row["directory"] = True
                else:
                    raise ValueError(f"Unsupported saved output symlink: {path}")
            elif stat.S_ISREG(mode):
                row = {"kind": "file", "bytes": path.stat().st_size, "sha256": result_store.sha256(path)}
            elif stat.S_ISDIR(mode):
                row = {"kind": "directory"}
            else:
                raise ValueError(f"Unsupported saved output type: {path}")
            rows[relative] = row
    if "models" in rows and (rows["models"].get("kind") != "link"
                             or rows["models"].get("directory") is not True):
        raise ValueError("Saved models entry must be a directory symlink")
    return dict(sorted(rows.items()))


def _verify_payload(info, payload):
    if result_store.sha256(payload / "CRISPRCasFinder.pl") != assembly.ccf.SOURCE_SHA256:
        raise ValueError("Saved native source does not match the pinned release")
    settings = info["settings"]
    level = assembly.ccf._int(settings.get("min_evidence_level", 3), "minimum array evidence level", 1)
    if level > 4:
        raise ValueError("Minimum array evidence level must be 1..4")
    return assembly.parse_assembly(payload, inputs.read_fasta(info["sample"]["assembly"]),
        min_evidence_level=level,
        genetic_code=assembly.ccf._int(settings.get("genetic_code", 11), "genetic code", 1),
        artifact_root=info["destination"])


def _verified_record(path, destination, identity=None):
    row = result_store._record(path, expected_identity=identity, destination=destination)
    payload = Path(row["payload_root"])
    if payload.resolve() != payload or result_store.output_inventory(payload) != row["artifacts"]:
        raise ValueError("Verified saved output has changed")
    return row


def inspect_sample(isolate, *, workflow_config=None, context=None, store=None):
    isolate = Path(isolate).expanduser().resolve()
    _progress(f"{isolate}: checking saved result identity and payload")
    base = isolate / "safety"
    destination, receipt = base / "native/crisprcasfinder", base / "completion/crisprcasfinder.json"
    target = Path(store).expanduser().resolve() if store else native_result_store(
        ROOT, workflow_config or {}, isolate, MODULE).resolve()
    stores = {target, (base / "results-cache/crisprcasfinder").resolve()}
    existing = None
    if os.path.lexists(receipt):
        # A prior interruption may have replaced the alias before writing the
        # receipt. Validate both records first, then bind the alias below.
        existing = _verified_record(receipt, destination)
        attempt = Path(existing["payload_root"]).parent
        stores.add(attempt.parent.parent)
    else:
        dated = []
        for root in sorted(stores):
            for path in root.glob("*/attempt-*/started.json"):
                row = _object(path)
                if row.get("recovery_of") and not (path.parent / "completed.json").exists():
                    continue  # Interrupted snapshots are retained, never selected as native attempts.
                stamp = datetime.fromisoformat(row["started_at"])
                if stamp.tzinfo is None:
                    raise ValueError("Saved attempt timestamp lacks a timezone")
                dated.append((stamp, str(path)))
        if not dated:
            return {"isolate": str(isolate), "status": "no_saved_attempt"}
        dated.sort()
        if len(dated) > 1 and dated[-1][0] == dated[-2][0]:
            raise ValueError("Ambiguous latest saved attempt")
        attempt = Path(dated[-1][1]).parent
    payload = attempt / "data"
    source_store = attempt.parent.parent
    if (attempt.resolve() != attempt or attempt.is_symlink() or payload.is_symlink()
            or not payload.is_dir() or source_store not in stores
            or not re.fullmatch(r"attempt-[0-9a-f]{32}", attempt.name)
            or not re.fullmatch(r"[0-9a-f]{64}", attempt.parent.name)):
        raise ValueError("Saved attempt must be an ordinary directory in the selected sample result store")
    if (target != source_store and (target.is_relative_to(source_store) or source_store.is_relative_to(target))
            or target.is_relative_to(base / "metadata") or target.is_relative_to(destination)
            or destination.is_relative_to(target)):
        raise ValueError("Target result store overlaps protected inputs or original results")
    _require_local(target)
    started = _object(attempt / "started.json")
    original = started.get("identity", {})
    if (started.get("schema") != result_store.SCHEMA or started.get("name") != MODULE
            or started.get("status") != "started" or started.get("key") != attempt.parent.name
            or result_store.digest(original) != started["key"]):
        raise ValueError("Saved attempt identity is invalid")
    sample, config = _configured_inputs(isolate, context)
    identity, settings = _identity(original, sample, config)
    completed = None
    if (attempt / "completed.json").exists():
        completed = _verified_record(attempt / "completed.json", destination, original)
    else:
        failure = _object(attempt / "failure.json")
        if (original == identity or failure.get("schema") != result_store.SCHEMA
                or failure.get("name") != MODULE or failure.get("key") != started["key"]
                or failure.get("status") != "failed" or failure.get("identity") != original
                or failure.get("error") != OLD_ERROR):
            raise ValueError("Saved attempt did not fail with the supported legacy array-bounds error")
    commands = runtime.read_json(attempt / "commands.json")
    expected = ["python", "basis_ccf_launch.py", settings.get("executable", "perl"),
        "CRISPRCasFinder.pl", "-in", "input.fna", "-out", "native", "-so", "./sel392v2.so",
        "-cas", "-keep", "-log", "-cpuM", str(settings["threads"]),
        "-gcode", str(assembly.ccf._int(settings.get("genetic_code", 11), "genetic code", 1))]
    if (not isinstance(commands, list) or len(commands) != 1 or not isinstance(commands[0], dict)
            or commands[0].get("argv") != expected or commands[0].get("status") != "completed"
            or type(commands[0].get("returncode")) is not int or commands[0]["returncode"] != 0
            or not commands[0].get("started_at") or not commands[0].get("finished_at")):
        raise ValueError("Saved native command is not the expected successful invocation")
    isolation = _object(payload / "runtime_isolation.json")
    if (not Path(commands[0].get("cwd", "")).is_absolute()
            or commands[0]["cwd"] != isolation.get("working_directory")
            or isolation.get("macsyfinder_executable") not in original["extra_inputs"]
            or str(Path(isolation.get("python", "")).resolve()) not in original["extra_inputs"]):
        raise ValueError("Saved command and runtime executable isolation identities disagree")
    logged = assembly.ccf._ANSI.sub("", (attempt / "commands.log").read_text(encoding="utf-8", errors="replace"))
    if assembly.ccf._ERROR.search(logged):
        raise ValueError("Original command log records a native error")
    info = dict(isolate=str(isolate), attempt=attempt, source_store=source_store, store=target,
        destination=destination, receipt=receipt, sample=sample, config=config, settings=settings,
        identity=identity, original=original, existing=existing)
    info["parsed"] = _verify_payload(info, payload)
    info["inventory"] = _inventory(payload)
    info["command_files"] = {name: result_store.sha256(attempt / name) for name in ("commands.json", "commands.log")}
    cache = target / result_store.digest(identity) / "cache.json"
    if completed and original == identity and source_store == target:
        if cache.exists():
            _verified_record(cache, destination, identity)
        info.update(status="current_verified_result", completed=completed)
    elif cache.exists():
        row = _verified_record(cache, destination, identity)
        if _verify_payload(info, Path(row["payload_root"])) != runtime.read_json(Path(row["payload_root"]) / "parsed.json"):
            raise ValueError("Current recovery cache parsed output differs from current verification")
        info.update(status="current_verified_cache", completed=row)
    else:
        info["status"] = "ready_to_revalidate"
    if os.path.lexists(destination):
        allowed = [row for row in (existing, info.get("completed")) if row is not None]
        if (not destination.is_symlink() or destination.resolve() not in
                {Path(row["payload_root"]) for row in allowed}):
            raise ValueError("Unexpected native destination without a matching verified completion receipt or cache")
    info["alias_missing"] = not os.path.lexists(destination)
    info["cache_missing"] = not cache.exists()
    return info


def _link_or_copy(source, target):
    # Immutable payload files can share inodes. Writes to derived parsed.json
    # use runtime.write_json's atomic replacement, never an in-place overwrite.
    if not stat.S_ISREG(Path(source).lstat().st_mode):
        raise ValueError(f"Only ordinary files can be hardlinked: {source}")
    try:
        os.link(source, target)
    except OSError as error:
        if error.errno not in {errno.EXDEV, errno.EPERM, errno.EACCES, errno.ENOTSUP,
                               errno.EOPNOTSUPP, errno.ENOSYS, errno.EMLINK}:
            raise
        shutil.copy2(source, target)
    return str(target)


def _snapshot(info):
    if "completed" in info:
        return info["completed"]
    key = result_store.digest(info["identity"])
    attempt = info["store"] / key / ("attempt-" + uuid.uuid4().hex)
    payload = attempt / "data"
    payload.mkdir(parents=True)
    stamp = datetime.now(timezone.utc).isoformat()
    started = {"schema": result_store.SCHEMA, "status": "started", "name": MODULE,
        "key": key, "identity": info["identity"], "started_at": stamp,
        "recovery_of": str(info["attempt"])}
    runtime.write_json(attempt / "started.json", started)
    try:
        _progress(f"{info['isolate']}: snapshotting verified saved files")
        source = info["attempt"] / "data"
        shutil.copytree(source, payload, dirs_exist_ok=True, symlinks=True, copy_function=_link_or_copy)
        if _inventory(payload) != info["inventory"] or _inventory(source) != info["inventory"]:
            raise ValueError("Saved output changed during revalidation")
        for name, checksum in info["command_files"].items():
            _link_or_copy(info["attempt"] / name, attempt / name)
            if result_store.sha256(attempt / name) != checksum:
                raise ValueError("Saved command diagnostics changed during revalidation")
        parsed = _verify_payload(info, payload)
        if parsed != info["parsed"]:
            raise ValueError("Saved output changed during parsing")
        runtime.write_json(payload / "parsed.json", parsed)
        model = payload / "models"
        if model.is_symlink():
            model.unlink()
        runtime.write_json(attempt / "recovery.json", {"schema": SCHEMA,
            "original_attempt": str(info["attempt"]), "original_identity": info["original"],
            "original_command_files": info["command_files"], "native_commands_run": False,
            "original_files_removed": False})
        result_store._verify_execution_inputs(info["identity"])
        row = {"schema": result_store.SCHEMA, "status": "completed", "name": MODULE,
            "key": key, "identity": info["identity"], "payload_root": str(payload),
            "destination": str(info["destination"]), "artifacts": result_store.output_inventory(payload),
            "started_at": stamp, "finished_at": datetime.now(timezone.utc).isoformat()}
        runtime.write_json(attempt / "completed.json", row)
        return row
    except BaseException as error:
        runtime.write_json(attempt / "failure.json", {**started, "status": "failed",
            "error": str(error) or type(error).__name__, "finished_at": datetime.now(timezone.utc).isoformat()})
        raise  # Keep partial recovery attempts and the original source for retry.


def apply_samples(isolates, **options):
    require_stopped()
    previews = [inspect_sample(isolate, **options) for isolate in isolates]
    roots = sorted({row[key] for row in previews if "attempt" in row for key in ("source_store", "store")})
    with ExitStack() as locks:
        for root in roots:
            locks.enter_context(result_store._lock(root))
        require_stopped()
        # All selected samples pass full preflight under every store lock before
        # any cache, completion receipt or native alias is published.
        checked = [inspect_sample(isolate, **options) for isolate in isolates]
        if any(row.get(key) != before.get(key) for row, before in zip(checked, previews)
               for key in ("attempt", "source_store", "store", "identity")):
            raise ValueError("Saved result selection changed during preflight")
        rows = [(_snapshot(info) if "attempt" in info else None) for info in checked]
        require_stopped()
        for info in checked:
            if "attempt" in info:
                fresh, _ = _identity(info["original"], *_configured_inputs(Path(info["isolate"]), options.get("context")))
                if fresh != info["identity"]:
                    raise ValueError("Execution identity changed before publication")
        results = []
        for info, row in zip(checked, rows):
            if row is None:
                results.append({"isolate": info["isolate"], "status": info["status"]})
                continue
            if (info["status"] != "current_verified_result" or info["existing"] is None
                    or info["alias_missing"] or info["cache_missing"]):
                cache = Path(row["payload_root"]).parents[1] / "cache.json"
                runtime.write_json(cache, row)
                result_store._publish_link(info["destination"], Path(row["payload_root"]))
                runtime.write_json(info["receipt"], row)
            results.append({"isolate": info["isolate"], "status": "verified_saved_result_published",
                            "receipt": str(info["receipt"]), "payload_root": row["payload_root"],
                            "resume_threads": info["settings"]["threads"]})
        return results


def apply_sample(isolate, **options):
    return apply_samples([isolate], **options)[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configfile", nargs="+", action="extend", default=[])
    parser.add_argument("--isolate")
    parser.add_argument("--store", help="Explicit target result store, only with --isolate")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if not args.configfile and not args.isolate:
        parser.error("provide --configfile and/or --isolate")
    if args.store and not args.isolate:
        parser.error("--store requires --isolate")
    config, context = {}, None
    if args.configfile:
        config, _ = load_configuration(ROOT, args.configfile)
        context = SafetyContext(config, ROOT)
        isolates = [s for s in context.samples if context.module_enabled(s, MODULE)]
        if args.isolate:
            selected = str(Path(args.isolate).expanduser().resolve())
            if selected not in isolates:
                parser.error("--isolate must select an enabled sample in the configuration")
            isolates = [selected]
    else:
        isolates = [args.isolate]
    options = dict(workflow_config=config, context=context, store=args.store)
    if args.apply:
        rows = apply_samples(isolates, **options)
    else:
        previews = [inspect_sample(isolate, **options) for isolate in isolates]
        rows = [{key: row[key] for key in ("isolate", "status")} for row in previews]
    print(json.dumps({"results": rows, "native_commands_run": False, "original_files_removed": False}, indent=2))


if __name__ == "__main__":
    main()
