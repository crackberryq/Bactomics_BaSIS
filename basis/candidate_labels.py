"""Choose source-supported candidate labels without changing scientific results.

These are database finding labels for navigation and reporting, not a functional
annotation of the query protein. Original source evidence is always retained.
"""
from __future__ import annotations

from copy import deepcopy


POLICY = "qualified_source_labels_v1"


def qualification_reason(finding: dict) -> str | None:
    """Recognize explicit screen qualification; native demotions take priority."""
    attributes = finding.get("attributes") or {}
    cutoff = str(attributes.get("cutoff") or "").casefold()
    tool = str(finding.get("tool") or finding.get("module") or "").casefold()
    if (finding.get("category") == "ml_attention" or
            finding.get("evidence_level") == "exploratory" or
            attributes.get("passes_threshold") is False or
            (tool == "rgi" and (cutoff == "loose" or
             str(attributes.get("nudged") or "").casefold() == "true"))):
        return None
    if attributes.get("passes_threshold") is True:
        return "configured_reporting_thresholds_passed"
    if finding.get("evidence_level") == "curated_model":
        return "curated_model_screen_evidence"
    if tool == "rgi" and cutoff in {"strict", "perfect"}:
        return "native_rgi_" + cutoff
    return None


def refresh_candidate_labels(candidate: dict) -> tuple[dict, dict]:
    """Return a copy with qualified labels and an explicit field-change audit.

    Keep an existing label if a qualifying finding supports it. Otherwise choose
    the first qualifying source in its recorded order. Qualification classes are
    not ranked as comparable measures of biological confidence. Gene and family
    come from the same finding, so a missing qualified family clears an unrelated
    family inherited from a weaker hit. All alternate labels remain in the
    original source_evidence/family_labels; candidate identity, selection,
    historical plans, analyses and interpretations are untouched.
    """
    updated = deepcopy(candidate)
    audit = {"policy": POLICY, "candidate_id": candidate.get("candidate_id"),
             "protein_id": candidate.get("protein_id"), "changed_fields": {},
             "selected_source": None,
             "scope": "source_label_reporting_metadata_only"}
    qualified = []
    for index, finding in enumerate(candidate.get("source_evidence") or []):
        reason = qualification_reason(finding)
        if reason and (finding.get("gene") or finding.get("family")):
            qualified.append((index, finding, reason))
    if not qualified:
        return updated, audit

    # Prefer current labels only when supported by qualifying evidence, then
    # use stable recorded order; never compare scores across databases.
    same_gene = [row for row in qualified if candidate.get("gene") and
                 row[1].get("gene") == candidate.get("gene")]
    same_pair = [row for row in same_gene if
                 row[1].get("family") == candidate.get("family")]
    index, source, reason = (same_pair or same_gene or qualified)[0]
    provenance = {
        "policy": POLICY,
        "source_finding_id": source.get("finding_id") or source.get("id"),
        "source_record_id": source.get("source_record_id"),
        "source_index": index,
        "tool": source.get("tool") or source.get("module"),
        "reference": source.get("reference"),
        "qualification": reason,
        "tie_policy": "keep_existing_supported_label_then_recorded_source_order",
        "interpretation": "Source database labels only; query function and phenotype are not established.",
    }
    updated["gene"] = source.get("gene")
    updated["family"] = source.get("family")
    updated["source_gene_labels"] = list(dict.fromkeys(
        row[1]["gene"] for row in qualified if row[1].get("gene")))
    updated["label_provenance"] = provenance
    audit["selected_source"] = deepcopy(provenance)
    for field in ("gene", "family", "source_gene_labels", "label_provenance"):
        if field not in candidate or candidate[field] != updated[field]:
            audit["changed_fields"][field] = {
                "previously_present": field in candidate,
                "before": deepcopy(candidate.get(field)), "after": deepcopy(updated[field])}
    return updated, audit
