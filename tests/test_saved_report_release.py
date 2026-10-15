"""Installed offline reporting contracts on synthetic saved evidence."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.control_panel import build_control_panel, collect_run
from basis.report_refresh import refresh_saved_report


def saved(selected=False, failed=False):
    candidate = {"candidate_id": "fixture-candidate", "protein_id": "fixture-protein", "sample_id": "s1",
                 "selected": selected, "selection_queue": "exploratory",
                 "execution": "failed" if failed else "completed" if selected else "deferred",
                 "assessment": {"interpretation": "unresolved"}, "analyses": {}}
    return {"schema_version": "basis-report-1", "run": {
        "run_id": "synthetic-release-fixture", "version": "0.1.0", "completeness": "incomplete",
        "enabled_modules": ["amrfinder"], "options": {"deep": True}},
        "samples": [{"sample_id": "s1"}],
        "records": [{"sample_id": "s1", "module": "amrfinder", "execution_status": "completed", "findings": []}],
        "deep_records": [{"sample_id": "s1", "module": "deepsafe", "execution_status": "completed",
                          "candidates": [candidate], "findings": [], "upstream_incomplete": []}]}


class SavedReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source_dir = self.root / "source"
        self.source_dir.mkdir()
        self.source = self.source_dir / "evidence.json"

    def store(self, evidence):
        self.source.write_text(json.dumps(evidence), encoding="utf-8")

    def test_replay_preserves_recorded_evidence_and_zero_selection(self):
        evidence = saved()
        self.store(evidence)
        raw = self.source.read_bytes()
        output = self.root / "replayed"
        with patch("subprocess.run", side_effect=AssertionError("No subprocess in report replay")):
            merged, artifacts = refresh_saved_report(self.source, output)
        self.assertEqual(self.source.read_bytes(), raw)
        for key in ("samples", "records", "deep_records"):
            self.assertEqual(evidence[key], merged[key])
        for key, value in evidence["run"].items():
            self.assertEqual(value, merged["run"][key])
        audit = json.loads(Path(artifacts["replay_audit"]).read_text())
        self.assertTrue(audit["finalized"])
        self.assertTrue(audit["source_unchanged"])
        self.assertFalse(audit["native_analyses_run"])
        self.assertEqual(audit["source_evidence"]["sha256"], hashlib.sha256(raw).hexdigest())
        html = Path(artifacts["html"]).read_text()
        self.assertIn("None selected", html)
        self.assertIn("no candidates selected", html)
        self.assertNotIn("not requested — no candidates selected", html)
        self.assertIn("Requested modules completed", html)
        self.assertIn("Saved-evidence reporting refresh", html)
        self.assertNotIn("0 / 0 completed", html)
        for relative, sha in audit["output_file_sha256"].items():
            self.assertEqual(hashlib.sha256((output / relative).read_bytes()).hexdigest(), sha)
        run = collect_run(output, "r1", self.root / "panel")
        self.assertEqual(run["status"], "incomplete")
        self.assertEqual(len(run["modules"]), 2)
        self.assertEqual(run["candidates"], [])

    def test_failed_selected_candidate_is_not_completion_headline(self):
        self.store(saved(selected=True, failed=True))
        _, artifacts = refresh_saved_report(self.source, self.root / "failed")
        html = Path(artifacts["html"]).read_text()
        self.assertIn("Execution review required", html)
        self.assertNotIn("Selected analyses completed", html)
        self.assertIn("Recorded overall scope: incomplete", html)

    def test_refuses_overwrite_and_invalid_source_without_writing(self):
        self.store(saved())
        out = self.root / "existing"
        out.mkdir()
        (out / "keep.txt").write_text("preserve")
        with self.assertRaises(FileExistsError):
            refresh_saved_report(self.source, out)
        self.assertEqual((out / "keep.txt").read_text(), "preserve")
        for change in (
                lambda e: e.update(schema_version="unknown"),
                lambda e: e.update(records={}),
                lambda e: e["deep_records"][0].update(candidates="bad")):
            evidence = saved()
            change(evidence)
            self.store(evidence)
            with self.assertRaises(ValueError):
                refresh_saved_report(self.source, self.root / "never-written")
            self.assertFalse((self.root / "never-written").exists())

    def test_detects_renderer_mutation_and_keeps_incomplete_audit(self):
        self.store(saved())
        def mutate(run, samples, records, deep_records, outdir):
            records[0]["execution_status"] = "failed"
            return {}
        out = self.root / "mutated"
        with patch("basis.report.write_report", side_effect=mutate):
            with self.assertRaisesRegex(RuntimeError, "Renderer changed"):
                refresh_saved_report(self.source, out)
        self.assertFalse(json.loads((out / "replay-status.json").read_text())["finalized"])
        self.assertFalse((out / "replay-audit.json").exists())

    def test_repeat_refresh_retains_history(self):
        evidence = saved()
        evidence["run"]["report_replays"] = [{"source_run_id": "older", "native_analyses_run": False}]
        self.store(evidence)
        merged, _ = refresh_saved_report(self.source, self.root / "repeat")
        self.assertEqual(merged["run"]["report_replays"][:-1], evidence["run"]["report_replays"])

    def test_control_panel_saved_bundle_and_no_overwrite(self):
        self.store(saved(selected=True))
        (self.source_dir / "report.html").write_text("fixture")
        out = self.root / "panel"
        with patch("subprocess.run", side_effect=AssertionError("No subprocess in control panel")):
            result = build_control_panel(results=[self.source_dir], outdir=out)
        data = json.loads(Path(result["inventory"]).read_text())
        self.assertEqual(result["run_count"], 1)
        self.assertEqual(len(data["runs"][0]["candidates"]), 1)
        self.assertEqual(data["runs"][0]["modules"][0]["status"], "completed")
        with self.assertRaises(FileExistsError):
            build_control_panel(results=[self.source_dir], outdir=out)
        with self.assertRaisesRegex(ValueError, "outside"):
            build_control_panel(results=[self.source_dir], outdir=self.source_dir / "inside")
        with self.assertRaisesRegex(ValueError, "No run outputs"):
            build_control_panel(outdir=self.root / "empty")


if __name__ == "__main__":
    unittest.main()
