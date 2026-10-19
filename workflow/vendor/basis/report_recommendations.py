"""Join recorded DeepSafe decisions to reporting priorities without inference.

This module only reads completed evidence. It neither changes native findings nor
runs analyses, assigns biological function, or promotes ML attention into a hazard.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json


def _ids(value):
    if value is None:
        return []
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    return sorted({str(item) for item in value if item is not None and str(item)})


def _target(row):
    if row.get("protein_id"):
        return ("protein", str(row["protein_id"]))
    if row.get("locus_id"):
        return ("locus", str(row["locus_id"]))
    return None


def _stamp(row):
    """IDs describe the final recommendation, including any revised scope."""
    identity = [row.get(key) for key in (
        "sample_id", "category", "assessment_type", "trigger", "evidence_ids")]
    row["recommendation_id"] = "rec-" + hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode()).hexdigest()[:12]
    return row


def refine_recommendations(samples, records, deep_records, recommendations):
    """Return evidence-linked priorities; never mutate any supplied object.

    Taxonomic holds affect only explicitly linked findings in the same sample.
    Comparator and subgroup uncertainty remain visible as computational review
    priorities without suppressing otherwise appropriate phenotype-evidence review.
    Missing links are disclosed and never matched speculatively by gene name.
    """
    # Imported at call time: report imports this function and owns normalization,
    # including stable IDs for source findings that do not provide their own ID.
    from .report import _all_findings, _execution, _recommendation_trigger, _sample_id

    records, deep_records = list(records), list(deep_records)
    rows = _all_findings(records + deep_records)
    index = {}
    for row in rows:
        if row["execution_status"] == "completed":
            index.setdefault((row["sample_id"], row["finding_id"]), row)

    grouped = {}
    taxonomic_holds = {}
    for record in deep_records:
        if _execution(record) != "completed":
            continue
        sid = _sample_id(record)
        for candidate in record.get("candidates", []) or []:
            if not isinstance(candidate, dict) or not candidate.get("selected"):
                continue
            if _execution(candidate) != "completed":
                continue
            # A mismatched candidate cannot change another sample's priorities.
            if candidate.get("sample_id") is not None and str(candidate["sample_id"]) != sid:
                continue
            assessment = candidate.get("assessment") or {}
            if not isinstance(assessment, dict):
                continue
            followup = assessment.get("followup")
            if followup == "hold_for_taxonomic_review":
                kind = "deep_taxonomic_review"
            elif followup == "hold_for_comparator_review":
                kind = "deep_comparator_review"
            elif followup == "stop_automatic_tree_followup":
                kind = "deep_interpretation_review"
            else:
                continue

            source_ids = set(_ids(candidate.get("finding_ids")))
            for source in candidate.get("source_evidence", []) or []:
                if isinstance(source, dict):
                    if source.get("sample_id") is not None and str(source["sample_id"]) != sid:
                        continue
                    source_ids.update(_ids(source.get("finding_id") or source.get("id")))
            if kind == "deep_taxonomic_review":
                taxonomy = candidate.get("taxonomy_review") or {}
                checks = taxonomy.get("checks") if isinstance(taxonomy, dict) else None
                if checks:
                    # Review may be specific to one model on a protein that also
                    # has independent, applicable native findings.
                    source_ids = set()
                    for check in checks:
                        if isinstance(check, dict) and "review_required" in str(check.get("status", "")):
                            source_ids.update(_ids(check.get("finding_id")))
            linked = {fid for fid in source_ids if (sid, fid) in index}
            unlinked = source_ids - linked
            if kind == "deep_taxonomic_review":
                taxonomic_holds.setdefault(sid, set()).update(linked)
            bucket = grouped.setdefault((sid, kind), {
                "candidate_ids": set(), "evidence_ids": set(),
                "unlinked_source_evidence_ids": set(), "targets": set(),
                "missing_comparison_groups": set(), "unlinked_candidates": 0,
            })
            bucket["candidate_ids"].update(_ids(candidate.get("candidate_id")))
            bucket["evidence_ids"].update(linked)
            bucket["unlinked_source_evidence_ids"].update(unlinked)
            bucket["missing_comparison_groups"].update(_ids(assessment.get("missing_comparison_groups")))
            if not linked:
                bucket["unlinked_candidates"] += 1
            for fid in linked:
                target = _target(index[(sid, fid)])
                if target:
                    bucket["targets"].add(target)

    output = []
    for original in recommendations:
        row = deepcopy(original)
        sid = str(row.get("sample_id", "unknown"))
        evidence_ids = _ids(row.get("evidence_ids"))
        category = row.get("category")
        if category in {"amr", "virulence", "bgc"} and row.get("assessment_type") in {
                "targeted_assessment_review", "phenotype_evidence_review"}:
            retained = [fid for fid in evidence_ids if fid not in taxonomic_holds.get(sid, set())]
            if evidence_ids and not retained:
                continue
            excluded = sorted(set(evidence_ids) - set(retained))
            evidence_ids = retained
            if excluded:
                row["excluded_taxonomic_hold_evidence_ids"] = excluded
                row["recommendation"] += (
                    " Findings held for taxonomic-scope review are listed separately and do not "
                    "contribute to this phenotype-assessment trigger.")
        if category == "ml_attention":
            row["assessment_type"] = "model_evidence_review"
        if category == "stress":
            row["assessment_type"] = "computational_review"
            row["provided_phenotype_status"] = "not_applicable_computational_review"
        row["evidence_ids"] = evidence_ids
        linked_rows = [index[(sid, fid)] for fid in evidence_ids if (sid, fid) in index]
        targets = {target for item in linked_rows if (target := _target(item)) is not None}
        row["finding_record_count"] = len(evidence_ids)
        row["distinct_target_count"] = len(targets)
        row["unlinked_evidence_ids"] = [fid for fid in evidence_ids if (sid, fid) not in index]
        if category in {"amr", "virulence", "bgc", "stress", "ml_attention"}:
            # Use the established VFDB threshold wording where all links resolve.
            if len(linked_rows) == len(evidence_ids):
                row["trigger"] = _recommendation_trigger(linked_rows)
            else:
                row["trigger"] = str(len(evidence_ids)) + " finding record(s) selected for review"
            row["trigger"] += "; " + str(len(targets)) + " distinct identified protein/locus target(s) across all linked evidence"
            if row["unlinked_evidence_ids"]:
                row["trigger"] += "; " + str(len(row["unlinked_evidence_ids"])) + " evidence ID(s) require linkage review"
        output.append(_stamp(row))

    wording = {
        "deep_taxonomic_review": (
            "Review applicability of the flagged model to the sample taxon before using the affected "
            "finding to select a resistance- or function-specific phenotype assessment. DeepSafe recorded "
            "a taxonomic-scope hold; the source call is retained, but neither its applicability nor the "
            "candidate's function is established."),
        "deep_comparator_review": (
            "Review the missing or ineligible family comparators identified by DeepSafe. Retain the "
            "unresolved interpretation until comparator identity, coverage and relevant comparison groups "
            "are adequate. This computational hold does not by itself remove an independently justified "
            "phenotype-evidence review or establish a resistance or virulence function."),
        "deep_interpretation_review": (
            "Review the recorded alignments, reference labels and bounded phylogenetic follow-up together. "
            "DeepSafe has stopped automatic tree follow-up; subgroup support or a supported relationship "
            "does not establish the query protein's function. Preserve unresolved or review-required "
            "interpretations and use the existing evidence to decide whether further assessment is justified."),
    }
    priorities = []
    order = {"deep_taxonomic_review": 0, "deep_comparator_review": 1, "deep_interpretation_review": 2}
    for (sid, kind), bucket in sorted(grouped.items(), key=lambda item: (item[0][0], order[item[0][1]])):
        ids = sorted(bucket["evidence_ids"])
        trigger = (str(len(bucket["candidate_ids"])) + " identified DeepSafe candidate(s); "
                   + str(len(ids)) + " linked finding record(s); "
                   + str(len(bucket["targets"])) + " distinct identified protein/locus target(s)")
        if bucket["unlinked_candidates"]:
            trigger += "; " + str(bucket["unlinked_candidates"]) + " candidate(s) without a verified source finding link"
        row = {"sample_id": sid, "category": kind, "priority": "review",
               "assessment_type": "computational_review", "trigger": trigger,
               "evidence_ids": ids, "recommendation": wording[kind],
               "provided_phenotype_status": "not_applicable_computational_review",
               "candidate_ids": sorted(bucket["candidate_ids"]),
               "missing_comparison_groups": sorted(bucket["missing_comparison_groups"]),
               "unlinked_source_evidence_ids": sorted(bucket["unlinked_source_evidence_ids"]),
               "finding_record_count": len(ids), "distinct_target_count": len(bucket["targets"])}
        priorities.append(_stamp(row))
    # Re-refining an already refined export is safe: don't duplicate grouped rows.
    priority_keys = {(row["sample_id"], row["category"]) for row in priorities}
    output = [row for row in output if (str(row.get("sample_id")), row.get("category")) not in priority_keys]
    return priorities + output
