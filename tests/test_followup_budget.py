"""Synthetic work-budget tests; no sequence analysis or native execution."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from basis.aware import audit
from basis.config import default_config
from basis.deep import select_candidates


def record(index, *, duplicate=False, admitted=True):
    """An existing reported record, with no actual sequence or gene content."""
    return {"finding_id": f"fixture-{index}" + ("-copy" if duplicate else ""),
            "tool": "amrfinder", "category": "amr", "protein_id": f"fixture-protein-{index}",
            "gene": "synthetic_label", "family": "synthetic_family",
            "identity": 100.0, "coverage": 100.0, "evidence_level": "curated_model",
            "attributes": {"native_method": "EXACTP" if admitted else "UNSUPPORTED_FIXTURE"}}


def aware_config(**overrides):
    return {"aware": {"enabled": True, "preset": "auto-medium"}, **overrides}


class FollowupBudgetTests(unittest.TestCase):
    def test_standard_and_fallback_budgets_select_at_most_five(self):
        self.assertEqual(default_config()["deep"]["maximum_candidates"], 5)
        rows = [record(i) for i in range(12)]
        for config in (default_config()["deep"], aware_config(), {},
                       {"aware": {"enabled": False}}):
            with self.subTest(config=config):
                selected = select_candidates(rows, config)
                self.assertEqual(len(selected), 12)
                self.assertEqual(sum(c["selected"] for c in selected), 5)
                deferred = [c for c in selected if not c["selected"]]
                self.assertEqual(len(deferred), 7)
                self.assertTrue(all(c["execution"] == "deferred" for c in deferred))
                self.assertTrue(all(c["reason"] == "candidate_resource_budget" for c in deferred))

    def test_duplicates_share_one_budget_slot_and_keep_every_source(self):
        rows = [record(i, duplicate=duplicate) for i in range(8) for duplicate in (False, True)]
        original = copy.deepcopy(rows)
        candidates = select_candidates(rows, aware_config())
        self.assertEqual(len(candidates), 8)
        self.assertEqual(sum(c["selected"] for c in candidates), 5)
        self.assertTrue(all(len(c["source_evidence"]) == 2 for c in candidates))
        self.assertCountEqual([f["finding_id"] for c in candidates for f in c["source_evidence"]],
                              [f["finding_id"] for f in rows])
        self.assertEqual(rows, original)

    def test_small_or_empty_eligible_sets_are_never_padded(self):
        for count in (0, 2, 5):
            with self.subTest(qualifying=count):
                rows = [record(i, admitted=i < count) for i in range(9)]
                candidates = select_candidates(rows, aware_config())
                self.assertEqual(sum(c["selected"] for c in candidates), count)
                self.assertTrue(all(c["reason"] == "aware_admission_deferred"
                                    for c in candidates if not c["selected"]))
        self.assertEqual(select_candidates([], aware_config()), [])

    def test_explicit_limits_are_honored_without_changing_eligibility(self):
        rows = [record(i) for i in range(12)]
        baseline = select_candidates(rows, aware_config())
        expected_ids = {c["candidate_id"] for c in baseline}
        for limit in (1, 3, 8):
            with self.subTest(limit=limit):
                config = aware_config(maximum_candidates=limit)
                candidates = select_candidates(rows, config)
                self.assertEqual(sum(c["selected"] for c in candidates), limit)
                self.assertEqual({c["candidate_id"] for c in candidates}, expected_ids)
                self.assertTrue(all(c["aware"]["eligible"] for c in candidates))
                self.assertEqual(audit(candidates, rows, config)["budget"]["maximum_candidates"], limit)

    def test_audit_reports_default_budget_and_separate_deferral_causes(self):
        rows = [record(i) for i in range(8)] + [record(8, admitted=False)]
        config = aware_config()
        candidates = select_candidates(rows, config)
        result = audit(candidates, rows, config)
        self.assertEqual(result["budget"]["maximum_candidates"], 5)
        self.assertEqual(result["counts"]["selected_candidates"], 5)
        self.assertEqual(result["counts"]["budget_deferred"], 3)
        self.assertEqual(result["counts"]["admission_deferred"], 1)
        self.assertEqual(result["counts"]["unique_candidates"], 9)


if __name__ == "__main__":
    unittest.main()
