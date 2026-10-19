"""One scientific DeepSafe task per Snakemake invocation.

The retained RC analysis functions supply selection and interpretation. This
module supplies serialization only: it never creates environments, schedules
jobs, retries commands, or decides whether a completed job needs rerunning.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from datetime import datetime, timezone

from basis import deep_stages as stages
from basis.deep_candidate_jobs import (
    apply_fragment, candidate_ids, finalize_state, placement_budget_plan,
    run_candidate_stage, tree_budget_plan,
)

SCHEMA = "basis-snakemake-deep-state-v1"
PHASES = ("domains", "phylogeny", "patterns", "profiles", "motifs", "placement")


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str,
                                    allow_nan=False) + "\n")
    os.replace(temporary, path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
                                     allow_nan=False).encode()).hexdigest()


def load_document(path):
    document = read(path)
    if document.get("schema") != SCHEMA:
        raise ValueError("Unrecognized DeepSafe state: " + str(path))
    if document.get("context_sha256") != digest(document["context"]):
        raise ValueError("DeepSafe context changed: " + str(path))
    context = document["context"]
    expected = stages._identity(context["sample"], context["records"],
                                context["config"], context["work"])
    if document["state"].get("identity") != expected:
        raise ValueError("DeepSafe state does not match its saved inputs")
    return document


def combine(parent, documents):
    """Merge exactly one successful candidate task per selected candidate."""
    state = copy.deepcopy(parent["state"])
    expected = candidate_ids(state)
    documents = list(documents)
    received = [document.get("candidate_id") for document in documents]
    if len(received) != len(set(received)) or set(received) != set(expected):
        raise ValueError("Expected one state for every selected candidate")
    replacements = {}
    for document in documents:
        if (document["context_sha256"] != parent["context_sha256"] or
                document["state"]["identity"] != state["identity"]):
            raise ValueError("Candidate aggregation input identity mismatch")
        rows = document["state"]["candidates"]
        if len(rows) != 1 or rows[0]["candidate_id"] != document["candidate_id"]:
            raise ValueError("Candidate state changed its identifier")
        reject_failures(document["state"])
        replacements[document["candidate_id"]] = rows[0]
        state["artifacts"].extend(document["state"].get("artifacts", []))
        state["notes"].extend(document["state"].get("notes", []))
    state["candidates"] = [copy.deepcopy(replacements.get(row["candidate_id"], row))
                           for row in state["candidates"]]
    state["artifacts"] = list(dict.fromkeys(state["artifacts"]))
    return state


def reject_failures(state):
    failures = [row for row in state["candidates"]
                if row.get("selected") and row.get("execution") == "failed"]
    if failures:
        raise RuntimeError("; ".join(str(row.get("protein_id") or row["candidate_id"])
                                     + ": " + str(row.get("reason")) for row in failures))


def prepare(isolate, records, native, threads, runner):
    isolate = Path(isolate).resolve()
    sample = read(isolate / "safety/metadata/sample.json")
    full_config = read(isolate / "safety/metadata/config.json")
    config = copy.deepcopy(full_config["deep"])
    config["command_prefix"] = []
    # All mutable placement files belong to a Snakemake output directory. Never
    # inherit a cache directory from an earlier installed RC or another isolate.
    config.pop("placement_cache_dir", None)
    records = [read(path) for path in records]
    for record in records:
        if record.get("sample_id") != sample["sample_id"]:
            raise ValueError("DeepSafe upstream evidence belongs to another sample")
        if record.get("execution_status") in {"failed", "blocked", "not_started"}:
            raise RuntimeError("DeepSafe upstream did not complete: " + str(record.get("module")))
    work = str((isolate / "safety/deepsafe").resolve())
    context = {"sample": sample, "records": records, "config": config, "work": work}
    effective = execution_config(config, native, threads)
    state = stages.run_stage("prepare", sample, records, effective, native, runner)
    state["identity"] = stages._identity(sample, records, config, work)
    reject_failures(state)
    return {"schema": SCHEMA, "context": context, "context_sha256": digest(context),
            "state": state, "selected_candidate_ids": candidate_ids(state),
            "stage": "prepare"}


def execution_config(config, native, threads):
    result = copy.deepcopy(config)
    result.update(threads=int(threads), command_prefix=[],
                  placement_cache_dir=str(Path(native) / "placement-work"))
    return result


def invoke(document, action, native, threads, runner, candidate=None, children=()):
    """Execute one phase; preserve the original scientific input identity."""
    document = copy.deepcopy(document)
    context = document["context"]
    sample, records = context["sample"], context["records"]
    config = execution_config(context["config"], native, threads)
    original_identity = copy.deepcopy(document["state"]["identity"])
    state = document["state"]
    if action in {"references", "placement-plan", "finalize"}:
        children = list(children)
        state = combine(document, children)
        commands = document.get("commands", []) + [command for child in children
                                                    for command in child.get("commands", [])]
        document["commands"] = list({digest(command): command for command in commands}.values())
    for row in state["candidates"]:
        if row.get("selected"):
            (Path(native) / row["candidate_id"]).mkdir(parents=True, exist_ok=True)
    state["identity"] = stages._identity(sample, records, config, native)

    if action in PHASES:
        if not re.fullmatch(r"cand_[a-f0-9]{20}", candidate or ""):
            raise ValueError("Invalid candidate identifier")
        rows = [row for row in state["candidates"] if row["candidate_id"] == candidate]
        if len(rows) != 1 or not rows[0].get("selected"):
            raise ValueError("Candidate is not selected")
        state["candidates"] = rows
        # Retain earlier candidate artifacts, but not the other candidates' files.
        if document.get("candidate_id") != candidate:
            state["artifacts"] = []
            state["notes"] = []
        budget = document.get("budget", {})
        fragment = run_candidate_stage(
            action, sample, records, config, native, runner, state, candidate,
            budget_start=budget.get("offsets", {}).get(candidate, 0),
            prepared_panel=budget.get("panels", {}).get(candidate))
        state = apply_fragment(state, fragment, action)
        document["candidate_id"] = candidate
    elif action == "references":
        state.update(stage="domains", completed_stages=["prepare", "domains"])
        state = stages.run_stage("references", sample, records, config, native, runner, state)
    elif action == "tree-plan":
        document["budget"] = tree_budget_plan(state, config)
    elif action == "placement-plan":
        document["budget"] = placement_budget_plan(state, config, native)
    elif action == "finalize":
        state = finalize_state(sample, records, config, native, runner, state)
    else:
        raise ValueError("Unknown DeepSafe action: " + action)
    state["identity"] = original_identity
    reject_failures(state)
    document.update(state=state, stage=action)
    return document


def evidence_record(document):
    context, state = document["context"], document["state"]
    payload = copy.deepcopy(state["payload"])
    upstream = [record["module"] for record in context["records"]
                if record.get("execution_status") != "completed"
                and record.get("execution_status") != "disabled"]
    payload.update(sample_id=context["sample"]["sample_id"], module="deepsafe",
                   execution_status="completed", mode="native", reused=False,
                   upstream_incomplete=upstream, effective_config=context["config"],
                   record_id="deep_" + document["context_sha256"][:20],
                   commands=document.get("commands", []),
                   finished_at=datetime.now(timezone.utc).isoformat())
    if upstream:
        payload["complete"] = False
    payload["completion_summary"]["upstream_incomplete"] = upstream
    return payload


def initialize_output(native, isolate):
    """Only reset this job's declared native directory when Snakemake runs it."""
    native = Path(native).absolute()
    base = (Path(isolate).resolve() / "safety/deepsafe/native").resolve()
    if native.is_symlink() or not native.resolve().is_relative_to(base) or native.resolve() == base:
        raise ValueError("DeepSafe job output is outside its owned native directory")
    if native.exists():
        shutil.rmtree(native)
    native.mkdir(parents=True)
    return native


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "references", "tree-plan", "placement-plan", "finalize") + PHASES)
    parser.add_argument("--isolate", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--native", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--parent")
    parser.add_argument("--candidate")
    parser.add_argument("--records", nargs="*", default=[])
    parser.add_argument("--children", nargs="*", default=[])
    parser.add_argument("--evidence")
    args = parser.parse_args(argv)
    from .runtime import DirectRunner
    if args.threads < 1:
        raise ValueError("DeepSafe threads must be positive")
    native = initialize_output(args.native, args.isolate)
    os.environ.pop("BASIS_VERBOSE", None)
    runner = DirectRunner({"threads": args.threads}, Path(args.log))
    if args.action == "prepare":
        document = prepare(args.isolate, args.records, native, args.threads, runner)
    else:
        document = invoke(load_document(args.parent), args.action, native, args.threads,
                          runner, args.candidate, [load_document(p) for p in args.children])
    document["commands"] = list(document.get("commands", [])) + list(getattr(runner, "commands", []))
    document["native_output"] = str(native)
    document["native_artifacts"] = [str(p) for p in sorted(native.rglob("*")) if p.is_file()]
    if args.evidence:
        write(args.evidence, evidence_record(document))
    write(args.output, document)


if __name__ == "__main__":
    main()
