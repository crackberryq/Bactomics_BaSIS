"""Saved-input workflow isolation with synthetic files and zero tool calls."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from basis.config import default_config
from safety.reporting import render
from safety.setup import validate


@unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake unavailable")
class SavedFollowupDagTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="basis-followup-dag-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.isolate = self.work / "synthetic"
        self.safety = self.isolate / "safety"
        annotation = self.isolate / "annotation"
        annotation.mkdir(parents=True)
        sample = {"sample_id": "synthetic", "isolate_dir": str(self.isolate),
                  "safety_dir": str(self.safety), "input_mode": "external", "taxon": ""}
        contents = {"assembly": ("fna", ">contig1\nATGAAATTTTAA\n"),
                    "proteins": ("faa", ">synthetic_cds\nMKF\n"),
                    "gff": ("gff", "##gff-version 3\ncontig1\tfixture\tCDS\t1\t12\t.\t+\t0\t"
                            "ID=synthetic_cds;locus_tag=synthetic_cds;transl_table=11\n")}
        for key, (suffix, content) in contents.items():
            path = annotation / ("synthetic." + suffix)
            path.write_text(content, encoding="utf-8")
            sample[key] = str(path)
        validate(sample, None, self.safety / "metadata")
        saved = json.loads((self.safety / "metadata/sample.json").read_text())
        hashes = saved["input_validation"]["input_hashes"]
        self.write("metadata/setup.ready.json", {"schema": "basis-snake-setup-v1",
                   "status": "completed", "inputs": hashes, "resources": [], "environments": []})
        deep = default_config()["deep"]
        deep.update(enabled=False, domain_scan=False, motifs=False, phylogeny=False,
                    auto_phylogeny=False, family_placement=False, prosite_profiles=False)
        config = {"version": "0.4.0a1", "engine": "snakemake", "options": {"deep": False},
                  "modules": {"minced": {"enabled": True}}, "deep": deep}
        config_path = self.write("metadata/config.json", config)
        self.source = self.write("evidence/minced.json", {
            "schema_version": 1, "sample_id": "synthetic", "module": "minced",
            "execution_status": "completed", "input_hashes": hashes,
            "findings": [], "metrics": {}, "artifacts": []})
        render(self.safety / "metadata/sample.json", config_path, [self.source], self.safety,
               view="screening", evidence_root=self.safety)
        sentinel = self.safety / "native/minced/sentinel.txt"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("Existing native output must remain unchanged.\n")
        self.supplied = {"isolate": str(self.isolate), "threads": 1,
                         "deep": {"enabled": True, "maximum_candidates": 5}}
        self.review_config = self.work / "review.yaml"
        self.save_review_config()
        self.workflow_root = ROOT
        if os.environ.get("BASIS_TEST_NO_BENCHMARK") == "1":
            # Container-only /proc sampling fallback. CI exercises production
            # rules unchanged; only these temporary copies omit benchmarks.
            self.workflow_root = self.work / "workflow-fixture"
            (self.workflow_root / "workflow/rules").mkdir(parents=True)
            shutil.copy2(ROOT / "Followup.smk", self.workflow_root / "Followup.smk")
            for name in ("lib", "vendor"):
                (self.workflow_root / "workflow" / name).symlink_to(ROOT / "workflow" / name,
                                                                   target_is_directory=True)
            (self.workflow_root / "envs").symlink_to(ROOT / "envs", target_is_directory=True)
            for name in ("deep.smk", "report.smk"):
                lines, skipped_indent = [], None
                for line in (ROOT / "workflow/rules" / name).read_text().splitlines():
                    indentation = len(line) - len(line.lstrip())
                    if line.lstrip().startswith("benchmark:"):
                        skipped_indent = indentation
                        continue
                    if skipped_indent is not None:
                        if not line.strip() or indentation > skipped_indent:
                            continue
                        skipped_indent = None
                    lines.append(line)
                (self.workflow_root / "workflow/rules" / name).write_text("\n".join(lines) + "\n")

    def write(self, relative, value):
        path = self.safety / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def save_review_config(self):
        self.review_config.write_text(yaml.safe_dump(self.supplied), encoding="utf-8")

    def snapshot(self):
        return {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                for path in self.safety.rglob("*") if path.is_file()}

    def assert_retained(self, snapshot):
        for name, expected in snapshot.items():
            path = Path(name)
            self.assertTrue(path.is_file(), name)
            self.assertEqual((hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns),
                             expected, name)

    def snake(self, *arguments):
        result = subprocess.run(
            [sys.executable, "-m", "snakemake", "--snakefile", str(self.workflow_root / "Followup.smk"),
             "--directory", str(self.work), "--cores", "1", "--configfile", str(self.review_config),
             *arguments], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        if result.returncode:
            for log in sorted((self.safety / "logs").rglob("*.log")):
                result.stdout += "\n" + str(log) + "\n" + log.read_text(errors="replace")[-1800:]
        return result

    def test_registered_rules_exclude_screening_and_preparation(self):
        before = self.snapshot()
        result = self.snake("--list-rules")
        self.assertEqual(result.returncode, 0, result.stdout[-6000:])
        rules = set(result.stdout.splitlines())
        self.assertIn("capture_followup_review", rules)
        self.assertIn("safety_deep_prepare", rules)
        self.assertIn("safety_deep_report", rules)
        for name in ("setup", "prepare_resource", "capture_environment", "validate_inputs",
                     "sample_setup_ready", "resolve_module_config", "minced", "amrfinder",
                     "crisprcasfinder_assembly", "normalize_native", "screening_reports", "safety_report"):
            self.assertNotIn(name, rules)
        self.assert_retained(before)
        self.assertFalse((self.safety / "deepsafe").exists())

    def test_missing_evidence_stops_without_producing_screening_or_followup(self):
        self.source.unlink()
        before = self.snapshot()
        result = self.snake("--dry-run")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing saved screening prerequisite", result.stdout)
        self.assertFalse(self.source.exists())
        self.assertFalse((self.safety / "deepsafe").exists())
        self.assert_retained(before)

    def test_empty_followup_resume_and_configuration_change_preserve_screening(self):
        original = self.snapshot()
        first = self.snake()
        self.assertEqual(first.returncode, 0, first.stdout[-12000:])
        self.assert_retained(original)
        self.assertTrue((self.safety / "deepsafe-report/report.html").is_file())
        result = json.loads((self.safety / "evidence/deepsafe.json").read_text())
        self.assertEqual(result["metrics"]["selected_candidates"], 0)
        self.assertEqual(result["commands"], [])
        complete = self.snapshot()
        resumed = self.snake("--dry-run")
        self.assertEqual(resumed.returncode, 0, resumed.stdout[-6000:])
        self.assertIn("Nothing to be done", resumed.stdout)
        self.assert_retained(complete)
        self.supplied["deep"]["maximum_candidates"] = 4
        self.save_review_config()
        changed = self.snake()
        self.assertEqual(changed.returncode, 0, changed.stdout[-12000:])
        self.assert_retained(original)
        effective = json.loads((self.safety / "deepsafe/review/config.json").read_text())
        self.assertEqual(effective["deep"]["maximum_candidates"], 4)
        result = json.loads((self.safety / "evidence/deepsafe.json").read_text())
        self.assertEqual(result["commands"], [])
        self.assertEqual(result["effective_config"]["maximum_candidates"], 4)


if __name__ == "__main__":
    unittest.main()
