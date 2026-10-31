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
from safety.deep import load_document
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

    def isolate_workflow_sources(self):
        """Permit source edits without touching the checkout or shared symlinks."""
        source = self.workflow_root
        isolated = self.work / "isolated-workflow"
        (isolated / "workflow/rules").mkdir(parents=True)
        shutil.copy2(source / "Followup.smk", isolated / "Followup.smk")
        for name in ("lib", "vendor"):
            shutil.copytree(ROOT / "workflow" / name, isolated / "workflow" / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (isolated / "envs").symlink_to(ROOT / "envs", target_is_directory=True)
        for name in ("deep.smk", "report.smk"):
            shutil.copy2(source / "workflow/rules" / name, isolated / "workflow/rules" / name)
        self.workflow_root = isolated
        return isolated / "workflow/vendor/basis/deep_reporting.py"

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

    def test_nonempty_amr_review_through_saved_workflow_resume_and_report(self):
        """Real entry point and report, synthetic queries, no native binaries."""
        sample = json.loads((self.safety / "metadata/sample.json").read_text())
        Path(sample["assembly"]).write_text(">contig1\n" + "ATGAAATTTTAA" * 2 + "\n")
        Path(sample["proteins"]).write_text(">synthetic_cds\nMKF\n>synthetic_second\nMKF\n")
        Path(sample["gff"]).write_text(
            "##gff-version 3\n"
            "contig1\tfixture\tCDS\t1\t12\t.\t+\t0\tID=synthetic_cds;locus_tag=synthetic_cds;transl_table=11\n"
            "contig1\tfixture\tCDS\t13\t24\t.\t+\t0\tID=synthetic_second;locus_tag=synthetic_second;transl_table=11\n")
        validate(sample, None, self.safety / "metadata")
        sample = json.loads((self.safety / "metadata/sample.json").read_text())
        hashes = sample["input_validation"]["input_hashes"]
        self.write("metadata/setup.ready.json", {"schema": "basis-snake-setup-v1",
                   "status": "completed", "inputs": hashes, "resources": [], "environments": []})
        config = json.loads((self.safety / "metadata/config.json").read_text())
        config["modules"] = {name: {"enabled": True} for name in ("amrfinder", "rgi")}
        config["deep"]["prosite_patterns"] = False
        config_path = self.write("metadata/config.json", config)
        findings = [{"finding_id": "synthetic-hmm-" + str(i), "tool": "amrfinder",
                     "category": "amr", "gene": "synthetic_family", "protein_id": pid,
                     "contig": "contig1", "start": 1 + 12 * i, "end": 12 + 12 * i,
                     "strand": "+", "identity": 85.67 - i, "coverage": 100.,
                     "evidence_level": "curated_model", "attributes": {
                         "native_method": "HMM", "flags": ["family_hmm_only"], "raw": {"Method": "HMM"}}}
                    for i, pid in enumerate(("synthetic_cds", "synthetic_second"))]
        record = {"schema_version": 1, "sample_id": "synthetic", "module": "amrfinder",
                  "execution_status": "completed", "input_hashes": hashes,
                  "findings": findings, "metrics": {}, "artifacts": []}
        sources = [self.write("evidence/amrfinder.json", record)]
        linked = {**findings[0], "finding_id": "synthetic-linked-exact", "tool": "rgi",
                  "identity": 100., "attributes": {"cutoff": "perfect", "nudged": False,
                  "model_type": "protein homolog model"}}
        sources.append(self.write("evidence/rgi.json", {**record, "module": "rgi", "findings": [linked]}))
        render(self.safety / "metadata/sample.json", config_path, sources, self.safety,
               view="screening", evidence_root=self.safety)
        self.supplied["deep"].update(maximum_candidates=1,
                                     aware={"enabled": True, "scope": "amrfinder-review"})
        self.save_review_config()
        original = self.snapshot()

        first = self.snake("--until", "safety_deep_tree_plan")
        self.assertEqual(first.returncode, 0, first.stdout[-12000:])
        self.assert_retained(original)
        self.assertFalse((self.safety / "deepsafe-report/report.html").exists())
        prepare = load_document(self.safety / "deepsafe/states/prepare.json")
        self.assertEqual(len(prepare["selected_candidate_ids"]), 1)
        audit = prepare["state"]["aware_audit"]
        self.assertEqual((audit["counts"]["findings_received"], audit["counts"]["unique_candidates"],
                          audit["counts"]["selected_candidates"]), (3, 2, 1))
        retained = self.snapshot()

        finished = self.snake("--rerun-incomplete")
        self.assertEqual(finished.returncode, 0, finished.stdout[-12000:])
        self.assert_retained(retained)
        result = json.loads((self.safety / "evidence/deepsafe.json").read_text())
        self.assertEqual(result["commands"], [])
        selected = next(c for c in result["candidates"] if c["selected"])
        deferred = next(c for c in result["candidates"] if not c["selected"])
        self.assertEqual(len(selected["source_evidence"]), 2)
        self.assertEqual(selected["analysis_plan"]["route"], "ambiguous_amr_family_investigation")
        self.assertEqual((selected["execution"], selected["reason"]),
                         ("not_assessed", "reference_catalogue_not_configured"))
        self.assertEqual(deferred["reason"], "candidate_resource_budget")
        self.assertFalse(selected["assessment"]["function_established"])
        for phase in ("domains", "phylogeny", "patterns", "profiles", "motifs", "placement"):
            self.assertTrue((self.safety / "deepsafe/candidates" / selected["candidate_id"] / (phase + ".json")).is_file())
        self.assertFalse((self.safety / "deepsafe/candidates" / deferred["candidate_id"]).exists())
        report = self.safety / "deepsafe-report"
        bundle = json.loads((report / "evidence.json").read_text())
        from safety import __version__
        self.assertEqual(bundle["run"]["version"], __version__)
        self.assertEqual(bundle["run"]["screening_version"], "0.4.0a1")
        review = bundle["amr_review"]
        self.assertEqual(review["counts"]["investigation_findings"], 2)
        self.assertEqual({s for row in review["rows"] for s in row["followup_status"]},
                         {"not_assessed", "deferred"})
        self.assertIn("AMR evidence review outstanding", (report / "report.html").read_text())
        summary = json.loads((report / "metadata/run-summary.json").read_text())
        self.assertEqual(summary["samples"][0]["amr_review"]["counts"], review["counts"])
        complete = self.snapshot()
        unchanged = self.snake("--dry-run")
        self.assertEqual(unchanged.returncode, 0, unchanged.stdout[-6000:])
        self.assertIn("Nothing to be done", unchanged.stdout)
        self.assert_retained(complete)

        export = report / "tables/amr-review.tsv"
        expected = export.read_bytes()
        export.unlink()
        restored = self.snake()
        self.assertEqual(restored.returncode, 0, restored.stdout[-12000:])
        self.assertEqual(export.read_bytes(), expected)
        self.assert_retained(original)
        self.assert_retained({name: state for name, state in complete.items()
                              if "/deepsafe/" in name or name.endswith("/evidence/deepsafe.json")})

        # A terminal search with no eligible comparators is also unresolved.
        # Metadata-only synthetic retrieval exercises orchestration, not search
        # sensitivity or a native-tool validation claim.
        fasta = self.work / "synthetic-reference.faa"
        fasta.write_text(">synthetic-reference\nMKF\n")
        metadata = self.work / "synthetic-reference.tsv"
        metadata.write_text("reference_id\taccession\tfamily\tcomparison_group\trole\treview_status\tsource\n"
                            "synthetic-reference\tTEST.1\tunrelated_synthetic\tother\treference\tunreviewed\tsynthetic_fixture\n")
        self.supplied["deep"].update(reference_fasta=str(fasta), reference_metadata=str(metadata),
                                     reference_search_tool="metadata")
        self.save_review_config()
        reviewed = self.snake()
        self.assertEqual(reviewed.returncode, 0, reviewed.stdout[-12000:])
        self.assert_retained(original)
        result = json.loads((self.safety / "evidence/deepsafe.json").read_text())
        self.assertEqual(result["commands"], [])
        selected = next(c for c in result["candidates"] if c["selected"])
        self.assertEqual((selected["execution"], selected["assessment"]["interpretation"]),
                         ("completed", "unresolved"))
        self.assertFalse(selected["assessment"]["function_established"])
        review = json.loads((report / "evidence.json").read_text())["amr_review"]
        self.assertEqual(review["counts"]["investigation_findings"], 2)
        self.assertEqual({s for row in review["rows"] for s in row["followup_status"]},
                         {"completed_unresolved", "deferred"})

    def test_summary_helper_edit_refreshes_only_finalization_and_report(self):
        helper = self.isolate_workflow_sources()
        original = self.snapshot()
        first = self.snake()
        self.assertEqual(first.returncode, 0, first.stdout[-12000:])
        self.assert_retained(original)
        upstream = {
            name: identity for name, identity in self.snapshot().items()
            if ("/deepsafe/states/" in name and not name.endswith("/finalize.json"))
            or "/deepsafe/review/" in name
            or "/deepsafe/native/prepare/" in name
        }
        self.assertTrue(upstream)
        evidence = self.safety / "evidence/deepsafe.json"
        report = self.safety / "deepsafe-report/report.html"
        old_outputs = {path: path.stat().st_mtime_ns for path in (evidence, report)}
        helper.write_text(helper.read_text() + "\n# Synthetic reporting-only source revision.\n")

        preview = self.snake("--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stdout[-12000:])
        self.assertIn("safety_deep_finalize", preview.stdout)
        self.assertIn("safety_deep_report", preview.stdout)
        for rule in ("capture_followup_review", "safety_deep_resource_inputs",
                     "safety_deep_prepare", "safety_deep_candidate", "safety_deep_references",
                     "safety_deep_tree_plan", "safety_deep_placement_plan"):
            self.assertNotIn(rule, preview.stdout, preview.stdout[-12000:])
        refreshed = self.snake()
        self.assertEqual(refreshed.returncode, 0, refreshed.stdout[-12000:])
        self.assert_retained(original)
        self.assert_retained(upstream)
        for path, previous_time in old_outputs.items():
            self.assertGreater(path.stat().st_mtime_ns, previous_time, str(path))
        self.assertEqual(json.loads(evidence.read_text())["commands"], [])
        complete = self.snapshot()
        resumed = self.snake("--dry-run")
        self.assertEqual(resumed.returncode, 0, resumed.stdout[-6000:])
        self.assertIn("Nothing to be done", resumed.stdout)
        self.assert_retained(complete)

    def test_candidate_rules_exclude_summary_helper_but_consumers_track_it(self):
        helper = self.isolate_workflow_sources()
        inventory = self.work / "declared-sources.json"
        snakefile = self.workflow_root / "Followup.smk"
        with snakefile.open("a") as stream:
            stream.write(
                "\nrule inspect_declared_sources:\n"
                f"    output: {str(inventory)!r}\n"
                "    run:\n"
                "        names = ('safety_deep_prepare', 'safety_deep_candidate', "
                "'safety_deep_references', 'safety_deep_tree_plan', "
                "'safety_deep_placement_plan', 'safety_deep_finalize', 'safety_deep_report')\n"
                "        Path(output[0]).write_text(json.dumps({name: "
                "list(workflow.get_rule(name).input.source) for name in names}))\n")
        result = self.snake("--", "inspect_declared_sources")
        self.assertEqual(result.returncode, 0, result.stdout[-12000:])
        sources = json.loads(inventory.read_text())
        for rule, paths in sources.items():
            if rule in {"safety_deep_finalize", "safety_deep_report"}:
                self.assertIn(str(helper), paths, rule)
            else:
                self.assertNotIn(str(helper), paths, rule)
        self.assertFalse((self.safety / "deepsafe").exists())


if __name__ == "__main__":
    unittest.main()
