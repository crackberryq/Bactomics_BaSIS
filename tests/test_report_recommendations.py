"""Decision routing is sample-scoped and never erases native source evidence."""
import copy
import unittest

from basis.report_recommendations import refine_recommendations


def finding(fid, protein="p1", category="amr", **kw):
    return dict(finding_id=fid, protein_id=protein, category=category, **kw)


def record(sample="A", findings=None, **kw):
    return dict(sample_id=sample, module="rgi", execution_status="completed",
                findings=findings or [], **kw)


def candidate(ids, *, sample="A", name="candidate1", followup="hold_for_taxonomic_review", **kw):
    result = dict(sample_id=sample, candidate_id=name, selected=True, execution="completed",
                  finding_ids=ids, assessment={"followup": followup, "interpretation": "unresolved",
                                             "function_established": False})
    result.update(kw)
    return result


def deep(candidates, sample="A", status="completed"):
    return dict(sample_id=sample, module="deepsafe", execution_status=status,
                candidates=candidates, findings=[])


def recommendation(ids, sample="A", category="amr"):
    return dict(recommendation_id="old", sample_id=sample, category=category, priority="review",
                assessment_type="targeted_assessment_review", trigger="old trigger", evidence_ids=ids,
                recommendation="Review source evidence before assessment.",
                provided_phenotype_status="not_provided_for_this_assessment")


class RecommendationRefinementTests(unittest.TestCase):
    def refine(self, records, deep_records, recs):
        return refine_recommendations([{"sample_id": "A"}, {"sample_id": "B"}], records, deep_records, recs)

    def test_taxonomic_hold_removed_from_generic_only_and_preserved_separately(self):
        records = [record(findings=[finding("tax", "rpoc"), finding("other", "abc")])]
        c = candidate(["tax"], taxonomy_review={"checks": [{"finding_id": "tax", "status": "outside_declared_genus_review_required"}]})
        deep_records = [deep([c])]
        recs = [recommendation(["tax", "other"])]
        inputs = copy.deepcopy((records, deep_records, recs))
        result = self.refine(records, deep_records, recs)
        amr = next(r for r in result if r["category"] == "amr")
        held = next(r for r in result if r["category"] == "deep_taxonomic_review")
        self.assertEqual(amr["evidence_ids"], ["other"])
        self.assertEqual(amr["excluded_taxonomic_hold_evidence_ids"], ["tax"])
        self.assertEqual(held["evidence_ids"], ["tax"])
        self.assertEqual(held["assessment_type"], "computational_review")
        self.assertEqual((records, deep_records, recs), inputs)
        self.assertEqual(self.refine(records, deep_records, result), result)

    def test_all_held_omits_generic_phenotype_recommendation(self):
        result = self.refine([record(findings=[finding("tax")])],
                             [deep([candidate(["tax"])])], [recommendation(["tax"])])
        self.assertEqual([r["category"] for r in result], ["deep_taxonomic_review"])

    def test_hold_is_model_specific_other_call_on_same_protein_remains(self):
        c = candidate(["tax", "applicable"], taxonomy_review={"checks": [
            {"finding_id": "tax", "status": "outside_declared_genus_review_required"},
            {"finding_id": "applicable", "status": "within_declared_genus"}]})
        result = self.refine([record(findings=[finding("tax"), finding("applicable")])],
                             [deep([c])], [recommendation(["tax", "applicable"])])
        self.assertEqual(next(r for r in result if r["category"] == "amr")["evidence_ids"], ["applicable"])

    def test_same_finding_ids_in_other_sample_are_not_removed(self):
        records = [record("A", [finding("shared")]), record("B", [finding("shared")])]
        result = self.refine(records, [deep([candidate(["shared"])])],
                             [recommendation(["shared"], "A"), recommendation(["shared"], "B")])
        amr = [r for r in result if r["category"] == "amr"]
        self.assertEqual(len(amr), 1)
        self.assertEqual(amr[0]["sample_id"], "B")
        self.assertEqual(amr[0]["evidence_ids"], ["shared"])

    def test_comparator_and_subgroup_review_do_not_suppress_amr(self):
        c1 = candidate(["a"], followup="hold_for_comparator_review")
        c1["assessment"]["missing_comparison_groups"] = ["comparator_group"]
        c2 = candidate(["b"], name="candidate2", followup="stop_automatic_tree_followup")
        result = self.refine([record(findings=[finding("a"), finding("b", "p2")])],
                             [deep([c1, c2])], [recommendation(["a", "b"])])
        self.assertEqual(next(r for r in result if r["category"] == "amr")["evidence_ids"], ["a", "b"])
        self.assertEqual(next(r for r in result if r["category"] == "deep_comparator_review")["missing_comparison_groups"], ["comparator_group"])
        self.assertIn("deep_interpretation_review", [r["category"] for r in result])

    def test_missing_ids_are_disclosed_never_guessed_by_protein(self):
        c = candidate(["missing"], protein_id="p1")
        result = self.refine([record(findings=[finding("known")])], [deep([c])],
                             [recommendation(["known", "also_missing"])])
        amr = next(r for r in result if r["category"] == "amr")
        held = next(r for r in result if r["category"] == "deep_taxonomic_review")
        self.assertEqual(amr["evidence_ids"], ["also_missing", "known"])
        self.assertEqual(amr["unlinked_evidence_ids"], ["also_missing"])
        self.assertEqual(amr["distinct_target_count"], 1)
        self.assertEqual(held["evidence_ids"], [])
        self.assertEqual(held["unlinked_source_evidence_ids"], ["missing"])
        self.assertIn("without a verified source finding link", held["trigger"])

    def test_missing_native_id_normalization_matches_report(self):
        from basis.report import _all_findings
        records = [record(findings=[{"protein_id": "p1", "category": "amr"}])]
        fid = _all_findings(records)[0]["finding_id"]
        result = self.refine(records, [deep([candidate([fid])])], [recommendation([fid])])
        self.assertEqual(result[0]["evidence_ids"], [fid])
        self.assertEqual(len(result), 1)

    def test_only_completed_selected_candidates_can_route_holds(self):
        for changes, status in [({"execution": "failed"}, "completed"),
                                ({"selected": False}, "completed"), ({}, "failed"),
                                ({"sample_id": "B"}, "completed")]:
            with self.subTest(changes=changes, status=status):
                c = candidate(["a"], **changes)
                result = self.refine([record(findings=[finding("a")])], [deep([c], status=status)],
                                     [recommendation(["a"])])
                self.assertEqual([r["category"] for r in result], ["amr"])
                self.assertEqual(result[0]["evidence_ids"], ["a"])

    def test_deduplicate_records_and_count_unique_targets(self):
        rows = [finding("a"), finding("b"), finding("c", None, locus_id="l2"),
                finding("d", None, locus_id="l2"), finding("e", None)]
        result = self.refine([record(findings=rows)], [], [recommendation(["a", "a", "b", "c", "d", "e"])])[0]
        self.assertEqual(result["finding_record_count"], 5)
        self.assertEqual(result["distinct_target_count"], 2)
        self.assertIn("5 finding record(s)", result["trigger"])
        self.assertIn("2 distinct identified", result["trigger"])

    def test_ml_priority_is_model_evidence_review_not_phenotype(self):
        result = self.refine([record(findings=[finding("attention", category="ml_attention")])], [],
                             [recommendation(["attention"], category="ml_attention")])[0]
        self.assertEqual(result["assessment_type"], "model_evidence_review")
        self.assertEqual(result["evidence_ids"], ["attention"])

    def test_source_evidence_id_can_link_when_candidate_id_list_missing(self):
        c = candidate(None, source_evidence=[{"finding_id": "tax", "sample_id": "A"}])
        result = self.refine([record(findings=[finding("tax")])], [deep([c])], [recommendation(["tax"])])
        self.assertEqual([r["category"] for r in result], ["deep_taxonomic_review"])
        self.assertEqual(result[0]["evidence_ids"], ["tax"])


if __name__ == "__main__":
    unittest.main()
