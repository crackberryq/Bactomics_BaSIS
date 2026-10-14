"""Optional activation must preserve reference review status and prior evidence."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from basis.deep import select_candidates
from basis.optional_setup import merged_catalogue, pins_match, prepare_configs, save_immutable
from basis.config import default_config, CORE
from basis.references import CATALOG_FIELDS, load_catalog, sequence_hash, write_fasta


class OptionalSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def catalogue(self):
        fasta, metadata = self.root / "old.faa", self.root / "old.tsv"
        write_fasta({"anchor": "MACD"}, fasta)
        row = dict(zip(CATALOG_FIELDS, ("anchor", "TEST1", "test", "group", "anchor", "reviewed",
                                       "synthetic fixture", sequence_hash("MACD"), "2026-01-01")))
        row["evidence_basis"] = "synthetic_review_fixture"
        with metadata.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row), delimiter="\t")
            writer.writeheader()
            writer.writerow(row)
        return {"reference_fasta": str(fasta), "reference_metadata": str(metadata)}

    def test_merge_preserves_reviewed_anchor_and_keeps_new_references_unreviewed(self):
        deep = self.catalogue()
        fasta = self.root / "bacmet.faa"
        fasta.write_text(">BAC0001|abc|sp|TEST2|TEST_X description\nMACE\n")
        old_bytes = Path(deep["reference_metadata"]).read_bytes()
        a, b = merged_catalogue(self.root, deep, fasta)
        merged = load_catalog(Path(a), Path(b))
        self.assertEqual(merged.metadata["anchor"]["role"], "anchor")
        self.assertEqual(merged.metadata["anchor"]["review_status"], "reviewed")
        self.assertEqual(merged.metadata["anchor"]["evidence_basis"], "synthetic_review_fixture")
        added = merged.metadata["BAC0001|abc|sp|TEST2|TEST_X"]
        self.assertEqual((added["role"], added["review_status"], added["comparison_group"]),
                         ("reference", "unreviewed", "unknown"))
        self.assertEqual(Path(deep["reference_metadata"]).read_bytes(), old_bytes)
        self.assertEqual(merged_catalogue(self.root, deep, fasta), (a, b))
        Path(a).write_text(">anchor\nAAAA\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            merged_catalogue(self.root, deep, fasta)

    def test_catalogue_collision_is_failure(self):
        deep = self.catalogue()
        fasta = self.root / "bacmet.faa"
        write_fasta({"anchor": "MACE"}, fasta)
        with self.assertRaisesRegex(ValueError, "collide"):
            merged_catalogue(self.root, deep, fasta)

    def test_stress_followup_is_explicit_and_does_not_claim_amr(self):
        finding = {"finding_id": "f1", "tool": "bacmet", "category": "stress", "gene": "example",
                   "protein_id": "P1", "evidence_level": "homology", "attributes": {"passes_threshold": True}}
        self.assertEqual(select_candidates([finding], {}), [])
        selected = select_candidates([finding], {"include_stress": True})
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0]["selected"])
        self.assertEqual(selected[0]["selection_reasons"], ["stress_screen"])
        self.assertEqual(selected[0]["source_evidence"][0]["category"], "stress")
        with self.assertRaisesRegex(ValueError, "boolean"):
            select_candidates([finding], {"include_stress": "yes"})

    def test_failed_and_below_threshold_bacmet_do_not_become_native_candidates(self):
        finding = {"finding_id": "f1", "tool": "bacmet", "category": "stress", "gene": "example",
                   "protein_id": "P1", "evidence_level": "exploratory", "attributes": {"passes_threshold": False}}
        rows = select_candidates([finding], {"include_stress": True, "include_exploratory": False})
        self.assertFalse(rows[0]["selected"])
        self.assertEqual(select_candidates([{"execution_status": "failed", "module": "bacmet",
                                            "findings": [finding]}], {"include_stress": True}), [])

    def test_version_pins_do_not_match_similar_prefix(self):
        self.assertTrue(pins_match([{"name": "python", "version": "3.10.18"}], {"python": "3.10"}))
        self.assertFalse(pins_match([{"name": "python", "version": "3.100"}], {"python": "3.10"}))
        self.assertFalse(pins_match([], {"python": "3.10"}))

    def test_immutable_writer_preserves_local_changes(self):
        path = self.root / "config.yaml"
        save_immutable(path, "x: 1\n")
        save_immutable(path, "x: 1\n")
        with self.assertRaisesRegex(ValueError, "preserved"):
            save_immutable(path, "x: 2\n")
        self.assertEqual(path.read_text(), "x: 1\n")

    def test_changed_imported_artifact_blocks_config_preparation(self):
        cfg = default_config()
        cfg["outdir"] = str(self.root / "old")
        sample = {"sample_id": "isolate1", "taxon": "Lysinibacillus irui"}
        path = self.root / "old/results/isolate1/amrfinder/result.json"
        path.parent.mkdir(parents=True)
        artifact = self.root / "native.tsv"
        artifact.write_text("changed")
        path.write_text(json.dumps({"artifact_checksums": {str(artifact): "wrong"}}))
        with patch("basis.optional_setup.load_samples", return_value=[sample]), \
             patch("basis.optional_setup.validate_sample", return_value={"errors": []}), \
             patch("basis.pipeline._import_result", return_value={}):
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                prepare_configs(self.root, cfg, {})
        self.assertEqual(list(self.root.glob("config.*.yaml")), [])


if __name__ == "__main__":
    unittest.main()
