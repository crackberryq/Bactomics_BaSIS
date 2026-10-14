"""Pure planning and descriptive scoring for seed-panel internal holdouts.

This is similarity-blocked internal cross-validation of a source-audited seed
panel. It is not independent functional validation, a clade-disjoint benchmark,
probability calibration, or a prediction of isolate resistance. Frozen research
decision thresholds are specified before the native runner obtains predictions.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import itertools
import json
import math
import re


DEFAULT_POLICY = {
    "cluster_identity": 70.0,
    "cluster_coverage": 80.0,
    "min_anchors_per_group": 2,
    "min_training_references": 4,
    "placement_min_mass": 0.8,
    "placement_min_margin": 0.2,
    "blast_min_identity": 30.0,
    "blast_min_query_coverage": 70.0,
    "blast_min_reference_coverage": 70.0,
    "blast_max_evalue": 1e-5,
}
ABSTAIN = "__abstained__"
FAILED = "__failed__"
SCOPE = "source_audited_seed_panel_internal_cluster_holdout"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def _number(value, name, low=None, high=None, integer=False):
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be numeric")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number) or (low is not None and number < low) or (
            high is not None and number > high) or (integer and not number.is_integer()):
        raise ValueError(f"{name} is outside the permitted range")
    return int(number) if integer else number


def normalize_policy(policy=None):
    """Freeze explicit research thresholds; reject unknown or weakened gates."""
    if policy is not None and not isinstance(policy, dict):
        raise ValueError("Benchmark policy must be an object")
    result = dict(DEFAULT_POLICY)
    if policy:
        unknown = set(policy) - set(result)
        if unknown:
            raise ValueError(f"Unknown benchmark policy keys: {sorted(unknown)}")
        result.update(policy)
    for name in result:
        if name in {"min_anchors_per_group", "min_training_references"}:
            result[name] = _number(result[name], name,
                                   low=2 if name == "min_anchors_per_group" else 4,
                                   integer=True)
        else:
            maximum = (1 if name.startswith("placement_") else
                       1000 if name == "blast_max_evalue" else 100)
            result[name] = _number(result[name], name, low=0, high=maximum)
    return result


def _members(panel):
    if not isinstance(panel, dict) or not isinstance(panel.get("members"), list) or not panel["members"]:
        raise ValueError("Panel requires a nonempty members list")
    members, sequences, group_roles = {}, set(), {}
    for raw in panel["members"]:
        if not isinstance(raw, dict):
            raise ValueError("Panel members must be objects")
        tip = _text(raw.get("tip_id"), "tip_id")
        if tip in members:
            raise ValueError("Duplicate panel tip_id")
        sequence = raw.get("sequence")
        if not isinstance(sequence, str) or not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWYBXZJUO]+", sequence):
            raise ValueError("Panel sequences must already be normalized unaligned proteins")
        sha = hashlib.sha256(sequence.encode("ascii")).hexdigest()
        if sha != raw.get("sequence_sha256"):
            raise ValueError(f"Panel sequence checksum mismatch: {tip}")
        if sha in sequences:
            raise ValueError("Duplicate panel sequence would inflate benchmark counts")
        sequences.add(sha)
        role, group = raw.get("role"), raw.get("group")
        if role not in {"amr", "alternative", "context"}:
            raise ValueError("Invalid panel member role")
        if role == "context":
            if group is not None:
                raise ValueError("Context members must have no functional group")
        else:
            _text(group, "member group")
            if group in {ABSTAIN, FAILED}:
                raise ValueError("Reserved functional group name")
            if group in group_roles and group_roles[group] != role:
                raise ValueError("Functional group has conflicting roles")
            group_roles[group] = role
        if raw.get("label_conflict"):
            raise ValueError("Conflicting source labels cannot enter a seed benchmark")
        accessions = raw.get("accessions", [])
        if not isinstance(accessions, list) or any(not isinstance(a, str) or not a for a in accessions):
            raise ValueError("Panel accessions must be a list of nonempty strings")
        members[tip] = deepcopy(raw)
    if not {"amr", "alternative"}.issubset(set(group_roles.values())):
        raise ValueError("Benchmark panel requires AMR and alternative-function seed groups")
    return members, group_roles


def validate_pairwise_rows(rows, members):
    """Validate BLAST-style protein HSPs; coverage uses ungapped coordinate spans.

    Members may be a tip-ID mapping or a list. Missing pairs mean no reported
    HSP under the runner's recorded search settings, not proven non-homology.
    """
    if not isinstance(rows, list):
        raise ValueError("Pairwise rows must be a list")
    if isinstance(members, list):
        if len({m["tip_id"] for m in members}) != len(members):
            raise ValueError("Duplicate member IDs")
        members = {m["tip_id"]: m for m in members}
    if not isinstance(members, dict):
        raise ValueError("Members must be a mapping or list")
    validated = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("Pairwise alignment row must be an object")
        row = dict(raw)
        for key in ("query_id", "reference_id"):
            if row.get(key) not in members:
                raise ValueError(f"Unknown pairwise {key}: {row.get(key)}")
        row["identity"] = _number(row.get("identity"), "identity", 0, 100)
        row["evalue"] = _number(row.get("evalue"), "evalue", 0)
        row["bitscore"] = _number(row.get("bitscore"), "bitscore", 0)
        row["alignment_length"] = _number(row.get("alignment_length"), "alignment_length", 1, integer=True)
        for prefix, key in (("query", "query_id"), ("reference", "reference_id")):
            length = _number(row.get(prefix + "_length"), prefix + "_length", 1, integer=True)
            if length != len(members[row[key]]["sequence"]):
                raise ValueError(f"Pairwise {prefix} length disagrees with panel sequence")
            start = _number(row.get(prefix + "_start"), prefix + "_start", 1, length, integer=True)
            end = _number(row.get(prefix + "_end"), prefix + "_end", start, length, integer=True)
            if row["alignment_length"] < end - start + 1:
                raise ValueError("Alignment length is shorter than its sequence span")
            row[prefix + "_length"] = length
            row[prefix + "_start"] = start
            row[prefix + "_end"] = end
            row[prefix + "_coverage"] = 100 * (end - start + 1) / length
        validated.append(row)
    return validated


def build_protocol(panel, pairwise_rows, policy=None):
    """Freeze label-blind similarity components and leave-one-component-out folds."""
    policy = normalize_policy(policy)
    members, group_roles = _members(panel)
    rows = validate_pairwise_rows(pairwise_rows, members)
    parent = {tip: tip for tip in members}

    def find(tip):
        while parent[tip] != tip:
            parent[tip] = parent[parent[tip]]
            tip = parent[tip]
        return tip

    edges = {}
    for row in rows:
        q, r = row["query_id"], row["reference_id"]
        if q == r:
            continue
        if (row["identity"] >= policy["cluster_identity"] and
                min(row["query_coverage"], row["reference_coverage"]) >= policy["cluster_coverage"]):
            edge = tuple(sorted((q, r)))
            # Undirected relation: either direction may establish an edge.
            edges[edge] = True
            a, b = sorted((find(q), find(r)))
            parent[b] = a
    components = {}
    for tip in sorted(members):
        components.setdefault(find(tip), []).append(tip)
    clusters, folds, queries = [], [], []
    all_ids = set(members)
    policy_sha = _digest(policy)
    for ids in sorted(components.values(), key=lambda ids: sorted(members[t]["sequence_sha256"] for t in ids)):
        hashes = sorted(members[t]["sequence_sha256"] for t in ids)
        cluster_id = "cluster_" + _digest(hashes)[:20]
        query_ids = [t for t in ids if members[t]["role"] != "context"]
        cluster = {"cluster_id": cluster_id, "member_ids": ids, "query_ids": query_ids,
                   "member_count": len(ids), "labelled_query_count": len(query_ids)}
        clusters.append(cluster)
        if not query_ids:
            continue
        refs = sorted(all_ids - set(ids))
        counts = {group: sum(members[t]["group"] == group for t in refs) for group in sorted(group_roles)}
        reasons = ["insufficient_remaining_anchors:" + group for group, n in counts.items()
                   if n < policy["min_anchors_per_group"]]
        if len(refs) < policy["min_training_references"]:
            reasons.append("insufficient_remaining_unique_references")
        fold_id = "fold_" + _digest({"cluster": cluster_id, "references": sorted(
            members[t]["sequence_sha256"] for t in refs), "policy": policy_sha})[:20]
        fold = {"fold_id": fold_id, "cluster_id": cluster_id, "held_out_ids": ids,
                "reference_ids": refs, "query_ids": query_ids,
                "training_group_counts": counts, "status": "held" if reasons else "ready",
                "reasons": reasons,
                "adequacy": {"eligible": not reasons, "reasons": reasons,
                             "distinct_qualified_anchors_by_group": counts,
                             "required_groups": sorted(group_roles),
                             "min_anchors_per_group": policy["min_anchors_per_group"],
                             "distinct_sequences": len(refs)},
                "exclusion_audit": {"excluded_entire_similarity_component": True,
                                    "context_members_participate_in_clustering": True,
                                    "exact_sequence_disjoint": True,
                                    "qualifying_cross_partition_edges": 0,
                                    "clade_disjointness": "not_assessed"}}
        folds.append(fold)
        for tip in query_ids:
            m = members[tip]
            queries.append({"query_id": tip, "sequence_sha256": m["sequence_sha256"],
                            "cluster_id": cluster_id, "fold_id": fold_id, "true_group": m["group"],
                            "role": m["role"], "accessions": m.get("accessions", [])})
    return {"schema": "basis-placement-seed-benchmark-protocol-v1", "scope": SCOPE,
            "panel_id": panel.get("panel_id"), "panel_sha256": panel.get("panel_sha256"),
            "policy": policy, "policy_sha256": policy_sha,
            "decision_policy_status": "predeclared_research_thresholds_not_calibrated",
            "clustering_threshold_basis": (
                "Primary 70% identity and 80% bilateral span coverage were specified from label-blind seed-sequence geometry before prediction outcomes; no threshold optimization is performed."
                if policy == DEFAULT_POLICY else
                "Explicit alternative research policy recorded before prediction; no threshold optimization is performed."),
            "members": [{k: deepcopy(m[k]) for k in ("tip_id", "sequence_sha256", "group", "role", "accessions")
                         if k in m} for _, m in sorted(members.items())],
            "clusters": clusters, "folds": folds, "queries": queries,
            "audit": {"unique_sequence_count": len(members), "labelled_query_count": len(queries),
                      "context_count": sum(m["role"] == "context" for m in members.values()),
                      "pairwise_row_count": len(rows), "pairwise_rows_sha256": _digest(sorted(
                          rows, key=lambda r: (r["query_id"], r["reference_id"], -r["bitscore"], r["query_start"], r["reference_start"]))),
                      "qualifying_similarity_edges": [list(e) for e in sorted(edges)],
                      "clustering_uses_function_labels": False,
                      "clade_disjointness": "not_assessed",
                      "fold_eligibility_policy": "same_minimum_panel_gate_for_all_methods"},
            "warnings": ["small_seed_panel_internal_validation_only", "registry_labels_are_not_independent_test_truth"],
            "limitations": _limitations(), "function_probability": None}


def _limitations():
    return [
        "Source-audited seed-panel labels are reused as scoring truth; this is internal cross-validation, not independent functional validation.",
        "Similarity components are label-blind connected components, including context proteins. They are not independently established evolutionary clades.",
        "No qualifying reported similarity edge crosses a fold boundary; absent search hits do not establish absence of homology.",
        "Both methods use the same minimum remaining-reference gate; results describe this shared eligible-panel comparison, not unrestricted BLAST performance.",
        "Research thresholds are frozen before prediction and are not calibrated functional probabilities or resistance-phenotype decision thresholds.",
        "Small correlated seed panels support descriptive counts only; no confidence intervals or generalization claims are inferred.",
    ]


def placement_call(scores, policy=None):
    """Apply a predeclared raw placement-mass decision rule without calibration."""
    policy = normalize_policy(policy)
    if not isinstance(scores, dict) or not isinstance(scores.get("group_support"), dict):
        raise ValueError("Placement scores require group_support")
    if scores.get("status", "completed") != "completed":
        raise ValueError("Only completed placement scores can produce a decision")
    groups = []
    for group, value in scores["group_support"].items():
        _text(group, "placement group")
        if group in {ABSTAIN, FAILED}:
            raise ValueError("Reserved placement group")
        groups.append((group, _number(value, "placement mass", 0, 1 + 1e-6)))
    if math.fsum(m for _, m in groups) > 1 + 1e-6:
        raise ValueError("Placement group mass exceeds one")
    groups.sort(key=lambda item: (-item[1], item[0]))
    best, mass = groups[0] if groups else (None, 0.0)
    second = groups[1][1] if len(groups) > 1 else 0.0
    margin = mass - second
    reason, predicted = "placement_thresholds_satisfied", best
    if len(groups) < 2:
        reason, predicted = "fewer_than_two_scored_groups", None
    elif math.isclose(mass, second, abs_tol=1e-12, rel_tol=1e-12):
        reason, predicted = "tied_placement_groups", None
    elif mass + 1e-12 < policy["placement_min_mass"]:
        reason, predicted = "placement_mass_below_threshold", None
    elif margin + 1e-12 < policy["placement_min_margin"]:
        reason, predicted = "placement_margin_below_threshold", None
    return {"status": "completed", "predicted_group": predicted, "abstained": predicted is None,
            "reason": reason, "metrics": {"best_group": best, "best_group_mass": mass,
                "runner_up_mass": second, "margin": margin, "group_support": dict(groups)},
            "function_probability": None}


def blast_call(query_id, training_members, rows, policy=None):
    """Call the group of the best eligible BLAST HSP, with explicit tie abstention.

    Rows must first be validated against the complete seed panel by the protocol
    planner. Only declared training-member rows are eligible for this baseline.
    Alignment thresholds are independent of the stricter clustering threshold.
    """
    policy = normalize_policy(policy)
    _text(query_id, "query_id")
    if isinstance(training_members, list):
        if len({m["tip_id"] for m in training_members}) != len(training_members):
            raise ValueError("Duplicate training member IDs")
        training_members = {m["tip_id"]: m for m in training_members}
    if not isinstance(training_members, dict):
        raise ValueError("Training members must be a mapping or list")
    if query_id in training_members:
        raise ValueError("Query cannot be its own training reference")
    accepted = []
    for row in rows:
        if row.get("query_id") != query_id or row.get("reference_id") not in training_members:
            continue
        identity = _number(row.get("identity"), "identity", 0, 100)
        bit = _number(row.get("bitscore"), "bitscore", 0)
        evalue = _number(row.get("evalue"), "evalue", 0)
        spans = {}
        for prefix in ("query", "reference"):
            length = _number(row.get(prefix + "_length"), prefix + "_length", 1, integer=True)
            start = _number(row.get(prefix + "_start"), prefix + "_start", 1, length, integer=True)
            end = _number(row.get(prefix + "_end"), prefix + "_end", start, length, integer=True)
            spans[prefix] = 100 * (end - start + 1) / length
        if (evalue <= policy["blast_max_evalue"] and
                identity >= policy["blast_min_identity"] and spans["query"] >= policy["blast_min_query_coverage"]
                and spans["reference"] >= policy["blast_min_reference_coverage"]):
            accepted.append((bit, row["reference_id"], identity, spans))
    best = max((item[0] for item in accepted), default=None)
    ties = [item for item in accepted if math.isclose(item[0], best, rel_tol=1e-12, abs_tol=1e-12)] if best is not None else []
    labels = {training_members[t[1]].get("group") if training_members[t[1]].get("role") != "context" else None for t in ties}
    reason, predicted = "no_eligible_blast_alignment", None
    if ties:
        if len(labels) > 1:
            reason = "best_bitscore_tie_across_groups_or_context"
        elif None in labels:
            reason = "best_blast_reference_is_unlabelled_context"
        else:
            predicted = next(iter(labels))
            reason = "best_eligible_blast_group"
    return {"status": "completed", "predicted_group": predicted, "abstained": predicted is None,
            "reason": reason, "metrics": {"best_bitscore": best,
                "best_reference_ids": sorted({t[1] for t in ties}),
                "eligible_alignment_count": len(accepted)}, "function_probability": None}


def _metrics(rows):
    confusion, per_cluster = {}, {}
    total = len(rows)
    accepted = correct = failed = held = completed_abstained = 0
    for row in rows:
        truth, status = row["true_group"], row["status"]
        predicted = FAILED if status == "failed" else (row["predicted_group"] or ABSTAIN)
        confusion.setdefault(truth, Counter())[predicted] += 1
        counts = per_cluster.setdefault(row["cluster_id"], Counter())
        counts["planned"] += 1
        if status == "failed":
            failed += 1
            counts["failed"] += 1
        elif status == "held":
            held += 1
            counts["held"] += 1
        elif row["predicted_group"] is None:
            completed_abstained += 1
            counts["completed_abstained"] += 1
        else:
            accepted += 1
            correct += predicted == truth
            counts["accepted"] += 1
            counts["correct"] += int(predicted == truth)
    per_group = {}
    for group in sorted(confusion):
        n = sum(confusion[group].values())
        tp = confusion[group].get(group, 0)
        called_group = sum(c.get(group, 0) for c in confusion.values())
        per_group[group] = {"planned_count": n, "correct_count": tp, "recall_all_planned": tp / n,
                            "precision_accepted": tp / called_group if called_group else None}
    return {"planned_count": total, "completed_count": total - failed - held,
            "failed_count": failed, "held_count": held,
            "completed_abstained_count": completed_abstained,
            "biological_abstained_count": held + completed_abstained,
            "accepted_count": accepted, "correct_count": correct,
            "incorrect_accepted_count": accepted - correct,
            "correct_fraction_all_planned": correct / total if total else None,
            "accepted_accuracy": correct / accepted if accepted else None,
            "coverage_all_planned": accepted / total if total else None,
            "confusion": {g: dict(sorted(c.items())) for g, c in sorted(confusion.items())},
            "per_group": per_group,
            "per_cluster": {g: dict(sorted(c.items())) for g, c in sorted(per_cluster.items())}}


def aggregate(rows):
    """Summarize every planned query, separating execution errors and abstentions.

    Each method must have exactly one row for the same planned query IDs. Paired
    comparisons additionally report the subset completed by both methods, with
    decision-rule abstentions retained. No probability metrics or CIs are fitted.
    """
    if not isinstance(rows, list) or not rows:
        raise ValueError("Benchmark evaluation requires nonempty result rows")
    methods, metadata = {}, {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("Benchmark result rows must be objects")
        method = _text(raw.get("method"), "method")
        query = _text(raw.get("query_id"), "query_id")
        cluster = _text(raw.get("cluster_id"), "cluster_id")
        truth = _text(raw.get("true_group"), "true_group")
        if truth in {ABSTAIN, FAILED}:
            raise ValueError("Reserved true_group")
        if query in metadata and metadata[query] != (truth, cluster):
            raise ValueError("Methods disagree on query truth or cluster membership")
        metadata[query] = truth, cluster
        method_rows = methods.setdefault(method, {})
        if query in method_rows:
            raise ValueError("Duplicate method/query result")
        status = raw.get("status")
        if status not in {"completed", "held", "failed"}:
            raise ValueError("Result status must be completed, held or failed")
        predicted = raw.get("predicted_group")
        if predicted is not None:
            _text(predicted, "predicted_group")
            if predicted in {ABSTAIN, FAILED} or status != "completed":
                raise ValueError("Only completed predictions can name a functional group")
        if "abstained" in raw:
            if status == "failed":
                if raw["abstained"] is not None and raw["abstained"] is not False:
                    raise ValueError("Execution failure is not a biological abstention")
            elif type(raw["abstained"]) is not bool or raw["abstained"] != (predicted is None):
                raise ValueError("abstained must agree with null predicted_group")
        if "elapsed_seconds" in raw:
            _number(raw["elapsed_seconds"], "elapsed_seconds", 0)
        method_rows[query] = raw
    expected = set(metadata)
    if any(set(items) != expected for items in methods.values()):
        raise ValueError("Every method must account for every planned query")
    metrics = {method: _metrics(list(items.values())) for method, items in sorted(methods.items())}
    paired = []
    for a, b in itertools.combinations(sorted(methods), 2):
        ids = sorted(q for q in expected if methods[a][q]["status"] == methods[b][q]["status"] == "completed")
        paired.append({"methods": [a, b], "scope": "same_queries_completed_by_both_including_decision_abstentions",
                       "query_ids": ids, "query_count": len(ids), "excluded_planned_count": len(expected) - len(ids),
                       "metrics": {m: _metrics([methods[m][q] for q in ids]) for m in (a, b)},
                       "accepted_group_agreement_count": sum(
                           methods[a][q]["predicted_group"] is not None and
                           methods[a][q]["predicted_group"] == methods[b][q]["predicted_group"] for q in ids)})
    return {"schema": "basis-placement-seed-benchmark-evaluation-v1", "scope": SCOPE,
            "planned_query_count": len(expected), "cluster_count": len({c for _, c in metadata.values()}),
            "methods": metrics, "paired_comparisons": paired,
            "abstention_column": ABSTAIN, "failure_column": FAILED,
            "warnings": ["small_seed_panel_internal_validation_only", "counts_are_not_independent_population_estimates"],
            "limitations": _limitations(), "function_probability": None,
            "calibration": {"status": "not_assessed", "reason": "uncalibrated_research_scores"}}
