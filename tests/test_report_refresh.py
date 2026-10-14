import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.report_refresh import prepare_refresh, protected_candidates, refresh_report, verify_source
from basis.util import hash_object, sha256_file


def fixture():
    selected = {
        "candidate_id": "c1", "protein_id": "p1", "gene": "EreB",
        "family": "macrolide esterase", "selected": True,
        "selection_queue": "native_screen", "execution": "completed",
        "source_evidence": [
            {"tool": "rgi", "finding_id": "f1", "gene": "EreB", "family": "macrolide esterase",
             "evidence_level": "exploratory", "category": "amr", "attributes": {"cutoff": "loose"}},
            {"tool": "bacmet", "finding_id": "f2", "gene": "chrR", "family": None,
             "evidence_level": "homology", "category": "stress", "attributes": {"passes_threshold": True}}],
        "analysis_plan": {"route": "historical", "decision_trace": [{"source_gene_labels": []}]},
        "analyses": {"domains": {"status": "completed", "hits": [{"name": "domain"}]}},
        "profile": {"profile_id": "generic"},
        "assessment": {"interpretation": "unresolved", "function_established": False},
    }
    deferred = {"candidate_id": "c2", "gene": "weak", "family": None, "selected": False,
                "selection_queue": "exploratory", "execution": "deferred",
                "source_evidence": [{"gene": "weak", "evidence_level": "exploratory"}]}
    ice = {"sample_id": "s1", "module": "icescreen", "execution_status": "completed",
           "findings": [], "metrics": {"predicted_element_count": 0,
                                         "summary": {"1": {"Unassigned Integrase": 7}}}}
    deep = {"sample_id": "s1", "module": "deepsafe", "execution_status": "completed",
            "complete": False, "upstream_incomplete": [], "candidates": [selected, deferred],
            "metrics": {"selected_candidates": 1, "completed_candidates": 1, "deferred_candidates": 1},
            "cache_key": "original_key", "cache_hit": True,
            "cache_checked_at": "original_time", "deep_cache": {"source": "historical"}}
    reconciliation = {"schema_version": "basis-ice-reconciliation-1", "sample_id": "s1",
                      "retry_record_sha256": hash_object(ice),
                      "deep_changes": [{"sample_id": "s1", "candidate_evidence_sha256": hash_object(deep["candidates"])}]}
    return {"schema_version": "basis-report-1", "run": {
                "run_id": "original-run", "completeness": "incomplete",
                "incomplete_modules": ["s1:deepsafe"], "finished_at": "original_time",
                "report_reconciliation": reconciliation},
            "samples": [{"sample_id": "s1", "taxon": "Lysinibacillus irui"}],
            "records": [ice, {"module": "bacmet", "sample_id": "s1", "execution_status": "completed",
                              "findings": [{"raw": {"preserve": "all original annotations"}}]}],
            "deep_records": [deep]}


class ReportRefreshTests(unittest.TestCase):
    def test_valid_source_and_copy_preserve_scientific_and_native_evidence(self):
        source = fixture()
        original = copy.deepcopy(source)
        verify_source(source)
        merged = prepare_refresh(source, "/source/evidence.json", "source-sha", "new-time")
        self.assertEqual(source, original)
        self.assertEqual(merged["records"], original["records"])
        self.assertEqual(merged["samples"], original["samples"])
        for key, value in original["run"].items():
            self.assertEqual(merged["run"][key], value, key)
        old, new = original["deep_records"][0], merged["deep_records"][0]
        self.assertEqual(new["candidates"][0]["gene"], "chrR")
        self.assertIsNone(new["candidates"][0]["family"])
        self.assertEqual(protected_candidates(new["candidates"]), protected_candidates(old["candidates"]))
        self.assertEqual(new["candidates"][1], old["candidates"][1])
        for field in ("complete", "upstream_incomplete", "metrics", "execution_status"):
            self.assertEqual(new[field], old[field], field)
        audit = merged["run"]["report_refresh"]
        self.assertEqual(audit["canonical_label_corrections"], 1)
        self.assertFalse(audit["native_analyses_run"])
        self.assertFalse(audit["gpu_inference_run"])
        self.assertEqual(audit["native_records_sha256"], hash_object(original["records"]))
        record_audit = audit["deep_changes"][0]
        self.assertEqual(record_audit["record_before_sha256"], hash_object(old))
        self.assertEqual(record_audit["record_after_sha256"], hash_object(new))
        self.assertEqual(set(record_audit["removed_cache_metadata"]),
                         {"cache_key", "cache_hit", "cache_checked_at", "deep_cache"})
        for field in record_audit["removed_cache_metadata"]:
            self.assertNotIn(field, new)
            self.assertEqual(source["deep_records"][0][field], old[field])
        self.assertIn("Source database labels only", new["candidates"][0]["label_provenance"]["interpretation"])

    def test_unchanged_record_retains_cache_metadata(self):
        source = fixture()
        source["deep_records"][0]["candidates"][0]["selected"] = False
        source["run"]["report_reconciliation"]["deep_changes"][0]["candidate_evidence_sha256"] = hash_object(source["deep_records"][0]["candidates"])
        merged = prepare_refresh(source, "/source.json", "sha", "now")
        self.assertEqual(merged["deep_records"], source["deep_records"])
        audit = merged["run"]["report_refresh"]
        self.assertEqual(audit["canonical_label_corrections"], 0)
        self.assertEqual(audit["deep_changes"][0]["removed_cache_metadata"], {})

    def test_rejects_corrupt_ice_seal(self):
        source = fixture()
        source["records"][0]["metrics"]["predicted_element_count"] = 1
        with self.assertRaisesRegex(ValueError, "ICEscreen record differs"):
            verify_source(source)

    def test_rejects_corrupt_candidate_seal(self):
        source = fixture()
        source["deep_records"][0]["candidates"][0]["assessment"]["function_established"] = True
        with self.assertRaisesRegex(ValueError, "candidate evidence differs"):
            verify_source(source)

    def test_rejects_incomplete_ambiguous_or_repeated_refresh(self):
        for mutate in (
                lambda s: s["deep_records"].append(copy.deepcopy(s["deep_records"][0])),
                lambda s: s["records"].append(copy.deepcopy(s["records"][0])),
                lambda s: s["run"].update(report_refresh={"already": True}),
                lambda s: s["deep_records"][0].update(execution_status="failed")):
            source = fixture()
            mutate(source)
            with self.assertRaises(ValueError):
                verify_source(source)

    def test_existing_output_is_guarded_before_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.json"
            source.write_text(json.dumps(fixture()))
            out = root / "out"
            out.mkdir()
            (out / "keep.txt").write_text("preserve")
            with patch("basis.report.write_report") as writer:
                with self.assertRaises(FileExistsError):
                    refresh_report(source, out)
                writer.assert_not_called()
            self.assertEqual((out / "keep.txt").read_text(), "preserve")

    def test_successful_write_has_audit_and_unchanged_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, out = root / "source.json", root / "out"
            source.write_text(json.dumps(fixture()))
            before = source.read_bytes()
            def write(run, samples, records, deep_records, directory):
                target = directory / "report.html"
                target.write_text("synthetic rendered report")
                return {"html": str(target)}
            with patch("basis.report.write_report", side_effect=write) as writer:
                merged, artifacts = refresh_report(source, out)
            writer.assert_called_once()
            self.assertEqual(source.read_bytes(), before)
            audit = json.loads((out / "review.json").read_text())
            self.assertTrue(audit["source_unchanged"])
            self.assertEqual(audit["source_evidence"]["sha256"], sha256_file(source))
            self.assertEqual(audit["output_file_sha256"]["report.html"], sha256_file(out / "report.html"))
            self.assertEqual(artifacts["html"], str(out / "report.html"))
            self.assertEqual(merged["run"]["completeness"], "incomplete")

    def test_renderer_mutation_does_not_finalize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, out = root / "source.json", root / "out"
            source.write_text(json.dumps(fixture()))
            def mutate(run, samples, records, deep_records, directory):
                deep_records[0]["candidates"][0]["execution"] = "failed"
                return {}
            with patch("basis.report.write_report", side_effect=mutate):
                with self.assertRaisesRegex(RuntimeError, "Renderer changed"):
                    refresh_report(source, out)
            self.assertFalse((out / "review.json").exists())

    def test_source_mutation_does_not_finalize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, out = root / "source.json", root / "out"
            source.write_text(json.dumps(fixture()))
            def mutate(run, samples, records, deep_records, directory):
                source.write_text(source.read_text() + "\n")
                return {}
            with patch("basis.report.write_report", side_effect=mutate):
                with self.assertRaisesRegex(RuntimeError, "Source evidence changed"):
                    refresh_report(source, out)
            self.assertFalse((out / "review.json").exists())


if __name__ == "__main__":
    unittest.main()
