"""Pure state adapters for candidate-sized Snakemake jobs.

No new scientific policies live here. Native handlers and all eligibility gates
remain the same functions used by the compatibility stage API. Budget offsets
are assigned in existing candidate order before parallel native work starts.
"""
from __future__ import annotations

import copy
from pathlib import Path

from . import deep_stages as stages

CANDIDATE_STAGES = ("domains", "phylogeny", "patterns", "profiles", "motifs", "placement")
FRAGMENT_SCHEMA = "basis-deepsafe-candidate-stage-v1"


def candidate_ids(state):
    return [candidate["candidate_id"] for candidate in state["candidates"] if candidate.get("selected")]


def _candidate(state, candidate_id):
    matches = [candidate for candidate in state["candidates"] if candidate["candidate_id"] == candidate_id]
    if len(matches) != 1 or not matches[0].get("selected"):
        raise ValueError("Unknown or unselected DeepSafe candidate: " + candidate_id)
    return matches[0]


def run_candidate_stage(stage, sample, records, config, outdir, runner, state,
                        candidate_id, *, budget_start=0, prepared_panel=None):
    """Run one existing layer on one selected candidate, leaving input immutable."""
    if stage not in CANDIDATE_STAGES:
        raise ValueError("Not a candidate stage: " + stage)
    if state.get("identity") != stages._identity(sample, records, config, outdir):
        raise ValueError("Candidate state does not match the execution identity")
    candidate = _candidate(state, candidate_id)
    working = copy.deepcopy(state)
    working["candidates"] = [copy.deepcopy(candidate)]
    working["artifacts"] = []
    working["notes"] = []
    working["tree_budget"] = {"used": int(budget_start)}
    effective = dict(config)
    effective.update(state.get("internal", {}))
    outdir = Path(outdir).resolve()
    if stage in {"patterns", "profiles"}:
        # Each scan owns a separate directory; retries cannot touch other
        # candidates' completed outputs or a previous layer's files.
        scan_root = outdir / candidate_id / "candidate-scans"
        scan_root.mkdir(parents=True, exist_ok=True)
        stages._HANDLERS[stage](sample, records, effective, scan_root, runner, working)
    elif stage == "placement":
        from .placement import run_family_placements
        catalog = stages._catalog(effective, working)
        working["artifacts"].extend(run_family_placements(
            working["candidates"], effective, outdir, runner, catalogue=catalog,
            prepared_panels=({candidate_id: prepared_panel} if prepared_panel is not None else None),
            initial_used=budget_start))
    else:
        stages._HANDLERS[stage](sample, records, effective, outdir, runner, working)
    return {"schema": FRAGMENT_SCHEMA, "identity": copy.deepcopy(state["identity"]),
            "stage": stage, "candidate_id": candidate_id,
            "input_candidate_sha256": stages._digest(candidate),
            "candidate": working["candidates"][0], "artifacts": working["artifacts"],
            "notes": working["notes"], "tree_budget": working["tree_budget"]}


def apply_fragment(state, fragment, stage=None):
    """Validate and apply a candidate output to an immutable copy of its input."""
    expected_stage = stage or fragment.get("stage")
    if (fragment.get("schema") != FRAGMENT_SCHEMA or fragment.get("identity") != state.get("identity")
            or fragment.get("stage") != expected_stage):
        raise ValueError("Candidate fragment identity or stage mismatch")
    candidate = _candidate(state, fragment["candidate_id"])
    if fragment.get("input_candidate_sha256") != stages._digest(candidate):
        raise ValueError("Candidate fragment input checksum mismatch")
    if fragment.get("candidate", {}).get("candidate_id") != candidate["candidate_id"]:
        raise ValueError("Candidate fragment changed the candidate identifier")
    result = copy.deepcopy(state)
    result["candidates"] = [copy.deepcopy(fragment["candidate"]) if row["candidate_id"] == candidate["candidate_id"]
                            else row for row in result["candidates"]]
    result["artifacts"] = list(dict.fromkeys(result["artifacts"] + fragment.get("artifacts", [])))
    result["notes"].extend(fragment.get("notes", []))
    return result


def merge_fragments(state, stage, fragments):
    fragments = list(fragments)
    expected = candidate_ids(state)
    received = [fragment.get("candidate_id") for fragment in fragments]
    if len(received) != len(set(received)) or set(received) != set(expected):
        raise ValueError("Candidate stage requires exactly one fragment for every selected candidate")
    by_id = {fragment["candidate_id"]: fragment for fragment in fragments}
    result = copy.deepcopy(state)
    for candidate_id in expected:
        result = apply_fragment(result, by_id[candidate_id], stage)
    return result


def tree_budget_plan(state, config):
    """Reserve native tree slots using exactly the existing pre-native gates."""
    from .deep_trees import eligible, bounded_inputs
    catalog = stages._catalog(config, state)
    used = 0
    offsets = {}
    for candidate in state["candidates"]:
        if not candidate.get("selected"):
            continue
        offsets[candidate["candidate_id"]] = used
        if candidate.get("execution") != "pending" or not config.get("auto_phylogeny", False):
            continue
        if stages._deep.reuse_review(candidate, config) is not None or eligible(candidate):
            continue
        maximum = int(config.get("maximum_tree_candidates", 6))
        if maximum < 0:
            raise ValueError("maximum_tree_candidates must be >=0")
        if used >= maximum:
            continue
        sequences, _, _ = bounded_inputs(candidate, catalog)
        if len(set(sequences.values())) >= 4:
            used += 1
    return {"offsets": offsets, "used": used}


def placement_budget_plan(state, config, outdir):
    """Prepare existing panels and assign slots without running native analyses."""
    from .placement import DEFAULT_REGISTRY, placement_gate
    from .placement_panels import load_registry, prepare_panel
    if not config.get("family_placement", False):
        return {"offsets": {cid: 0 for cid in candidate_ids(state)}, "panels": {}, "used": 0}
    registry = load_registry(config.get("placement_registry") or DEFAULT_REGISTRY)
    catalog = stages._catalog(config, state)
    cache = Path(config.get("placement_cache_dir") or Path(outdir) / "placement-cache")
    maximum = int(config.get("maximum_placement_candidates", 20))
    used, offsets, panels = 0, {}, {}
    for candidate in state["candidates"]:
        if not candidate.get("selected"):
            continue
        cid = candidate["candidate_id"]
        offsets[cid] = used
        if placement_gate(candidate):
            continue
        panel = prepare_panel(candidate, registry, cache / "sequences", catalogue=catalog,
                              allow_download=config.get("placement_allow_download", True))
        panels[cid] = panel
        if panel.get("adequacy", {}).get("eligible", False) and used < maximum:
            used += 1
    return {"offsets": offsets, "panels": panels, "used": used}


def finalize_state(sample, records, config, outdir, runner, state):
    """Fill deferred-layer statuses and call the unchanged final evidence writer."""
    result = copy.deepcopy(state)
    deferred = [candidate for candidate in result["candidates"] if not candidate.get("selected")]
    if deferred:
        view = {**result, "candidates": deferred, "artifacts": []}
        for stage in ("patterns", "profiles", "motifs", "placement"):
            stages._HANDLERS[stage](sample, records, dict(config), Path(outdir), runner, view)
    stages._finalize(sample, records, config, Path(outdir), runner, result)
    result.update(stage="finalize", completed_stages=list(stages.STAGES))
    return result
