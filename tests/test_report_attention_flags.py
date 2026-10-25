"""Deferred report labels retain original evidence and execution distinctions."""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workflow/vendor"))
from basis.report import write_report


class ReportAttentionFlagTests(unittest.TestCase):
    def inputs(self):
        run = {"run_id": "synthetic-attention-flags", "version": "test",
               "report_view": "deep", "options": {"deep": True},
               "completeness": "incomplete", "enabled_modules": []}
        samples = [{"sample_id": "fixture", "taxon": "", "input_mode": "external"}]
        candidates = [
            {"candidate_id": "selected", "protein_id": "P0", "selected": True,
             "selection_queue": "native_screen", "execution": "completed",
             "reason": "aware_criteria_met", "source_evidence": [], "analyses": {}},
            {"candidate_id": "budget", "protein_id": "P1", "selected": False,
             "selection_queue": "native_screen", "execution": "deferred",
             "reason": "candidate_resource_budget", "source_evidence": [{"fixture": "budget"}]},
            {"candidate_id": "admission", "protein_id": "P2", "selected": False,
             "selection_queue": "not_admitted", "execution": "deferred",
             "reason": "aware_admission_deferred", "source_evidence": [{"fixture": "admission"}]},
        ]
        deep = [{"sample_id": "fixture", "module": "deepsafe", "execution_status": "completed",
                 "complete": False, "findings": [], "metrics": {}, "artifacts": [],
                 "candidates": candidates}]
        return run, samples, [], deep

    def render(self, inputs):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        before = copy.deepcopy(inputs)
        with patch("subprocess.Popen", side_effect=AssertionError("Reporting invoked a native tool")):
            artifacts = write_report(*inputs, root)
        self.assertEqual(inputs, before)
        evidence = json.loads(Path(artifacts["evidence_json"]).read_text())
        for key, value in zip(("run", "samples", "records", "deep_records"), before):
            self.assertEqual(evidence[key], value)
        return root, Path(artifacts["html"]).read_text()

    def rows(self, root):
        with (root / "deferred-candidates.tsv").open() as stream:
            return list(csv.DictReader(stream, delimiter="\t"))

    def test_budget_and_admission_deferrals_are_flagged_without_changing_evidence(self):
        root, body = self.render(self.inputs())
        self.assertIn("Requires attention: 2 records", body)
        self.assertTrue("candidate resource budget" in body, "Recorded budget reason missing")
        self.assertTrue("aware admission deferred" in body, "Recorded admission reason missing")
        rows = self.rows(root)
        self.assertEqual({row["candidate_id"] for row in rows}, {"budget", "admission"})
        self.assertEqual({row["reason"] for row in rows},
                         {"candidate_resource_budget", "aware_admission_deferred"})
        for row in rows:
            self.assertEqual(row["review_status"], "Requires attention")
            self.assertEqual(row["followup_status"], "Follow-up not performed")
            self.assertEqual(row["execution"], "deferred")
            self.assertEqual(row["selected"], "False")

    def test_retained_execution_is_not_relabelled_unperformed(self):
        for status in ("completed", "failed", "unknown"):
            with self.subTest(status=status):
                inputs = self.inputs()
                inputs[3][0]["candidates"][1]["execution"] = status
                root, _ = self.render(inputs)
                row = next(row for row in self.rows(root) if row["candidate_id"] == "budget")
                self.assertEqual(row["execution"], status)
                self.assertIn("Recorded follow-up status:", row["followup_status"])
                self.assertNotEqual(row["followup_status"], "Follow-up not performed")

    def test_unknown_reason_is_escaped_in_html_and_preserved_in_source(self):
        inputs = self.inputs()
        inputs[3][0]["candidates"][1]["reason"] = "<script>synthetic</script>"
        _, body = self.render(inputs)
        self.assertNotIn("<script>synthetic</script>", body)
        self.assertIn("&lt;script&gt;synthetic&lt;/script&gt;", body)

    def test_screening_has_no_followup_attention_queue(self):
        inputs = self.inputs()
        inputs[0].update(report_view="screening", options={"deep": False})
        inputs = (*inputs[:3], [])
        root, body = self.render(inputs)
        self.assertNotIn("Requires attention", body)
        self.assertFalse((root / "deferred-candidates.tsv").exists())


if __name__ == "__main__":
    unittest.main()
