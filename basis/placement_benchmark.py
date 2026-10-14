"""Audit and evaluate a frozen, independently labelled placement benchmark.

This module neither learns a classifier nor calibrates placement weights.
Manifest ``true_group`` labels must be independently established functional
ground truth, not imported automatically from the screening/reference panels.

Manifest schema (version 1):
  evaluation_split: "test"
  policy: {threshold_source: "predeclared" | "calibration",
           threshold_frozen: true, description: optional text}
  cases: [{query_id, sequence_sha256, cluster_id, clade_id,
           split: "train" | "calibration" | "test", true_group}]

Prediction bundle schema (version 1):
  method_id: descriptive identifier
  score_type: "uncalibrated_placement_likelihood_weight_mass"
  predictions: [{query_id, predicted_group: text | null, abstained: boolean,
                 group_support: {group: raw likelihood-weight mass},
                 reference_query_ids: [IDs of manifest training cases]}]

Optional ``experiment_id`` (manifest) and ``baseline_id`` (predictions) are
reported as provenance only.  Every test case needs exactly one prediction,
including cases for which the method abstains.  All reference-panel members,
including unlabelled context sequences, must be registered as training cases;
``true_group`` may be null only for these non-test context/training cases.
The caller must record *every* reference used, not only matching references.
Sequence, cluster and clade IDs are independently supplied audit units.

CLI: python -m basis.placement_benchmark --manifest cases.json \
     --predictions calls.json --out evaluation.json
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sys


SCORE_TYPE = "uncalibrated_placement_likelihood_weight_mass"
ABSTAIN = "__abstained__"


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _group(value, name, nullable=False):
    if value is None and nullable:
        return None
    value = _text(value, name)
    if value == ABSTAIN:
        raise ValueError(f"{ABSTAIN} is reserved for abstentions")
    return value


def _manifest(manifest):
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise ValueError("Expected benchmark manifest schema_version 1")
    if manifest.get("evaluation_split", "test") != "test":
        raise ValueError("This evaluator requires an untouched test split")
    policy = manifest.get("policy")
    if (not isinstance(policy, dict) or policy.get("threshold_frozen") is not True
            or policy.get("threshold_source") not in ("predeclared", "calibration")):
        raise ValueError("A frozen predeclared or calibration-derived decision policy is required")
    raw = manifest.get("cases")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Manifest requires nonempty benchmark cases")
    cases, sequences = {}, {}
    fold_units = {"cluster_id": {}, "clade_id": {}}
    for case in raw:
        if not isinstance(case, dict):
            raise ValueError("Each benchmark case must be an object")
        query = _text(case.get("query_id"), "query_id")
        if query in cases:
            raise ValueError("Duplicate manifest query_id")
        split = case.get("split")
        if split not in ("train", "calibration", "test"):
            raise ValueError("Invalid benchmark split")
        sequence = case.get("sequence_sha256")
        if not isinstance(sequence, str) or not re.fullmatch(r"[0-9a-f]{64}", sequence):
            raise ValueError("sequence_sha256 must be a lowercase SHA256 digest")
        if sequence in sequences:
            previous = sequences[sequence]
            if previous != split:
                raise ValueError("Exact sequence leakage across benchmark folds")
            raise ValueError("Duplicate sequence within benchmark fold would inflate evaluation")
        sequences[sequence] = split
        for name, assignment in fold_units.items():
            unit = _text(case.get(name), name)
            if unit in assignment and assignment[unit] != split:
                raise ValueError(f"{name} leakage across benchmark folds")
            assignment[unit] = split
        _group(case.get("true_group"), "true_group", nullable=(split != "test"))
        cases[query] = case
    split_counts = Counter(case["split"] for case in cases.values())
    if not split_counts["train"] or not split_counts["test"]:
        raise ValueError("Benchmark requires both training references and test cases")
    test_groups = {case["true_group"] for case in cases.values() if case["split"] == "test"}
    if len(test_groups) < 2:
        raise ValueError("Test holdout requires at least two independently labelled functional groups")
    if policy["threshold_source"] == "calibration" and not split_counts["calibration"]:
        raise ValueError("Calibration-derived policy requires a separate calibration split")
    return cases, split_counts, test_groups


def _predictions(bundle, cases):
    if not isinstance(bundle, dict) or type(bundle.get("schema_version")) is not int or bundle["schema_version"] != 1:
        raise ValueError("Expected prediction bundle schema_version 1")
    _text(bundle.get("method_id"), "method_id")
    if bundle.get("score_type") != SCORE_TYPE:
        raise ValueError("This evaluator accepts uncalibrated placement mass only")
    predictions = bundle.get("predictions")
    if not isinstance(predictions, list):
        raise ValueError("Predictions must be a list")
    seen = set()
    for row in predictions:
        if not isinstance(row, dict):
            raise ValueError("Each prediction must be an object")
        query = _text(row.get("query_id"), "prediction query_id")
        if query in seen:
            raise ValueError("Duplicate prediction for test query")
        seen.add(query)
        if query not in cases or cases[query]["split"] != "test":
            raise ValueError("Predictions must cover only declared test queries")
        abstained = row.get("abstained")
        if type(abstained) is not bool:
            raise ValueError("abstained must be a boolean")
        predicted = _group(row.get("predicted_group"), "predicted_group", nullable=True)
        if abstained != (predicted is None):
            raise ValueError("Abstention must agree with null predicted_group")
        support = row.get("group_support")
        if not isinstance(support, dict):
            raise ValueError("group_support must be an object")
        values = []
        for group, mass in support.items():
            _group(group, "support group")
            if type(mass) not in (int, float):
                raise ValueError("Group support must contain finite numeric masses")
            try:
                mass = float(mass)
            except (OverflowError, ValueError) as exc:
                raise ValueError("Group support must contain finite numeric masses") from exc
            if not math.isfinite(mass) or not 0 <= mass <= 1 + 1e-6:
                raise ValueError("Group support must be finite raw mass between zero and one")
            values.append(mass)
        if math.fsum(values) > 1 + 1e-6:
            raise ValueError("Group support mass exceeds one")
        if predicted is not None and (predicted not in support or support[predicted] <= 0):
            raise ValueError("A non-abstained prediction requires positive group support")
        references = row.get("reference_query_ids")
        if (not isinstance(references, list)
                or any(not isinstance(item, str) or not item for item in references)
                or len(references) != len(set(references))):
            raise ValueError("reference_query_ids must list every unique panel member")
        if not references and not abstained:
            raise ValueError("A non-abstained prediction requires a recorded reference panel")
        for reference in references:
            if reference not in cases:
                raise ValueError("Reference-panel member is absent from the manifest")
            ref, case = cases[reference], cases[query]
            if ref["split"] != "train":
                raise ValueError("Reference panel may use training cases only; heldout labels are scoring-only")
            for field in ("sequence_sha256", "cluster_id", "clade_id"):
                if ref[field] == case[field]:
                    raise ValueError(f"Query/reference {field} leakage")
    expected = {query for query, case in cases.items() if case["split"] == "test"}
    if seen != expected:
        raise ValueError("Every test query needs a prediction or explicit abstention")
    return predictions


def evaluate_predictions(manifest: dict, predictions: dict) -> dict:
    """Validate heldout provenance and evaluate frozen calls, never calibrate.

    Accuracy among accepted calls is null when all cases abstain.  Abstentions
    count as false negatives in per-class recall/F1 and have their own confusion
    column.  No probability metrics are calculated from raw placement mass.
    """
    cases, split_counts, true_groups = _manifest(manifest)
    rows = _predictions(predictions, cases)
    confusion = {group: Counter() for group in sorted(true_groups)}
    called = correct = 0
    called_groups = set()
    for row in rows:
        actual = cases[row["query_id"]]["true_group"]
        predicted = ABSTAIN if row["abstained"] else row["predicted_group"]
        confusion[actual][predicted] += 1
        if not row["abstained"]:
            called += 1
            called_groups.add(predicted)
            correct += actual == predicted
    # Include predicted-only groups, so unsupported extra classes are penalized.
    class_metrics = {}
    for group in sorted(true_groups | called_groups):
        tp = confusion.get(group, {}).get(group, 0)
        fp = sum(row.get(group, 0) for actual, row in confusion.items() if actual != group)
        fn = sum(confusion.get(group, {}).values()) - tp
        support = sum(confusion.get(group, {}).values())
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
        class_metrics[group] = {"true_count": support, "true_positives": tp,
                                "false_positives": fp, "false_negatives": fn,
                                "precision": precision, "recall": recall, "f1": f1}
    total = len(rows)
    warnings = []
    test_clusters = {case["cluster_id"] for case in cases.values() if case["split"] == "test"}
    test_clades = {case["clade_id"] for case in cases.values() if case["split"] == "test"}
    if any(item["true_count"] < 5 for item in class_metrics.values()):
        warnings.append("small_class_counts_require_cautious_interpretation")
    if len(test_clusters) < total or len(test_clades) < total:
        warnings.append("test_cases_share_holdout_units; uncertainty_requires_cluster_or_clade_resampling")
    if called == 0:
        warnings.append("all_test_cases_abstained; selective_accuracy_is_undefined")
    return {
        "schema": "basis-placement-benchmark-evaluation-v1", "status": "completed",
        "method_id": predictions["method_id"], "baseline_id": predictions.get("baseline_id"),
        "experiment_id": manifest.get("experiment_id"),
        "audit": {"status": "passed_for_declared_metadata", "evaluation_split": "test",
                  "fold_counts": dict(sorted(split_counts.items())),
                  "sequence_cluster_and_clade_disjointness": "verified",
                  "reference_policy": "training_cases_only", "complete_test_accounting": True,
                  "test_cluster_count": len(test_clusters), "test_clade_count": len(test_clades),
                  "frozen_policy": dict(manifest["policy"]),
                  "manifest_sha256": _digest(manifest), "predictions_sha256": _digest(predictions)},
        "metrics": {"test_count": total, "accepted_count": called,
                    "abstained_count": total - called, "correct_accepted_count": correct,
                    "coverage": called / total, "selective_accuracy": correct / called if called else None,
                    "accuracy_counting_abstentions_incorrect": correct / total,
                    "macro_f1_including_abstention_false_negatives":
                        math.fsum(item["f1"] for item in class_metrics.values()) / len(class_metrics)},
        "per_group": class_metrics,
        "confusion": {group: dict(sorted(values.items())) for group, values in confusion.items()},
        "abstention_column": ABSTAIN, "score_type": SCORE_TYPE,
        "calibration": {"status": "not_assessed", "reason": "Raw placement likelihood weights are not calibrated functional probabilities; Brier score, log loss and reliability calibration are not computed."},
        "warnings": warnings,
        "limitations": [
            "The audit verifies declared metadata; it cannot prove independent experimental truth, correct cluster/clade definitions, complete reference disclosure, or genuine prior freezing of the decision rule.",
            "Test labels are used only to evaluate submitted predictions. This evaluator neither fits thresholds nor changes reference panels.",
            "Existing screening or placement-panel annotations must not automatically be reused as independent test truth.",
            "Counts and point estimates are descriptive; uncertainty must respect cluster/clade dependence and sufficient independent holdout units.",
            "This is benchmark audit/evaluation support, not evidence that BaSIS has achieved validated predictive performance or probability calibration.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit leakage and evaluate frozen functional-group calls on an independent test split.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text())
        predictions = json.loads(args.predictions.read_text())
        result = evaluate_predictions(manifest, predictions)
        with args.out.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
    except (OSError, ValueError, TypeError) as exc:
        print(f"Benchmark evaluation refused: {exc}", file=sys.stderr)
        return 2
    metrics = result["metrics"]
    print(f"Declared leakage audit passed | test cases: {metrics['test_count']} | accepted: {metrics['accepted_count']} | abstained: {metrics['abstained_count']}")
    print(f"Evaluation: {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
