"""Declared per-sample execution scope; no inferred biological classification.

Planning records missing prerequisites and explicit exclusions separately. A hold
on requested work remains incomplete, while an exclusion changes requested scope.
"""
from __future__ import annotations

import copy

from .config import ALL_MODULES
from .input_context import taxonomy_is_missing


def _overrides(config: dict) -> dict:
    overrides = config.get("sample_module_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("sample_module_overrides must be a mapping keyed by sample_id")
    for sid, modules in overrides.items():
        if not isinstance(sid, str) or not sid.strip():
            raise ValueError("sample_module_overrides sample_id keys must be nonempty strings")
        if not isinstance(modules, dict):
            raise ValueError(f"sample_module_overrides.{sid} must be a module mapping")
        for module, declaration in modules.items():
            if module not in ALL_MODULES:
                raise ValueError(f"Unknown sample override module: {sid}:{module}")
            if not isinstance(declaration, dict) or set(declaration) != {"enabled", "reason"}:
                raise ValueError(f"{sid}:{module}: sample override requires only enabled and reason")
            if declaration["enabled"] is not False:
                raise ValueError(f"{sid}:{module}: sample overrides may only exclude with enabled: false")
            if not isinstance(declaration["reason"], str) or not declaration["reason"].strip():
                raise ValueError(f"{sid}:{module}: sample exclusion requires a nonempty reason")
    return overrides


def validate_sample_overrides(config: dict, samples: list[dict]) -> None:
    """Reject malformed overrides and sample names absent from this manifest."""
    overrides = _overrides(config)
    known = {sample["sample_id"] for sample in samples}
    unknown = set(overrides) - known
    if unknown:
        raise ValueError(f"Sample overrides refer to unknown sample IDs: {sorted(unknown)}")


def _decision(action: str, requested: bool, reason: str, warnings=None, **extra) -> dict:
    return {"action": action, "requested": requested,
            "scope_incomplete": action == "withheld" and requested,
            "reason": reason, "warnings": list(warnings or []), **extra}


def plan_sample_modules(config: dict, sample: dict, extend: bool = False) -> dict:
    """Return immutable-by-convention decisions without tools, downloads or I/O.

    ``taxon_phylum`` is an explicit sample declaration. This function neither
    looks up lineage nor derives phylum from a taxon name. Import mode uses the
    existing input-identity checks instead of applying native prerequisites.
    """
    overrides = _overrides(config).get(sample["sample_id"], {})
    decisions = {}
    unknown_taxon = taxonomy_is_missing(sample.get("taxon"))
    for module in ALL_MODULES:
        cfg = config["modules"][module]
        if module == "pathogenfinder2" and not extend:
            decisions[module] = _decision("disabled", False, "extension_not_requested")
            continue
        if not cfg.get("enabled"):
            decisions[module] = _decision("disabled", False, cfg.get("disabled_reason") or "module_disabled")
            continue
        if module in overrides:
            decisions[module] = _decision("withheld", False, "sample_scope_exclusion",
                                          declared_reason=overrides[module]["reason"].strip())
            continue
        warnings = ["taxonomic_context_unavailable"] if unknown_taxon else []
        if cfg.get("mode", "native") == "import":
            decisions[module] = _decision("run", True, "import_requested", warnings)
            continue
        if module == "amrfinder" and not sample.get("amrfinder_organism"):
            warnings.append("organism_specific_coverage_not_configured")
        if module in {"mlst", "crisprcasfinder"}:
            decisions[module] = _decision("withheld", True, "adapter_unavailable", warnings)
            continue
        if module == "icescreen":
            phylum = str(sample.get("taxon_phylum") or "").strip()
            context = {"taxon_phylum": phylum, "taxon_phylum_source": "sample_declaration" if phylum else "not_recorded"}
            reason = None
            if not sample.get("genbank"):
                reason = "matching_genbank_required"
            elif not phylum:
                reason = "phylum_scope_requires_review"
            elif phylum.casefold() not in {"bacillota", "firmicutes"}:
                reason = "outside_declared_module_scope"
            elif cfg.get("phylum") and str(cfg["phylum"]).strip().casefold() not in {"bacillota", "firmicutes"}:
                reason = "module_phylum_configuration_conflict"
            if reason:
                decisions[module] = _decision("withheld", True, reason, warnings, context=context)
                continue
            decisions[module] = _decision("run", True, "declared_phylum_within_module_scope", warnings, context=context)
            continue
        decisions[module] = _decision("run", True, "configured_module_requested", warnings)
    return decisions


def module_decision(plan: dict, sample: dict | str, module: str) -> dict:
    """Read a saved decision, with the former global scope for legacy plans."""
    sid = sample["sample_id"] if isinstance(sample, dict) else sample
    saved = plan.get("module_decisions", {}).get(sid, {}).get(module)
    if saved is not None:
        return copy.deepcopy(saved)
    if module in plan.get("enabled_modules", []):
        return _decision("run", True, "legacy_global_scope")
    cfg = plan.get("config", {}).get("modules", {}).get(module, {})
    reason = ("extension_not_requested" if module == "pathogenfinder2" and not plan.get("options", {}).get("extend")
              else cfg.get("disabled_reason") or "module_disabled")
    return _decision("disabled", False, reason)
