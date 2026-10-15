"""Snakemake workers for durable, independently retryable DeepSafe candidates."""
from __future__ import annotations

import argparse
import copy
import os
import sys
import uuid
from pathlib import Path

from . import deep_stages as stages
from . import workflow_runtime as runtime
from .deep_candidate_jobs import (apply_fragment, candidate_ids, finalize_state,
                                 merge_fragments, placement_budget_plan,
                                 run_candidate_stage, tree_budget_plan)
from .execution import CommandRunner
from .util import now, sha256_file

PHASES = ("domains", "phylogeny", "patterns", "profiles", "motifs", "placement")


def stage_path(root, sid, stage):
    return Path(root) / sid / "deep-stages" / (stage + ".json")


def candidate_path(root, sid, candidate_id, phase):
    if phase not in PHASES or not candidate_id.startswith("cand_") or not all(c.isalnum() or c == "_" for c in candidate_id):
        raise ValueError("Invalid DeepSafe candidate job identifier")
    return Path(root) / sid / "deep-candidates" / candidate_id / (phase + ".json")


def _gate(path, plan, root, sid):
    value = runtime._read_gate(path, plan, root, sid)
    ready = runtime._read_gate(runtime.ready_path(path), plan, root, sid)
    if ready.get("receipt_sha256") != sha256_file(path):
        raise ValueError("DeepSafe upstream receipt changed: " + str(path))
    for name, digest in value.get("dependency_checksums", {}).items():
        if not Path(name).is_file() or sha256_file(name) != digest:
            raise ValueError("DeepSafe upstream dependency changed: " + name)
    for name, digest in value.get("artifact_checksums", {}).items():
        if not Path(name).is_file() or sha256_file(name) != digest:
            raise ValueError("DeepSafe upstream artifact changed: " + name)
    return value


def _checksums(paths):
    return {str(path.resolve()): sha256_file(path) for path in paths if path.is_file()}


def _owned_files(directory):
    if not directory or not Path(directory).exists():
        return []
    return [path for path in Path(directory).rglob("*") if path.is_file()
            and "interrupted-candidate-artifacts" not in path.parts and path.name != "commands.log"]


def _phase_files(work, candidate_id, phase):
    """Fingerprint only outputs owned by this rule, never stale descendants."""
    directory = Path(work) / candidate_id
    if phase == "domains":
        paths = [directory / name for name in ("domains.domtblout", "domains.hmmscan.txt")]
    elif phase == "phylogeny":
        paths = [directory / name for name in ("query_plus_references.faa", "phylogeny_labels.json", "alignment.faa")]
        paths.extend(directory.glob("phylogeny.*"))
        paths.extend(_owned_files(directory / "bounded_trees"))
    elif phase in {"patterns", "profiles"}:
        paths = _owned_files(directory / "candidate-scans" / ("prosite_patterns" if phase == "patterns" else "prosite_profiles"))
    elif phase == "placement":
        paths = _owned_files(directory / "family-placement")
    else:
        paths = []
    return paths


def _commit(path, value, dependencies, plan, root, sid, artifacts=()):
    value.update(schema="basis-workflow-candidate-job-v1", **runtime._identity(plan, root, sid),
                 producer_launch_id=runtime._report_root(root).name,
                 finished_at=now(), dependency_checksums=_checksums([runtime.ready_path(p) for p in dependencies]),
                 artifact_checksums=_checksums([Path(p) for p in artifacts]))
    value = runtime._write(path, value)
    if value.get("status") not in {"failed", "blocked"}:
        runtime._ready(path, plan, root, sid)
    return value


def _single(state, candidate_id):
    result = copy.deepcopy(state)
    result["candidates"] = [row for row in result["candidates"] if row["candidate_id"] == candidate_id]
    if len(result["candidates"]) != 1 or not result["candidates"][0].get("selected"):
        raise ValueError("Unknown selected DeepSafe candidate")
    if result.get("candidate_job_id") != candidate_id:
        result["artifacts"] = []
        result["notes"] = []
    result["candidate_job_id"] = candidate_id
    return result


def _archive_interrupted(work, candidate_id, phase):
    directory = Path(work) / candidate_id
    if phase == "domains":
        paths = [directory / name for name in ("domains.domtblout", "domains.hmmscan.txt")]
    elif phase == "phylogeny":
        paths = [directory / name for name in ("bounded_trees", "query_plus_references.faa", "phylogeny_labels.json", "alignment.faa")]
        paths.extend(directory.glob("phylogeny.*"))
    elif phase in {"patterns", "profiles"}:
        paths = [directory / "candidate-scans" / ("prosite_patterns" if phase == "patterns" else "prosite_profiles")]
    elif phase == "placement":
        paths = [directory / "family-placement"]
    else:
        paths = []
    paths = [path for path in paths if path.exists()]
    if not paths:
        return None
    archive = Path(work) / "interrupted-candidate-artifacts" / candidate_id / (phase + "-" + uuid.uuid4().hex)
    archive.mkdir(parents=True, exist_ok=False)
    for path in paths:
        path.rename(archive / path.name)
    return str(archive)


def prepare(plan, root, sid, force=False, threads=None):
    value = runtime.run_deep_stage(plan, root, sid, "prepare", force, threads)
    state = value.get("state")
    value["selected_candidate_ids"] = candidate_ids(state) if state else []
    paths = []
    if state:
        for candidate in state["candidates"]:
            for field in (candidate.get("query", {}).get("path"), candidate.get("analyses", {}).get("context", {}).get("sequence_path")):
                if field:
                    paths.append(Path(field))
    value["artifact_checksums"] = _checksums(paths)
    path = stage_path(root, sid, "prepare")
    value = runtime._write(path, value)
    if value.get("status") not in {"failed", "blocked"}:
        runtime._ready(path, plan, root, sid)
    return value


def run_candidate(plan, root, sid, candidate_id, phase, threads=None):
    path = candidate_path(root, sid, candidate_id, phase)
    runtime.ready_path(path).unlink(missing_ok=True)
    prepare_path = stage_path(root, sid, "prepare")
    prepared = _gate(prepare_path, plan, root, sid)
    envelope = prepared["envelope"]
    if envelope["stopped"]:
        raise ValueError("Candidate work cannot run for a stopped DeepSafe envelope")
    cfg, work = envelope["config"], Path(envelope["work"])
    if threads is not None and int(cfg.get("threads", 1)) > threads:
        raise ValueError("DeepSafe candidate allocation changed; restart with the original core allocation")
    offset, panel = 0, None
    if phase == "domains":
        parent_path, parent = prepare_path, prepared
    elif phase == "phylogeny":
        parent_path = stage_path(root, sid, "tree-plan")
        parent = _gate(parent_path, plan, root, sid)
        offset = parent["budget"]["offsets"][candidate_id]
    elif phase == "placement":
        parent_path = stage_path(root, sid, "placement-plan")
        parent = _gate(parent_path, plan, root, sid)
        offset = parent["budget"]["offsets"][candidate_id]
        panel = parent["budget"]["panels"].get(candidate_id)
    else:
        parent_path = candidate_path(root, sid, candidate_id, PHASES[PHASES.index(phase) - 1])
        parent = _gate(parent_path, plan, root, sid)
    state = _single(parent["state"], candidate_id)
    archive = _archive_interrupted(work, candidate_id, phase)
    logfile = work / candidate_id / "job-logs" / (phase + "-" + uuid.uuid4().hex + ".log")
    runner = CommandRunner(cfg, logfile)
    value = {"candidate_id": candidate_id, "phase": phase, "status": "completed", "state": state}
    if archive:
        value["preserved_interrupted_output"] = archive
    generated_artifacts = []
    try:
        fragment = run_candidate_stage(phase, runtime._sample(plan, sid), envelope["records"], cfg,
                                       work, runner, state, candidate_id,
                                       budget_start=offset, prepared_panel=panel)
        generated_artifacts = [Path(p) for p in fragment.get("artifacts", [])]
        value["state"] = apply_fragment(state, fragment, phase)
        candidate = fragment["candidate"]
        if candidate.get("execution") == "failed":
            raise RuntimeError(candidate.get("reason", "Candidate analysis failed"))
    except Exception as exc:
        value.update(status="failed", error=str(exc))
        value["state"]["candidates"][0].update(execution="failed", reason=str(exc))
    value.update(commands=runner.commands, command_log=str(logfile))
    return _commit(path, value, [prepare_path, parent_path], plan, root, sid,
                   _phase_files(work, candidate_id, phase) + generated_artifacts + [logfile])


def _combine(plan, root, sid, state, phase):
    result = copy.deepcopy(state)
    dependencies, commands = [], []
    for candidate_id in candidate_ids(state):
        path = candidate_path(root, sid, candidate_id, phase)
        document = _gate(path, plan, root, sid)
        row = document["state"]
        if row.get("identity") != state["identity"] or len(row.get("candidates", [])) != 1:
            raise ValueError("Candidate aggregation identity mismatch")
        candidate = row["candidates"][0]
        if candidate["candidate_id"] != candidate_id or candidate.get("execution") == "failed":
            raise ValueError("Candidate aggregation requires successful matching candidate jobs")
        result["candidates"] = [copy.deepcopy(candidate) if c["candidate_id"] == candidate_id else c for c in result["candidates"]]
        result["artifacts"].extend(row.get("artifacts", []))
        result["notes"].extend(row.get("notes", []))
        dependencies.append(path)
        commands.extend(document.get("commands", []))
    result["artifacts"] = list(dict.fromkeys(result["artifacts"]))
    return result, dependencies, commands


def run_shared(plan, root, sid, action, threads=None):
    parent_stage = {"references": "prepare", "tree-plan": "references", "placement-plan": "tree-plan", "finalize": "placement-plan"}[action]
    parent_path = stage_path(root, sid, parent_stage)
    parent = _gate(parent_path, plan, root, sid)
    # Envelope is kept in shared checkpoints only; candidate receipts carry only
    # one candidate and immutable links back to shared inputs.
    envelope = copy.deepcopy(parent["envelope"])
    state = copy.deepcopy(parent.get("state"))
    path = stage_path(root, sid, action)
    runtime.ready_path(path).unlink(missing_ok=True)
    dependencies = [parent_path]
    value = {"stage": action, "status": parent.get("status", "completed"), "envelope": envelope, "state": state}
    runner = None
    if not envelope["stopped"]:
        cfg, work = envelope["config"], Path(envelope["work"])
        if threads is not None and int(cfg.get("threads", 1)) > threads:
            raise ValueError("DeepSafe allocation changed between stages")
        logfile = work / "shared-job-logs" / (action + "-" + uuid.uuid4().hex + ".log")
        runner = CommandRunner(cfg, logfile)
        try:
            if action == "references":
                state, deps, commands = _combine(plan, root, sid, state, "domains")
                dependencies.extend(deps)
                envelope["commands"].extend(commands)
                state.update(completed_stages=["prepare", "domains"], stage="domains")
                state = stages.run_stage("references", runtime._sample(plan, sid), envelope["records"], cfg, work, runner, state)
                failures = [c for c in state["candidates"] if c.get("selected") and c.get("execution") == "failed"]
                if failures:
                    raise RuntimeError("Reference preparation failed: " + "; ".join(str(c.get("reason")) for c in failures))
            elif action == "tree-plan":
                value["budget"] = tree_budget_plan(state, cfg)
            elif action == "placement-plan":
                state, deps, _ = _combine(plan, root, sid, state, "motifs")
                dependencies.extend(deps)
                value["budget"] = placement_budget_plan(state, cfg, work)
                state["tree_budget"] = {"used": parent["budget"]["used"]}
            elif action == "finalize":
                state, deps, _ = _combine(plan, root, sid, state, "placement")
                dependencies.extend(deps)
                for cid in candidate_ids(state):
                    for phase in PHASES[1:]:
                        document = _gate(candidate_path(root, sid, cid, phase), plan, root, sid)
                        envelope["commands"].extend(document.get("commands", []))
                state = finalize_state(runtime._sample(plan, sid), envelope["records"], cfg, work, runner, state)
            value.update(state=state, status="completed")
        except Exception as exc:
            value.update(status="failed", error=str(exc), state=state)
        envelope["commands"].extend(runner.commands)
    result = None
    if action == "finalize" and value["status"] not in {"failed", "blocked"}:
        payload = state.get("payload") if state and not envelope["stopped"] else None
        result = runtime.finish_deep_execution(plan, runtime._sample(plan, sid), envelope, payload)
    artifacts = []
    if state:
        artifacts = [Path(p) for p in state.get("artifacts", [])]
    value = _commit(path, value, dependencies, plan, root, sid, artifacts)
    if result is not None:
        runtime._attest(plan, root, sid, "deepsafe", result)
    return value


def failure_diagnostics(plan, root, sid):
    """Read errors bound to still-current prerequisites; leave history intact."""
    paths = list((Path(root) / sid / "deep-candidates").glob("*/*.json"))
    paths.extend((Path(root) / sid / "deep-stages").glob("*.json"))
    result = []
    for path in sorted(paths):
        if path.name.endswith(".ready.json"):
            continue
        try:
            document = runtime._read_gate(path, plan, root, sid)
            dependencies = document.get("dependency_checksums")
            # Failed workers publish no ready marker. Their own prerequisite
            # snapshot therefore establishes whether an error is still relevant.
            # Older, unbound prepare failures cannot safely be promoted to the
            # current launch's failure report.
            if not isinstance(dependencies, dict) or not dependencies:
                continue
            if any(not Path(name).is_file() or sha256_file(name) != digest
                   for name, digest in dependencies.items()):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if document.get("status") != "failed":
            continue
        candidate = next(iter((document.get("state") or {}).get("candidates", [])), {}) if document.get("candidate_id") else {}
        error = document.get("error") or document.get("envelope", {}).get("result", {}).get("error")
        result.append({"candidate_id": document.get("candidate_id"),
                       "protein_id": candidate.get("protein_id"),
                       "stage": document.get("phase") or document.get("stage"),
                       "error": error or "DeepSafe worker failed", "receipt": str(path),
                       "command_log": document.get("command_log"),
                       "producer_launch_id": document.get("producer_launch_id"),
                       "diagnostic_scope": "validated_dependency_snapshot"})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "candidate", "references", "tree-plan", "placement-plan", "finalize"))
    parser.add_argument("--plan", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--candidate")
    parser.add_argument("--phase", choices=PHASES)
    parser.add_argument("--threads", type=int)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    args.force = args.force or os.environ.get("BASIS_FORCE") == "1"
    plan, root, _ = runtime._load(args.plan, args.run_dir)
    if args.action == "prepare":
        result = prepare(plan, root, args.sample, args.force, args.threads)
    elif args.action == "candidate":
        if not args.candidate or not args.phase:
            parser.error("candidate workers require --candidate and --phase")
        result = run_candidate(plan, root, args.sample, args.candidate, args.phase, args.threads)
    else:
        result = run_shared(plan, root, args.sample, args.action, args.threads)
    if result.get("status") in {"failed", "blocked"}:
        print("DeepSafe job failed: " + str(result.get("error") or result.get("envelope", {}).get("result", {}).get("error", "execution blocked")), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
