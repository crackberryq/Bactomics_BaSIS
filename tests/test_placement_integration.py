import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.placement import DEFAULT_REGISTRY, placement_gate, run_family_placements, placement_summary
from basis.placement_panels import load_registry, prepare_panel


def candidate(identifier="cand1", gene="cfr"):
    return {"candidate_id": identifier, "protein_id": "p" + identifier, "selected": True,
            "execution": "completed", "gene": gene, "query": {"id": "Q" + identifier, "path": "/verified/query.faa"},
            "source_evidence": [{"tool": "amrfinder", "gene": gene, "evidence_level": "curated_model",
                                 "attributes": {}}],
            "analyses": {"integrity": {"status": "completed", "flags": []},
                         "phylogeny": {"status": "completed", "reason": "historical", "runs": [{"name": "old"}]},
                         "domains": {"status": "completed", "hits": [{"domain_name": "Radical_SAM"}]}},
            "assessment": {"interpretation": "subgroup_unresolved", "function_established": False},
            "decision_trace": [{"decision": "historical"}]}


def panel():
    return {"panel_id": "test", "panel_sha256": "a" * 64,
            "adequacy": {"eligible": True, "reasons": []}, "provenance": {},
            "members": [{"tip_id": "ref", "sequence": "ACDE", "sequence_sha256": "b" * 64,
                         "group": "Cfr", "role": "amr", "accessions": ["EXAMPLE"]}]}


class PlacementIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cfg = {"family_placement": True, "placement_allow_download": False, "maximum_placement_candidates": 20}

    def run_layer(self, candidates, config=None):
        return run_family_placements(candidates, config if config is not None else self.cfg,
                                     self.root / "work", object())

    def test_legacy_disabled_is_exact_noop_without_loading_registry(self):
        rows = [candidate()]
        before = copy.deepcopy(rows)
        with patch("basis.placement_panels.load_registry", side_effect=AssertionError("disabled layer loaded registry")):
            self.assertEqual(self.run_layer(rows, {}), [])
        self.assertEqual(rows, before)
        self.assertFalse((self.root / "work").exists())

    def test_bundled_registry_is_valid_and_supports_distinct_competing_anchors(self):
        registry = load_registry(DEFAULT_REGISTRY)
        prepared = prepare_panel(candidate(), registry, self.root / "cache", allow_download=False)
        self.assertTrue(prepared["adequacy"]["eligible"])
        self.assertGreater(prepared["adequacy"]["raw_reference_records"], prepared["adequacy"]["distinct_sequences"])
        self.assertTrue(all(n >= 2 for n in prepared["adequacy"]["distinct_qualified_anchors_by_group"].values()))
        self.assertEqual({m["role"] for m in prepared["members"]}, {"amr", "alternative", "context"})

    def test_biological_gates_prevent_panel_retrieval_and_native_calls(self):
        cases = []
        c = candidate(); c["selected"] = False; cases.append((c, "candidate_deferred"))
        c = candidate(); c["execution"] = "failed"; cases.append((c, "prior_candidate_analysis_failed"))
        c = candidate(); c["query"] = {}; cases.append((c, "query_not_available"))
        c = candidate(); c["taxonomy_review"] = {"status": "review_required"}; cases.append((c, "taxonomic_scope_review_required"))
        c = candidate(); c["analyses"]["integrity"]["flags"] = ["query_internal_stop"]; cases.append((c, "sequence_integrity_review_required"))
        c = candidate(); c["source_evidence"][0]["attributes"]["model_type"] = "protein variant model"
        cases.append((c, "mutation_model_requires_mechanism_specific_assessment"))
        with patch("basis.placement_panels.prepare_panel") as prepare, patch("basis.placement_native.run_placement") as native:
            for c, reason in cases:
                with self.subTest(reason=reason):
                    self.assertEqual(placement_gate(c), reason)
                    self.run_layer([c])
                    self.assertEqual(c["analyses"]["family_placement"]["reason"], reason)
            prepare.assert_not_called(); native.assert_not_called()

    def test_unqualified_variant_label_cannot_override_valid_family_route(self):
        c = candidate()
        c["source_evidence"].append({"tool": "rgi", "gene": "rpoC", "evidence_level": "exploratory",
                                      "attributes": {"model_type": "protein variant model", "cutoff": "Loose"}})
        self.assertIsNone(placement_gate(c))

    def test_unsupported_family_is_explicit_abstention_without_tool_execution(self):
        c = candidate(gene="unsupported_family")
        with patch("basis.placement_native.run_placement") as native:
            self.run_layer([c]); native.assert_not_called()
        result = c["analyses"]["family_placement"]
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIn("no_supported_family_panel", result["panel"]["adequacy"]["reasons"])
        self.assertEqual(c["execution"], "completed")
        self.assertIsNone(result["function_probability"])

    def test_new_scores_never_overwrite_existing_interpretation_or_analyses(self):
        c = candidate(); old = copy.deepcopy(c)
        native_result = {"status": "completed", "reason": "placement_completed", "artifacts": [],
                         "function_probability": 0.99, "scores": {"group_likelihood_weight": {"Cfr": 0.9}}}
        with patch("basis.placement_panels.prepare_panel", return_value=panel()), \
                patch("basis.placement_native.run_placement", return_value=native_result):
            artifacts = self.run_layer([c])
        self.assertEqual(c["assessment"], old["assessment"])
        for layer, value in old["analyses"].items():
            self.assertEqual(c["analyses"][layer], value)
        self.assertEqual(c["source_evidence"], old["source_evidence"])
        result = c["analyses"]["family_placement"]
        self.assertIsNone(result["function_probability"])
        self.assertNotIn("sequence", result["panel"]["members"][0])
        self.assertEqual(c["execution"], "completed")
        self.assertEqual(c["decision_trace"][0], old["decision_trace"][0])
        self.assertTrue(all(Path(p).is_file() for p in artifacts))

    def test_native_failure_cannot_appear_as_completed_selected_execution(self):
        c = candidate()
        with patch("basis.placement_panels.prepare_panel", return_value=panel()), \
                patch("basis.placement_native.run_placement", side_effect=RuntimeError("EPA exited 1")):
            self.run_layer([c])
        self.assertEqual(c["execution"], "failed")
        self.assertEqual(c["analyses"]["family_placement"]["status"], "failed")
        self.assertIn("EPA exited 1", c["reason"])
        self.assertEqual(placement_summary([c])["statuses"], {"failed": 1})

    def test_budget_stops_native_execution_and_marks_deferred_work(self):
        rows = [candidate("cand1"), candidate("cand2")]
        with patch("basis.placement_panels.prepare_panel", return_value=panel()), \
                patch("basis.placement_native.run_placement", return_value={"status": "completed", "artifacts": []}) as native:
            self.run_layer(rows, {**self.cfg, "maximum_placement_candidates": 1})
        self.assertEqual(native.call_count, 1)
        self.assertEqual(rows[1]["execution"], "not_assessed")
        self.assertEqual(rows[1]["analyses"]["family_placement"]["reason"], "placement_candidate_budget_exhausted")
        self.assertEqual(placement_summary(rows)["statuses"], {"completed": 1, "not_assessed": 1})

    def test_candidate_path_traversal_is_rejected_before_native_execution(self):
        c = candidate("../../outside")
        with patch("basis.placement_native.run_placement") as native:
            with self.assertRaises(ValueError):
                self.run_layer([c])
            native.assert_not_called()
        self.assertFalse((self.root / "outside").exists())


if __name__ == "__main__":
    unittest.main()
