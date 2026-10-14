"""External-input metadata clarity without new biological interpretation."""
from __future__ import annotations

import copy
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.config import default_config
from basis.hybas import OPERATIONAL_SOURCE, enrich_sample
from basis.input_context import describe_input_context, taxonomy_is_missing
from basis.inputs import load_samples
from basis.sample_import import import_samples, preview_samples
from basis.sample_policy import plan_sample_modules


PLACEHOLDERS = ("", " ", "NA", "n/a", "None", "NULL", "UNKNOWN", "unresolved", "Not Resolved", "not_recorded", " not supplied ")


class InputContextTests(unittest.TestCase):
    def test_all_missing_placeholders_and_none_recognized_without_guessing_names(self):
        for value in (*PLACEHOLDERS, None):
            with self.subTest(value=value):
                self.assertTrue(taxonomy_is_missing(value))
        for value in ("Example species", "NA taxon", "unknown species", "not_supplied", "Example sp."):
            with self.subTest(value=value):
                self.assertFalse(taxonomy_is_missing(value))

    def test_raw_declarations_retained_and_missing_status_overrides_stale_status(self):
        for value in PLACEHOLDERS:
            sample = {"input_mode": "external", "taxon": value,
                      "taxon_rank": " genus ", "taxon_source": " user declaration ",
                      "taxon_status": "declared"}
            for resolved in (False, True):
                result = describe_input_context(sample, resolved=resolved)
                self.assertEqual(result["taxon"], value)
                self.assertEqual(result["taxon_rank"], " genus ")
                self.assertEqual(result["taxon_source"], " user declaration ")
                self.assertEqual(result["taxon_status"], "not_recorded")
                self.assertFalse(result["taxonomic_classification_performed"])

    def test_statuses_distinguish_preview_from_resolved_metadata(self):
        sample = {"taxon": " Example species ", "taxon_status": "read_based_operational_target",
                  "hybas_context": {"status": "imported"}}
        policies = {"auto": "matching_layout_if_available", "hybas": "required_matching_layout",
                    "external": "not_consulted_external_mode"}
        for mode in policies:
            sample["input_mode"] = mode
            preview = describe_input_context(sample)
            resolved = describe_input_context(sample, resolved=True)
            self.assertEqual(preview["taxon_status"], "declared")
            self.assertEqual(resolved["taxon_status"], "read_based_operational_target")
            self.assertEqual(preview["taxon"], " Example species ")
            self.assertEqual(preview["hybas_metadata_policy"], policies[mode])
            self.assertEqual(preview["hybas_metadata_status"], "not_consulted" if mode == "external" else "not_evaluated")
            self.assertEqual(resolved["hybas_metadata_status"], "not_consulted" if mode == "external" else "imported")
        absent = describe_input_context({"taxon": "Example species"}, resolved=True)
        self.assertEqual(absent["taxon_status"], "declared")
        self.assertEqual(absent["hybas_metadata_status"], "not_found")

    def test_describer_has_no_filesystem_access_and_does_not_mutate(self):
        sample = {"sample_id": "S01", "input_mode": "auto", "taxon": "unknown",
                  "gff": "/unavailable/path.gff", "hybas_dir": "/unavailable",
                  "hybas_context": {"status": "imported", "sources": ["old"]}}
        original = copy.deepcopy(sample)
        with patch("builtins.open", side_effect=AssertionError("unexpected file read")), \
             patch.object(Path, "resolve", side_effect=AssertionError("unexpected path resolution")), \
             patch.object(Path, "is_file", side_effect=AssertionError("unexpected file inspection")), \
             patch.object(Path, "is_dir", side_effect=AssertionError("unexpected directory inspection")):
            for resolved in (False, True):
                self.assertEqual(describe_input_context(sample, resolved)["taxon_status"], "not_recorded")
        self.assertEqual(sample, original)

    def test_invalid_mode_can_be_described_on_blocked_preview_row(self):
        for mode in ("typo", " BAD ", 9, []):
            with self.subTest(mode=mode):
                result = describe_input_context({"input_mode": mode, "taxon": "Example species"})
                self.assertEqual(result["input_mode"], mode)
                self.assertEqual(result["hybas_metadata_policy"], "invalid")
                self.assertEqual(result["hybas_metadata_status"], "not_evaluated")


class ExternalInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        annotation = self.root / "S01" / "annotation"
        annotation.mkdir(parents=True)
        self.paths = {}
        for key, suffix in (("assembly", ".fna"), ("proteins", ".faa"), ("gff", ".gff")):
            path = annotation / ("S01" + suffix)
            path.write_text("fixture; metadata tests do not parse biological contents\n")
            self.paths[key] = str(path)
        metadata = self.root / "S01" / "metadata"
        metadata.mkdir()
        (metadata / "run_summary.json").write_text("invalid JSON that must never be read in external mode")

    def sheet(self, headers, row):
        path = self.root / "samples.tsv"
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(headers)
            writer.writerow(row)
        return path

    def sample(self, **kwargs):
        return {"sample_id": "S01", "input_mode": "external", **self.paths, **kwargs}

    def test_external_enrichment_preserves_placeholders_but_marks_context_missing(self):
        for value in PLACEHOLDERS:
            original = self.sample(taxon=value, taxon_source="caller", taxon_status="declared",
                                   hybas_context={"status": "imported"})
            result = enrich_sample(original)
            self.assertEqual(result["taxon"], value)
            self.assertEqual(result["taxon_source"], "caller")
            self.assertEqual(result["taxon_status"], "not_recorded")
            self.assertNotIn("hybas_context", result)
            self.assertIn("hybas_context", original)
        result = enrich_sample(self.sample(taxon=" Example species "))
        self.assertEqual(result["taxon"], " Example species ")
        self.assertEqual(result["taxon_source"], "sample_sheet")
        self.assertEqual(result["taxon_status"], "declared")

    def test_external_enrichment_does_not_resolve_or_read_hybas_paths(self):
        original = self.sample(taxon="Example species")
        before = copy.deepcopy(original)
        with patch("builtins.open", side_effect=AssertionError("unexpected file read")), \
             patch.object(Path, "resolve", side_effect=AssertionError("unexpected filesystem resolution")), \
             patch.object(Path, "is_file", side_effect=AssertionError("unexpected file inspection")), \
             patch.object(Path, "is_dir", side_effect=AssertionError("unexpected directory inspection")):
            result = enrich_sample(original)
        self.assertEqual(result["taxon_status"], "declared")
        self.assertEqual(original, before)
        self.assertNotIn("hybas_context", result)

    def test_direct_and_imported_external_provenance_conflict_rejected_even_without_taxon(self):
        headers = ["sample_id", "assembly", "proteins", "gff", "input_mode", "taxon", "taxon_source"]
        for value in ("", "NA", "unknown", "Example species"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "cannot claim"):
                    enrich_sample(self.sample(taxon=value, taxon_source=OPERATIONAL_SOURCE))
                source = self.sheet(headers, ["S01", *(self.paths[key] for key in ("assembly", "proteins", "gff")), "external", value, OPERATIONAL_SOURCE])
                with self.assertRaisesRegex(ValueError, "cannot claim"):
                    load_samples(source)
                with self.assertRaisesRegex(ValueError, "cannot claim"):
                    import_samples(source, self.root / "study")
                result = preview_samples(source)
                self.assertEqual(result["status"], "blocked")
                self.assertTrue(any("cannot claim" in error for error in result["samples"][0]["errors"]))
                self.assertFalse((self.root / "study").exists())

    def test_external_direct_manifest_unknown_does_not_read_matching_hybas_metadata(self):
        headers = ["sample_id", "assembly", "proteins", "gff", "input_mode", "taxon"]
        source = self.sheet(headers, ["S01", *(self.paths[key] for key in ("assembly", "proteins", "gff")), "external", "UNKNOWN"])
        result = load_samples(source)[0]
        self.assertEqual(result["taxon"], "UNKNOWN")
        self.assertEqual(result["taxon_status"], "not_recorded")
        self.assertNotIn("hybas_context", result)

    def test_missing_taxonomy_adds_advisory_only_and_does_not_change_actions(self):
        config = default_config()
        for settings in config["modules"].values():
            settings["enabled"] = True
        sample = self.sample(taxon="Example species", genbank="fixture.gbk", taxon_phylum="Bacillota")
        original = copy.deepcopy(config)
        named = plan_sample_modules(config, sample, extend=True)
        for value in (*PLACEHOLDERS, None):
            with self.subTest(value=value):
                missing = plan_sample_modules(config, {**sample, "taxon": value}, extend=True)
                for module, decision in missing.items():
                    self.assertEqual({k: v for k, v in decision.items() if k != "warnings"},
                                     {k: v for k, v in named[module].items() if k != "warnings"})
                    self.assertIn("taxonomic_context_unavailable", decision["warnings"])
                self.assertEqual(config, original)

    def test_direct_assembly_only_manifest_explains_annotation_requirement(self):
        for headers in (["sample_id", "assembly"], ["sample_id", "assembly", "proteins"],
                        ["sample_id", "assembly", "gff"]):
            row = ["S01" if key == "sample_id" else self.paths[key] for key in headers]
            source = self.sheet(headers, row)
            with self.subTest(headers=headers), self.assertRaises(ValueError) as error:
                load_samples(source)
            message = str(error.exception)
            self.assertIn("annotation_required", message)
            self.assertIn("matching protein FASTA (proteins) and GFF", message)
            self.assertIn("BaSIS does not run annotation", message)
            for missing in {"proteins", "gff"} - set(headers):
                self.assertIn(missing, message)

    def test_other_missing_required_columns_retain_generic_header_error(self):
        source = self.sheet(["assembly", "proteins", "gff"], [self.paths[key] for key in ("assembly", "proteins", "gff")])
        with self.assertRaisesRegex(ValueError, "Sample sheet requires columns"):
            load_samples(source)


if __name__ == "__main__":
    unittest.main()
