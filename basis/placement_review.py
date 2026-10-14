"""Add placement to saved DeepSafe evidence without repeating other analyses."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import time
import re

from .candidate_labels import refresh_candidate_labels
from .deep_consolidation import consolidate
from .deep_reporting import completion_summary
from .execution import CommandRunner
from .placement import run_family_placements, placement_summary
from .references import read_fasta, sequence_hash
from .util import atomic_json, hash_object, now, read_json, sha256_file


def verify_saved_queries(evidence):
    """Verify sequences and input links before using historical candidate evidence."""
    if evidence.get("schema_version") != "basis-report-1":
        raise ValueError("Expected a basis-report-1 evidence bundle")
    sample_rows = evidence.get("samples", [])
    if not isinstance(sample_rows, list) or any(not isinstance(s, dict) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", str(s.get("sample_id", ""))) for s in sample_rows):
        raise ValueError("Invalid saved sample identifier")
    samples = {s["sample_id"]: s for s in sample_rows}
    if len(samples) != len(sample_rows):
        raise ValueError("Duplicate saved sample identifiers")
    if not evidence.get("deep_records"):
        raise ValueError("No saved DeepSafe records")
    audit = []
    seen = set()
    for record in evidence["deep_records"]:
        sid = record.get("sample_id")
        if sid not in samples or sid in seen or record.get("execution_status") != "completed":
            raise ValueError("Expected one completed DeepSafe record per assessed sample")
        seen.add(sid)
        sample = samples[sid]
        inputs = record.get("input_hashes", {})
        required = {k for k in ("assembly", "proteins", "gff", "genbank") if sample.get(k)}
        if not {"assembly", "proteins"}.issubset(required) or set(inputs) != required or any(not inputs[k] for k in required):
            raise ValueError("Saved DeepSafe record lacks exact supplied-input checksums")
        for key, expected in inputs.items():
            if not sample.get(key) or sha256_file(sample[key]) != expected:
                raise ValueError(f"Saved input changed: {sid}: {key}")
        proteins = read_fasta(Path(sample["proteins"]))
        query_checks = []
        candidate_ids, protein_ids, query_ids = set(), set(), set()
        for candidate in record.get("candidates", []):
            cid = candidate.get("candidate_id")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", str(cid or "")) or cid in candidate_ids:
                raise ValueError("Invalid or duplicate saved candidate identifier")
            candidate_ids.add(cid)
            if not candidate.get("selected") or not candidate.get("query"):
                continue
            query = candidate["query"]
            if candidate.get("protein_id") in protein_ids or query.get("id") in query_ids:
                raise ValueError("Duplicate saved protein/query mapping")
            protein_ids.add(candidate.get("protein_id"))
            query_ids.add(query.get("id"))
            values = read_fasta(Path(query["path"]))
            if set(values) != {query["id"]}:
                raise ValueError("Saved query identifier mismatch: " + str(candidate.get("protein_id")))
            sequence = values[query["id"]]
            if sequence_hash(sequence) != query.get("sequence_sha256"):
                raise ValueError("Saved query checksum mismatch: " + str(candidate.get("protein_id")))
            if proteins.get(candidate.get("protein_id")) != sequence:
                raise ValueError("Saved query does not match the input proteome")
            query_checks.append({"candidate_id": candidate["candidate_id"], "protein_id": candidate.get("protein_id"),
                                 "sequence_sha256": query["sequence_sha256"]})
        audit.append({"sample_id": sid, "input_hashes": inputs, "verified_queries": query_checks})
    return audit


def review_saved_placement(source, outdir, config):
    from .report import write_report
    from .pipeline import code_fingerprint
    source, outdir = Path(source).resolve(), Path(outdir).resolve()
    if outdir.exists():
        raise FileExistsError("Choose a new placement review directory: " + str(outdir))
    source_hash = sha256_file(source)
    evidence = read_json(source)
    print("Verifying saved input and candidate protein checksums...", flush=True)
    queries = verify_saved_queries(evidence)
    merged = {key: deepcopy(evidence[key]) for key in ("run", "samples", "records", "deep_records")}
    cfg = deepcopy(config["deep"])
    if not cfg.get("family_placement"):
        raise ValueError("Enable deep.family_placement in the review configuration")
    source_config = config.get("config_path")
    audit = {"schema": "basis-placement-review-1", "source_evidence": str(source),
             "source_sha256": source_hash, "configuration": source_config,
             "config_sha256": hash_object(config), "started_at": now(), "verified_inputs": queries,
             "source_code_sha256": code_fingerprint(),
             "core_and_extend_rerun": False, "other_deep_analyses_rerun": False,
             "scope": "New family-placement analyses appended to verified historical DeepSafe evidence.",
             "records_sha256": hash_object(merged["records"]), "samples_sha256": hash_object(merged["samples"]),
             "deep_changes": []}
    outdir.mkdir(parents=True, exist_ok=False)
    for deep in merged["deep_records"]:
        before = deepcopy(deep)
        sid = deep["sample_id"]
        work = outdir / "placement" / sid
        runner = CommandRunner(cfg, work / "commands.log")
        deep["candidates"] = [refresh_candidate_labels(c)[0] for c in deep["candidates"]]
        start = time.monotonic()
        artifacts = run_family_placements(deep["candidates"], cfg, work, runner)
        # Existing assessment is retained; placement never promotes a function.
        for candidate in deep["candidates"]:
            candidate["consolidated_evidence"] = consolidate(candidate)
        metrics = deep.setdefault("metrics", {})
        metrics.update(family_placement=placement_summary(deep["candidates"]),
                       completed_candidates=sum(c.get("execution") == "completed" for c in deep["candidates"]),
                       failed_candidates=sum(c.get("execution") == "failed" for c in deep["candidates"]),
                       not_assessed_candidates=sum(c.get("execution") == "not_assessed" for c in deep["candidates"]))
        deep["complete"] = not deep.get("upstream_incomplete") and not any(metrics.get(k, 0) for k in ("failed_candidates", "not_assessed_candidates", "deferred_candidates"))
        deep["completion_summary"] = completion_summary(deep)
        for key in ("cache_key", "cache_hit", "cache_checked_at", "deep_cache"):
            deep.pop(key, None)
        deep["artifacts"] = list(dict.fromkeys(deep.get("artifacts", []) + artifacts + [str(runner.logfile)]))
        layer_audit = {"source_record_sha256": hash_object(before), "config": cfg,
                       "commands": runner.commands, "elapsed_seconds": round(time.monotonic() - start, 3),
                       "finished_at": now(), "artifact_checksums": {p: sha256_file(p) for p in artifacts},
                       "prior_execution_and_interpretations_are_historical": True}
        deep["placement_review"] = layer_audit
        atomic_json(work / "result.json", deep)
        audit["deep_changes"].append({"sample_id": sid, **layer_audit,
                                      "new_record_sha256": hash_object(deep)})
        print(sid, "| placement:", metrics["family_placement"]["statuses"], flush=True)
    if hash_object(merged["records"]) != audit["records_sha256"] or hash_object(merged["samples"]) != audit["samples_sha256"]:
        raise RuntimeError("Historical core or sample evidence changed")
    if sha256_file(source) != source_hash:
        raise RuntimeError("Source evidence changed during review")
    if verify_saved_queries(evidence) != queries:
        raise RuntimeError("Saved inputs or queries changed during placement review")
    # Never improve the historical whole-study completion based on placement.
    incomplete = [d["sample_id"] + ":deepsafe" for d in merged["deep_records"] if not d.get("complete")]
    if incomplete:
        merged["run"]["completeness"] = "incomplete"
        merged["run"]["incomplete_modules"] = list(dict.fromkeys(merged["run"].get("incomplete_modules", []) + incomplete))
    audit["finished_at"] = now()
    merged["run"]["placement_review"] = audit
    artifacts = write_report(merged["run"], merged["samples"], merged["records"], merged["deep_records"], outdir)
    atomic_json(outdir / "placement-review.json", audit)
    return merged, artifacts
