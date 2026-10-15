"""Optional-module report semantics: mapping and unassessed are distinct."""
import copy
import unittest

from basis.report_dashboard import NAMES, OPTIONAL_SCOPE, _omitted_row
from basis.report_genome import build_genome_data


def finding(ident, category, **changes):
    row = {"finding_id": ident, "locus_id": ident, "protein_id": None,
           "category": category, "gene": ident, "contig": "chr",
           "start": 20, "end": 100, "evidence_level": "prediction"}
    row.update(changes)
    return row


def record(module, findings=(), status="completed"):
    return {"sample_id": "s", "module": module, "execution_status": status,
            "findings": list(findings)}


def payload(records):
    run = {"input_validations": {"s": {"contig_lengths": {"chr": 1000},
        "protein_coordinates": {"p1": {"contig": "chr", "start": 10, "end": 50}}}}}
    return build_genome_data(run, [{"sample_id": "s"}], records, [])["samples"][0]


class OptionalReportTests(unittest.TestCase):
    def test_maps_only_completed_passing_optional_evidence_without_mutation(self):
        records = [record("bacmet", [
            finding("metal", "stress", protein_id="p1", start=None, end=None,
                    evidence_level="homology", attributes={"passes_threshold": True}),
            finding("weak", "stress", evidence_level="exploratory")]),
            record("isescan", [finding("IS1", "is", family="IS3")]),
            record("minced", [finding("array1", "crispr", start=200, end=350)])]
        old = copy.deepcopy(records)
        sample = payload(records)
        tracks = {t["id"]: t for t in sample["tracks"]}
        self.assertEqual({key: tracks[key]["count"] for key in
                          ("stress", "insertion_sequence", "crispr")},
                         {"stress": 1, "insertion_sequence": 1, "crispr": 1})
        self.assertEqual(tracks["amr"]["count"], 0)
        regions = sample["contigs"][0]["regions"]
        self.assertEqual(next(r for r in regions if r["track"] == "stress")["start"], 10)
        self.assertNotIn("weak", str(regions))
        self.assertTrue(any("active CRISPR-Cas" in n for n in sample["notes"]))
        self.assertTrue(any("BacMet matches" in n for n in sample["notes"]))
        self.assertEqual(records, old)

    def test_failed_and_disabled_modules_never_acquire_positive_tracks(self):
        sample = payload([record("bacmet", [finding("metal", "stress")], "failed"),
                          record("isescan", [finding("IS1", "is")], "not_assessed"),
                          record("minced")])
        tracks = {t["id"]: t for t in sample["tracks"]}
        self.assertEqual(tracks["stress"]["status"], "failed")
        self.assertEqual(tracks["insertion_sequence"]["status"], "not_assessed")
        self.assertEqual(tracks["crispr"]["status"], "completed")
        self.assertEqual(sum(t["count"] for t in tracks.values()), 0)

    def test_ice_signature_extremes_never_become_element_spans(self):
        unknown = finding("ICE1", "ice", attributes={
            "coordinate_semantics": "unknown_element_boundaries",
            "native": {"Start_of_most_upstream_SP": "20", "Stop_of_most_downstream_SP": "100"}})
        # The explicit semantics prevent mapping even if an imported record
        # erroneously populates canonical start/end with signature extremes.
        sample = payload([record("icescreen", [unknown])])
        ice = next(t for t in sample["tracks"] if t["id"] == "ice")
        self.assertEqual(ice["status"], "completed")
        self.assertEqual(ice["count"], 0)
        self.assertEqual(sample["contigs"][0]["regions"], [])
        self.assertTrue(any("not complete element boundaries" in n for n in sample["notes"]))
        self.assertTrue(any("Omitted 1 ICE / IME" in n for n in sample["notes"]))

    def test_optional_presentation_names_and_minced_scope(self):
        for module in ("bacmet", "isescan", "icescreen", "minced"):
            self.assertIn(module, NAMES)
        self.assertIn("Native adapter available", OPTIONAL_SCOPE["minced"][2])
        self.assertIn("without Cas-protein typing", OPTIONAL_SCOPE["minced"][2])
        self.assertIn("Native adapter available", OPTIONAL_SCOPE["crisprcasfinder"][2])

    def test_disabled_crispr_alternative_does_not_make_minced_partial(self):
        alternative = record("crisprcasfinder", status="not_assessed")
        alternative.update(reason="alternative_not_selected_minced", effective_config={"enabled": False})
        sample = payload([record("minced"), alternative])
        track = next(t for t in sample["tracks"] if t["id"] == "crispr")
        self.assertEqual(track["status"], "completed")
        self.assertEqual(track["count"], 0)

    def test_imported_cas_gene_is_not_drawn_as_crispr_array(self):
        rows = [finding("cas_gene", "crispr"), finding("array", "crispr", attributes={"feature_type": "array"})]
        sample = payload([record("crisprcasfinder", rows)])
        regions = sample["contigs"][0]["regions"]
        self.assertEqual(len(regions), 1)
        self.assertNotIn("cas_gene", regions[0]["label"])

    def test_omitted_reasons_explain_study_choices_without_universal_claims(self):
        rec = record("crisprcasfinder", status="not_assessed")
        rec.update(reason="alternative_not_selected_minced", effective_config={"enabled": False})
        self.assertIn("MinCED was selected", _omitted_row(rec)[4])
        rec = record("mlst", status="not_assessed")
        rec.update(reason="module_disabled", effective_config={
            "enabled": False, "disabled_reason": "no_applicable_scheme_identified"})
        self.assertIn("for this study", _omitted_row(rec)[4])
        self.assertIn("typing was not assessed", _omitted_row(rec)[4])
        ordinary = record("bacmet", status="not_assessed")
        ordinary["reason"] = "module_disabled"
        self.assertIn("Disabled in this run", _omitted_row(ordinary)[4])


if __name__ == "__main__":
    unittest.main()
