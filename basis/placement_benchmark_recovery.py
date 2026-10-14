"""Verify and render a saved benchmark without running any native analyses."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from .placement_benchmark_native import parse_blast, training_panel
from .placement_benchmark_protocol import aggregate, blast_call, build_protocol, placement_call
from .progress import emit, stage
from .references import read_fasta
from .report_placement_benchmark import write_benchmark_report
from .report_placement_tree import _validated
from .util import atomic_json, now, sha256_file


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _inside(path, root):
    path = Path(path).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("Saved native artifact lies outside the selected benchmark: " + str(path))
    return path


def validate_saved_benchmark(run_dir):
    """Return a verified copy and audit, refusing missing prediction rows."""
    root = Path(run_dir).resolve(strict=True)
    source = root / "benchmark.json"
    if not source.is_file():
        source = root / "checkpoint.json"
    bundle = _read(source)
    original_status = bundle.get("status")
    if bundle.get("schema") != "basis-placement-seed-benchmark-run-v1":
        raise ValueError("Not a supported saved benchmark bundle")
    checkpoint_path = root / "checkpoint.json"
    if source.name == "benchmark.json" and checkpoint_path.is_file():
        checkpoint = _read(checkpoint_path)
        for key in ("protocol", "results", "placement_candidates", "started_at"):
            if checkpoint.get(key) != bundle.get(key):
                raise ValueError("Final bundle differs from the saved checkpoint: " + key)
    protocol = _read(root / "frozen-protocol.json")
    if (sha256_file(root / "frozen-protocol.json") != bundle.get("frozen_protocol_file_sha256")
            or protocol != bundle.get("protocol")):
        raise ValueError("Frozen protocol checksum or content differs from saved evidence")
    search_audit = protocol["native_search_audit"]
    if sha256_file(root / "preflight.json") != search_audit["preflight_sha256"]:
        raise ValueError("Preflight checksum mismatch")
    if sha256_file(root / "all-versus-all.tsv") != search_audit["all_versus_all_sha256"]:
        raise ValueError("Clustering search checksum mismatch")
    preflight = _read(root / "preflight.json")
    if preflight["decision_policy"] != protocol["policy"]:
        raise ValueError("Decision thresholds differ from the preflight policy")
    panel = _read(root / "seed-panel.json")
    if panel["panel_sha256"] != protocol["panel_sha256"] or panel["panel_sha256"] != preflight["panel_sha256"]:
        raise ValueError("Seed-panel identity mismatch")
    members = {m["tip_id"]: m for m in panel["members"]}
    pairwise = parse_blast(root / "all-versus-all.tsv", members, set(members), set(members), True)
    rebuilt = build_protocol(panel, pairwise, protocol["policy"])
    for key, value in rebuilt.items():
        if protocol.get(key) != value:
            raise ValueError("Saved fold protocol failed reconstruction: " + key)
    queries = {q["query_id"]: q for q in protocol["queries"]}
    if len(queries) != len(protocol["queries"]) or not queries:
        raise ValueError("Missing or duplicate protocol queries")
    expected = {(qid, method) for qid in queries for method in ("blastp", "placement")}
    rows = bundle.get("results", [])
    pairs = [(r.get("query_id"), r.get("method")) for r in rows]
    if set(pairs) != expected or len(pairs) != len(expected):
        raise ValueError("Incomplete or duplicate saved results; report-only recovery cannot run missing analyses")
    for row in rows:
        q = queries[row["query_id"]]
        if any(row.get(k) != q[k] for k in ("true_group", "cluster_id", "fold_id", "accessions")):
            raise ValueError("Saved result metadata differs from the frozen query")
    evaluation = aggregate(rows)
    if bundle.get("evaluation") and bundle["evaluation"] != evaluation:
        raise ValueError("Saved evaluation disagrees with recorded query outcomes")
    by_key = {(r["query_id"], r["method"]): r for r in rows}
    candidates = {}
    for item in bundle.get("placement_candidates", []):
        qid = item.get("query_id") or item.get("candidate", {}).get("query", {}).get("id")
        if qid not in queries or qid in candidates:
            raise ValueError("Unknown or duplicate saved placement candidate")
        candidates[qid] = item
    verified_files, verified_placements, verified_blast = {}, 0, 0
    for fold in protocol["folds"]:
        if fold["status"] != "ready":
            for qid in fold["query_ids"]:
                if qid in candidates:
                    raise ValueError("Held fold unexpectedly contains native placement evidence")
                for method in ("blastp", "placement"):
                    row = by_key[qid, method]
                    if row["status"] != "held" or row["reason"] != ";".join(fold["reasons"]):
                        raise ValueError("Saved result bypassed the reference-adequacy gate")
            continue
        train = training_panel(panel, fold, protocol["policy"])
        folder = root / "folds" / fold["fold_id"]
        if _read(folder / "training-panel.json") != train:
            raise ValueError("Training-only panel content differs from frozen exclusions")
        if read_fasta(folder / "references.faa") != {m["tip_id"]: m["sequence"] for m in train["members"]}:
            raise ValueError("Training FASTA differs from the held-out panel")
        for qid in fold["query_ids"]:
            work = folder / qid
            if read_fasta(work / "query.faa") != {qid: members[qid]["sequence"]}:
                raise ValueError("Saved benchmark query sequence mismatch")
            row = by_key[qid, "blastp"]
            if row["status"] == "held":
                raise ValueError("Eligible folds cannot have a BLAST execution hold")
            if row["status"] == "completed":
                hits = parse_blast(work / "blast.tsv", members, {qid}, set(fold["reference_ids"]))
                decision = blast_call(qid, train["members"], hits, protocol["policy"])
                decision["metrics"]["raw_alignment_count"] = len(hits)
                for key, value in decision.items():
                    if row.get(key) != value:
                        raise ValueError("Saved BLAST decision differs from its native search: " + key)
                verified_blast += 1
            row = by_key[qid, "placement"]
            item = candidates.get(qid)
            if not item:
                if row["status"] != "failed":
                    raise ValueError("Saved placement evidence missing")
                continue
            candidate = item["candidate"]
            layer = candidate["analyses"]["family_placement"]
            if layer != _read(work / "placement-result.json"):
                raise ValueError("Candidate placement differs from saved native result")
            if candidate["query"]["id"] != qid or candidate["query"]["sequence_sha256"] != queries[qid]["sequence_sha256"]:
                raise ValueError("Placement query identity mismatch")
            seals = layer.get("artifact_checksums", {})
            for path in layer.get("artifacts", []):
                resolved = _inside(path, root)
                if str(resolved) not in seals or sha256_file(resolved) != seals[str(resolved)]:
                    raise ValueError("Native placement artifact checksum mismatch: " + str(resolved))
                verified_files[str(resolved)] = seals[str(resolved)]
            if layer["status"] == "completed":
                checked = _validated(candidate, item["deep_record"])
                if set(checked["labels"]) != set(fold["reference_ids"]):
                    raise ValueError("Placement tree tips differ from the held-out training fold")
                for member in train["members"]:
                    label = checked["labels"][member["tip_id"]]
                    if any(label.get(k) != member.get(k) for k in
                           ("group", "role", "accessions", "sequence_sha256")):
                        raise ValueError("Placement labels differ from frozen training references")
                decision = placement_call(checked["scores"], protocol["policy"])
                decision["metrics"]["reference_cache_reused"] = layer.get("reference_cache", {}).get("reused")
                for key, value in decision.items():
                    if row.get(key) != value:
                        raise ValueError("Saved placement decision differs from its native artifacts: " + key)
                verified_placements += 1
            elif layer["status"] in {"insufficient_evidence", "not_assessed"}:
                if row["status"] != "held" or row["reason"] != layer.get("reason"):
                    raise ValueError("Placement hold status mismatch")
            elif row["status"] != "failed":
                raise ValueError("Native placement failure was not retained")
    bundle = deepcopy(bundle)
    bundle["evaluation"] = evaluation
    status = "incomplete" if any(r["status"] == "failed" for r in rows) else "completed"
    if original_status not in {"prepared", status}:
        raise ValueError("Saved execution status disagrees with results")
    bundle["status"] = status
    recovered_at = now()
    audit = {"schema": "basis-placement-benchmark-report-recovery-v1",
             "source": str(source), "source_sha256": sha256_file(source),
             "original_bundle_status": original_status, "report_recovered_at": recovered_at,
             "original_finished_at": bundle.get("finished_at"),
             "finished_time_policy": "Original recorded value preserved; missing finalization time is not inferred.",
             "verified_placement_count": verified_placements, "verified_blast_count": verified_blast,
             "verified_native_artifacts": verified_files, "native_analyses_run": False,
             "source_policy": "Historical execution retained; current source is used only to verify and render saved evidence."}
    bundle["report_recovery"] = audit
    return bundle, audit


def recover_benchmark_report(run_dir, outdir=None):
    root = Path(run_dir).resolve(strict=True)
    with stage("Verify saved benchmark decisions, holdouts and native artifacts"):
        bundle, audit = validate_saved_benchmark(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = Path(outdir).resolve() if outdir else root / "report-recovery" / stamp
    destination.mkdir(parents=True, exist_ok=False)
    # Keep the frozen audit bytes alongside the newly rendered downloads.
    (destination / "frozen-protocol.json").write_bytes((root / "frozen-protocol.json").read_bytes())
    atomic_json(destination / "recovery.json", audit)
    emit("Render saved benchmark report and tree views; no native tools")
    artifacts = write_benchmark_report(bundle, destination)
    return bundle, artifacts
