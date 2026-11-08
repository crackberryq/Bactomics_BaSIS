"""Synthetic input warnings and coordinate contradictions remain reviewable."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/vendor"), str(ROOT / "workflow/lib")]
from basis.normalize import normalize_result
from basis.report import write_report
from test_report_screening import Text


class ProteinCoordinateConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.validation = {"protein_coordinates": {
            "p1": {"contig": "c1", "start": 10, "end": 39, "strand": "+"}},
            "contig_lengths": {"c1": 100, "c2": 100}, "contig_aliases": {"ACC.1": "c1"}}

    def normalize(self, **changes):
        finding = {"category": "amr", "protein_id": "p1", "gene": "synthetic"}
        finding.update(changes)
        payload = {"interpretation": "detected", "findings": [finding]}
        original = copy.deepcopy(payload)
        normalized = normalize_result(payload, "synthetic", {"sample_id": "s"}, self.validation)
        self.assertEqual(payload, original)
        self.assertEqual(normalize_result(normalized, "synthetic", {"sample_id": "s"}, self.validation), normalized)
        return normalized

    def test_absent_location_is_linked_and_remains_absent_in_native_snapshot(self):
        finding = self.normalize()["findings"][0]
        self.assertEqual(finding["start"], 10)
        self.assertEqual(finding["attributes"]["reported_coordinates"],
                         {"contig": None, "start": None, "end": None, "strand": None})

    def test_alias_and_contained_alignment_are_preserved_without_exact_endpoint_equality(self):
        finding = self.normalize(contig="ACC.1", start=13, end=33, strand="+")["findings"][0]
        self.assertEqual((finding["contig"], finding["start"], finding["end"]), ("c1", 10, 39))
        self.assertEqual(finding["attributes"]["native_contig_alias"], "ACC.1")
        self.assertEqual(finding["attributes"]["reported_coordinates"]["start"], 13)

    def test_unreported_strand_is_not_a_conflict(self):
        for strand in (None, ".", "?"):
            with self.subTest(strand=strand):
                self.normalize(start=10, end=39, strand=strand)

    def test_contradictory_locations_are_rejected_before_replacement(self):
        for changes, message in [({"contig": "c2"}, "contig conflicts"),
                                 ({"contig": "not-in-assembly"}, "contig conflicts"),
                                 ({"strand": "-"}, "strand conflicts"),
                                 ({"start": 1, "end": 39}, "outside protein CDS"),
                                 ({"start": 10, "end": 50}, "outside protein CDS"),
                                 ({"start": 20}, "invalid reported"),
                                 ({"start": True, "end": 30}, "invalid reported")]:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                self.normalize(**changes)

    def test_saved_conflicting_snapshot_cannot_be_erased_by_normalized_coordinates(self):
        normalized = self.normalize()
        normalized["findings"][0]["attributes"]["reported_coordinates"]["contig"] = "c2"
        with self.assertRaisesRegex(ValueError, "contig conflicts"):
            normalize_result(normalized, "synthetic", {"sample_id": "s"}, self.validation)

    def test_valid_legacy_normalized_record_without_snapshot_remains_unchanged(self):
        normalized = self.normalize()
        normalized["findings"][0]["attributes"].pop("reported_coordinates")
        output = normalize_result(normalized, "synthetic", {"sample_id": "s"}, self.validation)
        self.assertNotIn("reported_coordinates", output["findings"][0]["attributes"])
        self.assertEqual(normalize_result(output, "synthetic", {"sample_id": "s"}, self.validation), output)


class InputValidationPresentationTests(unittest.TestCase):
    def render(self, validation=None, *, sample_snapshot=None, view="screening"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        destination = Path(temporary.name)
        run = {"run_id": "synthetic", "version": "test", "report_view": view,
               "options": {"deep": False}, "completeness": "completed_requested_scope"}
        if validation is not None:
            run["input_validations"] = {"s": validation}
        samples = [{"sample_id": "s", "input_mode": "external"}]
        if sample_snapshot is not None:
            samples[0]["input_validation"] = sample_snapshot
        records = [{"sample_id": "s", "module": "amrfinder", "execution_status": "completed",
                    "interpretation": "not_detected", "findings": [], "metrics": {}, "artifacts": []}]
        original = copy.deepcopy((run, samples, records))
        with patch("subprocess.Popen", side_effect=AssertionError("Reporting launched analysis")):
            write_report(run, samples, records, [], destination)
        self.assertEqual((run, samples, records), original)
        return (destination / "report.html").read_text(), json.loads((destination / "evidence.json").read_text())

    def test_warnings_are_visible_before_results_and_retained_in_evidence(self):
        validation = {"warnings": ["Synthetic input consistency warning"],
                      "translation_validation": {"checked": 1, "unchecked": 1,
                                                  "warnings": ["Synthetic input consistency warning", "Translation unchecked: fixture"]},
                      "annotation_coverage": {"status": "complete_eligible_cds", "gff_cds_count": 2,
                                              "eligible_cds_count": 1, "omitted_pseudogene_cds_count": 1},
                      "limitations": ["Synthetic coverage limitation"]}
        page, evidence = self.render(validation)
        text = Text(page).text
        self.assertIn("Tools finished · review assessment limits", text)
        warning_panel = page.split('id="input-validation-warnings"', 1)[1].split('</div>', 1)[0]
        self.assertEqual(warning_panel.count("Synthetic input consistency warning"), 1)
        self.assertIn("Translation unchecked: fixture", warning_panel)
        self.assertLess(page.index('id="input-validation-warnings"'), page.index('id="screens"'))
        self.assertIn("Synthetic coverage limitation", text)
        self.assertIn("Omitted pseudogene cds count", text)
        self.assertEqual(evidence["run"]["input_validations"]["s"], validation)

    def test_legacy_sample_warnings_and_html_escaping(self):
        page, _ = self.render(sample_snapshot={"warnings": ['<script>synthetic()</script>']})
        self.assertIn("&lt;script&gt;synthetic()&lt;/script&gt;", page)
        self.assertNotIn("<script>synthetic()", page)
        self.assertIn('id="input-validation-warnings"', page)

    def test_no_warning_does_not_create_false_attention(self):
        page, _ = self.render({"warnings": [], "limitations": ["Scope limit remains"]})
        self.assertNotIn('id="input-validation-warnings"', page)
        self.assertIn("Scope limit remains", Text(page).text)
        self.assertIn("All requested tools finished", Text(page).text)

    def test_missing_validation_is_explicit_without_fabricated_pass(self):
        page, _ = self.render()
        self.assertIn("No input-validation record was supplied", Text(page).text)

    def test_combined_report_keeps_warnings_in_overview(self):
        page, _ = self.render({"warnings": ["Combined-view input warning"]}, view="combined")
        self.assertIn('id="input-validation-warnings"', page)
        self.assertIn("Combined-view input warning", Text(page).text)

    def test_many_warnings_keep_a_bounded_summary_and_complete_saved_details(self):
        warnings = [f"Synthetic warning {number}" for number in range(20)]
        page, evidence = self.render({"warnings": warnings})
        summary = page.split('id="input-validation-warnings"', 1)[1].split('</div>', 1)[0]
        self.assertIn("15 additional recorded warnings", summary)
        self.assertNotIn("Synthetic warning 19", summary)
        self.assertIn("Synthetic warning 19", page)
        self.assertIn("All recorded input warnings (20)", Text(page).text)
        self.assertEqual(evidence["run"]["input_validations"]["s"]["warnings"], warnings)


if __name__ == "__main__":
    unittest.main()
