"""Regression requirements for independent report scope and output isolation."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from safety.reporting import aggregate, render


class ReportSplitSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "safety"
        self.root.mkdir()
        self.sample = {"sample_id": "synthetic", "input_mode": "external"}
        self.config = {"modules": {"minced": {"enabled": True}},
                       "deep": {"enabled": True}, "requested_modules": ["minced", "deepsafe"]}
        self.native = {"sample_id": "synthetic", "module": "minced", "execution_status": "completed",
                       "findings": [], "metrics": {}, "artifacts": []}
        self.deep = {"sample_id": "synthetic", "module": "deepsafe", "execution_status": "completed",
                     "findings": [], "metrics": {}, "artifacts": [], "candidates": [], "complete": True}
        self.sample_path = self.write("metadata/sample.json", self.sample)
        self.config_path = self.write("metadata/config.json", self.config)
        self.native_path = self.write("evidence/minced.json", self.native)
        self.deep_path = self.write("evidence/deepsafe.json", self.deep)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_followup_output_alias_cannot_overwrite_screening(self):
        primary_html = self.root / "report.html"
        primary_html.write_text("existing screening report", encoding="utf-8")
        primary_evidence = self.write("evidence.json", {"existing": "screening"})
        before = {p: p.read_bytes() for p in (primary_html, primary_evidence)}
        alias = self.root / "deepsafe-report"
        alias.symlink_to(self.root, target_is_directory=True)
        with patch("subprocess.Popen", side_effect=AssertionError("native process forbidden")):
            with self.assertRaises(ValueError):
                render(self.sample_path, self.config_path, [self.native_path, self.deep_path],
                       alias, view="deep", evidence_root=self.root)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_stale_disabled_decision_cannot_hide_failed_parent(self):
        for status in ("failed", "blocked", "not_started"):
            with self.subTest(status=status):
                record = dict(self.deep, execution_status=status,
                              module_decision={"requested": False, "action": "disabled", "scope_incomplete": False},
                              candidates=[{"candidate_id": "synthetic-item", "selected": True,
                                           "execution": "completed", "analyses": {}}])
                self.write("evidence/deepsafe.json", record)
                try:
                    run, _, _ = aggregate(self.sample, self.config,
                                          [self.native_path, self.deep_path], view="deep")
                except ValueError:
                    continue  # Rejecting contradictory saved scope is also valid.
                self.assertEqual(run["completeness"], "incomplete")
                self.assertIn("synthetic:deepsafe", run["incomplete_modules"])


if __name__ == "__main__":
    unittest.main()
