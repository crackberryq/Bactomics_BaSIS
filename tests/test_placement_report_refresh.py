import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from BaSIS_refresh_placement_report import (
    derive_placement_diagnostics, refresh_placement_report, select_source,
    validate_evidence, verify_placement_artifacts,
)
from basis.util import hash_object, sha256_file


class PlacementReportRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tree = self.root / "reference.treefile"
        self.tree.write_text("(R1:0.1,R2:0.2,R3:0.3);\n")
        self.jplace = self.root / "epa_result.jplace"
        self.jplace.write_text('{"tree":"saved synthetic fixture"}\n')
        self.checks = {str(p): sha256_file(p) for p in (self.tree, self.jplace)}
        self.evidence = {
            "schema_version": "basis-report-1",
            "run": {"run_id": "historical", "finished_at": "original-finish", "completeness": "incomplete"},
            "samples": [{"sample_id": "isolate1"}], "records": [],
            "deep_records": [{"sample_id": "isolate1", "module": "deepsafe", "execution_status": "completed",
                "complete": False, "metrics": {"selected_candidates": 1, "deferred_candidates": 5},
                "placement_review": {"artifact_checksums": self.checks},
                "candidates": [{"candidate_id": "cand1", "protein_id": "P1", "selected": True,
                    "execution": "completed", "assessment": {"interpretation": "unresolved"},
                    "analyses": {"family_placement": {"status": "completed", "function_probability": None,
                        "artifacts": list(self.checks),
                        "reference_cache": {"directory": str(self.root),
                            "verified_artifact_hashes": {self.tree.name: self.checks[str(self.tree)]}}}}}]}],
        }
        self.seal()
        self.source = self.root / "evidence.json"
        self.save()

    def seal(self, timestamp="2026-10-10T22:20:00+00:00"):
        self.evidence["run"]["placement_review"] = {
            "schema": "basis-placement-review-1", "finished_at": timestamp,
            "records_sha256": hash_object(self.evidence["records"]),
            "samples_sha256": hash_object(self.evidence["samples"]),
            "deep_changes": [{"sample_id": d["sample_id"], "new_record_sha256": hash_object(d)}
                             for d in self.evidence["deep_records"]],
        }

    def save(self):
        self.source.parent.mkdir(parents=True, exist_ok=True)
        self.source.write_text(json.dumps(self.evidence))

    @staticmethod
    def fake_writer(run, samples, records, deep_records, outdir):
        html, evidence = outdir / "report.html", outdir / "evidence.json"
        html.write_text("<html>synthetic report-only output</html>")
        evidence.write_text(json.dumps({"run": run, "samples": samples, "records": records, "deep_records": deep_records}))
        return {"html": str(html), "evidence_json": str(evidence)}

    def test_report_only_preserves_all_scientific_records_and_source(self):
        original = copy.deepcopy(self.evidence)
        raw = self.source.read_bytes()
        with patch("basis.report.write_report", side_effect=self.fake_writer) as writer, \
                patch("basis.placement.run_family_placements") as native, \
                patch("basis.execution.CommandRunner.run") as command:
            merged, artifacts = refresh_placement_report(self.source, self.root / "out")
        writer.assert_called_once()
        native.assert_not_called()
        command.assert_not_called()
        self.assertEqual(self.source.read_bytes(), raw)
        for key in ("samples", "records", "deep_records"):
            self.assertEqual(merged[key], original[key])
        for key, value in original["run"].items():
            self.assertEqual(merged["run"][key], value)
        audit = merged["run"]["placement_report_refresh"]
        self.assertFalse(audit["native_analyses_run"])
        self.assertFalse(audit["gpu_inference_run"])
        self.assertFalse(audit["reference_downloads_run"])
        self.assertEqual(audit["source_sha256"], sha256_file(self.source))
        self.assertEqual({x["status"] for x in audit["artifact_checks"]}, {"verified"})
        output = self.root / "out"
        saved = json.loads((output / "placement-report-refresh.json").read_text())
        self.assertTrue(saved["source_unchanged"])
        self.assertEqual(saved["output_file_sha256"]["report.html"], sha256_file(artifacts["html"]))
        diagnostics = json.loads(Path(artifacts["placement_diagnostics_json"]).read_text())
        self.assertFalse(diagnostics["classification_policy_changed"])
        # This old fixture lacks the complete native artifact set; never infer a score.
        self.assertEqual(diagnostics["candidates"][0]["diagnostics"]["status"], "not_assessed")

    def test_derived_diagnostics_are_separate_from_historical_evidence(self):
        before = copy.deepcopy(self.evidence)
        checked = {"diagnostics": {"status": "completed", "function_probability": None,
                                  "exclusive_category_mass": {"competing_group_boundary": 1.0}},
                   "contents": {"epa_result.jplace": b"verified native fixture"}}
        with patch("basis.report_placement_tree._validated", return_value=checked), \
                patch("basis.execution.CommandRunner.run") as native:
            rows = derive_placement_diagnostics(self.evidence)
        native.assert_not_called()
        self.assertEqual(self.evidence, before)
        self.assertEqual(rows[0]["verification"], "native_artifacts_and_saved_scores_verified")
        self.assertEqual(rows[0]["source_assessment_sha256"], hash_object({"interpretation": "unresolved"}))
        rows[0]["diagnostics"]["status"] = "changed"
        self.assertEqual(checked["diagnostics"]["status"], "completed")

    def test_evidence_seal_changes_stop_before_rendering(self):
        for mutate in (
            lambda e: e["records"].append({"changed": True}),
            lambda e: e["samples"][0].update(taxon="changed"),
            lambda e: e["deep_records"][0]["candidates"][0]["assessment"].update(interpretation="changed"),
        ):
            evidence = copy.deepcopy(self.evidence)
            mutate(evidence)
            with self.assertRaisesRegex(ValueError, "seal differs|record differs"):
                validate_evidence(evidence)

    def test_tampered_artifact_aborts_before_creating_output(self):
        self.tree.write_text("changed\n")
        with patch("basis.report.write_report") as writer:
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                refresh_placement_report(self.source, self.root / "out")
            writer.assert_not_called()
        self.assertFalse((self.root / "out").exists())

    def test_missing_and_unsealed_artifacts_are_explicit_without_scientific_changes(self):
        self.tree.unlink()
        extra = self.root / "unsealed.txt"
        extra.write_text("unsealed")
        self.evidence["deep_records"][0]["candidates"][0]["analyses"]["family_placement"]["artifacts"].append(str(extra))
        results = {Path(x["path"]).name: x["status"] for x in verify_placement_artifacts(self.evidence)}
        self.assertEqual(results, {"reference.treefile": "missing", "epa_result.jplace": "verified", "unsealed.txt": "unrecorded_checksum"})

    def test_conflicting_seals_are_rejected(self):
        layer = self.evidence["deep_records"][0]["candidates"][0]["analyses"]["family_placement"]
        layer["reference_cache"]["verified_artifact_hashes"][self.tree.name] = "f" * 64
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            verify_placement_artifacts(self.evidence)

    def test_existing_output_is_never_overwritten(self):
        out = self.root / "out"
        out.mkdir()
        marker = out / "keep"
        marker.write_text("original")
        with patch("basis.report.write_report") as writer:
            with self.assertRaises(FileExistsError):
                refresh_placement_report(self.source, out)
            writer.assert_not_called()
        self.assertEqual(marker.read_text(), "original")

    def test_renderer_mutation_does_not_finalize_audit(self):
        def mutate(run, samples, records, deep_records, outdir):
            deep_records[0]["candidates"][0]["execution"] = "failed"
            return {}
        with patch("basis.report.write_report", side_effect=mutate):
            with self.assertRaisesRegex(RuntimeError, "Renderer changed"):
                refresh_placement_report(self.source, self.root / "out")
        self.assertFalse((self.root / "out/placement-report-refresh.json").exists())

    def test_source_and_artifact_changes_during_render_are_detected(self):
        for name, mutate in (("source", lambda: self.source.write_text(self.source.read_text() + "\n")),
                             ("artifact", lambda: self.tree.write_text("changed"))):
            def writer(*args):
                mutate()
                return {}
            with patch("basis.report.write_report", side_effect=writer):
                with self.assertRaisesRegex((ValueError, RuntimeError), "Source evidence changed|checksum mismatch"):
                    refresh_placement_report(self.source, self.root / name)
            self.assertFalse((self.root / name / "placement-report-refresh.json").exists())
            self.save()

    def test_default_discovery_uses_review_time_and_excludes_failed_reviews(self):
        review_root = self.root / "study/family-placement-reviews"
        self.source = review_root / "z-old/evidence.json"
        self.save()
        self.source = review_root / "a-new/evidence.json"
        self.seal("2026-10-11T22:20:00+00:00")
        self.save()
        chosen = self.source
        self.source = review_root / "latest-failed/evidence.json"
        self.evidence["deep_records"][0]["candidates"][0]["analyses"]["family_placement"]["status"] = "failed"
        self.seal("2026-10-12T22:20:00+00:00")
        self.save()
        self.assertEqual(select_source(self.root / "study"), chosen)

    def test_ambiguous_latest_review_requires_explicit_source(self):
        review_root = self.root / "study/family-placement-reviews"
        for name in ("one", "two"):
            self.source = review_root / name / "evidence.json"
            self.save()
        with self.assertRaisesRegex(ValueError, "share the latest"):
            select_source(self.root / "study")


if __name__ == "__main__":
    unittest.main()
