"""Disabled follow-up scope must stay absent from new report artifacts."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from basis.report import write_report
from basis.report_scope import deep_requested
from safety.reporting import aggregate, render


class DisabledReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "isolate/safety"
        self.sample = {"sample_id": "synthetic", "input_mode": "external", "taxon": ""}
        self.config = {"version": "0.4.0a1", "modules": {"minced": {"enabled": True}},
                       "deep": {"enabled": False}}
        self.record = {"sample_id": "synthetic", "module": "minced",
                       "execution_status": "completed", "findings": [], "metrics": {}, "artifacts": []}
        self.deep = {"sample_id": "synthetic", "module": "deepsafe",
                     "execution_status": "completed", "findings": [], "metrics": {},
                     "artifacts": [], "candidates": [], "complete": True}
        self.sample_path = self.write("metadata/sample.json", self.sample)
        self.config_path = self.write("metadata/config.json", self.config)
        self.record_path = self.write("evidence/minced.json", self.record)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def render(self, paths=None):
        with patch("subprocess.Popen", side_effect=AssertionError("report attempted a native process")):
            return render(self.sample_path, self.config_path,
                          paths if paths is not None else [self.record_path], self.root)

    def test_scope_is_explicit_and_legacy_records_remain_supported(self):
        self.assertFalse(deep_requested({"options": {"deep": False}}, []))
        self.assertTrue(deep_requested({"options": {"deep": True}}, []))
        self.assertFalse(deep_requested({}, []))
        self.assertTrue(deep_requested({}, [self.deep]))
        for options in ([], False, "disabled"):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "must be a mapping"):
                deep_requested({"options": options}, [])
        for value in ("false", 0, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "must be boolean"):
                deep_requested({"options": {"deep": value}}, [])

    def test_disabled_config_rejects_all_supplied_deep_records(self):
        for status in ("completed", "failed", "not_assessed", "not_requested"):
            with self.subTest(status=status):
                record = {**self.deep, "execution_status": status,
                          "module_decision": {"requested": True, "action": "run"}}
                path = self.write("evidence/deepsafe.json", record)
                with self.assertRaisesRegex(ValueError, "explicitly disabled report scope"):
                    aggregate(self.sample, self.config, [self.record_path, path])

    def test_direct_write_rejects_conflict_before_overwriting_report(self):
        path = self.root / "report.html"
        path.write_text("previous report", encoding="utf-8")
        original = copy.deepcopy(self.deep)
        with self.assertRaisesRegex(ValueError, "explicitly disabled report scope"):
            write_report({"options": {"deep": False}}, [self.sample],
                         [self.record], [self.deep], self.root)
        self.assertEqual(path.read_text(), "previous report")
        self.assertEqual(self.deep, original)

    def test_absent_legacy_config_accepts_explicit_deep_input(self):
        config = {"modules": {"minced": {"enabled": True}}}
        path = self.write("evidence/deepsafe.json", self.deep)
        run, _, deep = aggregate(self.sample, config, [self.record_path, path])
        self.assertTrue(deep_requested(run, deep))
        self.assertTrue(deep[0]["module_decision"]["requested"])

    def test_fresh_disabled_report_has_no_deep_outputs_or_navigation(self):
        artifacts = self.render()
        self.assertFalse((self.root / "deepsafe").exists())
        self.assertFalse((self.root / "metadata/deep-workflow.json").exists())
        self.assertFalse((self.root / "metadata/deep-workflow.tsv").exists())
        self.assertFalse((self.root / "tables/deferred-candidates.tsv").exists())
        self.assertFalse(any(key.startswith("deep_workflow_") for key in artifacts))
        body = (self.root / "report.html").read_text()
        for fragment in ('id="deep"', 'href="#deep"', 'id="deep-navigator"',
                         'id="evidence-matrix"', 'Selected candidates', 'Deferred candidates',
                         'deferred-candidates.tsv', 'deep-workflow.json'):
            with self.subTest(fragment=fragment):
                self.assertFalse(fragment in body, "Unexpected disabled-report UI: " + fragment)
        navigation = json.loads((self.root / "metadata/report-navigation.json").read_text())
        self.assertEqual(navigation["candidates"], [])
        self.assertEqual(navigation["report_sha256"],
                         hashlib.sha256((self.root / "report.html").read_bytes()).hexdigest())
        self.assertIn('href="tables/findings.tsv"', body)
        self.assertIn('href="metadata/files.html"', body)
        evidence = json.loads((self.root / "evidence.json").read_text())
        self.assertEqual(evidence["deep_records"], [])
        self.assertEqual(evidence["records"][0]["module"], "minced")
        self.assertFalse(evidence["run"]["options"]["deep"])
        for path in artifacts.values():
            self.assertTrue(Path(path).is_file(), path)

    def test_historical_deep_sources_preserved_but_not_linked(self):
        historical = ["deepsafe/candidates/old/state.json", "evidence/deepsafe.json",
                      "native/deepsafe/old.txt", "logs/deepsafe/old.log",
                      "logs/configs/deepsafe.log", "benchmarks/deepsafe/old.tsv"]
        saved = {self.write(path, {"historical": True}): None for path in historical}
        native = self.write("native/minced/results.json", {"current": True})
        saved[native] = None
        saved = {path: path.read_bytes() for path in saved}
        stale = ["deferred-candidates.tsv", "tables/deferred-candidates.tsv",
                 "metadata/deep-workflow.json", "metadata/deep-workflow.tsv"]
        for path in stale:
            self.write(path, {"stale": True})
        self.render()
        for path, content in saved.items():
            self.assertEqual(path.read_bytes(), content, str(path))
        files = json.loads((self.root / "metadata/files.json").read_text())
        indexed = {row["path"] for row in files["files"]}
        self.assertTrue(indexed.isdisjoint(historical))
        self.assertIn("native/minced/results.json", indexed)
        html = (self.root / "metadata/files.html").read_text()
        for path in historical:
            self.assertNotIn(path, html)
        for path in stale:
            self.assertFalse((self.root / path).exists(), path)

    def test_enabled_disabled_enabled_rerender_preserves_sources(self):
        deep_path = self.write("evidence/deepsafe.json", self.deep)
        self.config["deep"]["enabled"] = True
        self.write("metadata/config.json", self.config)
        self.render([self.record_path, deep_path])
        self.assertTrue((self.root / "metadata/deep-workflow.json").is_file())
        before = deep_path.read_bytes()
        self.config["deep"]["enabled"] = False
        self.write("metadata/config.json", self.config)
        self.render()
        self.assertFalse((self.root / "metadata/deep-workflow.json").exists())
        self.assertEqual(deep_path.read_bytes(), before)
        self.config["deep"]["enabled"] = True
        self.write("metadata/config.json", self.config)
        self.render([self.record_path, deep_path])
        self.assertTrue((self.root / "metadata/deep-workflow.json").is_file())
        self.assertTrue((self.root / "tables/deferred-candidates.tsv").is_file())
        self.assertEqual(deep_path.read_bytes(), before)

    @unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake unavailable")
    def test_primary_report_never_requests_deep_exports(self):
        self.write("evidence/deepsafe.json", self.deep)
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                snakefile = self.root.parent / "Snakefile"
                snakefile.write_text(
                    "from pathlib import Path\nimport sys\n"
                    f"ROOT = Path({str(ROOT)!r})\nPYTHON = sys.executable\nCODE_PATH = ''\n"
                    "class Context:\n"
                    f"    deep_enabled = {enabled!r}\n"
                    "    def evidence_targets(self, isolate):\n"
                    "        return [isolate + '/safety/evidence/minced.json']\n"
                    "CTX = Context()\n"
                    f"include: {str(ROOT / 'workflow/rules/report.smk')!r}\n",
                    encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, "-m", "snakemake", "--snakefile", str(snakefile),
                     "--directory", str(self.root.parent), "--cores", "1", "--dry-run",
                     "--summary",
                     str(self.root / "report.html")],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout[-6000:])
                for name in ("metadata/deep-workflow.json", "metadata/deep-workflow.tsv",
                             "tables/deferred-candidates.tsv"):
                    self.assertNotIn(name, result.stdout, name + "\n" + result.stdout[-6000:])


if __name__ == "__main__":
    unittest.main()
