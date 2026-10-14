"""Synthetic normalization and saved-record regression tests; no native tools."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from basis.normalize import normalize_result
from basis.pipeline import _import_result
from basis.util import hash_object, sha256_file


class NormalizationIdempotenceTests(unittest.TestCase):
    def setUp(self):
        self.sample = {"sample_id": "sampleA"}
        self.validation = {
            "contig_lengths": {"1": 1000},
            "contig_aliases": {"native.1": "1", "1": "1"},
            "protein_coordinates": {"proteinA": {
                "contig": "1", "start": 101, "end": 400, "strand": "+"}},
            "input_hashes": {"assembly": "synthetic-assembly", "proteins": "synthetic-proteins", "gff": "synthetic-gff"},
        }
        self.finding = {
            "category": "amr", "gene": "synthetic", "protein_id": "proteinA",
            "reference": "synthetic-reference", "contig": None, "start": None,
            "end": None, "strand": None, "identity": 80.0, "coverage": 90.0,
            "attributes": {"unrelated": {"native": ["preserve", 1]}},
        }

    def payload(self, finding=None):
        return {"interpretation": "detected", "findings": [copy.deepcopy(self.finding if finding is None else finding)]}

    def normalize(self, payload, module="amrfinder", validation=None):
        return normalize_result(payload, module, self.sample, self.validation if validation is None else validation)

    def assert_three_passes_equal(self, payload, module="amrfinder"):
        original = copy.deepcopy(payload)
        first = self.normalize(payload, module)
        second = self.normalize(first, module)
        third = self.normalize(second, module)
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertEqual(payload, original)
        return first["findings"][0]

    def test_protein_only_location_keeps_all_none_native_snapshot(self):
        finding = self.assert_three_passes_equal(self.payload())
        self.assertEqual(finding["attributes"]["reported_coordinates"],
                         {"contig": None, "start": None, "end": None, "strand": None})
        self.assertNotIn("native_contig_alias", finding["attributes"])
        self.assertEqual({k: finding[k] for k in ("contig", "start", "end", "strand")},
                         self.validation["protein_coordinates"]["proteinA"])
        self.assertEqual(finding["attributes"]["unrelated"], self.finding["attributes"]["unrelated"])

    def test_native_alias_and_native_alignment_span_are_preserved(self):
        native = {**self.finding, "contig": "native.1", "start": 130, "end": 360, "strand": "+"}
        finding = self.assert_three_passes_equal(self.payload(native))
        self.assertEqual(finding["attributes"]["native_contig_alias"], "native.1")
        # Preserve the established first-pass convention: alias-resolved contig.
        self.assertEqual(finding["attributes"]["reported_coordinates"],
                         {"contig": "1", "start": 130, "end": 360, "strand": "+"})
        self.assertEqual((finding["start"], finding["end"]), (101, 400))

    def test_context_alias_without_protein_is_idempotent(self):
        native = {**self.finding, "category": "ice", "protein_id": None,
                  "contig": "native.1", "start": 20, "end": 600, "strand": None}
        finding = self.assert_three_passes_equal(self.payload(native), "icescreen")
        self.assertEqual(finding["attributes"]["native_contig_alias"], "native.1")
        self.assertNotIn("reported_coordinates", finding["attributes"])

    def test_new_self_alias_does_not_create_native_alias_provenance(self):
        finding = self.assert_three_passes_equal(self.payload({**self.finding, "contig": "1"}))
        self.assertNotIn("native_contig_alias", finding["attributes"])

    def test_existing_legacy_self_alias_and_reported_coordinates_stay_as_recorded(self):
        payload = self.normalize(self.payload())
        finding = payload["findings"][0]
        finding["attributes"]["native_contig_alias"] = "1"
        # This simulates the legacy recorded ID for exactly these attributes.
        location = ["sampleA", "1", 101, 400, "+"]
        finding["finding_id"] = "finding_" + hash_object([
            "amrfinder", location, finding["gene"], finding["reference"], finding["attributes"]])[:16]
        finding["id"] = finding["finding_id"]
        self.assertEqual(self.normalize(payload), payload)

    def test_divergent_historical_coordinates_are_evidence_not_canonical_location(self):
        payload = self.normalize(self.payload())
        historical = {"contig": "original-name", "start": 900, "end": 1200, "strand": "-"}
        payload["findings"][0]["attributes"]["reported_coordinates"] = historical
        finding = self.assert_three_passes_equal(payload)
        self.assertEqual(finding["attributes"]["reported_coordinates"], historical)
        self.assertEqual((finding["contig"], finding["start"], finding["end"]), ("1", 101, 400))

    def test_missing_legacy_native_provenance_is_not_reconstructed(self):
        payload = self.normalize(self.payload())
        finding = payload["findings"][0]
        del finding["attributes"]["reported_coordinates"]
        finding["contig"] = "native.1"
        result = self.assert_three_passes_equal(payload)
        self.assertNotIn("reported_coordinates", result["attributes"])
        self.assertNotIn("native_contig_alias", result["attributes"])
        self.assertEqual(result["contig"], "1")

    def test_arbitrary_or_incoherent_ids_do_not_suppress_fresh_native_provenance(self):
        variants = [
            {"id": "adapter-id"},
            {"finding_id": "finding_" + "a" * 16, "id": "finding_" + "a" * 16},
            {"locus_id": "locus_" + "b" * 16, "finding_id": "finding_" + "a" * 16, "id": "different"},
            {"locus_id": "locus_bad", "finding_id": "finding_" + "a" * 16, "id": "finding_" + "a" * 16},
        ]
        for ids in variants:
            with self.subTest(ids=ids):
                result = self.assert_three_passes_equal(self.payload({**self.finding, "contig": "native.1", **ids}))
                self.assertIn("reported_coordinates", result["attributes"])
                self.assertEqual(result["attributes"]["native_contig_alias"], "native.1")

    def test_malformed_provenance_is_rejected_without_overwriting_it(self):
        reported = {"contig": None, "start": None, "end": None, "strand": None}
        bad_attributes = [None, [], "not-a-mapping"]
        bad_attributes += [{"native_contig_alias": value} for value in (None, "", [], 1)]
        bad_attributes += [{"reported_coordinates": value} for value in (
            None, [], "not-a-snapshot", {}, {**reported, "extra": "field"},
            {**reported, "start": True}, {**reported, "end": "400"},
            {**reported, "contig": []}, {**reported, "strand": 1},
        )]
        for attributes in bad_attributes:
            with self.subTest(attributes=attributes):
                payload = self.payload({**self.finding, "attributes": attributes})
                original = copy.deepcopy(payload)
                with self.assertRaisesRegex(ValueError, "Invalid .*attributes|Invalid native_contig_alias|Invalid reported_coordinates"):
                    self.normalize(payload)
                self.assertEqual(payload, original)

    def test_new_snapshot_shape_is_checked_on_first_pass(self):
        for key, value in (("start", "130"), ("end", True), ("strand", ["+"])):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "reported_coordinates"):
                    self.normalize(self.payload({**self.finding, key: value}))

    def test_canonical_revalidation_is_never_skipped_for_normalized_records(self):
        payload = self.normalize(self.payload())
        finding = payload["findings"][0]
        finding.update(contig="untrusted", start=-2, end=9999)
        repaired = self.normalize(payload)["findings"][0]
        self.assertEqual((repaired["contig"], repaired["start"], repaired["end"]), ("1", 101, 400))
        for feature in (
            {"contig": "missing", "start": 101, "end": 400, "strand": "+"},
            {"contig": "1", "start": 101, "end": 1001, "strand": "+"},
            {"contig": "1", "start": 400, "end": 101, "strand": "+"},
        ):
            with self.subTest(feature=feature):
                validation = copy.deepcopy(self.validation)
                validation["protein_coordinates"]["proteinA"] = feature
                with self.assertRaises(ValueError):
                    self.normalize(payload, validation=validation)

    def test_unmapped_canonical_coordinates_and_numeric_values_still_fail_validation(self):
        payload = self.normalize(self.payload())
        for fields in (
            {"protein_id": "unmapped", "contig": "missing"},
            {"protein_id": "unmapped", "end": 1001},
            {"protein_id": "unmapped", "start": 401},
            {"identity": 101}, {"coverage": float("nan")},
        ):
            with self.subTest(fields=fields):
                changed = copy.deepcopy(payload)
                changed["findings"][0].update(fields)
                with self.assertRaises(ValueError):
                    self.normalize(changed)

    def test_import_and_reimport_keep_normalized_findings_and_original_file(self):
        record = {**self.normalize(self.payload()), "schema_version": 1,
                  "sample_id": "sampleA", "module": "amrfinder", "execution_status": "completed",
                  "input_hashes": self.validation["input_hashes"]}
        with tempfile.TemporaryDirectory() as tmp:
            first_path = Path(tmp) / "native.json"
            first_path.write_text(json.dumps(record))
            original_hash = sha256_file(first_path)
            imported = _import_result(str(first_path), self.sample, "amrfinder", self.validation)
            self.assertEqual(imported["findings"], record["findings"])
            second_path = Path(tmp) / "imported.json"
            second_path.write_text(json.dumps(imported))
            reimported = _import_result(str(second_path), self.sample, "amrfinder", self.validation)
            self.assertEqual(reimported["findings"], record["findings"])
            self.assertEqual(sha256_file(first_path), original_hash)
            self.assertFalse(reimported["imported_evidence"]["native_execution_performed"])


if __name__ == "__main__":
    unittest.main()
