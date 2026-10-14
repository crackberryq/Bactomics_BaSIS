import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from basis.placement_benchmark import evaluate_predictions, main, SCORE_TYPE


def case(query, split, group):
    return {"query_id": query, "sequence_sha256": hashlib.sha256(query.encode()).hexdigest(),
            "cluster_id": "cluster_" + query, "clade_id": "clade_" + query,
            "split": split, "true_group": group}


def fixture():
    manifest = {"schema_version": 1, "evaluation_split": "test", "experiment_id": "synthetic-unit-test-only",
                "policy": {"threshold_source": "predeclared", "threshold_frozen": True},
                "cases": [case("refA", "train", "A"), case("refB", "train", "B"),
                          case("calA", "calibration", "A"), case("calB", "calibration", "B"),
                          case("q1", "test", "A"), case("q2", "test", "A"),
                          case("q3", "test", "B"), case("q4", "test", "B")]}
    bundle = {"schema_version": 1, "method_id": "synthetic-unit-test", "score_type": SCORE_TYPE,
              "predictions": [
                  {"query_id": "q1", "predicted_group": "A", "abstained": False,
                   "group_support": {"A": 0.8, "B": 0.1}, "reference_query_ids": ["refA", "refB"]},
                  {"query_id": "q2", "predicted_group": None, "abstained": True,
                   "group_support": {"A": 0.3, "B": 0.3}, "reference_query_ids": ["refA", "refB"]},
                  {"query_id": "q3", "predicted_group": "A", "abstained": False,
                   "group_support": {"A": 0.7, "B": 0.1}, "reference_query_ids": ["refA", "refB"]},
                  {"query_id": "q4", "predicted_group": "B", "abstained": False,
                   "group_support": {"A": 0.1, "B": 0.8}, "reference_query_ids": ["refA", "refB"]},
              ]}
    return manifest, bundle


class PlacementBenchmarkTests(unittest.TestCase):
    def test_metrics_preserve_abstentions_and_do_not_calibrate(self):
        result = evaluate_predictions(*fixture())
        self.assertEqual(result["metrics"]["accepted_count"], 3)
        self.assertEqual(result["metrics"]["abstained_count"], 1)
        self.assertEqual(result["metrics"]["coverage"], 0.75)
        self.assertAlmostEqual(result["metrics"]["selective_accuracy"], 2 / 3)
        self.assertEqual(result["metrics"]["accuracy_counting_abstentions_incorrect"], 0.5)
        self.assertEqual(result["confusion"]["A"], {"A": 1, "__abstained__": 1})
        self.assertEqual(result["confusion"]["B"], {"A": 1, "B": 1})
        self.assertAlmostEqual(result["per_group"]["A"]["f1"], 0.5)
        self.assertAlmostEqual(result["per_group"]["B"]["f1"], 2 / 3)
        self.assertAlmostEqual(result["metrics"]["macro_f1_including_abstention_false_negatives"], (0.5 + 2 / 3) / 2)
        self.assertEqual(result["calibration"]["status"], "not_assessed")
        self.assertNotIn("brier_score", result["metrics"])

    def test_all_abstained_selective_accuracy_is_null(self):
        manifest, bundle = fixture()
        for row in bundle["predictions"]:
            row.update(predicted_group=None, abstained=True, group_support={}, reference_query_ids=[])
        result = evaluate_predictions(manifest, bundle)
        self.assertEqual(result["metrics"]["accepted_count"], 0)
        self.assertEqual(result["metrics"]["coverage"], 0)
        self.assertIsNone(result["metrics"]["selective_accuracy"])
        self.assertEqual(result["metrics"]["macro_f1_including_abstention_false_negatives"], 0)

    def test_exact_sequence_leakage_rejected(self):
        manifest, bundle = fixture()
        manifest["cases"][4]["sequence_sha256"] = manifest["cases"][0]["sequence_sha256"]
        with self.assertRaisesRegex(ValueError, "Exact sequence leakage"):
            evaluate_predictions(manifest, bundle)

    def test_cross_fold_cluster_and_clade_leakage_rejected(self):
        for key in ["cluster_id", "clade_id"]:
            for first, second in [(0, 4), (2, 4), (0, 2)]:
                manifest, bundle = fixture()
                manifest["cases"][second][key] = manifest["cases"][first][key]
                with self.subTest(key=key, folds=(first, second)), self.assertRaisesRegex(ValueError, key + " leakage"):
                    evaluate_predictions(manifest, bundle)

    def test_duplicates_inside_fold_cannot_inflate_counts(self):
        manifest, bundle = fixture()
        manifest["cases"][5]["sequence_sha256"] = manifest["cases"][4]["sequence_sha256"]
        with self.assertRaisesRegex(ValueError, "Duplicate sequence within"):
            evaluate_predictions(manifest, bundle)

    def test_reference_panels_cannot_include_test_or_calibration_cases(self):
        for reference in ["q1", "q4", "calA", "calB", "missing"]:
            manifest, bundle = fixture()
            bundle["predictions"][0]["reference_query_ids"] = [reference]
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                evaluate_predictions(manifest, bundle)

    def test_full_test_accounting_required(self):
        for change in ["missing", "duplicate", "train", "unknown"]:
            manifest, bundle = fixture()
            if change == "missing":
                bundle["predictions"].pop()
            elif change == "duplicate":
                bundle["predictions"].append(copy.deepcopy(bundle["predictions"][0]))
            else:
                bundle["predictions"][0]["query_id"] = "refA" if change == "train" else "unknown"
            with self.subTest(change=change), self.assertRaises(ValueError):
                evaluate_predictions(manifest, bundle)

    def test_thresholds_must_be_frozen_outside_test_set(self):
        for policy in [{}, {"threshold_source": "test", "threshold_frozen": True},
                       {"threshold_source": "predeclared", "threshold_frozen": False}]:
            manifest, bundle = fixture()
            manifest["policy"] = policy
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                evaluate_predictions(manifest, bundle)
        manifest, bundle = fixture()
        manifest["policy"]["threshold_source"] = "calibration"
        self.assertEqual(evaluate_predictions(manifest, bundle)["audit"]["status"], "passed_for_declared_metadata")
        manifest["cases"] = [case for case in manifest["cases"] if case["split"] != "calibration"]
        with self.assertRaisesRegex(ValueError, "separate calibration split"):
            evaluate_predictions(manifest, bundle)

    def test_missing_holdout_labels_and_single_group_holdout_rejected(self):
        for group in [None, "A"]:
            manifest, bundle = fixture()
            for item in manifest["cases"]:
                if item["split"] == "test":
                    item["true_group"] = group
            with self.subTest(group=group), self.assertRaises(ValueError):
                evaluate_predictions(manifest, bundle)

    def test_context_training_references_permitted_without_function_label(self):
        manifest, bundle = fixture()
        manifest["cases"].append(case("context", "train", None))
        for row in bundle["predictions"]:
            row["reference_query_ids"].append("context")
        self.assertEqual(evaluate_predictions(manifest, bundle)["metrics"]["test_count"], 4)

    def test_bad_support_values_rejected(self):
        for mass in [-0.1, 1.1, float("nan"), float("inf"), True, "0.8", 10 ** 999]:
            manifest, bundle = fixture()
            bundle["predictions"][0]["group_support"]["A"] = mass
            with self.subTest(mass=str(mass)[:30]), self.assertRaises(ValueError):
                evaluate_predictions(manifest, bundle)
        manifest, bundle = fixture()
        bundle["predictions"][0]["group_support"] = {"A": 0.8, "B": 0.8}
        with self.assertRaisesRegex(ValueError, "mass exceeds"):
            evaluate_predictions(manifest, bundle)

    def test_abstention_and_predicted_label_must_agree(self):
        for field, value in [("abstained", 1), ("abstained", True),
                             ("predicted_group", None), ("predicted_group", "absent")]:
            manifest, bundle = fixture()
            bundle["predictions"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                evaluate_predictions(manifest, bundle)

    def test_non_abstained_calls_need_reference_panel(self):
        manifest, bundle = fixture()
        bundle["predictions"][0]["reference_query_ids"] = []
        with self.assertRaisesRegex(ValueError, "recorded reference panel"):
            evaluate_predictions(manifest, bundle)

    def test_predicted_only_class_is_included_in_macro_f1(self):
        manifest, bundle = fixture()
        bundle["predictions"][0].update(predicted_group="C", group_support={"C": 0.9})
        result = evaluate_predictions(manifest, bundle)
        self.assertEqual(result["per_group"]["C"]["false_positives"], 1)
        self.assertEqual(result["per_group"]["C"]["f1"], 0)
        self.assertIsNone(result["per_group"]["C"]["recall"])

    def test_same_holdout_unit_warns_against_independent_case_uncertainty(self):
        manifest, bundle = fixture()
        manifest["cases"][5]["clade_id"] = manifest["cases"][4]["clade_id"]
        result = evaluate_predictions(manifest, bundle)
        self.assertEqual(result["audit"]["test_clade_count"], 3)
        self.assertTrue(any("resampling" in warning for warning in result["warnings"]))

    def test_objects_preserved_and_provenance_fingerprinted(self):
        manifest, bundle = fixture()
        before = copy.deepcopy((manifest, bundle))
        result = evaluate_predictions(manifest, bundle)
        self.assertEqual((manifest, bundle), before)
        self.assertRegex(result["audit"]["manifest_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["audit"]["predictions_sha256"], r"^[0-9a-f]{64}$")

    def test_cli_creates_new_artifact_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest, bundle = fixture()
            (directory / "manifest.json").write_text(json.dumps(manifest))
            (directory / "predictions.json").write_text(json.dumps(bundle))
            argv = ["--manifest", str(directory / "manifest.json"), "--predictions", str(directory / "predictions.json"),
                    "--out", str(directory / "evaluation.json")]
            self.assertEqual(main(argv), 0)
            before = (directory / "evaluation.json").read_bytes()
            self.assertEqual(main(argv), 2)
            self.assertEqual((directory / "evaluation.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
