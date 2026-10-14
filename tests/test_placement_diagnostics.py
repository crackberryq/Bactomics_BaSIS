"""Diagnostic score-preservation, mass and topology regression tests."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from basis.placement_diagnostics import diagnose_jplace, _edge_details, _summarize_rows
from basis.placement_scoring import analyze_jplace, _graph, _labels, _edge_groups

TREE = "(A1:1{0},A2:1{1},(B1:1{2},B2:1{3}):2{4});"
FIELDS = ["edge_num", "likelihood", "like_weight_ratio", "distal_length", "pendant_length"]


def labels():
    return {tip: {"group": "Cfr" if tip.startswith("A") else "RlmN",
                  "role": "amr" if tip.startswith("A") else "alternative", "accessions": [tip]}
            for tip in ("A1", "A2", "B1", "B2")}


def data(weighted_edges=None, tree=TREE):
    return {"version": 3, "tree": tree, "fields": list(FIELDS),
            "placements": [{"n": ["Query"], "p": [[edge, -100.0, weight, 0.0, 0.1]
                for edge, weight in (weighted_edges if weighted_edges is not None else [(0, .2), (4, .8)])]}]}


class PlacementDiagnosticTests(unittest.TestCase):
    def test_exclusive_mass_and_original_score_preserved(self):
        native, ref = data(), labels()
        scores = analyze_jplace(native, ref, "Query")
        d = diagnose_jplace(native, ref, "Query", expected_scores=scores)
        self.assertEqual(d["schema"], "basis-placement-diagnostics-v1")
        self.assertEqual(d["status"], "completed")
        self.assertEqual(d["reason"], "competing_boundary_unresolved")
        self.assertIsNone(d["function_probability"])
        self.assertFalse(d["classification_policy_changed"])
        self.assertTrue(d["original_scores_verified"])
        self.assertEqual(d["original_group_support"], scores["group_support"])
        self.assertAlmostEqual(sum(d["exclusive_category_mass"].values()), 1)
        self.assertEqual(d["exclusive_category_mass"]["assigned_group_interior"], .2)
        self.assertEqual(d["exclusive_category_mass"]["competing_group_boundary"], .8)
        self.assertEqual(d["edges"][0]["category"], "assigned_group_interior")
        boundary = next(edge for edge in d["edges"] if edge["edge_num"] == 4)
        self.assertEqual(boundary["side_counts"], [{"Cfr": 2}, {"RlmN": 2}])

    def test_partial_output_is_not_renormalized(self):
        d = diagnose_jplace(data([(4, .7)]), labels(), "Query")
        self.assertAlmostEqual(d["unreported_mass"], .3)
        self.assertEqual(d["max_biological_split_lwr"], .7)
        self.assertEqual(d["split_set_95"]["status"], "insufficient_retained_mass")
        self.assertAlmostEqual(d["split_set_95"]["missing_mass_to_target"], .25)

    def test_retained_mass_below_target_is_explicitly_insufficient(self):
        d = diagnose_jplace(data([(4, .95 - 5e-13)]), labels(), "Query")
        self.assertEqual(d["split_set_95"]["status"], "insufficient_retained_mass")
        self.assertGreater(d["split_set_95"]["missing_mass_to_target"], 0)

    def test_cutoff_ties_are_all_included(self):
        d = diagnose_jplace(data([(0, .9), (4, .05), (2, .05)]), labels(), "Query")
        self.assertEqual(d["split_set_95"]["selected_split_count"], 3)
        self.assertEqual(d["split_set_95"]["cumulative_original_mass"], 1)
        self.assertEqual(d["split_set_95"]["cutoff_lwr"], .05)

    def test_reducer_aggregates_all_rows_per_edge(self):
        ref = labels(); a, e, t = _graph(TREE); g, _ = _labels(ref, t)
        detail = _edge_details(a, e, t, ref, _edge_groups(a, e, t, g)[0])
        d = _summarize_rows(detail, [{"edge_num": 4, "weight": .4}, {"edge_num": 4, "weight": .5},
                                   {"edge_num": 0, "weight": .1}])
        edge = next(row for row in d["edges"] if row["edge_num"] == 4)
        self.assertEqual(edge["placement_row_count"], 2)
        self.assertEqual(edge["likelihood_weight"], .9)
        self.assertEqual(d["reported_mass"], 1)

    def test_degree_two_root_halves_collapse_into_one_biological_split(self):
        tree = "((A1:1{0},A2:1{1}):1{4},(B1:1{2},B2:1{3}):1{5});"
        d = diagnose_jplace(data([(4, .4), (5, .55)], tree), labels(), "Query")
        self.assertAlmostEqual(d["max_biological_split_lwr"], .95)
        self.assertEqual(d["split_set_95"]["selected_split_count"], 1)
        self.assertAlmostEqual(d["exclusive_category_mass"]["competing_group_boundary"], .95)
        split = next(row for row in d["biological_splits"] if row["edge_numbers"] == [4, 5])
        self.assertEqual(split["placement_row_count"], 2)
        unsplit = diagnose_jplace(data([(4, .95)]), labels(), "Query")
        self.assertEqual(split["canonical_split_id"], unsplit["split_set_95"]["selected_split_ids"][0])

    def test_rerooting_and_legacy_boundary_reason_do_not_change_category(self):
        tree = "((A1:1{0},A2:1{1}):1{2},C:1{3},(B1:1{4},B2:1{5}):1{6});"
        ref = labels(); ref["C"] = {"group": None, "role": "context", "accessions": ["Context"]}
        original = diagnose_jplace(data([(2, .8), (3, .2)], tree), ref, "Query")
        rerooted = "((A1:1{0},A2:1{1}):0.5{2},(C:1{3},(B1:1{4},B2:1{5}):1{6}):0.5{7});"
        updated = diagnose_jplace(data([(2, .4), (7, .4), (3, .2)], rerooted), ref, "Query")
        self.assertEqual(original["exclusive_category_mass"], updated["exclusive_category_mass"])
        self.assertEqual(original["original_group_support"], updated["original_group_support"])
        self.assertEqual(original["max_biological_split_lwr"], updated["max_biological_split_lwr"])
        self.assertEqual(original["split_set_95"], updated["split_set_95"])

    def test_context_and_artificial_root_never_promoted(self):
        ref = labels(); ref["C"] = {"group": None, "role": "context", "accessions": ["Context"]}
        tree = "((A1:1{0},A2:1{1}):1{2},C:1{3},(B1:1{4},B2:1{5}):1{6});"
        d = diagnose_jplace(data([(2, .7), (3, .3)], tree), ref, "Query")
        self.assertEqual(d["exclusive_category_mass"]["context_branch"], .3)
        context_edge = next(edge for edge in d["edges"] if edge["edge_num"] == 3)
        self.assertTrue(any("C" in side["context_accessions"] for side in context_edge["sides"]))
        artificial = TREE[:-1] + ":1{5};"
        a = diagnose_jplace(data([(0, .9), (5, .1)], artificial), labels(), "Query")
        self.assertEqual(a["excluded_artificial_root_mass"], .1)
        self.assertEqual(a["biological_split_mass"], .9)
        self.assertEqual(a["split_set_95"]["status"], "insufficient_retained_mass")

    def test_singletons_and_one_group_remain_unassigned(self):
        ref = labels()
        for tip in ref: ref[tip]["group"] = tip
        d = diagnose_jplace(data([(0, 1)]), ref, "Query")
        self.assertEqual(d["exclusive_category_mass"]["other_unassigned"], 1)
        self.assertEqual(d["exclusive_category_mass"]["competing_group_boundary"], 0)
        ref = labels()
        for value in ref.values(): value.update(group="only_group", role="amr")
        d = diagnose_jplace(data([(0, 1)]), ref, "Query")
        self.assertEqual(d["exclusive_category_mass"]["other_unassigned"], 1)

    def test_zero_branch_lengths_and_zero_weights_are_supported(self):
        d = diagnose_jplace(data([(0, 0), (4, 1)], TREE.replace(":1", ":0").replace(":2", ":0")), labels(), "Query")
        self.assertEqual(d["max_biological_split_lwr"], 1)
        self.assertEqual(d["split_set_95"]["selected_split_count"], 1)
        zero = diagnose_jplace(data([(4, 0)]), labels(), "Query")
        self.assertEqual(zero["reason"], "no_positive_reported_placement_weight")
        self.assertEqual(zero["unreported_mass"], 1)
        self.assertEqual(zero["split_set_95"]["selected_split_count"], 0)

    def test_malformed_evidence_rejected_by_original_strict_scorer(self):
        cases = []
        bad = data(); bad["placements"][0]["p"][0][2] = float("nan"); cases.append(bad)
        bad = data(); bad["placements"][0]["p"][0][0] = 99; cases.append(bad)
        bad = data(); bad["placements"][0]["p"].append(bad["placements"][0]["p"][0]); cases.append(bad)
        bad = data(); bad["placements"][0]["n"] = ["Wrong"]; cases.append(bad)
        bad = data(); bad["tree"] = TREE.replace("{4}", "{3}"); cases.append(bad)
        for bad in cases:
            with self.subTest(data=bad), self.assertRaises(ValueError): diagnose_jplace(bad, labels(), "Query")

    def test_saved_score_mismatch_refused(self):
        expected = analyze_jplace(data(), labels(), "Query"); expected["group_support"]["Cfr"] = .9
        with self.assertRaisesRegex(ValueError, "expected_scores"):
            diagnose_jplace(data(), labels(), "Query", expected_scores=expected)

    def test_inputs_unchanged_and_output_has_no_mutable_aliases(self):
        native, ref = data(), labels(); expected = analyze_jplace(native, ref, "Query")
        before = deepcopy((native, ref, expected))
        d = diagnose_jplace(native, ref, "Query", expected_scores=expected)
        self.assertEqual((native, ref, expected), before)
        d["original_group_support"]["Cfr"] = 99
        d["edges"][0]["sides"][0]["all_tip_accessions"]["A1"].append("Changed")
        self.assertEqual((native, ref, expected), before)


class DiagnosticNativeIntegrationTests(unittest.TestCase):
    """Reuse deterministic native adapter fixture; no real tools are called."""
    def setUp(self):
        path = Path(__file__).with_name("test_placement_native.py")
        spec = importlib.util.spec_from_file_location("_placement_native_fixture", path)
        fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
        self.case = fixture.NativePlacementTests(); self.case.setUp()

    def tearDown(self):
        self.case.tearDown()

    def test_successful_native_run_saves_diagnostics(self):
        result = self.case.run_candidate()
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["diagnostics"]["status"], "completed")
        self.assertTrue(result["diagnostics"]["original_scores_verified"])
        self.assertEqual(result["diagnostics"]["original_group_support"], result["scores"]["group_support"])
        saved = json.loads((self.case.root / "one" / "placement" / "placement.json").read_text())
        self.assertEqual(saved["diagnostics"], result["diagnostics"])

    def test_diagnostic_exception_does_not_change_native_status_or_scores(self):
        normal = self.case.run_candidate()
        with patch("basis.placement_diagnostics.diagnose_jplace", side_effect=ValueError("diagnostic fixture failure")):
            result = self.case.run_candidate(attempt="diagnostic-failure")
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["reason"], normal["reason"])
        self.assertEqual(result["scores"], normal["scores"])
        self.assertEqual(result["diagnostics"]["status"], "failed")
        self.assertIn("diagnostic fixture failure", result["diagnostics"]["error"])
        self.assertNotIn("error", result)


if __name__ == "__main__":
    unittest.main()
