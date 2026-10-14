import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.placement_review import review_saved_placement, verify_saved_queries
from basis.references import sequence_hash
from basis.util import atomic_json, hash_object, sha256_file


class PlacementReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        paths = {key: self.root / name for key, name in
                 (("proteins", "proteins.faa"), ("assembly", "assembly.fna"), ("gff", "genes.gff"), ("genbank", "genes.gbk"))}
        paths["proteins"].write_text(">protein1 annotation\nACDEFGHIK\n")
        paths["assembly"].write_text(">1\nACGT\n")
        paths["gff"].write_text("##gff-version 3\n")
        paths["genbank"].write_text("LOCUS       original\n")
        query = self.root / "query.faa"
        query.write_text(">Q_cand1\nACDEFGHIK\n")
        c = {"candidate_id": "cand1", "protein_id": "protein1", "gene": "cfr", "selected": True,
             "selection_queue": "native_screen", "execution": "completed",
             "query": {"id": "Q_cand1", "path": str(query), "sequence_sha256": sequence_hash("ACDEFGHIK")},
             "source_evidence": [{"tool": "amrfinder", "gene": "cfr", "evidence_level": "curated_model",
                                  "attributes": {}}],
             "analyses": {"integrity": {"status": "completed", "flags": []},
                          "phylogeny": {"status": "completed", "reason": "historical_review"}},
             "assessment": {"interpretation": "subgroup_unresolved", "function_established": False}}
        self.evidence = {"schema_version": "basis-report-1", "run": {"run_id": "original",
                         "completeness": "incomplete", "incomplete_modules": ["isolate1:deepsafe"]},
                         "samples": [{"sample_id": "isolate1", **{k: str(v) for k, v in paths.items()}}],
                         "records": [{"sample_id": "isolate1", "module": "amrfinder", "execution_status": "completed",
                                      "findings": [{"gene": "cfr", "source": "historical"}]}],
                         "deep_records": [{"sample_id": "isolate1", "module": "deepsafe", "execution_status": "completed",
                                           "complete": True, "candidates": [c], "upstream_incomplete": [],
                                           "input_hashes": {k: sha256_file(v) for k, v in paths.items()},
                                           "metrics": {"selected_candidates": 1, "completed_candidates": 1,
                                                       "deferred_candidates": 0}, "artifacts": [],
                                           "cache_key": "historical-key", "cache_hit": True,
                                           "cache_checked_at": "historical-time", "deep_cache": {"seal": "old"}}]}
        self.source = self.root / "evidence.json"
        atomic_json(self.source, self.evidence)
        self.cfg = {"deep": {"family_placement": True, "placement_allow_download": False},
                    "config_path": str(self.root / "config.yaml")}

    def report_writer(self, run, samples, records, deep, outdir):
        path = Path(outdir) / "evidence.json"
        atomic_json(path, {"schema_version": "basis-report-1", "run": run,
                           "samples": samples, "records": records, "deep_records": deep})
        html = Path(outdir) / "report.html"
        html.write_text("<html>review</html>")
        return {"evidence_json": str(path), "html": str(html)}

    def fake_layer(self, candidates, config, work, runner):
        artifact = Path(work) / "placement.json"
        atomic_json(artifact, {"status": "completed", "function_probability": None})
        for candidate in candidates:
            candidate.setdefault("analyses", {})["family_placement"] = {
                "status": "completed", "reason": "placement_completed", "function_probability": None}
        return [str(artifact)]

    def run_review(self, side_effect=None, output="review"):
        with patch("basis.placement_review.run_family_placements", side_effect=side_effect or self.fake_layer), \
                patch("basis.report.write_report", side_effect=self.report_writer):
            return review_saved_placement(self.source, self.root / output, self.cfg)

    def test_verified_exact_queries_and_all_native_input_links(self):
        audit = verify_saved_queries(self.evidence)
        self.assertEqual(audit[0]["input_hashes"], self.evidence["deep_records"][0]["input_hashes"])
        self.assertEqual(audit[0]["verified_queries"][0]["protein_id"], "protein1")

    def test_query_hash_tamper_rejected_before_analysis(self):
        self.evidence["deep_records"][0]["candidates"][0]["query"]["sequence_sha256"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "checksum"):
            verify_saved_queries(self.evidence)

    def test_query_must_match_proteome_even_with_self_consistent_hash(self):
        query = self.evidence["deep_records"][0]["candidates"][0]["query"]
        Path(query["path"]).write_text(">Q_cand1\nACDEFGHIL\n")
        query["sequence_sha256"] = sequence_hash("ACDEFGHIL")
        with self.assertRaisesRegex(ValueError, "proteome"):
            verify_saved_queries(self.evidence)

    def test_query_id_and_input_tampering_rejected(self):
        query = self.evidence["deep_records"][0]["candidates"][0]["query"]
        Path(query["path"]).write_text(">unexpected\nACDEFGHIK\n")
        with self.assertRaisesRegex(ValueError, "identifier"):
            verify_saved_queries(self.evidence)
        Path(query["path"]).write_text(">Q_cand1\nACDEFGHIK\n")
        Path(self.evidence["samples"][0]["gff"]).write_text("modified\n")
        with self.assertRaisesRegex(ValueError, "input changed"):
            verify_saved_queries(self.evidence)

    def test_missing_or_unknown_input_hash_keys_are_rejected(self):
        for key in ("assembly", "gff", "genbank"):
            with self.subTest(key=key):
                data = copy.deepcopy(self.evidence)
                del data["deep_records"][0]["input_hashes"][key]
                with self.assertRaises(ValueError):
                    verify_saved_queries(data)
        data = copy.deepcopy(self.evidence)
        data["deep_records"][0]["input_hashes"]["arbitrary_file"] = "f" * 64
        with self.assertRaises(ValueError):
            verify_saved_queries(data)

    def test_duplicate_sample_candidate_and_query_ids_are_rejected(self):
        data = copy.deepcopy(self.evidence)
        data["samples"].append(copy.deepcopy(data["samples"][0]))
        with self.assertRaises(ValueError):
            verify_saved_queries(data)
        data = copy.deepcopy(self.evidence)
        data["deep_records"][0]["candidates"].append(copy.deepcopy(data["deep_records"][0]["candidates"][0]))
        with self.assertRaises(ValueError):
            verify_saved_queries(data)
        data["deep_records"][0]["candidates"][1]["candidate_id"] = "cand2"
        with self.assertRaises(ValueError):
            verify_saved_queries(data)

    def test_path_traversal_sample_or_candidate_rejected_without_writing(self):
        for kind in ("sample", "candidate"):
            with self.subTest(kind=kind):
                data = copy.deepcopy(self.evidence)
                if kind == "sample":
                    data["samples"][0]["sample_id"] = "../../outside"
                    data["deep_records"][0]["sample_id"] = "../../outside"
                else:
                    data["deep_records"][0]["candidates"][0]["candidate_id"] = "../../outside"
                atomic_json(self.source, data)
                with self.assertRaises(ValueError):
                    self.run_review()
                self.assertFalse((self.root / "review").exists())

    def test_review_preserves_original_source_core_and_interpretation_and_clears_cache_claim(self):
        before = self.source.read_bytes()
        merged, artifacts = self.run_review()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(merged["records"], self.evidence["records"])
        self.assertEqual(merged["samples"], self.evidence["samples"])
        deep = merged["deep_records"][0]
        original = self.evidence["deep_records"][0]
        self.assertEqual(deep["candidates"][0]["assessment"], original["candidates"][0]["assessment"])
        self.assertEqual(deep["candidates"][0]["analyses"]["phylogeny"], original["candidates"][0]["analyses"]["phylogeny"])
        for key in ("cache_key", "cache_hit", "cache_checked_at", "deep_cache"):
            self.assertNotIn(key, deep)
        self.assertEqual(deep["placement_review"]["source_record_sha256"], hash_object(original))
        self.assertTrue(deep["placement_review"]["prior_execution_and_interpretations_are_historical"])
        self.assertEqual(deep["placement_review"]["commands"], [])
        for path, sha in deep["placement_review"]["artifact_checksums"].items():
            self.assertEqual(sha256_file(path), sha)
        self.assertTrue(Path(artifacts["evidence_json"]).is_file())
        self.assertEqual(merged["run"]["completeness"], "incomplete")

    def test_prior_upstream_incomplete_cannot_be_erased(self):
        self.evidence["deep_records"][0]["upstream_incomplete"] = ["isolate1:amrfinder"]
        self.evidence["deep_records"][0]["complete"] = False
        atomic_json(self.source, self.evidence)
        merged, _ = self.run_review()
        self.assertFalse(merged["deep_records"][0]["complete"])
        self.assertEqual(merged["deep_records"][0]["completion_summary"]["upstream_incomplete"], ["isolate1:amrfinder"])

    def test_failed_placement_retains_failed_candidate_metrics(self):
        def fail(candidates, cfg, work, runner):
            artifacts = self.fake_layer(candidates, cfg, work, runner)
            candidates[0]["execution"] = "failed"
            candidates[0]["analyses"]["family_placement"]["status"] = "failed"
            return artifacts
        merged, _ = self.run_review(fail)
        deep = merged["deep_records"][0]
        self.assertEqual(deep["metrics"]["failed_candidates"], 1)
        self.assertEqual(deep["completion_summary"]["selected_execution"], "incomplete")
        self.assertFalse(deep["complete"])

    def test_inputs_changed_during_execution_prevent_final_reporting(self):
        def change(candidates, cfg, work, runner):
            artifacts = self.fake_layer(candidates, cfg, work, runner)
            Path(self.evidence["samples"][0]["assembly"]).write_text(">changed\nAAAA\n")
            return artifacts
        with patch("basis.placement_review.run_family_placements", side_effect=change), \
                patch("basis.report.write_report") as writer:
            with self.assertRaises(ValueError):
                review_saved_placement(self.source, self.root / "review", self.cfg)
            writer.assert_not_called()

    def test_source_changed_during_execution_prevents_reporting(self):
        def change(candidates, cfg, work, runner):
            artifacts = self.fake_layer(candidates, cfg, work, runner)
            self.source.write_text(self.source.read_text() + "\n")
            return artifacts
        with patch("basis.placement_review.run_family_placements", side_effect=change), \
                patch("basis.report.write_report") as writer:
            with self.assertRaises(RuntimeError):
                review_saved_placement(self.source, self.root / "review", self.cfg)
            writer.assert_not_called()

    def test_existing_output_is_preserved_and_not_reused_silently(self):
        out = self.root / "review"
        out.mkdir(); marker = out / "original.txt"; marker.write_text("preserved")
        with self.assertRaises(FileExistsError):
            self.run_review()
        self.assertEqual(marker.read_text(), "preserved")


if __name__ == "__main__":
    unittest.main()
