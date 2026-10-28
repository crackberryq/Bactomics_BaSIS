#!/usr/bin/env python3
"""Revalidate one saved ISEScan zero-result attempt without running native tools.

Preview is read-only. --apply copies the verified raw output into a new cache
snapshot and writes its completion/evidence records. --refresh-report also
renders the screening report from saved evidence; it never invokes Snakemake.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor"), str(ROOT / "scripts")]

from archive_run_results import active_workflows
from basis.report_scope import validate_report_output_paths
from safety import native, reporting, result_store, runtime

MODULE = "isescan"
SCHEMA = "basis-isescan-recovery-v1"
PARSER = "workflow/vendor/basis/adapters/context.py"
LEGACY_PARSER = "3ae8d2ce0e8189e2d2980c6a915777b625bf303990efe2e5ead54b7f9c12d2b8"
FIXED_PARSER = "52f3f6958058e0dfc1768a986a2bc97edb61e3205489b6cd9a1088cffe3e1bc3"
OLD_ERROR = "ISEScan missing table: no-hit transcript does not cover every input contig"


def sources():
    return [ROOT / "workflow/lib/safety" / name for name in
            ("native.py", "native_execution.py", "result_store.py", "runtime.py")] + [
            ROOT / PARSER, ROOT / "workflow/vendor/basis/inputs.py"]


def stopped():
    if active := active_workflows():
        raise ValueError("Wait for workflow workers to stop before recovery; active PIDs: "
                         + ", ".join(str(row["pid"]) for row in active))


def plain(path):
    path = Path(path).absolute()
    if path.is_symlink() or path.resolve() != path:
        raise ValueError(f"Recovery path must not contain symlink aliases: {path}")
    return path


def read(path):
    value = runtime.read_json(plain(path))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def inventory(payload):
    rows = result_store.output_inventory(plain(payload))
    if any(row["kind"] == "link" for row in rows.values()):
        raise ValueError("Recovery requires ordinary saved output files, without symlinks")
    return rows


def current_identity(original, sample, config):
    config = copy.deepcopy(config)
    settings = config["modules"][MODULE]
    if (settings.get("enabled", True) is not True or settings.get("cache", True) is not True
            or config.get("module_decisions", {}).get(MODULE, {}).get("action", "run") != "run"):
        raise ValueError("Recovery requires enabled ISEScan with cache reuse allowed")
    threads = original.get("settings", {}).get("threads")
    if type(threads) is not int or threads < 1:
        raise ValueError("Saved thread allocation is invalid")
    settings["threads"] = threads
    if set(original.get("sources", {})) != {str(p.resolve()) for p in sources()}:
        raise ValueError("Saved ISEScan source inventory does not match this checkout")
    if result_store.sha256(ROOT / PARSER) != FIXED_PARSER:
        raise ValueError("Recovery supports only the reviewed zero-result parser revision")
    executable = original.get("executable", {}).get("path")
    if not isinstance(executable, str) or not Path(executable).is_absolute():
        raise ValueError("Saved native executable path is missing")
    current = result_store.execution_identity(MODULE, sample, config, sources=sources(), executable=executable)
    legacy = copy.deepcopy(current)
    legacy["sources"][str((ROOT / PARSER).resolve())] = LEGACY_PARSER
    if original not in (current, legacy):
        raise ValueError("Saved inputs, configuration, resources, environment, executable or other sources changed")
    return current, settings


def checked_record(path, identity, destination):
    row = result_store._record(plain(path), expected_identity=identity, destination=destination)
    if inventory(row["payload_root"]) != row["artifacts"]:
        raise ValueError("Recovered payload changed after verification")
    return row


def inspect(isolate):
    isolate = plain(Path(isolate).expanduser())
    base = plain(isolate / "safety")
    store = plain(base / "results-cache/isescan")
    destination = base / "native/isescan"
    receipt = plain(base / "completion/isescan.json")
    for folder in (destination.parent, base / "evidence", base / "metadata/configs"):
        plain(folder)
    sample_path, config_path, validation_path = (base / "metadata" / name for name in
                                                ("sample.json", "configs/isescan.json", "validation.json"))
    sample, config, validation = read(sample_path), read(config_path), read(validation_path)
    metadata = {str(p): result_store.sha256(p) for p in (sample_path, config_path, validation_path)}
    if os.path.lexists(receipt):
        existing = read(receipt)
        identity, settings = current_identity(existing["identity"], sample, config)
        completed = checked_record(receipt, identity, destination)
        info = dict(status="already_recovered", completed=completed, original=completed["identity"],
                    attempt=Path(completed["payload_root"]).parent, identity=identity, settings=settings)
    else:
        attempts = []
        for path in store.glob("*/attempt-*/started.json"):
            started = read(path)
            if started.get("recovery_of"):
                continue
            stamp = datetime.fromisoformat(started["started_at"])
            if stamp.tzinfo is None:
                raise ValueError("Saved attempt timestamp lacks timezone")
            attempts.append((stamp, path.parent))
        attempts.sort()
        if not attempts or len(attempts) > 1 and attempts[-1][0] == attempts[-2][0]:
            raise ValueError("No unambiguous latest saved native attempt")
        attempt = plain(attempts[-1][1])
        if (not re.fullmatch(r"attempt-[0-9a-f]{32}", attempt.name)
                or not re.fullmatch(r"[0-9a-f]{64}", attempt.parent.name)
                or attempt.parent.parent != store):
            raise ValueError("Attempt does not belong to this sample's result store")
        started, failure = read(attempt / "started.json"), read(attempt / "failure.json")
        original = started.get("identity", {})
        for row, status in ((started, "started"), (failure, "failed")):
            if (row.get("schema") != result_store.SCHEMA or row.get("name") != MODULE
                    or row.get("status") != status or row.get("identity") != original
                    or row.get("key") != attempt.parent.name or result_store.digest(original) != row["key"]):
                raise ValueError("Saved attempt identity is invalid")
        if failure.get("error") != OLD_ERROR or (attempt / "completed.json").exists():
            raise ValueError("Saved attempt is not the supported legacy parser failure")
        identity, settings = current_identity(original, sample, config)
        if original["sources"][str((ROOT / PARSER).resolve())] != LEGACY_PARSER:
            raise ValueError("Failed attempt did not use the affected legacy parser")
        payload = plain(attempt / "data")
        commands_path = plain(attempt / "commands.json")
        commands = runtime.read_json(commands_path)
        expected = ["bash", "-euo", "pipefail", "-c",
                    native.command_shell(MODULE, sample, settings, payload, settings["threads"])]
        if (not isinstance(commands, list) or len(commands) != 1 or not isinstance(commands[0], dict)
                or commands[0].get("argv") != expected or commands[0].get("status") != "completed"
                or type(commands[0].get("returncode")) is not int or commands[0]["returncode"] != 0
                or not commands[0].get("finished_at")):
            raise ValueError("Saved native command is not the expected successful invocation")
        before = inventory(payload)
        parsed = native.parse_payload(MODULE, sample, settings, payload)
        if (parsed["findings"] or parsed["metrics"].get("insertion_sequence_count") != 0
                or parsed["metrics"].get("native_no_hit_format") != "isescan_1.7.3_no_significant_hits"):
            raise ValueError("This recovery is restricted to verified native zero findings")
        if inventory(payload) != before:
            raise ValueError("Saved output changed while parsing")
        info = dict(status="ready_to_revalidate", attempt=attempt, identity=identity, settings=settings,
                    original=original, parsed=parsed, inventory=before,
                    command_hashes={name: result_store.sha256(plain(attempt / name)) for name in
                                    ("started.json", "failure.json", "commands.json", "commands.log")})
        cache = store / result_store.digest(identity) / "cache.json"
        if cache.exists():
            info.update(status="already_recovered", completed=checked_record(cache, identity, destination))
    if os.path.lexists(destination):
        completed = info.get("completed")
        if not destination.is_symlink() or not completed or destination.resolve() != Path(completed["payload_root"]):
            raise ValueError("Native destination is not this recovery's verified output")
    info.update(isolate=isolate, base=base, store=store, destination=destination, receipt=receipt,
                sample=sample, config=config, validation=validation, metadata=metadata)
    for key, row in info["identity"]["inputs"].items():
        if validation.get("input_hashes", {}).get(key) != row["sha256"]:
            raise ValueError("Saved validation does not match current input hashes")
    plain(base / "evidence/isescan.json")
    return info


def report_inputs(info):
    base = info["base"]
    config = read(base / "metadata/config.json")
    configured = config.get("modules", {})
    expected = set(config.get("requested_modules", []))
    expected.update(name for name, value in configured.items() if reporting._enabled(value))
    decisions = config.get("module_decisions", {})
    decisions = decisions.get(info["sample"]["sample_id"], decisions)
    expected.update(name for name, row in decisions.items()
                    if isinstance(row, dict) and row.get("action") not in (None, "disabled"))
    expected.discard("deepsafe")
    if any(not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", name) for name in expected):
        raise ValueError("Invalid module name in saved report configuration")
    if MODULE not in expected:
        raise ValueError("Saved screening report configuration does not request ISEScan")
    paths = [plain(base / "evidence" / (name + ".json")) for name in sorted(expected) if name != MODULE]
    for path in paths:
        record = read(path)
        if record.get("input_hashes") != info["validation"]["input_hashes"]:
            raise ValueError(f"Saved evidence belongs to different inputs: {path}")
    # Validate the requested report scope before publication; this temporary
    # record is never published or rendered. Final evidence uses normalized_record.
    with tempfile.TemporaryDirectory(prefix="basis-isescan-report-check-") as temp:
        projected = Path(temp) / "isescan.json"
        runtime.write_json(projected, {"sample_id": info["sample"]["sample_id"], "module": MODULE,
            "execution_status": "completed", "interpretation": "inconclusive", "findings": []})
        reporting.aggregate(info["sample"], config, paths + [projected], view="screening")
    validate_report_output_paths(base)
    return paths + [base / "evidence/isescan.json"]


def snapshot(info):
    if "completed" in info:
        return info["completed"]
    key = result_store.digest(info["identity"])
    attempt = info["store"] / key / ("attempt-" + uuid.uuid4().hex)
    payload = attempt / "data"
    payload.mkdir(parents=True)
    stamp = datetime.now(timezone.utc).isoformat()
    runtime.write_json(attempt / "started.json", {"schema": result_store.SCHEMA, "status": "started",
        "name": MODULE, "key": key, "identity": info["identity"], "started_at": stamp,
        "recovery_of": str(info["attempt"])})
    source = info["attempt"] / "data"
    shutil.copytree(source, payload, dirs_exist_ok=True)
    if inventory(payload) != info["inventory"] or inventory(source) != info["inventory"]:
        raise ValueError("Saved raw output changed during recovery")
    parsed = native.parse_payload(MODULE, info["sample"], info["settings"], source)
    if parsed != info["parsed"]:
        raise ValueError("Saved parsed result changed during recovery")
    parsed = copy.deepcopy(parsed)
    parsed["artifacts"] = [str(payload / Path(path).relative_to(source)) for path in parsed["artifacts"]]
    runtime.write_json(payload / "parsed.json", parsed)
    for name, checksum in info["command_hashes"].items():
        if result_store.sha256(info["attempt"] / name) != checksum:
            raise ValueError("Original attempt diagnostics changed during recovery")
    audit = {"schema": SCHEMA, "original_attempt": str(info["attempt"]), "original_identity": info["original"],
             "original_inventory": info["inventory"], "original_record_hashes": info["command_hashes"],
             "native_commands_run": False, "original_files_removed": False,
             "recovery_script_sha256": result_store.sha256(Path(__file__))}
    runtime.write_json(payload / "recovery.json", audit)
    # Native transcripts retain their original absolute paths. They are copied
    # byte-for-byte; the audit binds them to the verified original attempt.
    row = {"schema": result_store.SCHEMA, "status": "completed", "name": MODULE, "key": key,
           "identity": info["identity"], "payload_root": str(payload), "destination": str(info["destination"]),
           "artifacts": inventory(payload), "started_at": stamp, "finished_at": datetime.now(timezone.utc).isoformat(),
           "recovery_of": str(info["attempt"])}
    return row


def recover(isolate, *, apply=False, refresh_report=False):
    stopped()
    info = inspect(isolate)
    if refresh_report:
        report_inputs(info)
    result = {"status": info["status"], "isolate": str(info["isolate"]), "attempt": str(info["attempt"]),
              "native_commands_run": False, "applied": False}
    if not apply:
        return result
    with result_store._lock(info["store"]):
        stopped()
        info = inspect(isolate)
        paths = report_inputs(info) if refresh_report else []
        record = snapshot(info)
        # Recheck identities, metadata and destination ownership immediately
        # before publishing any completion/evidence records.
        fresh = inspect(isolate)
        if fresh["identity"] != info["identity"] or fresh["metadata"] != info["metadata"]:
            raise ValueError("Saved inputs or metadata changed during recovery")
        for key in ("inventory", "command_hashes"):
            if fresh.get(key) != info.get(key):
                raise ValueError("Original attempt changed before publication")
        payload = Path(record["payload_root"])
        if inventory(payload) != record["artifacts"]:
            raise ValueError("Recovered snapshot changed before publication")
        evidence = native.normalized_record(MODULE, info["sample"], info["config"], info["validation"], payload)
        runtime.write_json(payload.parent / "completed.json", record)
        runtime.write_json(payload.parent.parent / "cache.json", record)
        result_store._publish_link(info["destination"], payload)
        runtime.write_json(info["receipt"], record)
        evidence_path = info["base"] / "evidence/isescan.json"
        if not evidence_path.exists() or read(evidence_path) != evidence:
            runtime.write_json(evidence_path, evidence)
        if refresh_report:
            # Recheck all report inputs before the renderer writes its exports.
            paths = report_inputs(inspect(isolate))
            reporting.render(info["base"] / "metadata/sample.json", info["base"] / "metadata/config.json",
                             paths, info["base"], view="screening", evidence_root=info["base"])
            result["report"] = str(info["base"] / "report.html")
        result.update(status="recovered", applied=True, receipt=str(info["receipt"]),
                      insertion_sequence_count=evidence["metrics"]["insertion_sequence_count"],
                      interpretation=evidence["interpretation"])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolate", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--refresh-report", action="store_true")
    args = parser.parse_args(argv)
    result = recover(args.isolate, apply=args.apply, refresh_report=args.refresh_report)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
