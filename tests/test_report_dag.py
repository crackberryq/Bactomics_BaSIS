"""Independent report dependencies over synthetic saved evidence only."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from safety.context import SafetyContext


class ReportTargetTests(unittest.TestCase):
    def context(self, enabled, samples=("/tmp/synthetic-a", "/tmp/synthetic-b")):
        # Exercise only target construction, without resource planning or input
        # analysis. Full SafetyContext parsing has its own synthetic tests.
        context = object.__new__(SafetyContext)
        context.samples = dict.fromkeys(samples, {})
        context.deep_enabled = enabled
        return context

    def test_enabled_scope_has_two_independent_report_targets_per_sample(self):
        context = self.context(True)
        screening = [path + "/safety/report.html" for path in context.samples]
        followup = [path + "/safety/deepsafe-report/report.html" for path in context.samples]
        self.assertEqual(context.screening_report_targets(), screening)
        self.assertEqual(context.followup_report_targets(), followup)
        self.assertEqual(context.report_targets(), screening + followup)

    def test_disabled_scope_requests_only_screening_reports(self):
        context = self.context(False)
        self.assertEqual(context.followup_report_targets(), [])
        self.assertEqual(context.report_targets(), context.screening_report_targets())

    def test_empty_samples_are_rejected_only_when_report_targets_are_requested(self):
        context = self.context(True, ())
        for method in (context.screening_report_targets, context.followup_report_targets,
                       context.report_targets):
            with self.subTest(method=method.__name__), self.assertRaisesRegex(
                    ValueError, "No samples selected.*setup target"):
                method()


@unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake unavailable")
class IndependentReportDagTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="basis-report-dag-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.isolate = self.work / "synthetic"
        self.safety = self.isolate / "safety"
        self.sample = {"sample_id": "synthetic", "input_mode": "external", "taxon": ""}
        self.config = {"version": "0.4.0a1", "modules": {"minced": {"enabled": True}},
                       "deep": {"enabled": True}}
        self.write("metadata/sample.json", self.sample)
        self.write("metadata/config.json", self.config)
        self.write("evidence/minced.json", {
            "sample_id": "synthetic", "module": "minced", "execution_status": "completed",
            "findings": [], "metrics": {}, "artifacts": []})

    def write(self, relative, value):
        path = self.safety / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def snakefile(self, enabled=True, *, empty=False):
        path = self.work / "Snakefile"
        samples = [] if empty else [str(self.isolate)]
        report_rules = ROOT / "workflow/rules/report.smk"
        if os.environ.get("BASIS_TEST_NO_BENCHMARK") == "1":
            # Same explicit local fallback as test_workflow: some test hosts
            # expose child PIDs outside the mounted /proc namespace. CI uses
            # production rules unchanged and exercises their benchmarks.
            lines, skipped_indent = [], None
            for line in report_rules.read_text().splitlines():
                indentation = len(line) - len(line.lstrip())
                if line.lstrip().startswith("benchmark:"):
                    skipped_indent = indentation
                    continue
                if skipped_indent is not None:
                    if not line.strip() or indentation > skipped_indent:
                        continue
                    skipped_indent = None
                lines.append(line)
            report_rules = self.work / "report-test.smk"
            report_rules.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.write_text(
            "from pathlib import Path\nimport sys\n"
            f"ROOT = Path({str(ROOT)!r})\nPYTHON = sys.executable\n"
            f"CODE_PATH = {str(ROOT / 'workflow/lib') + ':' + str(ROOT / 'workflow/vendor')!r}\n"
            "class Context:\n"
            f"    deep_enabled = {enabled!r}\n"
            f"    samples = {samples!r}\n"
            "    def evidence_targets(self, isolate):\n"
            "        return [isolate + '/safety/evidence/minced.json']\n"
            "    def screening_report_targets(self):\n"
            "        if not self.samples: raise ValueError('No samples selected; use setup target')\n"
            "        return [s + '/safety/report.html' for s in self.samples]\n"
            "    def followup_report_targets(self):\n"
            "        if not self.samples: raise ValueError('No samples selected; use setup target')\n"
            "        return [s + '/safety/deepsafe-report/report.html' for s in self.samples] if self.deep_enabled else []\n"
            "    def report_targets(self):\n"
            "        return self.screening_report_targets() + self.followup_report_targets()\n"
            "CTX = Context()\n"
            "rule all_reports:\n"
            "    input: lambda w: CTX.report_targets()\n"
            "    default_target: True\n"
            "rule setup:\n"
            "    input: []\n"
            f"include: {str(report_rules)!r}\n",
            encoding="utf-8")
        return path

    def snake(self, *arguments, enabled=True, empty=False):
        path = self.snakefile(enabled, empty=empty)
        result = subprocess.run(
            [sys.executable, "-m", "snakemake", "--snakefile", str(path),
             "--directory", str(self.work), "--cores", "1", *arguments],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        if result.returncode:
            for log in sorted((self.safety / "logs").glob("*report.log")):
                result.stdout += "\nRenderer log: " + log.name + "\n" + log.read_text(errors="replace")[-3000:]
        return result

    def test_screening_dry_run_does_not_require_missing_followup_evidence(self):
        self.assertFalse((self.safety / "evidence/deepsafe.json").exists())
        result = self.snake("--dry-run", "--summary", "screening_reports")
        self.assertEqual(result.returncode, 0, result.stdout[-6000:])
        self.assertIn(str(self.safety / "report.html"), result.stdout)
        self.assertNotIn("deepsafe-report/", result.stdout)
        self.assertNotIn("metadata/deep-workflow.", result.stdout)
        self.assertNotIn("tables/deferred-candidates.tsv", result.stdout)

    def test_screening_target_renders_without_any_followup_record(self):
        # The invoked job is the saved-evidence renderer; there are no native
        # analysis rules or executables in this synthetic Snakefile.
        result = self.snake("screening_reports")
        self.assertEqual(result.returncode, 0, result.stdout[-6000:])
        report = self.safety / "report.html"
        self.assertTrue(report.is_file())
        self.assertFalse((self.safety / "evidence/deepsafe.json").exists())
        self.assertFalse((self.safety / "deepsafe-report/report.html").exists())
        self.assertNotIn('id="deep"', report.read_text())

    def test_followup_report_can_render_failed_saved_parent_independently(self):
        self.write("evidence/deepsafe.json", {
            "sample_id": "synthetic", "module": "deepsafe", "execution_status": "failed",
            "reason": "synthetic parent failure", "findings": [], "metrics": {}, "artifacts": [],
            "complete": False, "candidates": [{
                "candidate_id": "synthetic-candidate", "protein_id": "P1", "selected": True,
                "execution": "completed", "analyses": {}}]})
        result = self.snake("followup_reports")
        self.assertEqual(result.returncode, 0, result.stdout[-6000:])
        report = self.safety / "deepsafe-report/report.html"
        self.assertTrue(report.is_file())
        self.assertFalse((self.safety / "report.html").exists())
        self.assertIn("Provisional", report.read_text())

    def test_enabled_default_dag_requests_both_bundles(self):
        self.write("evidence/deepsafe.json", {"synthetic": True})
        result = self.snake("--dry-run", "--summary")
        self.assertEqual(result.returncode, 0, result.stdout[-6000:])
        self.assertIn(str(self.safety / "report.html"), result.stdout)
        self.assertIn(str(self.safety / "deepsafe-report/report.html"), result.stdout)
        self.assertIn(str(self.safety / "deepsafe-report/metadata/deep-workflow.json"), result.stdout)
        self.assertNotIn(str(self.safety / "metadata/deep-workflow.json"), result.stdout)

    def test_disabled_default_dag_never_requests_followup_report(self):
        result = self.snake("--dry-run", "--summary", enabled=False)
        self.assertEqual(result.returncode, 0, result.stdout[-6000:])
        self.assertIn(str(self.safety / "report.html"), result.stdout)
        self.assertNotIn("deepsafe-report/", result.stdout)
        followup = self.snake("--dry-run", "followup_reports", enabled=False)
        self.assertEqual(followup.returncode, 0, followup.stdout[-6000:])
        self.assertNotIn("safety_deep_report", followup.stdout)

    def test_explicit_followup_target_has_separate_bundle(self):
        self.write("evidence/deepsafe.json", {"synthetic": True})
        result = self.snake("--dry-run", "--summary", "followup_reports")
        self.assertEqual(result.returncode, 0, result.stdout[-6000:])
        self.assertIn(str(self.safety / "deepsafe-report/report.html"), result.stdout)
        self.assertNotIn(str(self.safety / "report.html") + "\t", result.stdout)

    def test_setup_only_stays_parseable_without_samples(self):
        result = self.snake("--dry-run", "setup", empty=True)
        self.assertEqual(result.returncode, 0, result.stdout[-6000:])


if __name__ == "__main__":
    unittest.main()
