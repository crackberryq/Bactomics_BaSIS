"""Versioned review cues from reported AMRFinder metadata, not a risk classifier.

No search, similarity threshold, native call or phenotype is changed here.
Review obligations are independent of computational admission and budgets.
"""
from __future__ import annotations

VERSION = "basis-amrfinder-review-v1"
PARTIAL_METHODS = {"PARTIAL", "PARTIALP", "PARTIALX", "PARTIAL_CONTIG_END",
                   "PARTIAL_CONTIG_ENDP", "PARTIAL_CONTIG_ENDX"}
DISRUPTION_METHODS = {"INTERNAL_STOP", "INTERNAL_STOPP", "INTERNAL_STOPX",
                      "FRAME_SHIFT", "FRAME_SHIFTP", "FRAME_SHIFTX"}
REVIEW_METHODS = {"HMM"} | PARTIAL_METHODS | DISRUPTION_METHODS
OTHER_METHODS = {"ALLELE", "ALLELEP", "ALLELEX", "EXACT", "EXACTP", "EXACTX",
                 "BLAST", "BLASTP", "BLASTX", "POINT", "POINTP", "POINTX", "POINTN"}
REASONS = {
    "hmm_family_assignment": "AMRFinder reports a family-level HMM match; the specific functional interpretation needs review.",
    "partial_sequence": "AMRFinder reports a partial match; sequence completeness needs review.",
    "sequence_disruption": "AMRFinder reports a disrupted sequence; its functional interpretation needs review.",
    "putative_disruptive_variant": "AMRFinder reports a putative disruptive variant; retain its exploratory qualification for manual interpretation.",
    "native_method_missing_or_unsupported": "The native method is missing or unsupported by this review policy; inspect the source record.",
    "native_method_conflict": "The saved native method and normalized metadata disagree; reconcile the source record first.",
    "native_call_context_review": "Retain the reported AMR call for context and phenotype review; this policy does not request redundant sequence follow-up.",
}


def review_finding(finding):
    """Describe a source finding even when its follow-up is disabled or deferred."""
    if ((finding.get("module") or finding.get("tool")) != "amrfinder"
            or finding.get("category") != "amr"):
        return None
    attrs = finding.get("attributes") or {}
    raw = attrs.get("raw") or {}
    normalized = str(attrs.get("native_method") or "").strip().upper()
    native = str(raw.get("Method") or "").strip().upper()
    method = normalized or native
    flags = set(attrs.get("flags") or [])
    reasons = []
    if method == "HMM" or "family_hmm_only" in flags:
        reasons.append("hmm_family_assignment")
    if method in PARTIAL_METHODS or "partial_sequence" in flags:
        reasons.append("partial_sequence")
    if method in DISRUPTION_METHODS or flags.intersection({"internal_stop", "frame_shift"}):
        reasons.append("sequence_disruption")
    if "putative_disruptive_variant" in flags or str(attrs.get("subtype") or raw.get("Subtype") or "").upper() == "POINT_DISRUPT":
        reasons.append("putative_disruptive_variant")
    invalid = method not in REVIEW_METHODS | OTHER_METHODS
    conflict = bool(normalized and native and normalized != native)
    conflict |= ("family_hmm_only" in flags and method != "HMM")
    conflict |= ("partial_sequence" in flags and method not in PARTIAL_METHODS)
    conflict |= (bool(flags.intersection({"internal_stop", "frame_shift"})) and method not in DISRUPTION_METHODS)
    if invalid:
        reasons.append("native_method_missing_or_unsupported")
    if conflict:
        reasons.append("native_method_conflict")
    investigation = bool(reasons)
    return {"policy": VERSION, "status": "requires_review",
            "investigation_required": investigation,
            "automatic_followup": (method in REVIEW_METHODS and not invalid and not conflict
                                   and "putative_disruptive_variant" not in reasons),
            "native_method": method or None,
            "reasons": reasons or ["native_call_context_review"],
            "interpretation": "Operational review cue only; no resistance phenotype or safety verdict inferred."}
