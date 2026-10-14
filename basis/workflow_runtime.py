"""Small, independently scheduled workers for the BaSIS Snakemake workflow.

Scientific failures are recorded as data so the reporting DAG can finish. An
invalid plan or a changed stage receipt is an infrastructure error, never a
negative biological result.
"""
from __future__ import annotations

import argparse
import copy
import time
import uuid
from pathlib import Path

from .config import ALL_MODULES, PATH_KEYS
from .execution import CommandRunner
from .inputs import validate_sample
from .pipeline import (_base_record, _read_scheduler_result, _scheduler_root,
                       capture_workflow_provenance, collect_workflow_evidence,
                       execute_module, finish_deep_execution, module_config,
                       prepare_deep_execution, record_path, render_workflow_report)
from .sample_policy import module_decision
from .util import atomic_json, hash_object, now, read_json, sha256_file


def _write(path, value):
    value = dict(value)
    value.pop("content_sha256", None)
    value["content_sha256"] = hash_object(value)
    atomic_json(path, value)
    return value


def read_document(path):
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError("Workflow document is not a mapping: " + str(path))
    digest = value.pop("content_sha256", None)
    if digest != hash_object(value):
        raise ValueError("Workflow document checksum mismatch: " + str(path))
    value["content_sha256"] = digest
    return value


def _load(plan_path, run_dir):
    plan = read_json(plan_path)
    document = {k: v for k, v in plan.items() if k not in {"plan_hash", "plan_path"}}
    if hash_object(document) != plan.get("plan_hash"):
        raise ValueError("Execution plan checksum mismatch")
    root = _scheduler_root(plan, run_dir)
    run = read_document(root / "run-context.json")
    run.pop("content_sha256")
    if run.get("run_id") != root.name or run.get("source_hash") != plan["source_hash"] or run.get("config_hash") != hash_object(plan["config"]):
        raise ValueError("Workflow run context does not match execution plan")
    return plan, root, run


def _sample(plan, sid):
    return next(sample for sample in plan["samples"] if sample["sample_id"] == sid)


def _identity(plan, root, sid=None):
    return {"run_id": root.name, "plan_hash": plan["plan_hash"],
            "source_hash": plan["source_hash"], "sample_id": sid}


def _read_gate(path, plan, root, sid):
    value = read_document(path)
    if any(value.get(key) != expected for key, expected in _identity(plan, root, sid).items()):
        raise ValueError("Workflow gate identity mismatch: " + str(path))
    return value


def validate_inputs(plan, root, sid):
    sample = _sample(plan, sid)
    value = {"schema": "basis-workflow-input-check-v1", **_identity(plan, root, sid), "started_at": now()}
    try:
        checked = validate_sample(sample)
        if checked["input_hashes"] != plan["validations"][sid]["input_hashes"]:
            raise ValueError("Input files changed after planning; create a new execution plan")
        value.update(status="ready", input_hashes=checked["input_hashes"],
                     contigs=checked["contigs"], protein_count=checked["protein_count"])
    except Exception as exc:
        value.update(status="failed", error=str(exc))
    value["finished_at"] = now()
    return _write(root / sid / "input-validation.json", value)


def check_resources(plan, root, sid, module):
    sample = _sample(plan, sid)
    checked = _read_gate(root / sid / "input-validation.json", plan, root, sid)
    value = {"schema": "basis-workflow-resource-check-v1", **_identity(plan, root, sid),
             "module": module, "started_at": now(), "resources": {},
             "scope": "configured_path_availability; content fingerprints are verified by the scientific worker"}
    requested = plan["options"]["deep"] if module == "deepsafe" else module_decision(plan, sample, module)["action"] == "run"
    cfg = plan["config"]["deep"] if module == "deepsafe" else module_config(plan["config"], module, sample)
    try:
        if not requested:
            value.update(status="not_requested")
        else:
            if checked["status"] != "ready":
                raise ValueError("Input validation failed: " + checked.get("error", "unknown input error"))
            for key in sorted(PATH_KEYS - {"placement_cache_dir"}):
                if cfg.get(key):
                    path = Path(cfg[key])
                    value["resources"][key] = {"path": str(path), "exists": path.exists()}
                    if not path.exists():
                        raise ValueError("Configured resource does not exist: " + key + "=" + str(path))
            value["status"] = "ready"
    except Exception as exc:
        value.update(status="failed", error=str(exc))
    value["finished_at"] = now()
    return _write(root / sid / "checks" / (module + ".json"), value)


def _attest(plan, root, sid, module, result):
    target = record_path(plan, sid, module)
    atomic_json(root / sid / (module + ".json"), {
        "schema": "basis-scheduler-receipt-v1", **_identity(plan, root, sid),
        "module": module, "result_path": str(target.resolve()),
        "result_sha256": sha256_file(target), "execution_status": result["execution_status"],
        "recorded_at": now()})


def _execution_plan(plan, sample, module, allocated_threads=None):
    from .workflow_environments import execution_config
    selected = copy.deepcopy(plan)
    if module == "deepsafe":
        selected["config"]["deep"] = execution_config(plan, sample["sample_id"], module, selected["config"]["deep"])
    else:
        selected["config"]["modules"][module] = execution_config(plan, sample["sample_id"], module, selected["config"]["modules"][module])
    if allocated_threads is not None:
        cfg = selected["config"]["deep"] if module == "deepsafe" else selected["config"]["modules"][module]
        cfg["threads"] = min(int(cfg.get("threads", 1)), allocated_threads)
    return selected


def run_module(plan, root, sid, module, force=False, allocated_threads=None):
    sample = _sample(plan, sid)
    gate = _read_gate(root / sid / "checks" / (module + ".json"), plan, root, sid)
    if gate.get("module") != module:
        raise ValueError("Resource gate belongs to another module")
    try:
        if gate["status"] == "failed":
            raise ValueError(gate["error"])
        result = execute_module(_execution_plan(plan, sample, module, allocated_threads), sample, module, force)
    except Exception as exc:
        result = _base_record(plan, sample, module)
        result.update(execution_status="failed", interpretation="inconclusive", error=str(exc),
                      module_decision=module_decision(plan, sample, module), finished_at=now())
        atomic_json(record_path(plan, sid, module), result)
    _attest(plan, root, sid, module, result)
    return result


def run_deep_stage(plan, root, sid, stage, force=False, allocated_threads=None):
    from .deep_stages import STAGES, run_stage
    if stage not in STAGES:
        raise ValueError("Unknown DeepSafe workflow stage: " + stage)
    sample = _sample(plan, sid)
    index = STAGES.index(stage)
    previous_path = root / sid / "deep-stages" / (STAGES[index - 1] + ".json") if index else None
    if previous_path:
        previous = _read_gate(previous_path, plan, root, sid)
        if previous.get("stage") != STAGES[index - 1]:
            raise ValueError("DeepSafe workflow stage order mismatch")
        envelope, state = previous["envelope"], previous["state"]
    else:
        records = {module: _read_scheduler_result(plan, sid, module, root) for module in ALL_MODULES}
        gate = _read_gate(root / sid / "checks/deepsafe.json", plan, root, sid)
        try:
            if gate["status"] == "failed":
                raise ValueError(gate["error"])
            selected_plan = _execution_plan(plan, sample, "deepsafe", allocated_threads)
            envelope = prepare_deep_execution(selected_plan, sample, force, records)
        except Exception as exc:
            result = _base_record(plan, sample, "deepsafe")
            result.update(execution_status="failed", interpretation="inconclusive", error=str(exc), candidates=[])
            work = root / sid / "deep-stage-error"
            work.mkdir(parents=True, exist_ok=True)
            envelope = {"result": result, "records": list(records.values()), "config": {},
                        "work": str(work), "started": time.monotonic(), "cache_allowed": False,
                        "stopped": True, "commands": []}
        state = None
    status = "cached" if envelope.get("cached") else "not_requested" if not plan["options"]["deep"] else "blocked" if envelope["stopped"] else "completed"
    if not envelope["stopped"]:
        runner = CommandRunner(envelope["config"], Path(envelope["work"]) / "commands.log")
        try:
            if allocated_threads is not None and int(envelope["config"].get("threads", 1)) > allocated_threads:
                raise ValueError("DeepSafe allocation changed between stages; start a fresh execution plan")
            # These native wrappers require a fresh output directory. If a worker
            # was interrupted before committing its stage receipt, preserve that
            # attempt explicitly and create the stage again from its valid input.
            owned_name = {"patterns": "prosite_patterns", "profiles": "prosite_profiles"}.get(stage)
            if owned_name:
                interrupted = Path(envelope["work"]) / owned_name
                if interrupted.exists():
                    archive = Path(envelope["work"]) / "interrupted-stage-artifacts" / (stage + "-" + uuid.uuid4().hex)
                    archive.mkdir(parents=True, exist_ok=False)
                    archived = archive / owned_name
                    interrupted.rename(archived)
                    envelope.setdefault("stage_retries", []).append({"stage": stage, "previous_output": str(interrupted),
                        "preserved_output": str(archived), "reason": "output_exists_without_committed_stage_receipt", "at": now()})
            state = run_stage(stage, sample, envelope["records"], envelope["config"], Path(envelope["work"]), runner, state)
        except Exception as exc:
            envelope["result"].update(execution_status="failed", interpretation="inconclusive", error=str(exc), candidates=[])
            envelope["stopped"] = True
            status = "failed"
        envelope["commands"].extend(runner.commands)
    if stage == STAGES[-1]:
        payload = state.get("payload") if state and not envelope["stopped"] else None
        result = finish_deep_execution(plan, sample, envelope, payload)
        _attest(plan, root, sid, "deepsafe", result)
    value = {"schema": "basis-workflow-deep-stage-v1", **_identity(plan, root, sid),
             "stage": stage, "status": status, "finished_at": now(),
             "previous_sha256": sha256_file(previous_path) if previous_path else None,
             "envelope": envelope, "state": state}
    return _write(root / sid / "deep-stages" / (stage + ".json"), value)


def consolidate_evidence(plan, root, run):
    bundle = collect_workflow_evidence(plan, run, "snakemake", root)
    value = {"schema": "basis-workflow-consolidation-v1", **_identity(plan, root), "bundle": bundle}
    return _write(root / "consolidated.json", value)


def render_report(plan, root):
    snapshot = _read_gate(root / "consolidated.json", plan, root, None)
    artifacts = render_workflow_report(snapshot["bundle"], plan["outdir"])
    value = {"schema": "basis-workflow-report-v1", **_identity(plan, root), "artifacts": artifacts,
             "artifact_checksums": {path: sha256_file(path) for path in artifacts.values()
                                    if isinstance(path, str) and Path(path).is_file()}}
    return _write(root / "report-artifacts.json", value)


def capture_provenance(plan, root):
    snapshot = _read_gate(root / "consolidated.json", plan, root, None)
    report = _read_gate(root / "report-artifacts.json", plan, root, None)
    for path, digest in report["artifact_checksums"].items():
        if sha256_file(path) != digest:
            raise ValueError("Report artifact changed before provenance capture: " + path)
    result, code = capture_workflow_provenance(plan, snapshot["bundle"], report["artifacts"])
    return _write(root / "outcome.json", {"schema": "basis-workflow-outcome-v1",
                  **_identity(plan, root), "result": result, "exit_code": code})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "resources", "module", "deep-stage", "consolidate", "report", "provenance"))
    parser.add_argument("--plan", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--sample")
    parser.add_argument("--module", choices=ALL_MODULES + ["deepsafe"])
    parser.add_argument("--stage")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--threads", type=int)
    args = parser.parse_args(argv)
    if args.threads is not None and args.threads < 1:
        parser.error("--threads must be positive")
    plan, root, run = _load(args.plan, args.run_dir)
    if args.action in {"validate", "resources", "module", "deep-stage"} and not args.sample:
        parser.error("this worker requires --sample")
    if args.action in {"resources", "module"} and not args.module:
        parser.error("this worker requires --module")
    if args.action == "deep-stage" and not args.stage:
        parser.error("deep-stage requires --stage")
    if args.action == "validate":
        validate_inputs(plan, root, args.sample)
    elif args.action == "resources":
        check_resources(plan, root, args.sample, args.module)
    elif args.action == "module":
        run_module(plan, root, args.sample, args.module, args.force, args.threads)
    elif args.action == "deep-stage":
        run_deep_stage(plan, root, args.sample, args.stage, args.force, args.threads)
    elif args.action == "consolidate":
        consolidate_evidence(plan, root, run)
    elif args.action == "report":
        render_report(plan, root)
    else:
        capture_provenance(plan, root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
