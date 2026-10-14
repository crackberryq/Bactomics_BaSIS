"""Preserve native completeness/fragment caveats without inferring function."""
import copy
import unittest

from basis.report_context import finding_context
from basis.report_explorer import render_genome_explorer
from basis.report_genome import build_genome_data


def bacmet():
    ref = "BAC0489|ALU1-P|tr|O52119|O52119_ARTVI"
    return {"sample_id": "s", "tool": "bacmet", "finding_id": "b1", "protein_id": "p1",
            "category": "stress", "gene": "ALU1-P", "evidence_level": "homology",
            "reference": ref, "identity": 87.8, "coverage": 87.7,
            "contig": "1", "start": 20, "end": 300,
            "attributes": {"passes_threshold": True, "query_coverage": 74.9,
                "source_reference_annotation": {"reference_id": ref,
                    "source_header": ref + " Aluminum resistance protein (Fragment) OS=Example"}}}


def is_call(code="p"):
    return {"sample_id": "s", "tool": "isescan", "finding_id": "i1", "locus_id": "is1",
            "category": "is", "gene": None, "family": "IS21", "evidence_level": "prediction",
            "contig": "1", "start": 10, "end": 900,
            "attributes": {"native": {"type": code}}}


class ContextCaveatTests(unittest.TestCase):
    def test_partial_complete_unknown_are_distinct(self):
        partial = finding_context(is_call())
        complete = finding_context(is_call("c"))
        unknown = finding_context(is_call("?"))
        self.assertEqual(partial["source_call"], "IS21 · Partial IS prediction")
        self.assertEqual(complete["source_call"], "IS21 · Complete IS prediction")
        self.assertIn("does not establish an active", complete["caveats"][0])
        self.assertIsNone(unknown["is_prediction_type"])
        self.assertIn("completeness not recorded", unknown["source_call"])

    def test_fragment_is_a_reference_qualifier_with_both_coverages(self):
        result = finding_context(bacmet())
        self.assertTrue(result["reference_fragment"])
        self.assertEqual(result["source_call"], "ALU1-P")
        self.assertEqual(result["coverage_text"],
                         "Identity 87.8% · Query coverage 74.9% · Reference coverage 87.7%")
        self.assertIn("not a complete protein", result["caveats"][0])
        self.assertIn("does not establish that the query is truncated", result["caveats"][0])

    def test_fragment_header_must_match_reference_id(self):
        call = bacmet()
        call["reference"] = "another_id"
        self.assertFalse(finding_context(call)["reference_fragment"])
        call["attributes"]["subject_title"] = "another_id Protein (Fragment)"
        self.assertTrue(finding_context(call)["reference_fragment"])
        call["attributes"]["subject_title"] = "another_id Protein fragment-binding domain"
        self.assertFalse(finding_context(call)["reference_fragment"])

    def test_missing_and_invalid_coverage_is_not_zero_or_fabricated(self):
        call = bacmet()
        call["identity"] = True
        call["coverage"] = float("nan")
        call["attributes"].pop("query_coverage")
        result = finding_context(call)
        self.assertEqual(result["coverage_text"].count("not recorded"), 3)
        for invalid in (-1, 101, "nan", "inf"):
            call["coverage"] = invalid
            self.assertTrue(finding_context(call)["coverage_text"].endswith("Reference coverage not recorded"))

    def test_other_modules_not_reinterpreted(self):
        call = bacmet()
        call["tool"] = "vfdb"
        result = finding_context(call)
        self.assertEqual(result["caveats"], [])
        self.assertIsNone(result["coverage_text"])
        self.assertFalse(result["reference_fragment"])

    def test_genome_tooltips_keep_qualifiers_and_do_not_mutate_source(self):
        records = [{"sample_id": "s", "module": module, "execution_status": "completed", "findings": [call]}
                   for module, call in (("bacmet", bacmet()), ("isescan", is_call()))]
        before = copy.deepcopy(records)
        payload = build_genome_data({"input_validations": {"s": {"contig_lengths": {"1": 1000}}}},
                                    [{"sample_id": "s"}], records, [])
        regions = payload["samples"][0]["contigs"][0]["regions"]
        labels = [r["label"] for r in regions]
        self.assertTrue(any("Partial IS prediction" in label for label in labels))
        self.assertTrue(any("fragment reference" in label and "Query coverage 74.9%" in label for label in labels))
        rendered = render_genome_explorer(payload)
        self.assertIn("Partial IS prediction", rendered)
        self.assertIn("fragment reference", rendered)
        self.assertEqual(records, before)


if __name__ == "__main__":
    unittest.main()
