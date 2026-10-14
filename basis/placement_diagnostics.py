"""Descriptive placement diagnostics; no changes to scientific group scores.

The original scorer remains authoritative. Diagnostics partition its reported
likelihood weight into exclusive explanations, retaining uncertain boundaries
between competing labelled groups. Degree-two serialization roots are collapsed
by canonical tip bipartitions when summarizing biological placement splits.
Nothing here estimates a probability of function, phenotype or organism safety.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math

from .placement_scoring import analyze_jplace, _graph, _labels, _component, _edge_groups

SCHEMA = "basis-placement-diagnostics-v1"
POLICY = "exclusive_cut_sides_with_original_weight_split_set_v1"
CATEGORIES = ("assigned_group_interior", "artificial_root_edge",
              "competing_group_boundary", "context_branch", "other_unassigned")
TIE_TOLERANCE = 1e-12


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _close(a, b):
    return math.isclose(a, b, rel_tol=TIE_TOLERANCE, abs_tol=TIE_TOLERANCE)


def _edge_details(adjacency, edges, tips, labels, assignments):
    """Classify every edge once; retain both cut sides and legacy categories."""
    groups, _ = _labels(labels, tips)
    all_nodes = frozenset(adjacency)
    answer = {}
    for number, edge in sorted(edges.items()):
        a, b = edge["nodes"]
        left = _component(adjacency, a, {a, b})
        sides = []
        for nodes in (left, all_nodes - left):
            side_tips = sorted(tip for tip, node in tips.items() if node in nodes)
            counts = Counter(groups[tip] for tip in side_tips if tip in groups)
            context = [tip for tip in side_tips if tip not in groups]
            sides.append({
                "tips": side_tips,
                "labelled_group_counts": dict(sorted(counts.items())),
                "qualified_anchor_count": sum(counts.values()),
                "pure_qualified_group": (next(iter(counts))
                    if len(counts) == 1 and sum(counts.values()) >= 2 else None),
                "context_tip_count": len(context),
                "context_accessions": {tip: labels[tip].get("accessions", [])
                                       for tip in context},
                "all_tip_accessions": {tip: labels[tip].get("accessions", [])
                                       for tip in side_tips},
            })
        sides.sort(key=lambda side: tuple(side["tips"]))
        group, legacy_reason = assignments[number]
        pure = [side["pure_qualified_group"] for side in sides]
        if group is not None:
            category = "assigned_group_interior"
        elif edge["artificial"]:
            category = "artificial_root_edge"
        elif all(pure) and pure[0] != pure[1]:
            category = "competing_group_boundary"
        elif any(side["qualified_anchor_count"] == 0 for side in sides):
            category = "context_branch"
        else:
            category = "other_unassigned"
        bipartition = [side["tips"] for side in sides]
        split_id = "split_" + _digest(json.dumps(bipartition, separators=(",", ":")).encode())
        answer[number] = {
            "edge_num": number,
            "edge_length": edge["length"],
            "category": category,
            "assigned_group": group,
            "legacy_reason": legacy_reason,
            "artificial_root": edge["artificial"],
            "sides": sides,
            "side_counts": [dict(side["labelled_group_counts"]) for side in sides],
            "canonical_split_id": split_id,
        }
    return answer


def _summarize_rows(details, rows, target=0.95):
    """Sum every supplied row, including repeated edge rows; never renormalize.

    Source jplace validation remains the responsibility of analyze_jplace. Its
    current pinned schema disallows duplicate edge rows. This reducer does not
    silently discard them if reused with another validated representation.
    """
    weights = defaultdict(list)
    row_counts = Counter()
    category_weights = {name: [] for name in CATEGORIES}
    for row in rows:
        edge = row["edge_num"]
        if edge not in details:
            raise ValueError("Placement references unknown edge")
        weight = row.get("weight", row.get("like_weight_ratio"))
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise ValueError("Placement weight must be numeric")
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("Placement weight must be finite and nonnegative")
        weights[edge].append(float(weight))
        category_weights[details[edge]["category"]].append(float(weight))
        row_counts[edge] += 1
    reported = math.fsum(w for values in weights.values() for w in values)
    if reported > 1.0 + 1e-6:
        raise ValueError("Placement weight exceeds one")
    edge_records = []
    splits = {}
    for number, detail in sorted(details.items()):
        record = dict(detail)
        record["placement_row_count"] = row_counts[number]
        record["likelihood_weight"] = math.fsum(weights[number])
        edge_records.append(record)
        if detail["artificial_root"]:
            continue
        sid = detail["canonical_split_id"]
        split = splits.setdefault(sid, {
            "canonical_split_id": sid,
            "bipartition": [side["tips"] for side in detail["sides"]],
            "edge_numbers": [], "categories": set(),
            "_weights": [], "placement_row_count": 0,
        })
        split["edge_numbers"].append(number)
        split["categories"].add(detail["category"])
        split["_weights"].extend(weights[number])
        split["placement_row_count"] += row_counts[number]
    for split in splits.values():
        split["likelihood_weight"] = math.fsum(split.pop("_weights"))
        split["categories"] = sorted(split["categories"])
    ordered = sorted(splits.values(), key=lambda row: (-row["likelihood_weight"], row["canonical_split_id"]))
    positive = [row for row in ordered if row["likelihood_weight"] > 0]
    biological = math.fsum(row["likelihood_weight"] for row in ordered)
    selected = []
    cumulative = 0.0
    cutoff = None
    for row in positive:
        if cutoff is not None and not _close(row["likelihood_weight"], cutoff):
            break
        selected.append(row["canonical_split_id"])
        cumulative = math.fsum(splits[sid]["likelihood_weight"] for sid in selected)
        if cutoff is None and cumulative >= target:
            cutoff = row["likelihood_weight"]
    reached = cumulative >= target
    return {
        "exclusive_category_mass": {name: math.fsum(values) for name, values in category_weights.items()},
        "reported_mass": reported,
        "unreported_mass": max(0.0, 1.0 - reported),
        "biological_split_mass": biological,
        "excluded_artificial_root_mass": math.fsum(category_weights["artificial_root_edge"]),
        "max_biological_split_lwr": max((row["likelihood_weight"] for row in ordered), default=0.0),
        "split_set_95": {
            "target_original_mass": target,
            "status": "reached" if reached else "insufficient_retained_mass",
            "selected_split_ids": selected,
            "selected_split_count": len(selected),
            "cumulative_original_mass": cumulative,
            "cutoff_lwr": cutoff,
            "ties_at_cutoff_included": True,
            "tie_absolute_and_relative_tolerance": TIE_TOLERANCE,
            "missing_mass_to_target": max(0.0, target - cumulative),
            "normalization": "original_likelihood_weights_not_renormalized",
            "interpretation": "A descriptive placement split set, not a confidence interval or functional probability.",
        },
        "edges": edge_records,
        "biological_splits": ordered,
    }



def diagnose_jplace(data, labels, query_id, expected_scores=None):
    """Explain validated placement weight without modifying input or decisions.

    If supplied, ``expected_scores`` must exactly match a fresh call to the
    unchanged scorer. Invalid or detached evidence raises ``ValueError``. Native
    integration records diagnostic errors separately from execution and scores.
    No native tools, reference retrieval, root audit or model fitting are run.
    """
    scores = analyze_jplace(data, labels, query_id)
    if expected_scores is not None and scores != expected_scores:
        raise ValueError("Recomputed original placement scores differ from expected_scores")
    adjacency, edges, tips = _graph(data["tree"])
    groups, _ = _labels(labels, tips)
    assignments = _edge_groups(adjacency, edges, tips, groups)[0]
    details = _edge_details(adjacency, edges, tips, labels, assignments)
    result = _summarize_rows(details, scores["placements"])
    masses = result["exclusive_category_mass"]
    if not _close(result["reported_mass"], math.fsum(masses.values())):
        raise ValueError("Exclusive diagnostic category mass is not conserved")
    if not _close(masses["assigned_group_interior"], scores["assigned_mass"]):
        raise ValueError("Diagnostic assignment mass differs from original score")
    if masses["competing_group_boundary"] > 0:
        reason = "competing_boundary_unresolved"
    elif masses["other_unassigned"] > 0:
        reason = "other_unassigned_placement_mass"
    elif masses["context_branch"] > 0:
        reason = "context_branch_unresolved"
    elif masses["artificial_root_edge"] > 0:
        reason = "artificial_root_unresolved"
    elif result["reported_mass"] > 0:
        reason = "reported_mass_assigned_to_group_interiors"
    else:
        reason = "no_positive_reported_placement_weight"
    result.update({
        "schema": SCHEMA, "policy": POLICY, "status": "completed",
        "query_id": query_id, "reason": reason, "function_probability": None,
        "classification_policy_changed": False,
        "original_scoring_policy": scores["policy"],
        "original_scores_verified": expected_scores is not None,
        "original_group_support": dict(scores["group_support"]),
        "original_scores_warnings": list(scores["warnings"]),
        "interpretation": (
            "Descriptive topology diagnostics conditional on the saved reference panel, alignment, tree and evolutionary model. "
            "A competing boundary separates opposing qualified groups and is assigned to neither. "
            "Likelihood weights and the 95% original-weight split set are not functional probabilities or confidence intervals. "
            "Original scientific scores and decisions are unchanged."),
    })
    # Returned metadata shares no mutable containers with caller-owned labels.
    return deepcopy(result)
