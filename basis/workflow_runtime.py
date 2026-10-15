"""Small, independently scheduled workers for the BaSIS Snakemake workflow.

Execution failures are durable evidence and nonzero scheduler outcomes. Only
successful workers publish readiness markers, so dependent analyses cannot run
after a failed job. Scientific uncertainty and deferred candidates are not tool
failures.
"""
from __future__ import annotations

import argparse
import copy
import os
import sys
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


def ready_path(receipt):
    """Separate disposable DAG outputs from durable diagnostic evidence."""
    return Path(receipt).with_suffix(".ready.json")


def _ready(receipt, plan, root, sid):
    receipt = Path(receipt)
    value = read_json(receipt)
    dependencies = dict(value.get("dependency_checksums") or {})
    parent = receipt.parent
    if sid is not None:
        if parent == root / sid / "checks":
            paths = [ready_path(root / sid / "input-validation.json")]
            from .bootstrap import bootstrap_dependencies
            paths.extend(Path(p) for p in bootstrap_dependencies(plan, sid, value["module"]))
        elif parent == root / sid and value.get("schema") == "basis-scheduler-receipt-v1":
            paths = [ready_path(root / sid / "checks" / (value["module"] + ".json"))]
            if value["module"] == "deepsafe":
                final = ready_path(root / sid / "deep-stages/finalize.json")
                if final.exists():
                    paths.append(final)
        elif parent == root / sid / "deep-stages" and value.get("stage") == "prepare":
            paths = [ready_path(root / sid / (module + ".json")) for module in ALL_MODULES]
            paths.append(ready_path(root / sid / "checks/deepsafe.json"))
        else:
            paths = []
        dependencies.update({str(path): sha256_file(path) for path in paths if path.exists()})
    return _write(ready_path(receipt), {
        "schema": "basis-workflow-ready-v1", **_identity(plan, root, sid),
        "receipt": str(receipt.resolve()), "receipt_sha256": sha256_file(receipt),
        "dependency_checksums": dependencies})


def _commit_check(path, value, plan, root, sid):
    value = _write(path, value)
    if value["status"] in {"ready", "not_requested"}:
        _ready(path, plan, root, sid)
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
    run = read_document(_report_root(root) / "run-context.json")
    run.pop("content_sha256")
    if run.get("run_id") != _report_root(root).name or run.get("source_hash") != plan["source_hash"] or run.get("config_hash") != hash_object(plan["config"]):
        raise ValueError("Workflow run context does not match execution plan")
    return plan, root, run


def _report_root(root):
    value = os.environ.get("BASIS_SCHEDULER_LAUNCH_DIR")
    if not value:
        return root
    path = Path(value).resolve()
    if path.parent != Path(root).resolve().parent or path == Path(root).resolve():
        raise ValueError("Launch history must be a separate sibling of scheduler state")
    return path


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
    path = root / sid / "input-validation.json"
    ready_path(path).unlink(missing_ok=True)
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
    return _commit_check(path, value, plan, root, sid)


def check_resources(plan, root, sid, module):
    sample = _sample(plan, sid)
    receipt = root / sid / "checks" / (module + ".json")
    ready_path(receipt).unlink(missing_ok=True)
    checked = _read_gate(root / sid / "input-validation.json", plan, root, sid)
    value = {"schema": "basis-workflow-resource-check-v1", **_identity(plan, root, sid),
             "module": module, "started_at": now(), "resources": {},
             "scope": "required resource content and native tool identity verified before global setup barrier"}
    requested = plan["options"]["deep"] if module == "deepsafe" else module_decision(plan, sample, module)["action"] == "run"
    try:
        if not requested:
            value.update(status="not_requested")
        else:
            if checked["status"] != "ready":
                raise ValueError("Input validation failed: " + checked.get("error", "unknown input error"))
            from .bootstrap import apply_resources
            resolved = apply_resources(plan, root, module, sample_id=sid)
            from .workflow_environments import execution_config
            cfg = resolved["config"]["deep"] if module == "deepsafe" else module_config(resolved["config"], module, sample)
            cfg = execution_config(resolved, sid, module, cfg)
            for key in sorted(PATH_KEYS - {"placement_cache_dir"}):
                if cfg.get(key):
                    path = Path(cfg[key])
                    value["resources"][key] = {"path": str(path), "exists": path.exists()}
                    if not path.exists():
                        raise ValueError("Configured resource does not exist: " + key + "=" + str(path))
            from .scheduler_persistence import resource_fingerprints, capture_tool_identity
            value["verified_config"] = cfg
            value["resource_fingerprints"] = resource_fingerprints(cfg, require_present=True)
            if cfg.get("mode", "native") == "native" and plan["config"].get("validation_mode") != "synthetic_fixture":
                runner = CommandRunner(cfg, _report_root(root) / sid / "logs" / ("check_" + module + ".commands.log"))
                value["tool_identity"] = capture_tool_identity(module, cfg, runner)
                if module != "deepsafe":
                    from .execution import probe_version
                    version = probe_version(module, cfg, runner)
                    value["tool_version"] = version
                    if version.get("status") == "failed":
                        raise ValueError("Required tool startup failed: " + version.get("error", module))
            value["status"] = "ready"
    except Exception as exc:
        value.update(status="failed", error=str(exc))
    value["finished_at"] = now()
    return _commit_check(receipt, value, plan, root, sid)


def setup_ready(plan, root):
    """All requested tools/resources must be ready before native sample jobs."""
    from .progress import emit
    receipt = root / "setup.json"
    ready_path(receipt).unlink(missing_ok=True)
    gates = []
    value = {"schema": "basis-workflow-setup-v1", **_identity(plan, root), "started_at": now()}
    try:
        for sample in plan["samples"]:
            sid = sample["sample_id"]
            for module in ALL_MODULES + ["deepsafe"]:
                gate_path = root / sid / "checks" / (module + ".json")
                gate = _read_gate(gate_path, plan, root, sid)
                if gate.get("module") != module or gate.get("status") not in {"ready", "not_requested"}:
                    raise ValueError("Setup prerequisite failed: " + sid + ":" + module)
                gates.append({"sample_id": sid, "module": module, "status": gate["status"],
                              "path": str(gate_path), "sha256": sha256_file(gate_path)})
        value.update(status="ready", gates=gates)
        emit("Setup complete: all requested tools, databases and models verified; sample analyses may start")
    except Exception as exc:
        value.update(status="failed", error=str(exc), gates=gates)
    value["finished_at"] = now()
    return _commit_check(receipt, value, plan, root, None)


def _attest(plan, root, sid, module, result):
    from .scheduler_state import started_path
    target = record_path(plan, sid, module)
    receipt = root / sid / (module + ".json")
    started = started_path(root, sid, module)
    atomic_json(receipt, {
        "schema": "basis-scheduler-receipt-v1", **_identity(plan, root, sid),
        "module": module, "result_path": str(target.resolve()),
        "result_sha256": sha256_file(target), "execution_status": result["execution_status"],
        "started_sha256": sha256_file(started) if started.exists() else None,
        "producer_launch_id": _report_root(root).name,
        "recorded_at": now()})
    if result["execution_status"] not in {"failed", "blocked"}:
        _ready(receipt, plan, root, sid)


def _execution_plan(plan, sample, module, allocated_threads=None):
    from .workflow_environments import execution_config
    from .bootstrap import apply_resources
    import os
    run_id = os.environ.get("BASIS_SCHEDULER_RUN_ID")
    # Direct unit/programmatic callers without bootstrap tasks retain the old path.
    root = Path(plan["outdir"]) / "provenance" / "scheduler" / run_id if run_id else None
    selected = apply_resources(plan, root, module, sample_id=sample["sample_id"]) if plan.get("bootstrap_resources") else copy.deepcopy(plan)
    if module == "deepsafe":
        selected["config"]["deep"] = execution_config(selected, sample["sample_id"], module, selected["config"]["deep"])
    else:
        selected["config"]["modules"][module] = execution_config(selected, sample["sample_id"], module, selected["config"]["modules"][module])
    if root is not None:
        gate_path = root / sample["sample_id"] / "checks" / (module + ".json")
        if gate_path.is_file():
            gate = _read_gate(gate_path, plan, root, sample["sample_id"])
            cfg = selected["config"]["deep"] if module == "deepsafe" else selected["config"]["modules"][module]
            cfg["scheduler_setup_identity"] = hash_object({
                "resources": gate.get("resource_fingerprints"),
                "tools": gate.get("tool_identity"), "version": gate.get("tool_version")})
            if (gate.get("resource_fingerprints", {}).get("untracked_resource_scopes")
                    or gate.get("tool_identity", {}).get("reuse_supported") is False):
                cfg["cache"] = False
    if allocated_threads is not None:
        cfg = selected["config"]["deep"] if module == "deepsafe" else selected["config"]["modules"][module]
        cfg["threads"] = min(int(cfg.get("threads", 1)), allocated_threads)
    return selected


def run_module(plan, root, sid, module, force=False, allocated_threads=None):
    sample = _sample(plan, sid)
    from .scheduler_state import mark_started
    mark_started(plan, root, sid, module)
    ready_path(root / sid / (module + ".json")).unlink(missing_ok=True)
    gate = _read_gate(root / sid / "checks" / (module + ".json"), plan, root, sid)
    if gate.get("module") != module:
        raise ValueError("Resource gate belongs to another module")
    if gate["status"] == "failed":
        result = _base_record(plan, sample, module)
        result.update(execution_status="blocked", interpretation="inconclusive", error=gate["error"],
                      reason="resource_check_failed", blocked_by=["check_configured_resources"],
                      module_decision=module_decision(plan, sample, module), finished_at=now())
        atomic_json(record_path(plan, sid, module), result)
        _attest(plan, root, sid, module, result)
        return result
    try:
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
    from .scheduler_state import mark_started
    mark_started(plan, root, sid, "deepsafe", stage=stage)
    path = root / sid / "deep-stages" / (stage + ".json")
    ready_path(path).unlink(missing_ok=True)
    ready_path(root / sid / "deepsafe.json").unlink(missing_ok=True)
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
        blocked_by = [module for module, record in records.items()
                      if record.get("execution_status") in {"failed", "blocked"}]
        if gate["status"] == "failed":
            blocked_by.append("check_configured_resources")
        try:
            if gate["status"] == "failed":
                raise ValueError(gate["error"])
            if blocked_by:
                raise ValueError("Upstream execution failed: " + ", ".join(blocked_by))
            selected_plan = _execution_plan(plan, sample, "deepsafe", allocated_threads)
            envelope = prepare_deep_execution(selected_plan, sample, force, records)
        except Exception as exc:
            result = _base_record(plan, sample, "deepsafe")
            result.update(execution_status="blocked" if blocked_by else "failed",
                          interpretation="inconclusive", error=str(exc), candidates=[])
            if blocked_by:
                result.update(blocked_by=blocked_by, reason="upstream_job_failed")
            work = root / sid / "deep-stage-error"
            work.mkdir(parents=True, exist_ok=True)
            envelope = {"result": result, "records": list(records.values()), "config": {},
                        "work": str(work), "started": time.monotonic(), "cache_allowed": False,
                        "stopped": True, "commands": []}
        state = None
    status = ("cached" if envelope.get("cached") else "not_requested" if not plan["options"]["deep"]
              else "failed" if not previous_path and envelope["result"].get("execution_status") == "failed"
              else "blocked" if envelope["stopped"] else "completed")
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
            failures = [c for c in state.get("candidates", []) if c.get("selected") and c.get("execution") == "failed"]
            if failures:
                raise RuntimeError("DeepSafe " + stage + " failed for " + "; ".join(
                    str(c.get("protein_id") or c.get("candidate_id")) + ": " + str(c.get("reason", "analysis failed"))
                    for c in failures))
        except Exception as exc:
            envelope["result"].update(execution_status="failed", interpretation="inconclusive", error=str(exc))
            envelope["stopped"] = True
            status = "failed"
        envelope["commands"].extend(runner.commands)
    if status == "failed":
        # The rest of the DAG will be blocked. Seal partial evidence now rather
        # than relying on the final stage to run after a failure.
        candidates = copy.deepcopy(state.get("candidates", [])) if state else []
        for candidate in candidates:
            if candidate.get("selected") and candidate.get("execution") != "failed":
                candidate.update(execution="not_assessed", reason="workflow_blocked_after_stage_failure")
        envelope["result"].update(candidates=candidates, failed_stage=stage, complete=False,
            metrics={"candidate_count": len(candidates), "selected_candidates": sum(bool(c.get("selected")) for c in candidates),
                     "completed_candidates": 0, "failed_candidates": sum(c.get("execution") == "failed" for c in candidates),
                     "not_assessed_candidates": sum(c.get("execution") == "not_assessed" for c in candidates),
                     "deferred_candidates": sum(c.get("execution") == "deferred" for c in candidates)})
        if state:
            envelope["result"]["artifacts"] = list(dict.fromkeys(envelope["result"].get("artifacts", []) + state.get("artifacts", [])))
    if stage == STAGES[-1] or status == "failed" or (not previous_path and status == "blocked"):
        payload = state.get("payload") if state and not envelope["stopped"] else None
        result = finish_deep_execution(plan, sample, envelope, payload)
        _attest(plan, root, sid, "deepsafe", result)
    value = {"schema": "basis-workflow-deep-stage-v1", **_identity(plan, root, sid),
             "stage": stage, "status": status, "finished_at": now(),
             "previous_sha256": sha256_file(previous_path) if previous_path else None,
             "envelope": envelope, "state": state}
    value = _write(path, value)
    if status not in {"failed", "blocked"}:
        _ready(path, plan, root, sid)
    return value


def consolidate_evidence(plan, root, run):
    bundle = collect_workflow_evidence(plan, run, "snakemake", root)
    value = {"schema": "basis-workflow-consolidation-v1", **_identity(plan, root), "bundle": bundle}
    return _write(_report_root(root) / "consolidated.json", value)


def render_report(plan, root):
    snapshot = _read_gate(_report_root(root) / "consolidated.json", plan, root, None)
    artifacts = render_workflow_report(snapshot["bundle"], plan["outdir"])
    value = {"schema": "basis-workflow-report-v1", **_identity(plan, root), "artifacts": artifacts,
             "artifact_checksums": {path: sha256_file(path) for path in artifacts.values()
                                    if isinstance(path, str) and Path(path).is_file()}}
    return _write(_report_root(root) / "report-artifacts.json", value)


def capture_provenance(plan, root):
    snapshot = _read_gate(_report_root(root) / "consolidated.json", plan, root, None)
    report = _read_gate(_report_root(root) / "report-artifacts.json", plan, root, None)
    for path, digest in report["artifact_checksums"].items():
        if sha256_file(path) != digest:
            raise ValueError("Report artifact changed before provenance capture: " + path)
    result, code = capture_workflow_provenance(plan, snapshot["bundle"], report["artifacts"])
    return _write(_report_root(root) / "outcome.json", {"schema": "basis-workflow-outcome-v1",
                  **_identity(plan, root), "result": result, "exit_code": code})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "resources", "setup", "module", "deep-stage", "consolidate", "report", "provenance"))
    parser.add_argument("--plan", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--sample")
    parser.add_argument("--module", choices=ALL_MODULES + ["deepsafe"])
    parser.add_argument("--stage")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--threads", type=int)
    args = parser.parse_args(argv)
    args.force = args.force or os.environ.get("BASIS_FORCE") == "1"
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
        result = validate_inputs(plan, root, args.sample)
    elif args.action == "resources":
        result = check_resources(plan, root, args.sample, args.module)
    elif args.action == "setup":
        result = setup_ready(plan, root)
    elif args.action == "module":
        result = run_module(plan, root, args.sample, args.module, args.force, args.threads)
    elif args.action == "deep-stage":
        result = run_deep_stage(plan, root, args.sample, args.stage, args.force, args.threads)
    elif args.action == "consolidate":
        result = consolidate_evidence(plan, root, run)
    elif args.action == "report":
        result = render_report(plan, root)
    else:
        result = capture_provenance(plan, root)
    if result.get("execution_status", result.get("status")) in {"failed", "blocked"}:
        error = result.get("error") or result.get("envelope", {}).get("result", {}).get("error") or result.get("reason", "execution blocked")
        print(f"BaSIS worker {args.action} failed: {error}", file=sys.stderr, flush=True)
        print(f"Durable diagnostics: {root}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
