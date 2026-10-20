"""Describe configured family routing and saved execution; never infer function.

The existing profile and placement routers remain the authorities. This module
records their coverage and exposes it without running a search, downloading a
reference, changing an analysis gate, or assigning a functional probability.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path

SCHEMA = "basis-deep-workflow-v1"
STAGES = ("integrity", "context", "domains", "references", "phylogeny",
          "prosite_patterns", "prosite_profiles", "motifs", "family_placement")
SCOPE = ("Configured coverage and execution status only. A matched family, completed "
         "analysis or tree does not establish function, resistance or organism safety. "
         "Placement weights are not calibrated functional probabilities.")


def record_family_routes(candidates, config):
    """Record routing from the same snapshotted inputs used by the analyses."""
    from .references import validate_profiles
    from .placement import DEFAULT_REGISTRY
    from .placement_panels import load_registry, _route

    profiles, errors = validate_profiles(Path(config["profiles_dir"]) if config.get("profiles_dir") else None)
    if errors:
        raise ValueError("Invalid profile registry: " + "; ".join(errors))
    enabled = bool(config.get("family_placement", False))
    registry = load_registry(config.get("placement_registry") or DEFAULT_REGISTRY) if enabled else None
    inventory = {
        "schema": "basis-deep-family-inventory-v1", "scope": SCOPE,
        "profiles": [{"id": p["profile_id"], "validation_status": p.get("validation_status"),
                      "sha256": p.get("registry_sha256")} for p in profiles],
        "placement_enabled": enabled, "placement_registry": None,
    }
    if registry is not None:
        inventory["placement_registry"] = {
            "id": registry["registry_id"], "sha256": registry["_registry_sha256"],
            "panels": [{"id": p["panel_id"], "validation_status": p.get("validation_status"),
                        "reference_count": len(p["references"])} for p in registry["panels"]],
        }
    for candidate in candidates:
        if not candidate.get("selected"):
            candidate["family_routing"] = {"profile": {"status": "deferred", "reason": "candidate_deferred"},
                                           "placement": {"route_status": "deferred", "reason": "candidate_deferred"}}
            continue
        profile = candidate.get("profile") or {}
        reason = profile.get("reason")
        status = ("ambiguous" if reason == "multiple_matching_profiles" else
                  "no_configured_family" if reason == "no_family_profile" else
                  "matched" if profile.get("profile_id") and profile.get("profile_id") != "generic" else "not_recorded")
        # This repeats the existing exact profile alias comparison only to make
        # ambiguity inspectable; it never selects or substitutes a profile.
        names = {str(candidate.get(k) or "").casefold() for k in ("gene", "family")}
        names.update(str(v).casefold() for v in candidate.get("family_labels", []))
        matched = sorted(p["profile_id"] for p in profiles
                         if names.intersection(a.casefold() for a in p["candidate_aliases"]))
        profile_route = {"id": profile.get("profile_id"), "status": status,
                         "reason": reason, "matched_profiles": matched,
                         "validation_status": profile.get("validation_status"),
                         "sha256": profile.get("registry_sha256")}
        placement_route = {"route_status": "not_requested", "reason": "family_placement_not_requested",
                           "matched_panels": [], "registry_id": None, "registry_sha256": None}
        if registry is not None:
            panels, trace = _route(candidate, registry)
            placement_route.update(
                route_status="matched" if len(panels) == 1 else "ambiguous" if panels else "no_configured_family",
                reason=None if len(panels) == 1 else "ambiguous_family_routing" if panels else "no_supported_family_panel",
                matched_panels=trace["matched_panels"], registry_id=registry["registry_id"],
                registry_sha256=registry["_registry_sha256"], policy=trace["policy"],
                qualified_labels=trace["qualified_labels"])
        candidate["family_routing"] = {"profile": profile_route, "placement": placement_route}
    return inventory


def candidate_workflow(candidate):
    """Summarize recorded decisions without treating absent evidence as negative."""
    routing = candidate.get("family_routing") or {}
    profile = deepcopy(routing.get("profile") or {"id": None, "status": "not_recorded", "reason": "routing_not_recorded"})
    placement = deepcopy(routing.get("placement") or {"route_status": "not_recorded", "reason": "routing_not_recorded"})
    route_reason = placement.get("reason")
    analyses = candidate.get("analyses") or {}
    native = analyses.get("family_placement") or {}
    panel = native.get("panel") or {}
    placement.update(status=native.get("status", "not_requested" if placement.get("route_status") == "not_requested" else "not_recorded"),
                     reason=native.get("reason") or route_reason,
                     route_reason=route_reason, panel_id=panel.get("panel_id"),
                     adequacy_reasons=deepcopy((panel.get("adequacy") or {}).get("reasons", [])))
    if not routing:
        status, reason = "not_recorded", "routing_not_recorded"
    elif not candidate.get("selected"):
        status, reason = "deferred", "candidate_deferred"
    elif profile["status"] == "ambiguous" or placement["route_status"] == "ambiguous":
        status, reason = "ambiguous", "multiple_matching_family_definitions"
    elif profile["status"] == "matched" or placement["route_status"] == "matched":
        status, reason = "configured", "configured_family_matched"
    else:
        status, reason = "no_configured_family", "no_configured_family_matched"
    stages = []
    for name in STAGES:
        value = analyses.get(name) or {}
        stages.append({"name": name, "status": value.get("status", "not_recorded"),
                       "reason": value.get("reason")})
    assessment = candidate.get("assessment") or {}
    return {"schema": SCHEMA, "scope": SCOPE, "candidate_id": candidate.get("candidate_id"),
            "protein_id": candidate.get("protein_id"), "sample_id": candidate.get("sample_id"),
            "selected": bool(candidate.get("selected")), "selection_queue": candidate.get("selection_queue"),
            "routing_status": status, "routing_reason": reason, "profile": profile, "placement": placement,
            "stages": stages, "execution": candidate.get("execution"),
            "interpretation": assessment.get("interpretation"), "followup": assessment.get("followup")}


def summarize(rows):
    selected = [r for r in rows if r.get("selected") is True]
    return {"candidates": len(rows), "selected": len(selected),
            "not_selected": len(rows) - len(selected),
            "deferred": sum(r.get("execution") == "deferred" for r in rows),
            "selected_routing": dict(Counter(r.get("routing_status", "not_recorded") for r in selected)),
            "selected_execution": dict(Counter(r.get("execution", "not_recorded") for r in selected)),
            "selected_profile_coverage": dict(Counter(r.get("profile", {}).get("status", "not_recorded") for r in selected)),
            "selected_placement_coverage": dict(Counter(r.get("placement", {}).get("route_status", "not_recorded") for r in selected)),
            "function_probability_calibrated": False}
