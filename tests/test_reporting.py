"""Reporting verifies saved execution scope without running native analyses."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety.reporting import aggregate, render


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "isolate-test/safety"
        (self.root / "metadata").mkdir(parents=True)
        (self.root / "evidence").mkdir()
        self.sample = {"sample_id": "sample1", "input_mode": "external", "taxon": ""}
        self.config = {"version": "0.4.0a1", "modules": {"amrfinder": {"enabled": True},
                       "mlst": {"enabled": False}}, "deep": {"enabled": True}}
        self.amr = {"sample_id": "sample1", "module": "amrfinder", "execution_status": "completed",
                    "findings": [], "metrics": {}, "artifacts": []}
        self.deep = {"sample_id": "sample1", "module": "deepsafe", "execution_status": "completed",
                     "findings": [], "metrics": {}, "artifacts": [], "complete": False,
                     "upstream_incomplete": [], "candidates": [
                        {"candidate_id": "candidate-1", "protein_id": "P1", "selected": True,
                         "selection_queue": "native_screen", "execution": "completed", "analyses": {},
                         "assessment": {"interpretation": "unresolved", "followup": "review_existing_evidence"}},
                        {"candidate_id": "candidate-2", "protein_id": "P2", "selected": False,
                         "selection_queue": "exploratory", "execution": "deferred"}]}
        self.sample_path = self.write("metadata/sample.json", self.sample)
        self.config_path = self.write("metadata/config.json", self.config)
        self.amr_path = self.write("evidence/amrfinder.json", self.amr)
        self.deep_path = self.write("evidence/deepsafe.json", self.deep)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_missing_failed_or_cross_sample_evidence_cannot_finalize(self):
        with self.assertRaisesRegex(ValueError, "Missing requested evidence: deepsafe"):
            aggregate(self.sample, self.config, [self.amr_path])
        self.amr["execution_status"] = "failed"
        self.write("evidence/amrfinder.json", self.amr)
        with self.assertRaisesRegex(ValueError, "amrfinder is failed"):
            aggregate(self.sample, self.config, [self.amr_path, self.deep_path])
        self.amr.update(execution_status="completed", sample_id="other")
        self.write("evidence/amrfinder.json", self.amr)
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            aggregate(self.sample, self.config, [self.amr_path, self.deep_path])

    def test_deferred_scope_preserved_and_selected_execution_separate(self):
        run, records, deep = aggregate(self.sample, self.config, [self.amr_path, self.deep_path])
        self.assertEqual(run["completeness"], "incomplete")
        self.assertEqual(run["incomplete_modules"], ["sample1:deepsafe"])
        self.assertEqual(deep[0]["completion_summary"]["selected_execution"], "completed")
        self.assertEqual(deep[0]["completion_summary"]["deferred_candidates"], 1)
        self.assertFalse(next(r for r in records if r["module"] == "mlst")["module_decision"]["requested"])

    def test_explicit_hold_is_not_hidden_as_disabled(self):
        self.config["modules"]["icescreen"] = {"enabled": True}
        record = {"sample_id": "sample1", "module": "icescreen", "execution_status": "not_assessed",
                  "reason": "phylum_scope_requires_review", "findings": [],
                  "module_decision": {"action": "withheld", "requested": True, "scope_incomplete": True,
                                      "reason": "phylum_scope_requires_review"}}
        path = self.write("evidence/icescreen.json", record)
        run, _, _ = aggregate(self.sample, self.config, [self.amr_path, self.deep_path, path])
        self.assertIn("sample1:icescreen", run["incomplete_modules"])
        self.assertEqual(run["module_decisions"]["sample1"]["icescreen"]["action"], "withheld")

    def test_render_keeps_native_files_and_exports_citations_and_valid_hashes(self):
        native = self.root / "native/amrfinder/calls.tsv"
        native.parent.mkdir(parents=True)
        native.write_text("Protein\tGene\n", encoding="utf-8")
        self.amr["artifacts"] = [str(native)]
        self.write("evidence/amrfinder.json", self.amr)
        before = {p: p.read_bytes() for p in [native, self.amr_path, self.deep_path, self.sample_path, self.config_path]}
        # Reporting is pure saved-data processing: any subprocess launch fails.
        with patch("subprocess.Popen", side_effect=AssertionError("report attempted a native process")):
            artifacts = render(self.sample_path, self.config_path, [self.amr_path, self.deep_path], self.root)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        report = (self.root / "report.html").read_text()
        self.assertIn('href="tables/findings.tsv"', report)
        self.assertIn('href="metadata/citations/CITATION.cff"', report)
        self.assertIn('href="metadata/files.html"', report)
        self.assertIn("Motifs &amp; patterns", report)
        self.assertIn("Jimi Goldstein", report)
        citation = (self.root / "metadata/citations/CITATION.cff").read_text()
        self.assertIn("0000-0002-7829-0090", citation)
        self.assertIn("license: MIT", citation)
        self.assertFalse((self.root / "findings.tsv").exists())
        nav = json.loads((self.root / "metadata/report-navigation.json").read_text())
        self.assertEqual(nav["report_sha256"], hashlib.sha256((self.root / "report.html").read_bytes()).hexdigest())
        files = json.loads((self.root / "metadata/files.json").read_text())
        self.assertIn("native/amrfinder/calls.tsv", {f["path"] for f in files["files"]})
        for path in artifacts.values():
            self.assertTrue(Path(path).exists(), path)
        class Links(HTMLParser):
            hrefs = []
            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    self.hrefs.extend(v for k, v in attrs if k == "href")
        links = Links()
        links.feed(report)
        for href in links.hrefs:
            parsed = urlsplit(href)
            if not parsed.scheme and parsed.path:
                target = self.root / unquote(parsed.path)
                self.assertTrue(target.exists(), f"Broken report link: {href}")


if __name__ == "__main__":
    unittest.main()
