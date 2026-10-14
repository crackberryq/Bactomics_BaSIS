"""Native, similarity-blocked internal benchmark of an embedded seed panel.

The full seed catalogue is used only to define exclusions. Each inference sees
only the remaining references and an unlabelled query. Registry seed labels are
evaluation labels, not independent experimental test truth.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import time

from .execution import CommandRunner
from .placement import DEFAULT_REGISTRY
from .placement_benchmark_protocol import (
    aggregate, blast_call, build_protocol, normalize_policy, placement_call,
    validate_pairwise_rows,
)
from .placement_native import _IDENTITY_SCRIPT, run_placement
from .placement_panels import load_registry, prepare_panel
from .progress import emit, stage
from .references import write_fasta
from .report_placement_benchmark import write_benchmark_report
from .util import atomic_json, hash_object, now, sha256_file


BLAST_FIELDS = (
    "query_id", "reference_id", "identity", "alignment_length", "query_length",
    "reference_length", "query_start", "query_end", "reference_start",
    "reference_end", "evalue", "bitscore",
)
BLAST_SEARCH = {
    "task": "blastp", "word_size": 2, "seg": "no", "comp_based_stats": 0,
    "evalue": 1000, "max_hsps": 1,
    "target_budget": "all_subject_sequences",
    "outfmt": "6 qseqid sseqid pident length qlen slen qstart qend sstart send evalue bitscore",
    "coverage": "single_HSP_ungapped_coordinate_spans_both_sequences",
}


def parse_blast(path, members, query_ids, reference_ids, require_self_hits=False):
    """Fail closed on truncated schema, duplicate pairs, and mismatched lengths."""
    rows, pairs = [], set()
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != len(BLAST_FIELDS):
            raise ValueError(f"BLAST row {number}: expected twelve columns")
        row = dict(zip(BLAST_FIELDS, fields))
        pair = (row["query_id"], row["reference_id"])
        if pair in pairs or pair[0] not in query_ids or pair[1] not in reference_ids:
            raise ValueError(f"BLAST row {number}: duplicate or out-of-scope pair")
        pairs.add(pair)
        for key in BLAST_FIELDS[2:]:
            row[key] = float(row[key]) if key in {"identity", "evalue", "bitscore"} else int(row[key])
        rows.append(row)
    rows = validate_pairwise_rows(rows, members)
    if require_self_hits:
        if set(query_ids) != set(reference_ids):
            raise ValueError("Self-hit audit requires the same query and reference IDs")
        self_hits = {r["query_id"] for r in rows if r["query_id"] == r["reference_id"]
                     and r["identity"] == 100 and r["query_coverage"] == 100
                     and r["reference_coverage"] == 100}
        if self_hits != set(query_ids):
            raise ValueError("Incomplete all-versus-all search: full-length self hits missing")
    return rows


def blast_search(query_path, subject_path, target_count, output_path, runner, executable="blastp"):
    if target_count < 1:
        raise ValueError("BLAST requires at least one training reference")
    args = [executable, "-query", str(query_path), "-subject", str(subject_path),
            "-task", BLAST_SEARCH["task"], "-word_size", str(BLAST_SEARCH["word_size"]),
            "-seg", BLAST_SEARCH["seg"], "-comp_based_stats", str(BLAST_SEARCH["comp_based_stats"]),
            "-evalue", str(BLAST_SEARCH["evalue"]), "-max_hsps", "1",
            "-max_target_seqs", str(target_count), "-outfmt", BLAST_SEARCH["outfmt"]]
    # Subject mode is intentionally bounded and single threaded for these tiny
    # panels. Every subject can be reported; no top-k retrieval truncation.
    runner.run(args, stdout_path=output_path)


def training_panel(panel, fold, policy):
    """Construct a training-only payload; retain no held-out evidence records."""
    ids, excluded = set(fold["reference_ids"]), set(fold["held_out_ids"])
    available = {m["tip_id"] for m in panel["members"]}
    if ids & excluded or ids | excluded != available:
        raise ValueError("Fold must partition all reference panel members")
    members = [deepcopy(m) for m in panel["members"] if m["tip_id"] in ids]
    groups = sorted({m["group"] for m in panel["members"] if m["role"] != "context"})
    counts = {g: sum(m["group"] == g for m in members) for g in groups}
    eligible = len(members) >= policy["min_training_references"] and all(
        n >= policy["min_anchors_per_group"] for n in counts.values())
    if fold["status"] != "ready" or not eligible:
        raise ValueError("Inadequate fold cannot enter either prediction method")
    content = {"members": members, "policy": policy,
               "source_panel_sha256": panel["panel_sha256"]}
    return {"panel_id": panel["panel_id"] + "_" + fold["fold_id"],
            "panel_sha256": hash_object(content), "members": members,
            "adequacy": {"eligible": True, "reasons": [], "required_groups": groups,
                "min_anchors_per_group": policy["min_anchors_per_group"],
                "distinct_qualified_anchors_by_group": counts},
            "provenance": {"selection_policy": "whole_similarity_component_excluded",
                "source_panel_sha256": panel["panel_sha256"],
                "training_only": True, "function_probability": None}}


def _row(query, method, decision, elapsed=0):
    return {**{k: deepcopy(query[k]) for k in
               ("query_id", "cluster_id", "fold_id", "true_group", "accessions")},
            "method": method, **decision, "elapsed_seconds": round(elapsed, 6)}


def _failed(exc):
    return {"status": "failed", "predicted_group": None, "abstained": None,
            "reason": "native_or_audit_failure", "error": str(exc), "metrics": {}}


def run_seed_benchmark(outdir, config, registry_path=None,
                       panel_id="cfr_rlmn_source_audited_v1", policy=None,
                       prepare_only=False):
    """Create a fresh internal benchmark; no isolate analyses or downloads.

    Native failures are separate from biological abstentions. Both methods are
    recorded for every planned query, including held folds. Output directories
    must be new so another run cannot silently overwrite the frozen protocol.
    """
    config = deepcopy(config)
    config["gpus"] = 0
    config.setdefault("threads", 4)
    config.setdefault("timeout_seconds", 3600)
    config.setdefault("placement_model", "LG+G4")
    config.setdefault("placement_min_columns", 50)
    config.setdefault("placement_min_query_coverage", 0.7)
    policy = normalize_policy(policy)
    outdir = Path(outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=False)
    config["placement_cache_dir"] = str(outdir / "reference-tree-cache")
    started = now()
    registry_path = Path(registry_path or DEFAULT_REGISTRY).resolve()
    registry_sha = sha256_file(registry_path)
    registry = load_registry(registry_path)
    panel = prepare_panel({}, registry, outdir / "panel-cache", allow_download=False, panel_id=panel_id)
    if not panel.get("adequacy", {}).get("eligible"):
        raise ValueError("Seed panel is inadequate: " + str(panel.get("adequacy")))
    atomic_json(outdir / "seed-panel.json", panel)
    members = {m["tip_id"]: m for m in panel["members"]}
    panel_fasta = outdir / "seed-panel.faa"
    write_fasta({k: m["sequence"] for k, m in members.items()}, panel_fasta)
    frozen = {"schema": "basis-placement-seed-benchmark-preflight-v1", "created_at": started,
              "decision_policy": policy, "search_policy": BLAST_SEARCH, "native_config": config,
              "registry_path": str(registry_path), "registry_sha256": registry_sha,
              "panel_sha256": panel["panel_sha256"],
              "source_sha256": {name: sha256_file(Path(__file__).with_name(name)) for name in
                  ("placement_benchmark_native.py", "placement_benchmark_protocol.py", "placement_native.py",
                   "placement_panels.py", "placement_scoring.py")},
              "note": "Frozen before clustering or prediction; research thresholds, no optimization."}
    atomic_json(outdir / "preflight.json", frozen)
    frozen_hash = sha256_file(outdir / "preflight.json")
    runner = CommandRunner(config, outdir / "preprocessing.commands.log")
    start = time.monotonic()
    blast_exe = str(config.get("executables", {}).get("blastp", "blastp"))
    identity = json.loads(runner.run(["python", "-c", _IDENTITY_SCRIPT, blast_exe, "-version"]).stdout)
    with stage("Benchmark: label-blind sequence clustering"):
        blast_search(panel_fasta, panel_fasta, len(members), outdir / "all-versus-all.tsv", runner, blast_exe)
        pairwise = parse_blast(outdir / "all-versus-all.tsv", members, set(members), set(members), True)
        protocol = build_protocol(panel, pairwise, policy)
    protocol["native_search_audit"] = {"blast_identity": identity, "search_policy": BLAST_SEARCH,
        "all_versus_all_sha256": sha256_file(outdir / "all-versus-all.tsv"),
        "self_hits_verified": len(members), "elapsed_seconds": round(time.monotonic() - start, 6),
        "commands": runner.commands, "preflight_sha256": frozen_hash}
    protocol["native_settings"] = {k: config[k] for k in
        ("placement_model", "placement_min_columns", "placement_min_query_coverage", "threads")}
    atomic_json(outdir / "frozen-protocol.json", protocol)
    protocol_hash = sha256_file(outdir / "frozen-protocol.json")
    bundle = {"schema": "basis-placement-seed-benchmark-run-v1", "status": "prepared",
              "started_at": started, "finished_at": None, "protocol": protocol,
              "frozen_protocol_file_sha256": protocol_hash, "results": [], "evaluation": {},
              "placement_candidates": [], "function_probability": None,
              "comparators_not_assessed": [{"method": "installed_AMRFinder_or_CARD",
                  "reason": "Existing catalogues may contain held-out seeds; not a leakage-controlled baseline."}],
              "runtime_scope": "Per-query wall time includes native startup and validation. First placement per fold includes reference-tree construction; later queries reuse that fold's verified tree. Shared preprocessing time is reported separately."}
    atomic_json(outdir / "checkpoint.json", bundle)
    emit(f"Benchmark: {len(protocol['queries'])} labelled queries / {len(protocol['folds'])} folds; "
         f"{sum(f['status'] == 'ready' for f in protocol['folds'])} eligible folds")
    if prepare_only:
        bundle["finished_at"] = now()
        return bundle, write_benchmark_report(bundle, outdir)
    queries = {q["query_id"]: q for q in protocol["queries"]}
    for fold in protocol["folds"]:
        if fold["status"] != "ready":
            for qid in fold["query_ids"]:
                for method in ("blastp", "placement"):
                    bundle["results"].append(_row(queries[qid], method, {
                        "status": "held", "predicted_group": None, "abstained": True,
                        "reason": ";".join(fold["reasons"]), "metrics": {}}))
            atomic_json(outdir / "checkpoint.json", bundle)
            continue
        train = training_panel(panel, fold, policy)
        directory = outdir / "folds" / fold["fold_id"]
        directory.mkdir(parents=True)
        atomic_json(directory / "training-panel.json", train)
        train_fasta = directory / "references.faa"
        write_fasta({m["tip_id"]: m["sequence"] for m in train["members"]}, train_fasta)
        for qid in fold["query_ids"]:
            query = queries[qid]
            work = directory / qid
            work.mkdir()
            query_path = work / "query.faa"
            write_fasta({qid: members[qid]["sequence"]}, query_path)
            emit("Benchmark query: " + "/".join(query["accessions"]))
            cmd = CommandRunner(config, work / "commands.log")
            tick = time.monotonic()
            try:
                blast_search(query_path, train_fasta, len(train["members"]), work / "blast.tsv", cmd, blast_exe)
                rows = parse_blast(work / "blast.tsv", members, {qid}, set(fold["reference_ids"]))
                decision = blast_call(qid, train["members"], rows, policy)
                decision["metrics"]["raw_alignment_count"] = len(rows)
            except (OSError, ValueError, RuntimeError, KeyError) as exc:
                decision = _failed(exc)
            bundle["results"].append(_row(query, "blastp", decision, time.monotonic() - tick))
            # Query label and accession are withheld from the native inference.
            candidate = {"candidate_id": "benchmark_" + qid, "protein_id": qid,
                         "selected": True, "execution": "completed",
                         "query": {"id": qid, "path": str(query_path), "sequence_sha256": query["sequence_sha256"]},
                         "analyses": {"integrity": {"status": "completed", "flags": []}}}
            tick = time.monotonic()
            try:
                native = run_placement(candidate, train, config, work, cmd)
                if native["status"] == "completed":
                    decision = placement_call(native["scores"], policy)
                elif native["status"] in {"insufficient_evidence", "not_assessed"}:
                    decision = {"status": "held", "predicted_group": None, "abstained": True,
                                "reason": native.get("reason"), "metrics": {}}
                else:
                    decision = _failed(native.get("error") or native.get("reason"))
                native["artifact_checksums"] = {str(Path(p).resolve()): sha256_file(p)
                                               for p in native.get("artifacts", []) if Path(p).is_file()}
                native["panel"] = {k: v for k, v in train.items() if k != "members"}
                native["panel"]["members"] = [{k: v for k, v in m.items() if k != "sequence"} for m in train["members"]]
                candidate["analyses"]["family_placement"] = native
                decision["metrics"]["reference_cache_reused"] = native.get("reference_cache", {}).get("reused")
                bundle["placement_candidates"].append({"query_id": qid, "candidate": candidate,
                    "deep_record": {"execution_status": "completed", "sample_id": "benchmark",
                                    "placement_review": {"artifact_checksums": native["artifact_checksums"]}}})
                atomic_json(work / "placement-result.json", native)
            except (OSError, ValueError, RuntimeError, KeyError) as exc:
                decision = _failed(exc)
            bundle["results"].append(_row(query, "placement", decision, time.monotonic() - tick))
            atomic_json(work / "command-audit.json", cmd.commands)
            atomic_json(outdir / "checkpoint.json", bundle)
            emit(f"Benchmark placement: {decision['status']} | {decision['reason']}")
    if (sha256_file(outdir / "preflight.json") != frozen_hash
            or sha256_file(outdir / "frozen-protocol.json") != protocol_hash
            or sha256_file(registry_path) != registry_sha):
        raise ValueError("Frozen benchmark inputs changed during execution")
    for name, checksum in frozen["source_sha256"].items():
        if sha256_file(Path(__file__).with_name(name)) != checksum:
            raise ValueError("Benchmark source changed during execution: " + name)
    bundle["evaluation"] = aggregate(bundle["results"])
    bundle["status"] = "incomplete" if any(r["status"] == "failed" for r in bundle["results"]) else "completed"
    bundle["finished_at"] = now()
    # Persist final scientific results before optional HTML/tree rendering.
    atomic_json(outdir / "checkpoint.json", bundle)
    artifacts = write_benchmark_report(bundle, outdir)
    return bundle, artifacts
