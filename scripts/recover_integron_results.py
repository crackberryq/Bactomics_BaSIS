#!/usr/bin/env python3
"""Revalidate saved IntegronFinder outputs without running native commands.

Default: preview. --apply publishes a compact verified snapshot of final tables
and diagnostics. All original attempts and their intermediate files remain.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor"), str(ROOT / "scripts")]

from archive_run_results import active_workflows
from safety import integron, native, result_store, runtime
from safety.context import SafetyContext
from safety.doctor import load_configuration

SCHEMA = "basis-integron-recovery-v1"
OLD_ERROR = "IntegronFinder summary does not account for exactly the input contigs"


def sources(root=ROOT):
    return [Path(root) / "workflow/lib/safety" / name for name in
            ("native.py", "native_execution.py", "result_store.py", "runtime.py")] + [
            Path(root) / "workflow/vendor/basis/adapters/context.py",
            Path(root) / "workflow/vendor/basis/inputs.py"]


def require_stopped():
    active = active_workflows()
    if active:
        raise ValueError("Stop the workflow and wait for its workers before recovery; active PIDs: "
                         + ", ".join(str(row["pid"]) for row in active))


def inspect_sample(isolate):
    base = Path(isolate).absolute() / "safety"
    store = base / "results-cache/integronfinder"
    destination = base / "native/integronfinder"
    receipt = base / "completion/integronfinder.json"
    existing = None
    if os.path.lexists(receipt):
        existing = result_store.verify_receipt(receipt, destination)
        attempt = Path(existing["payload_root"]).parent
    else:
        records = list(store.glob("*/attempt-*/started.json"))
        if not records:
            return {"isolate": str(isolate), "status": "no_saved_attempt"}
        dated = []
        for path in records:
            row = runtime.read_json(path)
            # An interrupted copy is not a newer native analysis. Keep it for
            # diagnostics, then retry from the preserved original attempt.
            if row.get("recovery_of") and not (path.parent / "completed.json").exists():
                continue
            dated.append((datetime.fromisoformat(row["started_at"]), str(path)))
        dated.sort()
        if not dated:
            raise ValueError("No original native attempt remains for recovery")
        if len(dated) > 1 and dated[-1][0] == dated[-2][0]:
            raise ValueError("Ambiguous latest attempt; inspect saved results before recovery")
        attempt = Path(dated[-1][1]).parent
    if (attempt.resolve() != attempt or attempt.is_symlink()
            or attempt.parent.parent != store or (attempt / "data").is_symlink()):
        raise ValueError("Saved attempt must belong to the sample's original result store")
    started = runtime.read_json(attempt / "started.json")
    original = started.get("identity", {})
    if (started.get("schema") != result_store.SCHEMA or started.get("name") != "integronfinder"
            or started.get("status") != "started" or started.get("key") != attempt.parent.name
            or result_store.digest(original) != started["key"]):
        raise ValueError("Saved attempt identity is invalid")
    sample = runtime.read_json(base / "metadata/sample.json")
    config = copy.deepcopy(runtime.read_json(base / "metadata/configs/integronfinder.json"))
    settings = config["modules"]["integronfinder"]
    if (not settings.get("enabled", True) or settings.get("cache", True) is not True
            or config.get("module_decisions", {}).get("integronfinder", {}).get("action", "run") != "run"):
        raise ValueError("Recovery requires an enabled module with cache reuse allowed")
    threads = original.get("settings", {}).get("threads")
    if type(threads) is not int or threads < 1:
        raise ValueError("Saved thread allocation is invalid")
    settings["threads"] = threads
    old_sources = {str(p.resolve()) for p in sources()}
    new_sources = old_sources | {str(Path(integron.__file__).resolve())}
    recorded_sources = set(original.get("sources", {}))
    if recorded_sources not in (old_sources, new_sources):
        raise ValueError("Saved source inventory is not the expected IntegronFinder worker")
    current = result_store.execution_identity("integronfinder", sample, config,
        sources=sorted(recorded_sources), executable=original.get("executable", {}).get("path"))
    if current != original:
        raise ValueError("Saved inputs, settings, executable, environment, resources or sources changed")
    identity = result_store.execution_identity("integronfinder", sample, config,
        sources=sorted(new_sources), executable=original["executable"]["path"])
    info = {"isolate": str(isolate), "base": base, "store": store, "destination": destination,
            "receipt": receipt, "attempt": attempt, "sample": sample, "config": config,
            "settings": settings, "identity": identity, "original": original}
    if recorded_sources == new_sources:
        completed = result_store._record(attempt / "completed.json", expected_identity=identity,
                                         destination=destination)
        if result_store.output_inventory(attempt / "data") != completed["artifacts"]:
            raise ValueError("Verified saved output has changed")
        info.update(status="current_verified_result", completed=completed)
        return info
    if existing is None:
        failure = runtime.read_json(attempt / "failure.json")
        if (failure.get("schema") != result_store.SCHEMA or failure.get("status") != "failed"
                or failure.get("identity") != original or failure.get("error") != OLD_ERROR):
            raise ValueError("Saved attempt did not fail with the supported summary-coverage error")
        if os.path.lexists(destination):
            raise ValueError("Unexpected native destination without a verified completion receipt")
    commands = runtime.read_json(attempt / "commands.json")
    expected = ["bash", "-euo", "pipefail", "-c",
                native.command_shell("integronfinder", sample, settings, attempt / "data", threads)]
    if (not isinstance(commands, list) or len(commands) != 1 or commands[0].get("argv") != expected
            or commands[0].get("status") != "completed" or type(commands[0].get("returncode")) is not int
            or commands[0]["returncode"] != 0 or not commands[0].get("finished_at")):
        raise ValueError("Saved native command is not the expected successful invocation")
    parsed = integron.parse_payload(sample, settings, attempt / "data")
    selected = [Path(p) for p in parsed["artifacts"]] + [attempt / "data/command.sh"]
    for path in selected:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(attempt / "data"):
            raise ValueError("Final output/diagnostic is missing or outside its original payload")
    info.update(status="ready_to_revalidate", selected=selected,
                coverage=parsed["metrics"]["contig_assessment"])
    return info


def apply_sample(isolate):
    require_stopped()
    info = inspect_sample(isolate)
    if info["status"] == "no_saved_attempt":
        return {"isolate": str(isolate), "status": "no_saved_attempt"}
    # The same lock protects original attempts and the new cache. No nested
    # locking, native fallback or failure pruning occurs in this recovery.
    with result_store._lock(info["store"]):
        require_stopped()
        info = inspect_sample(isolate)
        identity = info["identity"]
        key = result_store.digest(identity)
        cache = info["store"] / key / "cache.json"
        if info["status"] == "current_verified_result":
            row = info["completed"]
        elif cache.exists():
            row = result_store._record(cache, expected_identity=identity, destination=info["destination"])
            if result_store.output_inventory(row["payload_root"]) != row["artifacts"]:
                raise ValueError("Recovery cache contents changed")
        else:
            attempt = cache.parent / ("attempt-" + uuid.uuid4().hex)
            payload = attempt / "data"
            payload.mkdir(parents=True)
            stamp = datetime.now(timezone.utc).isoformat()
            runtime.write_json(attempt / "started.json", {"schema": result_store.SCHEMA,
                "status": "started", "name": "integronfinder", "key": key,
                "identity": identity, "started_at": stamp, "recovery_of": str(info["attempt"])})
            original_payload = info["attempt"] / "data"
            hashes = {str(p.relative_to(original_payload)): result_store.sha256(p) for p in info["selected"]}
            for path in info["selected"]:
                target = payload / path.relative_to(original_payload)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            for relative, checksum in hashes.items():
                if result_store.sha256(payload / relative) != checksum or result_store.sha256(original_payload / relative) != checksum:
                    raise ValueError("Saved output changed during recovery")
            for name in ("commands.json", "commands.log"):
                shutil.copy2(info["attempt"] / name, attempt / name)
            integron.verify(info["sample"], info["settings"], payload, origin_payload=original_payload)
            runtime.write_json(payload / "recovery.json", {"schema": SCHEMA,
                "original_attempt": str(info["attempt"]), "original_identity": info["original"],
                "copied_files": hashes, "native_commands_run": False,
                "original_files_removed": False, "retained_content": "final tables and diagnostics"})
            result_store._verify_execution_inputs(identity)
            row = {"schema": result_store.SCHEMA, "status": "completed", "name": "integronfinder",
                "key": key, "identity": identity, "payload_root": str(payload),
                "destination": str(info["destination"]), "artifacts": result_store.output_inventory(payload),
                "started_at": stamp, "finished_at": datetime.now(timezone.utc).isoformat()}
            runtime.write_json(attempt / "completed.json", row)
        require_stopped()
        runtime.write_json(cache, row)
        result_store._publish_link(info["destination"], Path(row["payload_root"]))
        runtime.write_json(info["receipt"], row)
    return {"isolate": str(isolate), "status": "verified_saved_result_published",
            "receipt": str(info["receipt"]), "native_commands_run": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--isolate")
    selection.add_argument("--configfile", nargs="+")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.isolate:
        isolates = [args.isolate]
    else:
        config, _ = load_configuration(ROOT, args.configfile)
        context = SafetyContext(config, ROOT)
        isolates = [s for s in context.samples if context.module_enabled(s, "integronfinder")]
    # Verify the whole selected batch before publishing any result.
    previews = [inspect_sample(s) for s in isolates]
    for row in previews:
        print(f"{row['isolate']}: {row['status']}", file=sys.stderr, flush=True)
    if args.apply:
        result = [apply_sample(s) for s in isolates]
    else:
        result = [{k: row[k] for k in ("isolate", "status", "coverage") if k in row} for row in previews]
    print(json.dumps({"results": result, "native_commands_run": False, "original_files_removed": False}, indent=2))


if __name__ == "__main__":
    main()
