import copy
import unittest

from basis.candidate_labels import qualification_reason, refresh_candidate_labels
from basis.deep import select_candidates


def source(gene, *, tool="rgi", family=None, level="exploratory", **attributes):
    return {"finding_id": tool + ":" + gene, "source_record_id": "s:" + tool,
            "tool": tool, "gene": gene, "family": family,
            "protein_id": "p1", "category": "stress" if tool == "bacmet" else "amr",
            "evidence_level": level, "attributes": attributes}


class CandidateLabelTests(unittest.TestCase):
    def test_qualified_bacmet_replaces_loose_rgi_gene_and_clears_its_family(self):
        for old_gene, old_family, new_gene in (
                ("EreB", "macrolide esterase", "chrR"),
                ("CMY-80", "CMY beta-lactamase", "ALU1-P")):
            with self.subTest(gene=new_gene):
                candidate = {"candidate_id": "c1", "protein_id": "p1",
                             "gene": old_gene, "family": old_family,
                             "source_evidence": [
                                 source(old_gene, family=old_family, cutoff="loose"),
                                 source(new_gene, tool="bacmet", level="homology",
                                        passes_threshold=True, function_confirmed=False)]}
                updated, audit = refresh_candidate_labels(candidate)
                self.assertEqual(updated["gene"], new_gene)
                self.assertIsNone(updated["family"])
                self.assertEqual(updated["source_gene_labels"], [new_gene])
                self.assertEqual(audit["changed_fields"]["gene"]["before"], old_gene)
                self.assertEqual(audit["selected_source"]["source_finding_id"], "bacmet:" + new_gene)
                self.assertIn("not established", updated["label_provenance"]["interpretation"])

    def test_same_tier_keeps_existing_cfr_label_and_all_source_evidence(self):
        a = source("cfr", tool="amrfinder", family="Cfr family", level="curated_model")
        b = source("clbA", family="Cfr clade", level="curated_model", cutoff="strict")
        candidate = {"gene": "cfr", "family": "Cfr family", "source_evidence": [b, a]}
        updated, audit = refresh_candidate_labels(candidate)
        self.assertEqual((updated["gene"], updated["family"]), ("cfr", "Cfr family"))
        self.assertNotIn("gene", audit["changed_fields"])
        self.assertEqual(updated["source_evidence"], [b, a])
        self.assertEqual(updated["source_gene_labels"], ["clbA", "cfr"])

    def test_native_demotion_overrides_normalized_qualified_label(self):
        for attributes in ({"cutoff": "loose"}, {"cutoff": "strict", "nudged": True},
                           {"cutoff": "perfect", "nudged": "true"},
                           {"passes_threshold": False}):
            with self.subTest(attributes=attributes):
                self.assertIsNone(qualification_reason(source("weak", level="curated_model", **attributes)))
        self.assertIsNone(qualification_reason(source("weak", level="exploratory", passes_threshold=True)))
        ml = source("attention", level="curated_model")
        ml["category"] = "ml_attention"
        self.assertIsNone(qualification_reason(ml))

    def test_no_qualified_source_leaves_candidate_unchanged(self):
        candidate = {"gene": "weak", "family": "weak family", "source_evidence": [source("weak")]}
        updated, audit = refresh_candidate_labels(candidate)
        self.assertEqual(updated, candidate)
        self.assertIsNot(updated, candidate)
        self.assertEqual(audit["changed_fields"], {})
        self.assertIsNone(audit["selected_source"])

    def test_no_mutation_or_historical_analysis_rewrite_and_idempotence(self):
        candidate = {
            "candidate_id": "c1", "gene": "weak", "family": "wrong",
            "family_labels": ["weak", "wrong", "chrR"],
            "source_evidence": [source("weak"), source("chrR", tool="bacmet", level="homology", passes_threshold=True)],
            "analysis_plan": {"route": "old", "decision_trace": [{"source_gene_labels": []}]},
            "decision_trace": [{"source_gene_labels": []}],
            "profile": {"profile_id": "generic"}, "analyses": {"domains": {"status": "completed"}},
            "assessment": {"interpretation": "unresolved", "function_established": False},
            "execution": "completed", "selected": True, "selection_queue": "native_screen",
        }
        original = copy.deepcopy(candidate)
        updated, audit = refresh_candidate_labels(candidate)
        self.assertEqual(candidate, original)
        for key in set(candidate) - {"gene", "family"}:
            self.assertEqual(updated[key], candidate[key], key)
        updated["analyses"]["domains"]["status"] = "test mutation"
        self.assertEqual(candidate, original)
        updated, _ = refresh_candidate_labels(candidate)
        again, second = refresh_candidate_labels(updated)
        self.assertEqual(again, updated)
        self.assertEqual(second["changed_fields"], {})

    def test_future_selection_uses_qualified_labels_without_changing_queue_or_ids(self):
        rows = [source("EreB", family="macrolide esterase", cutoff="loose"),
                source("chrR", tool="bacmet", level="homology", passes_threshold=True)]
        one = select_candidates(rows, {"include_stress": True})[0]
        two = select_candidates(rows[::-1], {"include_stress": True})[0]
        self.assertEqual(one["gene"], "chrR")
        self.assertIsNone(one["family"])
        self.assertEqual(two["gene"], "chrR")
        self.assertEqual(one["candidate_id"], two["candidate_id"])
        self.assertEqual(one["selection_queue"], "native_screen")
        self.assertTrue(one["selected"])
        self.assertEqual(len(one["source_evidence"]), 2)

    def test_missing_threshold_is_not_invented_from_identity(self):
        finding = source("homolog", tool="bacmet", level="homology")
        finding.update(identity=100.0, coverage=100.0)
        self.assertIsNone(qualification_reason(finding))


if __name__ == "__main__":
    unittest.main()
