import copy
import unittest

from basis.placement_scoring import analyze_jplace


TREE = "((A:1{0},B:1{1})95/99:1{2},C:1{3},D:1{4});"
FIELDS = ["edge_num", "likelihood", "like_weight_ratio", "distal_length", "pendant_length"]


def labels():
    return {tip: {"group": "resistance" if tip in "AB" else "alternative",
                  "role": "amr" if tip in "AB" else "alternative",
                  "accessions": ["REF_" + tip]}
            for tip in "ABCD"}


def data(placements=None, tree=TREE):
    return {"tree": tree, "version": 3, "fields": list(FIELDS),
            "placements": [{"n": ["query"], "p": placements or [
                [0, -100, 0.6, 0.5, 0.1], [2, -101, 0.2, 0.5, 0.1],
                [3, -102, 0.1, 0.5, 0.1]]}]}


class PlacementScoringTests(unittest.TestCase):
    def test_support_is_raw_mass_with_separate_boundary_and_missing_mass(self):
        result = analyze_jplace(data(), labels(), "query")
        self.assertEqual(result["group_support"], {"alternative": 0.1, "resistance": 0.6})
        self.assertAlmostEqual(result["unresolved_mass"], 0.2)
        self.assertAlmostEqual(result["missing_mass"], 0.1)
        self.assertAlmostEqual(result["assigned_mass"], 0.7)
        self.assertAlmostEqual(result["reported_mass"], 0.9)
        self.assertEqual(result["best_group"], "resistance")
        self.assertIsNone(result["function_probability"])
        self.assertEqual(result["placements"][1]["reason"], "homogeneous_clade_boundary")
        self.assertIn("uncalibrated", result["score_type"])
        self.assertIn("unreported_placement_weight_not_renormalized", result["warnings"])

    def test_root_invariance(self):
        rerooted = "(A:1{0},B:1{1},(C:1{3},D:1{4})99/100:1{2});"
        original = analyze_jplace(data(), labels(), "query")
        result = analyze_jplace(data(tree=rerooted), labels(), "query")
        for key in ["group_support", "unresolved_mass", "missing_mass", "placements",
                    "homogeneous_clades", "best_group", "reference_group_counts"]:
            self.assertEqual(result[key], original[key], key)

    def test_root_on_boundary_remains_unresolved(self):
        tree = "((A:1{0},B:1{1}):0.4{2},(C:1{3},D:1{4}):0.6{5});"
        result = analyze_jplace(data([[2, -5, 0.4, 0.2, 0], [5, -6, 0.6, 0.2, 0]], tree), labels(), "query")
        self.assertEqual(result["unresolved_mass"], 1.0)
        self.assertEqual(result["assigned_mass"], 0.0)
        self.assertIsNone(result["best_group"])

    def test_artificial_root_edge_is_never_group_evidence(self):
        tree = TREE[:-1] + ":0{5};"
        result = analyze_jplace(data([[5, -5, 1, 0, 0]], tree), labels(), "query")
        self.assertEqual(result["unresolved_mass"], 1)
        self.assertEqual(result["placements"][0]["reason"], "artificial_root_edge")

    def test_context_branch_stays_unresolved_inside_pure_side(self):
        tree = "((A:1{0},B:1{1},X:1{5}):1{2},C:1{3},D:1{4});"
        ref = labels()
        ref["X"] = {"group": None, "role": "context"}
        result = analyze_jplace(data([[5, -5, 0.8, 0.5, 0], [0, -6, 0.2, 0.5, 0]], tree), ref, "query")
        self.assertEqual(result["group_support"]["resistance"], 0.2)
        self.assertEqual(result["unresolved_mass"], 0.8)
        self.assertEqual(result["placements"][1]["reason"], "context_only_branch")

    def test_singleton_anchors_cannot_score(self):
        ref = labels()
        for tip in "ABCD":
            ref[tip]["group"] = tip
        result = analyze_jplace(data([[0, -5, 1, 0.5, 0]]), ref, "query")
        self.assertEqual(result["assigned_mass"], 0)
        self.assertEqual(result["unresolved_mass"], 1)
        self.assertIn("singleton_group_references_do_not_define_a_scored_clade", result["warnings"])

    def test_one_group_tree_not_automatically_assigned(self):
        ref = labels()
        for item in ref.values():
            item.update(group="resistance", role="amr")
        result = analyze_jplace(data([[0, -5, 1, 0.5, 0]]), ref, "query")
        self.assertEqual(result["assigned_mass"], 0)
        self.assertEqual(result["unresolved_mass"], 1)
        self.assertIn("fewer_than_two_functional_groups", result["warnings"])

    def test_mixed_tree_not_forced_into_global_group(self):
        ref = labels()
        ref["B"].update(group="alternative", role="alternative")
        ref["C"].update(group="resistance", role="amr")
        result = analyze_jplace(data(), ref, "query")
        self.assertEqual(result["homogeneous_clades"], [])
        self.assertEqual(result["assigned_mass"], 0)
        self.assertAlmostEqual(result["unresolved_mass"], 0.9)

    def test_polyphyletic_group_can_have_multiple_pure_local_clades(self):
        tree = "((A:1{0},B:1{1}):1{2},(C:1{3},D:1{4}):1{5},((E:1{6},F:1{7}):1{8},(G:1{9},H:1{10}):1{11}):1{12});"
        ref = labels()
        ref.update({tip: {"group": "resistance" if tip in "EF" else "alternative",
                          "role": "amr" if tip in "EF" else "alternative"}
                    for tip in "EFGH"})
        result = analyze_jplace(data([[0, -5, 0.3, 0.5, 0], [6, -6, 0.4, 0.5, 0],
                                     [12, -7, 0.3, 0.5, 0]], tree), ref, "query")
        self.assertAlmostEqual(result["group_support"]["resistance"], 0.7)
        self.assertAlmostEqual(result["unresolved_mass"], 0.3)
        self.assertEqual(sum(c["group"] == "resistance" for c in result["homogeneous_clades"]), 2)

    def test_tie_has_no_best_group(self):
        result = analyze_jplace(data([[0, -5, 0.5, 0.5, 0], [3, -5, 0.5, 0.5, 0]]), labels(), "query")
        self.assertIsNone(result["best_group"])
        self.assertIn("tied_best_group_support", result["warnings"])

    def test_field_order_is_not_assumed(self):
        payload = data()
        payload["fields"] = FIELDS[::-1]
        payload["placements"][0]["p"] = [row[::-1] for row in payload["placements"][0]["p"]]
        self.assertEqual(analyze_jplace(payload, labels(), "query"), analyze_jplace(data(), labels(), "query"))

    def test_nm_query_with_unit_multiplicity(self):
        payload = data()
        payload["placements"][0]["nm"] = [["query", 1]]
        del payload["placements"][0]["n"]
        self.assertEqual(analyze_jplace(payload, labels(), "query")["query_id"], "query")

    def test_quoted_tip_and_scientific_branch_length(self):
        payload = data(tree="(('A''s tip':1e0{0},B:1{1})'95/99':1{2},C:1{3},D:1{4});")
        ref = labels()
        ref["A's tip"] = ref.pop("A")
        self.assertEqual(analyze_jplace(payload, ref, "query")["group_support"]["resistance"], 0.6)

    def test_bad_labels_are_rejected(self):
        cases = []
        ref = labels(); ref["A"]["role"] = "alternative"; cases.append(ref)
        ref = labels(); ref["A"]["role"] = "context"; cases.append(ref)
        ref = labels(); ref["A"]["group"] = None; cases.append(ref)
        ref = labels(); ref.pop("D"); cases.append(ref)
        ref = labels(); ref["E"] = ref["A"]; cases.append(ref)
        ref = labels(); ref["A"]["accessions"] = ["x", "x"]; cases.append(ref)
        ref = labels(); ref["A"]["role"] = ["amr"]; cases.append(ref)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                analyze_jplace(data(), case, "query")

    def test_bad_tree_rejected(self):
        for tree in [
            TREE.replace("D:", "A:"), TREE.replace("{4}", "{3}"),
            TREE.replace("B:1{1}", "B"), TREE.replace("B:1", "B:-1"),
            TREE.replace("B:1", "B:nan"), TREE.replace("B:1", "B:inf"),
            TREE.replace("{4}", "{-1}"), TREE.replace("{4}", "{4.1}"),
            TREE.replace("B:1{1}", "(B:1{1}):1{5}"), TREE + "junk",
            TREE.replace("B:1{1}", "B{1}"), TREE[:-1], "();", "A:1{0};",
            TREE.replace("C:1{3},", ""), TREE[:-1] + ":1;",
            TREE.replace("B:1{1}", "'unterminated:1{1}"),
        ]:
            with self.subTest(tree=tree), self.assertRaises(ValueError):
                analyze_jplace(data(tree=tree), labels(), "query")

    def test_bad_query_and_schema_rejected(self):
        cases = []
        payload = data(); payload["version"] = 2; cases.append(payload)
        payload = data(); payload["fields"].append("edge_num"); cases.append(payload)
        payload = data(); payload["fields"][0] = "other"; cases.append(payload)
        payload = data(); payload["placements"].append(copy.deepcopy(payload["placements"][0])); cases.append(payload)
        payload = data(); payload["placements"][0]["n"] = ["other"]; cases.append(payload)
        payload = data(); payload["placements"][0]["n"] = ["query", "other"]; cases.append(payload)
        payload = data(); payload["placements"][0]["nm"] = [["query", 1]]; cases.append(payload)
        for multiplicity in [0, 2, True, "1", float("nan")]:
            payload = data(); del payload["placements"][0]["n"]
            payload["placements"][0]["nm"] = [["query", multiplicity]]; cases.append(payload)
        payload = data(); payload["placements"][0]["p"] = []; cases.append(payload)
        payload = data(); payload["placements"][0]["p"][0].pop(); cases.append(payload)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                analyze_jplace(case, labels(), "query")

    def test_bad_placement_numbers_rejected(self):
        for index, value in [(0, 0.5), (0, -1), (0, 999), (0, True), (1, float("inf")),
                             (2, -0.1), (2, float("nan")), (2, 1.1), (2, "0.5"),
                             (3, -0.1), (3, 1.01), (4, -1)]:
            payload = data()
            payload["placements"][0]["p"][0][index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                analyze_jplace(payload, labels(), "query")

    def test_duplicate_edge_and_excess_total_mass_rejected(self):
        for placements in [
            [[0, -5, 0.5, 0.5, 0], [0, -6, 0.5, 0.5, 0]],
            [[0, -5, 0.6, 0.5, 0], [3, -6, 0.6, 0.5, 0]],
        ]:
            with self.assertRaises(ValueError):
                analyze_jplace(data(placements), labels(), "query")

    def test_rounding_excess_not_silently_renormalized(self):
        result = analyze_jplace(data([[0, -5, 0.5000001, 0.5, 0], [3, -6, 0.5, 0.5, 0]]), labels(), "query")
        self.assertEqual(result["group_support"]["resistance"], 0.5000001)
        self.assertEqual(result["missing_mass"], 0)
        self.assertIn("reported_mass_exceeds_one_within_rounding_tolerance", result["warnings"])

    def test_input_objects_remain_unchanged(self):
        payload, ref = data(), labels()
        before = copy.deepcopy((payload, ref))
        analyze_jplace(payload, ref, "query")
        self.assertEqual((payload, ref), before)


if __name__ == "__main__":
    unittest.main()
