"""Describe supplied input context without filesystem reads or classification."""
from __future__ import annotations

from .inputs import normalize_input_mode

_MISSING_TAXONOMY = frozenset({
    "", "na", "n/a", "none", "null", "unknown", "unresolved",
    "not resolved", "not_recorded", "not supplied",
})


def taxonomy_is_missing(value) -> bool:
    """Recognize declaration placeholders without changing the original text."""
    return str("" if value is None else value).strip().casefold() in _MISSING_TAXONOMY


def describe_input_context(sample: dict, resolved: bool = False) -> dict:
    """Return metadata describing declarations and whether HybAs was consulted.

    ``resolved`` means an input resolver has already populated this sample. This
    helper never resolves paths, reads HybAs metadata, or classifies a taxon.
    Raw declaration strings are retained, including missing-value placeholders.
    """
    try:
        mode = normalize_input_mode(sample.get("input_mode"))
        valid_mode = True
    except ValueError:
        # Preview also describes invalid rows; leave their validation errors
        # with the resolver rather than raising from this descriptive helper.
        mode = sample.get("input_mode")
        valid_mode = False
    taxon = sample.get("taxon", "")
    status = "not_recorded" if taxonomy_is_missing(taxon) else (
        (sample.get("taxon_status") or "declared") if resolved else "declared")
    policies = {"external": "not_consulted_external_mode",
                "hybas": "required_matching_layout",
                "auto": "matching_layout_if_available"}
    policy = policies[mode] if valid_mode else "invalid"
    if not valid_mode:
        metadata_status = "not_evaluated"
    elif mode == "external":
        metadata_status = "not_consulted"
    elif not resolved:
        metadata_status = "not_evaluated"
    else:
        context = sample.get("hybas_context")
        metadata_status = context.get("status", "not_found") if isinstance(context, dict) else "not_found"
    return {"input_mode": mode,
            "taxon": taxon,
            "taxon_rank": sample.get("taxon_rank", ""),
            "taxon_source": sample.get("taxon_source", ""),
            "taxon_status": status,
            "hybas_metadata_policy": policy,
            "hybas_metadata_status": metadata_status,
            "taxonomic_classification_performed": False}
