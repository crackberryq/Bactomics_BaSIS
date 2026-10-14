"""Serializable DeepSafe stage execution for Snakemake-owned scheduling.

This module partitions the existing scientific operations; it does not change
candidate selection, thresholds, comparator eligibility, or interpretation.
Each state is bound to the same sample, upstream records, configuration and work
folder. Reference catalogues remain files rather than being copied into states.
The workflow worker owns atomic checkpoint writes and scheduler receipts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import deep as _deep

STAGES = ("prepare", "domains", "references", "phylogeny", "patterns", "profiles",
          "motifs", "placement", "finalize")
STATE_SCHEMA = "basis-deepsafe-stages-v1"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str, allow_nan=False).encode()).hexdigest()


def _identity(sample, records, config, outdir):
    return {"sample_id": sample["sample_id"], "sample_sha256": _digest(sample),
            "records_sha256": _digest(records), "config_sha256": _digest(config),
            "outdir": str(Path(outdir).resolve())}


def _queries(state):
    result = {}
    for candidate in state["candidates"]:
        query = candidate.get("query")
        if not query:
            continue
        rows = _deep.read_fasta(Path(query["path"]))
        if set(rows) != {query["id"]}:
            raise ValueError("DeepSafe stage query FASTA identity changed")
        sequence = rows[query["id"]]
        if _deep.sequence_hash(sequence) != query["sequence_sha256"]:
            raise ValueError("DeepSafe stage query sequence changed")
        result[query["id"]] = sequence
    return result


def _catalog(config, state, establish=False):
    fasta, metadata = config.get("reference_fasta"), config.get("reference_metadata")
    if bool(fasta) != bool(metadata):
        raise ValueError("reference_fasta and reference_metadata must be configured together")
    if not fasta:
        if establish:
            state["catalogue_configured"] = False
            state["catalogue_files"] = []
        return None
    paths = [Path(fasta), Path(metadata)]
    files = [{"path": str(path.resolve()), "sha256": _deep._file_hash(path)} for path in paths]
    if not establish and files != state.get("catalogue_files"):
        raise ValueError("Comparator catalogue changed between DeepSafe stages")
    catalog = _deep.load_catalog(*paths)
    if establish:
        state["catalogue_configured"] = True
        state["catalogue_files"] = files
    return catalog


def _prepare(sample, records, config, outdir, runner, state):
    motif_rules, motif_registry = _deep.load_rules(config.get("motif_rules")) if config.get("motifs", True) else ([], {})
    candidates = _deep.select_candidates(records, config, sample)
    _deep.emit(f"DeepSafe selection: {sum(bool(c.get('selected')) for c in candidates)} selected / {len(candidates)} candidates")
    candidate_number = 0
    # Candidate IDs are stable within a sample and cannot collide across samples.
    for candidate in candidates:
        identity = json.dumps([sample["sample_id"], candidate["candidate_id"]], separators=(",", ":"))
        candidate["candidate_id"] = "cand_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
    findings = list(_deep._flatten(records))
    profiles, profile_errors = _deep.validate_profiles(Path(config["profiles_dir"]) if config.get("profiles_dir") else None)
    if profile_errors:
        raise ValueError("Invalid profile registry: " + "; ".join(profile_errors))
    notes = ["Exploratory follow-up only: no organism-safe or categorical functional verdict is produced.",
             "Similarity thresholds retrieve references; they are not validated functional classification thresholds."]
    artifacts = []
    protein_path = sample.get("proteins")
    proteins = _deep.read_fasta(Path(protein_path)) if protein_path and Path(protein_path).is_file() else {}
    assembly = _deep.read_fasta(Path(sample["assembly"]))
    flank = int(config.get("context_flank", 20000))
    if flank < 0:
        raise ValueError("context_flank must be >=0")
    circular = set(sample.get("circular_contigs") or config.get("circular_contigs") or [])
    for candidate in candidates:
        candidate["sample_id"] = sample["sample_id"]
        candidate["references"] = []
        candidate["analyses"] = {}
        candidate["notes"] = []
        if not candidate["selected"]:
            candidate["analysis_plan"] = {"route": "resource_deferred", "analyses": {},
                "decision_trace": [{"decision": "defer", "reason": candidate["reason"]}]}
            candidate["decision_trace"] = candidate["analysis_plan"]["decision_trace"]
            continue
        candidate_number += 1
        _deep.emit(f"DeepSafe candidate {candidate_number}: {candidate.get('protein_id')} | {candidate.get('gene')} — extraction, integrity and context")
        directory = outdir / candidate["candidate_id"]
        directory.mkdir(exist_ok=True)
        # YAML may materialize unquoted review dates as datetime.date values.
        # The historical evidence JSON writes those as strings; normalize here
        # so a checkpoint can cross processes without requiring a custom encoder.
        profile = json.loads(json.dumps(_deep.profile_for_candidate(candidate, profiles),
                                        default=str, allow_nan=False))
        candidate["profile"] = profile
        candidate["analysis_plan"] = _deep.make_analysis_plan(candidate, profile, _deep.inspect_integrity(candidate, None), config)
        candidate["decision_trace"] = candidate["analysis_plan"]["decision_trace"]
        try:
            candidate["analyses"]["context"] = _deep._context(candidate, findings, assembly, flank, circular, directory)
            query, origin = _deep._extract_query(candidate, proteins)
            integrity = _deep.inspect_integrity(candidate, query)
            candidate["analyses"]["integrity"] = integrity
            candidate["analysis_plan"] = _deep.make_analysis_plan(candidate, profile, integrity, config)
            candidate["decision_trace"] = candidate["analysis_plan"]["decision_trace"]
            if query is None:
                candidate.update(execution="not_assessed", reason=origin)
                continue
            query_id = "Q_" + candidate["candidate_id"]
            query_path = directory / "query.faa"
            _deep.write_fasta({query_id: query}, query_path)
            candidate["query"] = {"id": query_id, "path": str(query_path), "origin": origin,
                                  "sequence_sha256": _deep.sequence_hash(query), "length": len(query)}
            artifacts.append(str(query_path))
        except (ValueError, OSError, RuntimeError) as exc:
            candidate.update(execution="failed", reason=str(exc))
    state.update(candidates=candidates, notes=notes, artifacts=artifacts,
                 motif_rules=motif_rules, motif_registry=motif_registry,
                 internal={}, tree_budget={"used": 0})


def _domains(sample, records, config, outdir, runner, state):
    _queries(state)  # Verify saved extraction before native tools consume it.
    for candidate in state["candidates"]:
        if candidate["execution"] != "pending":
            continue
        _deep.emit(f"DeepSafe {candidate.get('protein_id')}: Pfam domain evidence")
        try:
            candidate["analyses"]["domains"] = _deep._scan_domains(
                candidate, config, outdir / candidate["candidate_id"], runner)
        except (ValueError, OSError, RuntimeError) as exc:
            candidate["analyses"]["domains"] = {"status": "failed", "reason": str(exc)}
            candidate.update(execution="failed", reason=str(exc))


def _references(sample, records, config, outdir, runner, state):
    candidates, notes, artifacts = state["candidates"], state["notes"], state["artifacts"]
    queries = _queries(state)
    fasta, metadata = config.get("reference_fasta"), config.get("reference_metadata")
    if bool(fasta) != bool(metadata):
        raise ValueError("reference_fasta and reference_metadata must be configured together")
    _deep.emit("DeepSafe: loading comparator catalogue and searching references")
    catalog = _catalog(config, state, establish=True)
    hits = {}
    search_queries = {c["query"]["id"]: queries[c["query"]["id"]] for c in candidates
                      if c["execution"] == "pending" and c["analysis_plan"]["analyses"]["references"]}
    if catalog and search_queries:
        notes.extend(catalog.warnings)
        # A failed search raises, rather than being rewritten as an empty-hit result.
        hits, search_artifacts = _deep._search(search_queries, catalog, config, outdir, runner)
        artifacts.extend(search_artifacts)
        hits, anchor_artifacts = _deep.search_anchor_panel(search_queries, candidates, catalog, config, outdir, runner, hits)
        artifacts.extend(anchor_artifacts)
    for candidate in candidates:
        if candidate["execution"] != "pending":
            continue
        _deep.emit(f"DeepSafe {candidate.get('protein_id')}: comparator review")
        directory = outdir / candidate["candidate_id"]
        if not candidate["analysis_plan"]["analyses"]["references"]:
            candidate["analyses"]["references"] = {"status": "not_assessed", "reason": "not_requested_by_route"}
            candidate["analyses"]["phylogeny"] = {"status": "not_assessed", "reason": "not_requested_by_route"}
            candidate.update(execution="completed", reason="native_exact_context_review" if candidate["analysis_plan"]["route"] == "exact_curated_amr_context_review" else "profile_limited_evidence_prepared")
            continue
        if catalog is None:
            candidate["analyses"]["references"] = {"status": "not_assessed", "reason": "reference_catalogue_not_configured"}
            candidate["analyses"]["phylogeny"] = {"status": "not_assessed", "reason": "reference_catalogue_not_configured"}
            candidate.update(execution="not_assessed", reason="reference_catalogue_not_configured")
            continue
        try:
            references, warnings = _deep.select_references(catalog, hits.get(candidate["query"]["id"], []),
                candidate["profile"], candidate, int(config.get("max_references", 30)),
                metadata_only=config.get("reference_search_tool") == "metadata")
            audit_rows = [dict(row) for row in config.get("_retrieval_audit", [])
                          if row["query_id"] == candidate["query"]["id"]]
            selected_ids = {ref["reference_id"] for ref in references}
            anchor_checks = config.get("_anchor_review", {}).get(candidate["query"]["id"], [])
            candidate["analyses"]["anchor_review"] = {"status":"completed", "anchors":[
                {**check, "selected":check["reference_id"] in selected_ids} for check in anchor_checks]}

            for row in audit_rows:
                row["reference_selected"] = row["reference_id"] in selected_ids
                if row["passes_retrieval_filters"] and not row["reference_selected"]:
                    row["selection_exclusion"] = ("catalogue_rejected" if catalog.metadata[row["reference_id"]]["review_status"] == "rejected"
                                                  else "reference_budget_or_profile_selection")
            partial = _deep.partial_reference_review(audit_rows, candidate["analyses"].get("domains",{}).get("hits",[]), config)
            for row in partial:
                row["reference_metadata"] = catalog.manifest(row["reference_id"])
                row["catalogue_rejected"] = catalog.metadata[row["reference_id"]]["review_status"] == "rejected"
            partial = [row for row in partial if not row["catalogue_rejected"]]
            candidate["analyses"]["partial_references"] = {"status":"completed", "hits":partial,
                "eligible_for_whole_protein_tree":False, "minimum_query_domain_overlap":config.get("partial_domain_min_overlap",0.7)}
            candidate["analyses"]["retrieval_audit"] = {"status":"completed", "rows":audit_rows,
                "scope":"Reported native HSPs only; references not returned by the search are not assessed."}
            candidate["references"], candidate["notes"] = references, candidate["notes"] + warnings
            candidate["analyses"]["references"] = {"status": "completed", "selected_count": len(references),
                "selection_mode": config.get("reference_search_tool", "blastp"), "warnings": warnings}
            reference_path = directory / "reference_manifest.json"
            reference_path.write_text(json.dumps(references, indent=2) + "\n")
            artifacts.append(str(reference_path))
            if not references:
                candidate["analyses"]["phylogeny"] = {"status": "not_assessed", "reason": "no_eligible_comparator_references"}
                candidate.update(execution="completed", reason="partial_domain_comparators_only" if partial else "no_eligible_comparator_references")
                continue
        except (ValueError, OSError, RuntimeError) as exc:
            candidate.update(execution="failed", reason=str(exc))
    state["internal"] = {key: config[key] for key in ("_retrieval_audit", "_anchor_review")
                         if key in config}


def _phylogeny(sample, records, config, outdir, runner, state):
    candidates, artifacts = state["candidates"], state["artifacts"]
    queries = _queries(state)
    eligible = [c for c in candidates if c["execution"] == "pending"]
    if not eligible:
        return
    catalog = _catalog(config, state)
    tree_budget = state["tree_budget"]
    for candidate in eligible:
        _deep.emit(f"DeepSafe {candidate.get('protein_id')}: eligible tree follow-up")
        directory = outdir / candidate["candidate_id"]
        references = candidate["references"]
        try:
            if config.get("auto_phylogeny", False):
                phylogeny = _deep.reuse_review(candidate, config)
                if phylogeny is None:
                    phylogeny = _deep.run_bounded(candidate, catalog, config, directory, runner, tree_budget)
                candidate["analyses"]["phylogeny"] = phylogeny
                artifacts.extend(phylogeny.get("artifacts", []))
                if phylogeny.get("status") == "failed":
                    candidate.update(execution="failed", reason=phylogeny.get("reason"))
                    continue
            else:
                candidate["analyses"]["phylogeny"] = _deep._build_phylogeny(candidate["query"]["id"],
                    queries[candidate["query"]["id"]], references, catalog,
                    {**config, "phylogeny": candidate["analysis_plan"]["analyses"]["phylogeny"]}, directory, runner)
            insufficient = any(n.startswith(("missing_anchor:", "unreviewed_anchor:", "missing_reviewed_comparison_group:")) for n in candidate["analyses"]["references"].get("warnings", []))
            candidate.update(execution="completed", reason="insufficient_reviewed_comparators" if insufficient else "exploratory_evidence_prepared")
            candidate["notes"].append("Existing domain and context calls remain linked in source/context evidence.")
        except (ValueError, OSError, RuntimeError) as exc:
            candidate.update(execution="failed", reason=str(exc))


def _patterns(sample, records, config, outdir, runner, state):
    _deep.emit("DeepSafe: PROSITE patterns")
    from .prosite import run_prosite
    state["artifacts"].extend(run_prosite(state["candidates"], config, outdir, runner))


def _profiles(sample, records, config, outdir, runner, state):
    _deep.emit("DeepSafe: PROSITE profiles and residue mapping")
    from .prosite_profiles import run_profiles
    state["artifacts"].extend(run_profiles(state["candidates"], config, outdir, runner))


def _motifs(sample, records, config, outdir, runner, state):
    queries = _queries(state)
    for candidate in state["candidates"]:
        sequence = queries.get(candidate.get("query", {}).get("id"))
        candidate["analyses"]["motifs"] = _deep.assess_motifs(
            candidate, sequence, state["motif_rules"], state["motif_registry"],
            config.get("motifs", True))


def _placement(sample, records, config, outdir, runner, state):
    from .placement import run_family_placements
    catalog = _catalog(config, state)
    state["artifacts"].extend(run_family_placements(
        state["candidates"], config, outdir, runner, catalogue=catalog))


def _finalize(sample, records, config, outdir, runner, state):
    from .placement import placement_summary
    candidates, notes, artifacts = state["candidates"], state["notes"], state["artifacts"]
    for candidate in candidates:
        candidate["assessment"] = _deep.assess_candidate(candidate, candidate.get("analyses", {}).get("phylogeny", {}).get("followup_evidence"))
    _deep.emit("DeepSafe: motif assessment complete; consolidating evidence")
    from .deep_consolidation import consolidate
    for candidate in candidates:
        candidate["consolidated_evidence"] = consolidate(candidate)
        if candidate.get("selected"):
            _deep.emit(f"DeepSafe {candidate.get('protein_id')}: {candidate.get('execution')} | {candidate.get('assessment', {}).get('interpretation')} | tree: {candidate.get('analyses', {}).get('phylogeny', {}).get('reason')}")
    metrics = {"candidate_count": len(candidates), "selected_candidates": sum(c["selected"] for c in candidates),
               "completed_candidates": sum(c["execution"] == "completed" for c in candidates),
               "failed_candidates": sum(c["execution"] == "failed" for c in candidates),
               "deferred_candidates": sum(c["execution"] == "deferred" for c in candidates),
               "not_assessed_candidates": sum(c["execution"] == "not_assessed" for c in candidates),
               "reference_catalogue_configured": state["catalogue_configured"],
               "family_placement": placement_summary(candidates)}
    consolidated_path = outdir / "consolidated_evidence.json"
    consolidated_path.write_text(json.dumps({"schema": "basis-consolidated-v1", "candidates": [c["consolidated_evidence"] for c in candidates if c.get("selected")], "deferred_candidates": metrics["deferred_candidates"]}, indent=2, default=str) + "\n")
    artifacts.append(str(consolidated_path))
    summary_path = outdir / "candidates.json"
    summary_path.write_text(json.dumps(candidates, indent=2, default=str) + "\n")
    artifacts.append(str(summary_path))
    for candidate in candidates:
        context_path = candidate["analyses"].get("context", {}).get("sequence_path")
        if context_path:
            artifacts.append(context_path)
        for key in ("input", "labels", "alignment", "tree"):
            artifact = candidate["analyses"].get("phylogeny", {}).get(key)
            if artifact:
                artifacts.append(artifact)
        for key in ("domain_table", "native_output"):
            artifact = candidate["analyses"].get("domains", {}).get(key)
            if artifact:
                artifacts.append(artifact)
    complete = not any(metrics[k] for k in ("failed_candidates", "deferred_candidates", "not_assessed_candidates"))
    state["payload"] = {"findings": [], "metrics": metrics, "artifacts": list(dict.fromkeys(artifacts)), "notes": notes,
            "interpretation": "inconclusive" if candidates else "not_applicable",
            "candidates": candidates, "complete": complete,
            "completion_summary": _deep.completion_summary({"candidates": candidates})}


_HANDLERS = {stage: globals()["_" + stage] for stage in STAGES}


def run_stage(stage, sample, records, config, outdir, runner, state=None):
    """Run exactly one stage, retaining scientific failures in candidate evidence.

    A caller may persist the returned state with ``json.dump`` and supply it in
    the next process. Native search failures continue to raise; candidate-level
    failures, missing resources and deferred candidates retain their prior
    meanings. Calls in the wrong order or with different inputs are rejected.
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown DeepSafe stage: {stage}")
    outdir = Path(outdir).resolve()
    identity = _identity(sample, records, config, outdir)
    if state is None:
        if stage != "prepare":
            raise ValueError("DeepSafe stage prepare must run first")
        state = {"schema": STATE_SCHEMA, "identity": identity, "completed_stages": []}
    if state.get("schema") != STATE_SCHEMA or state.get("identity") != identity:
        raise ValueError("DeepSafe stage state does not match sample, evidence, configuration or output directory")
    completed = state.get("completed_stages")
    if completed != list(STAGES[:STAGES.index(stage)]):
        raise ValueError(f"DeepSafe stage {stage} is out of order")
    # Runtime-only caches use tuple keys and must not leak into JSON checkpoints.
    effective = dict(config)
    effective.update(state.get("internal", {}))
    outdir.mkdir(parents=True, exist_ok=True)
    _HANDLERS[stage](sample, records, effective, outdir, runner, state)
    state["completed_stages"].append(stage)
    state["stage"] = stage
    return state
