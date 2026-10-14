"""BacMet evidence remains distinct from AMR and phenotype assessments."""
import copy
from html.parser import HTMLParser
import unittest

from basis.report import _methods, build_recommendations
from basis.report_evidence_views import render_evidence_matrix
from basis.report_recommendations import refine_recommendations


def finding(fid, protein="P1", gene="chrR", passing=True):
    return {"finding_id": fid, "tool": "bacmet", "gene": gene,
            "category": "stress", "protein_id": protein,
            "evidence_level": "homology" if passing else "exploratory",
            "interpretation": "homology_requires_functional_review" if passing else "below_reporting_threshold",
            "contig": "c1", "start": 10, "end": 100,
            "attributes": {"passes_threshold": passing, "function_confirmed": False}}


def record(findings, status="completed", sample="S1"):
    return {"sample_id": sample, "module": "bacmet", "execution_status": status,
            "interpretation": "detected", "findings": findings}


def candidate(findings):
    return {"protein_id": "P1", "selected": True, "execution": "completed",
            "source_evidence": findings, "selection_queue": "native_screen", "analyses": {}}


class MatrixParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.headers = 0
        self.cells = 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "th" and attributes.get("scope") == "col":
            self.headers += 1
        if tag == "td":
            self.cells += 1


class BacMetReportingTests(unittest.TestCase):
    def test_methods_discloses_report_refresh_and_historical_audit_scope(self):
        run = {"report_refresh": {"report_generated_at_utc": "2026-10-10T10:00:00+00:00",
                                  "canonical_label_corrections": 2,
                                  "source_evidence": {"path": "/saved/evidence.json", "sha256": "a" * 64}}}
        before = copy.deepcopy(run)
        methods = _methods(run, [], [])
        for text in ("Report-only metadata refresh; no native analyses or GPU inference performed.",
                     "2026-10-10T10:00:00+00:00", "Canonical candidate label corrections: 2",
                     "/saved/evidence.json", "The original source snapshot is preserved.",
                     "Historical reconciliation hashes describe the source snapshot",
                     "new refresh audit records before/after hashes"):
            self.assertIn(text, methods)
        self.assertEqual(run, before)
        self.assertNotIn("Report-only metadata refresh", _methods({}, [], []))

    def test_matrix_has_explicit_stress_column_and_aligned_cells(self):
        f = finding("pass")
        html = render_evidence_matrix([("S1", candidate([f]))], [record([f])])
        self.assertIn("BacMet · stress", html)
        self.assertIn("Threshold pass: chrR", html)
        self.assertIn("do not establish expressed tolerance or antibiotic resistance", html)
        parsed = MatrixParser(html)
        self.assertEqual(parsed.headers, parsed.cells + 1)

    def test_matrix_weak_failed_and_other_sample_do_not_promote_bacmet_label(self):
        for passing, status, sample in [(False, "completed", "S1"),
                                        (True, "failed", "S1"),
                                        (True, "completed", "S2")]:
            with self.subTest(passing=passing, status=status, sample=sample):
                f = finding("weak", gene="NOT_A_QUALIFYING_LABEL", passing=passing)
                html = render_evidence_matrix([("S1", candidate([f]))], [record([f], status, sample)])
                self.assertNotIn("NOT_A_QUALIFYING_LABEL", html)
                if not passing:
                    self.assertIn("Exploratory only", html)

    def test_stress_priority_links_qualifying_records_and_counts_unique_proteins(self):
        passing = [finding("a"), finding("b"), finding("c", "P2", "ALU1-P")]
        records = [record(passing + [finding("below", passing=False)])]
        before = copy.deepcopy(records)
        result = build_recommendations([{"sample_id": "S1"}], records, [])
        stress = next(r for r in result if r["category"] == "stress")
        self.assertEqual(stress["evidence_ids"], ["a", "b", "c"])
        self.assertEqual(stress["finding_record_count"], 3)
        self.assertEqual(stress["distinct_target_count"], 2)
        self.assertIn("BacMet: 3 threshold-passing alignment record(s), 2 distinct", stress["trigger"])
        self.assertEqual(stress["assessment_type"], "computational_review")
        self.assertEqual(stress["provided_phenotype_status"], "not_applicable_computational_review")
        self.assertIn("not an automatic phenotype assessment", stress["recommendation"])
        self.assertIn("reference annotation and completeness", stress["recommendation"])
        self.assertEqual(records, before)
        self.assertEqual(refine_recommendations([], records, [], result), result)

    def test_exploratory_stress_has_separate_computational_scope(self):
        result = build_recommendations([], [record([finding("below", passing=False)])], [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "exploratory_evidence")
        self.assertEqual(result[0]["assessment_type"], "computational_review")
        self.assertEqual(result[0]["evidence_ids"], ["below"])
        self.assertIn("do not by themselves trigger a phenotype assessment", result[0]["recommendation"])

    def test_explicit_weak_marker_overrides_passing_flag(self):
        weak = finding("weak")
        weak["evidence_level"] = "exploratory"
        result = build_recommendations([], [record([weak])], [])
        self.assertEqual([r["category"] for r in result], ["exploratory_evidence"])

    def test_failed_and_empty_bacmet_do_not_create_stress_priority(self):
        result = build_recommendations([], [record([finding("provisional")], status="failed")], [])
        self.assertEqual([r["category"] for r in result], ["execution"])
        self.assertEqual(build_recommendations([], [record([])], []), [])

    def test_stress_does_not_create_amr_virulence_or_mobile_hazard_trigger(self):
        mobile = {"sample_id": "S1", "module": "isescan", "execution_status": "completed",
                  "findings": [{"finding_id": "is", "category": "is", "contig": "c1",
                                "start": 1, "end": 200, "evidence_level": "prediction"}]}
        result = build_recommendations([], [record([finding("metal")]), mobile], [])
        self.assertEqual([r["category"] for r in result], ["stress"])


if __name__ == "__main__":
    unittest.main()
