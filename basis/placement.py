"""Evidence-gated family placement, independent of functional interpretation."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re

from .progress import emit
from .candidate_labels import qualification_reason
from .util import atomic_json

DEFAULT_REGISTRY = Path(__file__).parent / "resources/placement/registry-v1.json"
SCOPE = ("Placement likelihood weights are conditional on the sampled references, alignment and model. "
         "They are not calibrated functional probabilities, resistance phenotypes or safety verdicts.")


def placement_gate(candidate):
    if not candidate.get("selected"):
        return "candidate_deferred"
    if candidate.get("execution") == "failed":
        return "prior_candidate_analysis_failed"
    if not candidate.get("query", {}).get("path"):
        return "query_not_available"
    if candidate.get("taxonomy_review", {}).get("status") == "review_required":
        return "taxonomic_scope_review_required"
    for finding in candidate.get("source_evidence", []):
        if not qualification_reason(finding):
            continue
        attributes = finding.get("attributes") or {}
        model = str(attributes.get("model_type") or attributes.get("raw", {}).get("Model_type") or "").lower()
        if "variant" in model or "mutation" in model:
            return "mutation_model_requires_mechanism_specific_assessment"
    integrity = candidate.get("analyses", {}).get("integrity", {})
    if integrity.get("status") != "completed" or integrity.get("flags"):
        return "sequence_integrity_review_required"
    return None


def run_family_placements(candidates, config, outdir, runner, catalogue=None):
    """Add the requested layer without replacing previous trees or decisions.

    Biological insufficiency is an explicit abstention. Tool/resource corruption
    is a failure. No result here can upgrade a candidate to confirmed AMR.
    """
    enabled = config.get("family_placement", False)
    if not enabled:
        return []
    from .placement_panels import load_registry, prepare_panel
    from .placement_native import run_placement
    registry = load_registry(config.get("placement_registry") or DEFAULT_REGISTRY) if enabled else None
    outdir = Path(outdir)
    cache = Path(config.get("placement_cache_dir") or outdir / "placement-cache")
    budget = int(config.get("maximum_placement_candidates", 20))
    used, artifacts = 0, []
    for candidate in candidates:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(candidate.get("candidate_id", ""))):
            raise ValueError("Invalid placement candidate identifier")
        result = {"status": "not_assessed", "reason": "family_placement_not_requested",
                  "function_probability": None, "scope": SCOPE}
        directory = outdir / candidate["candidate_id"] / "family-placement"
        reason = placement_gate(candidate) if enabled else None
        if enabled and reason:
            result["reason"] = reason
        elif enabled:
            emit(f"DeepSafe {candidate.get('protein_id')}: auditing competing reference panel")
            try:
                panel = prepare_panel(candidate, registry, cache / "sequences", catalogue=catalogue,
                                      allow_download=config.get("placement_allow_download", True))
                # Keep the complete, immutable panel in an inspectable artifact.
                directory.mkdir(parents=True, exist_ok=True)
                panel_path = directory / "panel.json"
                atomic_json(panel_path, panel)
                artifacts.append(str(panel_path))
                public_panel = {k: v for k, v in panel.items() if k != "members"}
                public_panel["members"] = [{k: v for k, v in m.items() if k != "sequence"}
                                            for m in panel.get("members", [])]
                result["panel"] = public_panel
                result["artifacts"] = [str(panel_path)]
                if not panel.get("adequacy", {}).get("eligible", False):
                    result.update(status="insufficient_evidence", reason="reference_panel_inadequate")
                elif used >= budget:
                    result.update(reason="placement_candidate_budget_exhausted")
                    candidate.update(execution="not_assessed", reason="placement_candidate_budget_exhausted")
                else:
                    used += 1
                    emit(f"DeepSafe {candidate.get('protein_id')}: reference tree and EPA-ng placement ({used}/{budget})")
                    native = run_placement(candidate, panel,
                        {**config, "placement_cache_dir": str(cache / "trees")}, directory, runner)
                    result.update(native)
                    result.update(panel=public_panel, function_probability=None, scope=SCOPE)
                    result["artifacts"] = list(dict.fromkeys([str(panel_path)] + native.get("artifacts", [])))
                    artifacts.extend(native.get("artifacts", []))
            except (ValueError, OSError, RuntimeError, KeyError) as exc:
                result.update(status="failed", reason="placement_execution_failed", error=str(exc))
        candidate.setdefault("analyses", {})["family_placement"] = result
        candidate.setdefault("decision_trace", []).append({"decision": "family_placement",
            "status": result["status"], "reason": result.get("reason"), "function_probability": None})
        if enabled and candidate.get("selected"):
            emit(f"DeepSafe {candidate.get('protein_id')}: placement {result['status']} | {result.get('reason')}")
            if result["status"] == "failed":
                candidate.update(execution="failed", reason=result.get("error") or result.get("reason"))
            result_path = directory / "placement.json"
            atomic_json(result_path, result)
            artifacts.append(str(result_path))
    return list(dict.fromkeys(artifacts))


def placement_summary(candidates):
    selected = [c for c in candidates if c.get("selected")]
    return {"statuses": dict(Counter(c.get("analyses", {}).get("family_placement", {}).get("status", "not_assessed")
                                     for c in selected)),
            "function_probability_calibrated": False, "scope": SCOPE}
