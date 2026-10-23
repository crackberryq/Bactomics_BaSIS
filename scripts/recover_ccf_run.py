#!/usr/bin/env python3
"""Preview or recover completed local outputs for the configured sample batch.

No native command runs and no original files are removed. Existing completion
receipts stay in place; the normal workflow still verifies their payloads.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor"), str(ROOT / "scripts")]

import recover_ccf_results as recovery
from safety.context import SafetyContext
from safety.doctor import load_configuration
from safety import result_store
from safety.storage import native_result_store

SCHEMA = "basis-ccf-run-recovery-v1"


def _latest_attempt(store):
    candidates = []
    for path in Path(store).glob("*/attempt-*/started.json"):
        row = json.loads(path.read_text())
        if (not isinstance(row, dict) or row.get("schema") != result_store.SCHEMA
                or row.get("name") != "crisprcasfinder" or row.get("status") != "started"):
            raise ValueError(f"Malformed attempt record: {path}")
        try:
            stamp = datetime.fromisoformat(row["started_at"])
            if stamp.tzinfo is None:
                raise ValueError("timestamp requires a timezone")
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid attempt start time: {path}") from error
        candidates.append((stamp, path.parent))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        raise ValueError(f"Ambiguous latest attempt in {store}; specify an attempt with recover_ccf_results.py")
    return candidates[0][1]


def build_run_plan(root, configfiles=()):
    root = Path(root).resolve()
    config, files = load_configuration(root, configfiles)
    storage = config.get("storage")
    if not isinstance(storage, dict) or "native_results_directory" not in storage:
        raise ValueError("Set storage.native_results_directory to a Linux directory before recovery")
    context = SafetyContext(config, root)
    if not context.samples:
        raise ValueError("No configured samples to review")
    rows = []
    for isolate in context.samples:
        if not context.module_enabled(isolate, "crisprcasfinder"):
            rows.append({"isolate": isolate, "status": "module_disabled"})
            continue
        base = Path(isolate) / "safety"
        native, receipt = base / "native/crisprcasfinder", base / "completion/crisprcasfinder.json"
        store = native_result_store(root, config, isolate, "crisprcasfinder")
        row = {"isolate": isolate, "store": str(store), "receipt": str(receipt)}
        if os.path.lexists(receipt):
            recorded = result_store._record(receipt, destination=native)
            result_store._check_link(recorded)
            if recorded.get("name") != "crisprcasfinder" or not Path(recorded["payload_root"]).is_dir():
                raise ValueError(f"Existing completion has an invalid or missing payload: {receipt}")
            row.update(status="existing_completion_unchanged", payload_root=recorded["payload_root"])
        else:
            attempt = _latest_attempt(base / "results-cache/crisprcasfinder")
            commands = []
            if attempt is not None and (attempt / "commands.json").exists():
                commands = json.loads((attempt / "commands.json").read_text())
                if not isinstance(commands, list) or any(not isinstance(item, dict) for item in commands):
                    raise ValueError(f"Malformed command record: {attempt / 'commands.json'}")
                if len(commands) > 1:
                    raise ValueError(f"Expected at most one recorded native command: {attempt / 'commands.json'}")
            if len(commands) != 1 or commands[0].get("status") != "completed" or commands[0].get("returncode") != 0:
                row.update(status="no_recoverable_native_completion", attempt=str(attempt) if attempt else None)
            else:
                plan = recovery.build_plan(attempt, base / "metadata/sample.json",
                    base / "metadata/configs/crisprcasfinder.json", store, native, receipt)
                row.update(status=plan["status"], recovery=plan)
        rows.append(row)
    return {"schema": SCHEMA, "root": str(root), "configfiles": files[1:],
            "status": "preview", "results": rows, "native_commands_will_run": False,
            "original_files_will_be_removed": False}


def apply_run_plan(plan):
    if plan.get("schema") != SCHEMA:
        raise ValueError("Unsupported batch recovery plan")
    recovery._require_stopped()
    # Rebuild every plan before the first mutation. A bad later sample must not
    # silently leave an earlier sample published before metadata preflight ends.
    fresh = build_run_plan(plan["root"], plan["configfiles"])
    for row in fresh["results"]:
        print(f"{row['isolate']}: {row['status']}", file=sys.stderr, flush=True)
        if "recovery" in row:
            row["result"] = recovery.apply_plan(row["recovery"])
            row["status"] = row["result"]["status"]
    fresh["status"] = "completed"
    return fresh


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configfile", nargs="+", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    plan = build_run_plan(ROOT, [path for group in args.configfile for path in group])
    print(json.dumps(apply_run_plan(plan) if args.apply else plan, indent=2))


if __name__ == "__main__":
    main()
