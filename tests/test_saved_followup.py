"""Saved-screening follow-up contracts using synthetic files; no analyses."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from basis.config import default_config
from basis.prosite import FILES as PROSITE_FILES
from safety.followup import SavedFollowupContext
from safety.reporting import render
from safety.setup import validate


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class SavedFollowupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="basis-saved-followup-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.isolate = self.work / "synthetic"
        self.safety = self.isolate / "safety"
        annotation = self.isolate / "annotation"
        annotation.mkdir(parents=True)
        self.assembly = annotation / "synthetic.fna"
        self.proteins = annotation / "synthetic.faa"
        self.gff = annotation / "synthetic.gff"
        self.assembly.write_text(">contig1\nATGAAATTTTAA\n", encoding="utf-8")
        self.proteins.write_text(">synthetic_cds\nMKF\n", encoding="utf-8")
        self.gff.write_text(
            "##gff-version 3\ncontig1\tfixture\tCDS\t1\t12\t.\t+\t0\t"
            "ID=synthetic_cds;locus_tag=synthetic_cds;transl_table=11\n",
            encoding="utf-8")
        sample = {"sample_id": "synthetic", "isolate_dir": str(self.isolate),
                  "safety_dir": str(self.safety), "input_mode": "external", "taxon": "",
                  "assembly": str(self.assembly), "proteins": str(self.proteins),
                  "gff": str(self.gff)}
        validate(sample, None, self.safety / "metadata")
        self.sample_path = self.safety / "metadata/sample.json"
        self.sample = self.read(self.sample_path)
        self.hashes = self.sample["input_validation"]["input_hashes"]
        self.write("metadata/setup.ready.json", {
            "schema": "basis-snake-setup-v1", "status": "completed", "inputs": self.hashes,
            "resources": [], "environments": []})
        resources = self.work / "prepared-resources"
        resources.mkdir()
        self.deep = copy.deepcopy(default_config()["deep"])
        self.deep["enabled"] = False
        for key, filename in (("reference_fasta", "references.faa"),
                              ("reference_metadata", "references.tsv"),
                              ("domain_database", "domains.hmm"),
                              ("motif_rules", "motifs.json")):
            path = resources / filename
            path.write_text("synthetic resource placeholder\n", encoding="utf-8")
            self.deep[key] = str(path)
        profiles = resources / "profiles"
        profiles.mkdir()
        (profiles / "synthetic.yaml").write_text("profile_id: synthetic\n", encoding="utf-8")
        self.deep["profiles_dir"] = str(profiles)
        prosite = resources / "prosite"
        prosite.mkdir()
        for filename in (*PROSITE_FILES, "provenance.json", "prorule.dat"):
            (prosite / filename).write_text("synthetic snapshot placeholder\n", encoding="utf-8")
        self.deep["prosite_snapshot"] = str(prosite)
        self.config = {"version": "0.4.0a1", "engine": "snakemake",
                       "modules": {"minced": {"enabled": True}}, "deep": self.deep,
                       "options": {"deep": False}}
        self.config_path = self.write("metadata/config.json", self.config)
        self.record = {"schema_version": 1, "sample_id": "synthetic", "module": "minced",
                       "execution_status": "completed", "input_hashes": self.hashes,
                       "findings": [], "metrics": {}, "artifacts": []}
        self.record_path = self.write("evidence/minced.json", self.record)
        self.render_screening()
        self.supplied = {"isolate": str(self.isolate), "deep": {"enabled": True}}

    def read(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def write(self, relative, value):
        path = self.safety / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def render_screening(self, paths=None):
        render(self.sample_path, self.config_path,
               paths if paths is not None else [self.record_path], self.safety,
               view="screening", evidence_root=self.safety)

    def context(self, supplied=None):
        return SavedFollowupContext(self.supplied if supplied is None else supplied, ROOT)

    def snapshot(self):
        return {str(p.relative_to(self.safety)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.safety.rglob("*") if p.is_file()}

    def replace_source_manifest(self, sources):
        """Simulate internally checksummed but structurally invalid saved data."""
        bundle = self.read(self.safety / "evidence.json")
        bundle["run"]["evidence_sources"] = sources
        self.write("evidence.json", bundle)
        navigation = self.read(self.safety / "metadata/report-navigation.json")
        navigation["evidence_sha256"] = digest(self.safety / "evidence.json")
        self.write("metadata/report-navigation.json", navigation)

    def test_context_is_read_only_and_publication_preserves_screening(self):
        before = self.snapshot()
        with patch("subprocess.run", side_effect=AssertionError("No subprocess is permitted")):
            context = self.context()
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(context.records, [str(self.record_path)])
            self.assertEqual(context.config["modules"], self.config["modules"])
            self.assertTrue(context.config["deep"]["enabled"])
            self.assertTrue(context.followup_only)
            self.assertTrue(context.deep_enabled)
            context.publish()
        for name, saved in before.items():
            path = self.safety / name
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), saved, name)
        config_path = Path(context.followup_config_path(str(self.isolate)))
        self.assertEqual(self.read(config_path), context.config)
        before_second_publish = self.snapshot()
        context.publish()
        self.assertEqual(self.snapshot(), before_second_publish)

    def test_enable_requires_explicit_boolean_and_screening_overrides_are_rejected(self):
        for deep in (None, {}, {"enabled": False}, {"enabled": "true"}, True):
            supplied = {"isolate": str(self.isolate)}
            if deep is not None:
                supplied["deep"] = deep
            with self.subTest(deep=deep), self.assertRaisesRegex(ValueError, "disabled"):
                self.context(supplied)
        for key, value in (("modules", {}), ("samples", "samples.tsv"), ("resources", {})):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "screening settings"):
                self.context({**self.supplied, key: value})

    def test_missing_screening_sources_fail_without_creating_outputs(self):
        for relative in ("report.html", "evidence.json", "metadata/report-navigation.json",
                         "metadata/sample.json", "metadata/config.json", "metadata/validation.json",
                         "metadata/setup.ready.json", "evidence/minced.json"):
            with self.subTest(path=relative):
                path = self.safety / relative
                original = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(ValueError, "Missing saved screening prerequisite"):
                    self.context()
                self.assertFalse((self.safety / "deepsafe/review").exists())
                path.write_bytes(original)

    def test_malformed_review_settings_are_rejected_without_coercion(self):
        cases = (({"motifs": "false"}, "YAML boolean"),
                 ({"domain_scan": "false"}, "YAML boolean"),
                 ({"maximum_candidates": 0}, "positive integer"),
                 ({"maximum_candiates": 0}, "Unknown follow-up settings"),
                 ({"profiles_dir": False}, "nonempty path or null"))
        before = self.snapshot()
        for update, message in cases:
            supplied = {**self.supplied, "deep": {"enabled": True, **update}}
            original = copy.deepcopy(supplied)
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, message):
                self.context(supplied)
            self.assertEqual(supplied, original)
            self.assertEqual(self.snapshot(), before)

    def test_execution_budgets_reject_negative_boolean_and_noninteger_values(self):
        keys = ("maximum_candidates", "maximum_attention_candidates", "maximum_exploratory_candidates",
                "maximum_tree_candidates", "maximum_placement_candidates")
        for key in keys:
            for value in (-1, True, 1.5, "2"):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, "integer"):
                    self.context({**self.supplied, "deep": {"enabled": True, key: value}})
        self.assertFalse((self.safety / "deepsafe/review").exists())

    def test_valid_policy_values_are_preserved_without_reinterpretation(self):
        update = {"enabled": True, "motifs": False, "domain_scan": False,
                  "maximum_candidates": 2, "maximum_attention_candidates": 0,
                  "maximum_exploratory_candidates": 0, "maximum_tree_candidates": 0,
                  "maximum_placement_candidates": 0,
                  "aware": {"enabled": True, "preset": "auto-medium", "tools": {}, "queues": {}}}
        supplied = {**self.supplied, "deep": update}
        original = copy.deepcopy(supplied)
        context = self.context(supplied)
        for key, value in update.items():
            self.assertEqual(context.config["deep"][key], value, key)
        self.assertEqual(supplied, original)
        self.assertEqual(self.read(self.config_path), self.config)

    def test_invalid_inherited_controls_are_rejected_when_followup_is_enabled(self):
        for key, value, message in (("motifs", "false", "YAML boolean"),
                                    ("profiles_dir", False, "nonempty path or null")):
            saved = copy.deepcopy(self.config)
            saved["deep"][key] = value
            self.write("metadata/config.json", saved)
            self.render_screening()
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, message):
                self.context()
            self.assertEqual(self.read(self.config_path), saved)
            self.write("metadata/config.json", self.config)
            self.render_screening()

    def test_changed_snapshot_sources_are_rejected(self):
        for relative in ("report.html", "evidence.json", "metadata/sample.json",
                         "metadata/config.json", "evidence/minced.json"):
            with self.subTest(path=relative):
                path = self.safety / relative
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                with self.assertRaisesRegex(ValueError, "stale|changed|does not match"):
                    self.context()
                path.write_bytes(original)

    def test_same_length_annotation_change_is_rejected(self):
        original = self.proteins.read_text()
        self.proteins.write_text(original.replace("MKF", "MKY"))
        with self.assertRaisesRegex(ValueError, "annotation is missing or changed: proteins"):
            self.context()

    def test_validation_setup_and_evidence_input_identities_must_agree(self):
        validation = self.read(self.safety / "metadata/validation.json")
        self.write("metadata/validation.json", {**validation, "unexpected": "changed"})
        with self.assertRaisesRegex(ValueError, "validation/setup snapshot"):
            self.context()
        self.write("metadata/validation.json", validation)
        ready_path = self.safety / "metadata/setup.ready.json"
        ready = self.read(ready_path)
        self.write("metadata/setup.ready.json", {**ready, "inputs": {}})
        with self.assertRaisesRegex(ValueError, "validation/setup snapshot"):
            self.context()
        self.write("metadata/setup.ready.json", ready)
        self.write("evidence/minced.json", {**self.record, "input_hashes": {}})
        self.render_screening()
        with self.assertRaisesRegex(ValueError, "evidence input identity differs"):
            self.context()

    def test_scope_holds_remain_visible_and_do_not_require_native_artifact_trees(self):
        held = {**self.record, "execution_status": "not_assessed", "reason": "synthetic_review_hold",
                "module_decision": {"requested": True, "action": "withheld", "scope_incomplete": True,
                                    "reason": "synthetic_review_hold"},
                "artifacts": [str(self.work / "old-native-directory")],
                "artifact_hashes": {str(self.work / "old-native-directory/missing.txt"): "0" * 64}}
        self.write("evidence/minced.json", held)
        self.render_screening()
        context = self.context()
        self.assertEqual(self.read(Path(context.records[0]))["reason"], "synthetic_review_hold")
        self.assertEqual(self.read(self.safety / "evidence.json")["run"]["completeness"], "incomplete")

    def test_stale_unrequested_evidence_is_not_discovered(self):
        self.write("evidence/deepsafe.json", {"execution_status": "failed"})
        self.write("evidence/amrfinder.json", {"execution_status": "failed"})
        self.assertEqual(self.context().records, [str(self.record_path)])

    def test_source_manifest_cannot_omit_or_duplicate_requested_modules(self):
        sources = self.read(self.safety / "evidence.json")["run"]["evidence_sources"]
        for changed, message in (([], "Missing requested evidence"),
                                 (sources + sources, "duplicate evidence module")):
            with self.subTest(sources=changed):
                self.replace_source_manifest(changed)
                with self.assertRaisesRegex(ValueError, message):
                    self.context()
                self.render_screening()

    def test_source_checksums_do_not_replace_sample_and_execution_checks(self):
        for changes, message in (({"sample_id": "another-sample"}, "sample identity mismatch"),
                                 ({"execution_status": "failed"}, "minced is failed")):
            with self.subTest(changes=changes):
                self.write("evidence/minced.json", {**self.record, **changes})
                self.replace_source_manifest([{"module": "minced", "path": str(self.record_path),
                                               "sha256": digest(self.record_path)}])
                with self.assertRaisesRegex(ValueError, message):
                    self.context()
                self.write("evidence/minced.json", self.record)
                self.render_screening()

    def test_input_and_output_aliases_cannot_modify_screening(self):
        original = self.record_path.read_bytes()
        target = self.work / "source-record.json"
        target.write_bytes(original)
        self.record_path.unlink()
        self.record_path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.context()
        self.record_path.unlink()
        self.record_path.write_bytes(original)
        review = self.safety / "deepsafe/review"
        review.mkdir(parents=True)
        (review / "config.json").symlink_to(self.config_path)
        saved = self.config_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.context()
        self.assertEqual(self.config_path.read_bytes(), saved)

    def test_publish_rechecks_source_snapshot_after_dag_creation(self):
        context = self.context()
        self.record_path.write_bytes(self.record_path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            context.publish()
        self.assertFalse((self.safety / "deepsafe/review/config.json").exists())

    def test_publish_rejects_output_directory_alias_added_after_preflight(self):
        context = self.context()
        deep = self.safety / "deepsafe"
        deep.mkdir()
        (deep / "review").symlink_to(self.safety / "metadata", target_is_directory=True)
        saved = self.config_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "symlink"):
            context.publish()
        self.assertEqual(self.config_path.read_bytes(), saved)

    def test_missing_resource_stops_without_preparation(self):
        Path(self.deep["domain_database"]).unlink()
        with patch("subprocess.run", side_effect=AssertionError("No setup is permitted")):
            with self.assertRaisesRegex(ValueError, "Missing prepared DeepSafe resource"):
                self.context()
        self.assertFalse((self.safety / "deepsafe/review").exists())

    def test_report_rejects_changed_screening_snapshot_before_writing_outputs(self):
        context = self.context()
        context.publish()
        deep_path = self.write("evidence/deepsafe.json", {
            "sample_id": "synthetic", "module": "deepsafe", "execution_status": "completed",
            "findings": [], "metrics": {}, "artifacts": [], "complete": True, "candidates": []})
        output = self.safety / "deepsafe-report"
        for source in (self.record_path, self.proteins):
            original = source.read_bytes()
            source.write_bytes(original + b"\n")
            before = self.snapshot()
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "changed after review"):
                render(self.sample_path, Path(context.followup_config_path(str(self.isolate))),
                       [self.record_path, deep_path], output, view="deep", evidence_root=self.safety)
            self.assertFalse(output.exists())
            self.assertEqual(self.snapshot(), before)
            source.write_bytes(original)

    def test_existing_receipts_fill_unset_paths_but_preserve_explicit_policy(self):
        original_deep = copy.deepcopy(self.deep)
        self.config["deep"] = {**self.deep, "reference_fasta": None, "reference_metadata": None}
        self.write("metadata/config.json", self.config)
        self.render_screening()
        artifact_paths = [Path(original_deep[key]) for key in ("reference_fasta", "reference_metadata")]
        receipt = self.work / "existing-receipt.json"
        receipt.write_text(json.dumps({
            "schema": "basis-snakemake-resource-v1", "status": "completed", "resource": "deep_catalogue",
            "artifacts": {str(p): {"bytes": p.stat().st_size, "sha256": digest(p)} for p in artifact_paths},
            "result": {"deep_updates": {"reference_fasta": original_deep["reference_fasta"],
                                        "reference_metadata": original_deep["reference_metadata"],
                                        "maximum_candidates": 99}}}), encoding="utf-8")
        supplied = {**self.supplied, "resource_receipts": [str(receipt)],
                    "deep": {"enabled": True, "maximum_candidates": 2}}
        context = self.context(supplied)
        self.assertEqual(context.config["deep"]["reference_fasta"], original_deep["reference_fasta"])
        self.assertEqual(context.config["deep"]["maximum_candidates"], 2)
        self.assertIn(str(receipt), context.prerequisites)
        artifact_paths[0].write_text("changed resource bytes\n")
        with self.assertRaisesRegex(ValueError, "Missing or changed|checksum mismatch"):
            self.context(supplied)


if __name__ == "__main__":
    unittest.main()
