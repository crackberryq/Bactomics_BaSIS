"""Review accounting and bounded selection; no native analyses or sequences."""
import copy
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from basis.adapters.amr import parse_amrfinder
from basis.amr_review import REVIEW_METHODS, VERSION, review_finding
from basis.aware import audit, resolve_policy
from basis.config import default_config
from basis.deep import select_candidates, make_analysis_plan
from basis.report import write_report
from basis.report_review import build_review_summary
from basis.run_summary import build_run_summary, format_run_summary
from safety.context import SafetyContext
from safety.reporting import render


class AmrReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis-amr-review-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        case = json.loads((ROOT / "tests/fixtures/amrfinder_review_cfr.json").read_text())
        path = self.root / "amrfinder.tsv"
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(case["native_row"]), delimiter="\t")
            writer.writeheader()
            writer.writerow(case["native_row"])
        self.finding = parse_amrfinder(path)[0]
        self.finding["finding_id"] = "fixture-cfr-finding"
        self.record = {"sample_id": case["sample_id"], "module": "amrfinder",
                       "execution_status": "completed", "interpretation": "detected",
                       "findings": [self.finding], "artifacts": []}
        self.sample = {"sample_id": case["sample_id"], "sample_alias": case["sample_alias"]}
        self.config = default_config()["deep"]

    def candidates(self, config=None, records=None):
        return select_candidates(records or [self.record], config or self.config, self.sample)

    def parent(self, candidates, **kwargs):
        return {"sample_id": self.sample["sample_id"], "module": "deepsafe",
                "execution_status": "completed", "candidates": candidates, **kwargs}

    def test_incident_is_reported_hmm_and_selected_across_presets(self):
        original = copy.deepcopy(self.record)
        self.assertEqual(self.sample["sample_alias"], "AFB01")
        self.assertEqual(self.finding["attributes"]["flags"], ["family_hmm_only"])
        for preset in ("auto-strict", "auto-medium", "auto-loose"):
            config = copy.deepcopy(self.config)
            config["aware"]["preset"] = preset
            candidates = self.candidates(config)
            self.assertEqual(sum(c["selected"] for c in candidates), 1)
            self.assertEqual(candidates[0]["review"]["reasons"], ["hmm_family_assignment"])
        self.assertEqual(self.record, original)
        self.assertFalse(self.finding["attributes"]["phenotypic_resistance_inferred"])

    def test_review_is_method_based_not_cfr_name_or_similarity_band(self):
        for method in sorted(REVIEW_METHODS):
            finding = copy.deepcopy(self.finding)
            finding.update(gene="arbitrary_family", identity=None, coverage=None)
            finding["attributes"] = {"native_method": method}
            candidates = self.candidates(records=[dict(self.record, findings=[finding])])
            self.assertTrue(candidates[0]["selected"], method)
        for method in ("EXACTP", "BLASTP", "POINTP"):
            finding = copy.deepcopy(self.finding)
            finding["attributes"] = {"native_method": method}
            candidate = self.candidates(records=[dict(self.record, findings=[finding])])[0]
            self.assertFalse(candidate["selected"])
            self.assertEqual(candidate["review"]["status"], "requires_review")
            self.assertIn("native_call_context_review", candidate["review"]["reasons"])

    def test_other_tools_cannot_admit_or_inflate_priority_in_review_scope(self):
        rgi = copy.deepcopy(self.finding)
        rgi.update(tool="rgi", finding_id="rgi-source", identity=100.)
        rgi["attributes"] = {"cutoff": "perfect", "nudged": False}
        second = copy.deepcopy(rgi)
        second.update(protein_id="other", contig="other", finding_id="other-rgi")
        candidates = self.candidates(records=[self.record, dict(self.record, module="rgi", findings=[rgi, second])])
        self.assertEqual(len(candidates), 2)
        selected = next(c for c in candidates if c["selected"])
        self.assertEqual(len(selected["source_evidence"]), 2)
        self.assertEqual(selected["evidence_priority"]["tools"], ["amrfinder"])
        self.assertEqual(sum(c["selected"] for c in candidates), 1)

    def test_linked_exact_call_cannot_shortcut_selected_hmm_investigation(self):
        rgi = copy.deepcopy(self.finding)
        rgi.update(tool="rgi", finding_id="rgi-perfect", identity=100., coverage=100.)
        rgi["attributes"] = {"cutoff": "perfect", "nudged": False, "model_type": "protein homolog model"}
        candidate = self.candidates(records=[self.record, dict(self.record, module="rgi", findings=[rgi])])[0]
        plan = make_analysis_plan(candidate, {"profile_id": "fixture", "validation_status": "unvalidated"},
                                  {"flags": []}, self.config)
        self.assertEqual(plan["route"], "ambiguous_amr_family_investigation")
        self.assertTrue(plan["analyses"]["references"])
        self.assertEqual(plan["decision_trace"][1]["exact_source_finding_ids"], ["rgi-perfect"])
        self.assertTrue(plan["decision_trace"][1]["targeted_amrfinder_review"])

    def test_report_module_identity_takes_precedence_over_nested_tool_label(self):
        other = dict(self.record, module="rgi")
        self.assertEqual(build_review_summary([other])["counts"]["review_findings"], 0)

    def test_budget_defers_without_removing_investigation(self):
        findings = [dict(copy.deepcopy(self.finding), finding_id=f"f{i}", protein_id=f"p{i}",
                         contig=f"c{i}") for i in range(8)]
        records = [dict(self.record, findings=findings)]
        candidates = self.candidates(records=records)
        self.assertEqual(sum(c["selected"] for c in candidates), 5)
        self.assertTrue(all(c["review"]["investigation_required"] for c in candidates))
        counts = audit(candidates, records, self.config)["counts"]
        self.assertEqual((counts["investigation_candidates"], counts["investigation_deferred"]), (8, 3))
        summary = build_review_summary(records, [self.parent(candidates)])
        self.assertEqual(summary["counts"]["investigation_findings"], 8)
        self.assertEqual(sum("deferred" in row["followup_status"] for row in summary["rows"]), 3)

    def test_manual_exclusion_does_not_remove_review_or_fill_empty_slots(self):
        for override in ({"enabled": False}, {"methods": ["EXACTP"]}, {"min_identity": 99.}):
            config = copy.deepcopy(self.config)
            config["aware"]["tools"] = {"amrfinder": override}
            candidate = self.candidates(config)[0]
            self.assertFalse(candidate["selected"])
            self.assertEqual(candidate["review"]["status"], "requires_review")
        self.assertEqual(select_candidates([], self.config, self.sample), [])

    def test_missing_unknown_and_conflicting_methods_require_manual_review(self):
        for attrs in ({}, {"native_method": "future_method"},
                      {"native_method": "EXACTP", "raw": {"Method": "HMM"}},
                      {"native_method": "EXACTP", "flags": ["family_hmm_only"]}):
            finding = dict(self.finding, attributes=attrs)
            candidate = self.candidates(records=[dict(self.record, findings=[finding])])[0]
            self.assertFalse(candidate["selected"])
            self.assertTrue(candidate["review"]["investigation_required"])
        finding = dict(self.finding, attributes={"raw": {"Method": "HMM"}})
        self.assertTrue(review_finding(finding)["automatic_followup"])

    def test_completed_inconclusive_failed_missing_reference_and_deferred_stay_open(self):
        for state, selected, parent_state, expected in (
                ("completed", True, "completed", "completed_unresolved"),
                ("failed", True, "failed", "failed"),
                ("not_assessed", True, "completed", "not_assessed"),
                ("deferred", False, "completed", "deferred"),
                ("completed", True, "failed", "provisional")):
            candidate = self.candidates()[0]
            candidate.update(execution=state, selected=selected,
                             reason="reference_catalogue_not_configured" if state == "not_assessed" else "fixture",
                             assessment={"execution": "completed", "interpretation": "subgroup_unresolved",
                                         "function_established": False})
            parent = self.parent([candidate], execution_status=parent_state)
            before = copy.deepcopy([self.record, parent])
            summary = build_review_summary([self.record], [parent])
            self.assertEqual(summary["status"], "requires_review")
            self.assertEqual(summary["rows"][0]["followup_status"], [expected])
            self.assertEqual([self.record, parent], before)

    def test_result_never_automatically_closes_review(self):
        candidate = self.candidates()[0]
        candidate.update(execution="completed", assessment={"interpretation": "some_new_result", "function_established": True})
        summary = build_review_summary([self.record], [self.parent([candidate])])
        self.assertEqual(summary["rows"][0]["followup_status"], ["completed_requires_review"])
        self.assertEqual(summary["status"], "requires_review")

    def test_source_identity_and_sample_boundaries_are_preserved(self):
        candidate = self.candidates()[0]
        other = self.parent([candidate], sample_id="another-sample")
        rows = build_review_summary([self.record], [other])["rows"]
        self.assertEqual(next(r for r in rows if r["sample_id"] == "isolate1")["followup_status"], ["no_linked_followup"])
        candidate["source_evidence"][0]["attributes"]["raw"]["Method"] = "EXACTP"
        rows = build_review_summary([self.record], [self.parent([candidate])])["rows"]
        self.assertEqual(len(rows), 2, "A reused finding ID cannot link changed evidence")
        self.assertEqual(rows[0]["followup_status"], ["no_linked_followup"])

    def test_failed_source_findings_remain_provisional(self):
        source = dict(self.record, execution_status="failed")
        self.assertEqual(self.candidates(records=[source]), [])
        summary = build_review_summary([source])
        self.assertEqual(summary["counts"]["provisional_source_findings"], 1)
        self.assertEqual(summary["status"], "requires_review")

    def test_putative_disruptive_variant_retains_manual_exploratory_review(self):
        finding = copy.deepcopy(self.finding)
        finding["attributes"].update(native_method="POINTP", subtype="POINT_DISRUPT",
                                     flags=["putative_disruptive_variant"], raw={"Method": "POINTP"})
        finding["evidence_level"] = "exploratory"
        config = copy.deepcopy(self.config)
        config["aware"]["preset"] = "auto-loose"
        candidate = self.candidates(config, [dict(self.record, findings=[finding])])[0]
        self.assertFalse(candidate["selected"])
        self.assertIn("putative_disruptive_variant", candidate["review"]["reasons"])
        self.assertEqual(candidate["source_evidence"][0]["evidence_level"], "exploratory")

    def test_reports_and_exports_keep_review_when_deep_is_off(self):
        run = {"report_view": "screening", "options": {"deep": False}, "completeness": "completed_requested_scope"}
        before = copy.deepcopy(self.record)
        with patch("subprocess.Popen", side_effect=AssertionError("Reporting attempted native execution")):
            write_report(run, [self.sample], [self.record], [], self.root / "report")
        page = (self.root / "report/report.html").read_text()
        evidence = json.loads((self.root / "report/evidence.json").read_text())
        self.assertIn("Analysis completed · AMR evidence review outstanding", page)
        self.assertIn("1 finding requires investigation", page)
        self.assertIn("hmm_family_assignment", (self.root / "report/amr-review.tsv").read_text())
        self.assertEqual(evidence["amr_review"]["status"], "requires_review")
        self.assertEqual(evidence["records"], [before])
        summary = build_run_summary(run, [self.sample], [self.record], [])
        self.assertEqual(summary["samples"][0]["completeness"], "completed_requested_scope")
        self.assertEqual(summary["samples"][0]["amr_review"]["status"], "requires_review")
        self.assertIn("AMR evidence review outstanding", format_run_summary(summary))

    def test_workflow_report_relocates_review_export_and_preserves_input_bytes(self):
        paths = [self.root / name for name in ("sample.json", "config.json", "amrfinder.json")]
        for path, value in zip(paths, (self.sample, {"deep": {"enabled": False}, "modules": {"amrfinder": {"enabled": True}}}, self.record)):
            path.write_text(json.dumps(value))
        original = {p: p.read_bytes() for p in paths}
        result = render(paths[0], paths[1], [paths[2]], self.root / "safety", view="screening")
        self.assertTrue(Path(result["amr_review_tsv"]).is_file())
        self.assertIn('href="tables/amr-review.tsv"', Path(result["html"]).read_text())
        self.assertEqual({p: p.read_bytes() for p in paths}, original)

    def test_invalid_scope_rejected_and_explicit_legacy_scope_retained(self):
        with self.assertRaises(ValueError):
            SafetyContext({"deep": {"aware": {"scope": "typo"}}}, ROOT)
        config = copy.deepcopy(self.config)
        config["aware"].update(scope="all-supported", preset="auto-strict")
        self.assertFalse(self.candidates(config)[0]["selected"])
        self.assertEqual(resolve_policy({"aware": {"enabled": True}})["scope"], "all-supported")

    def test_review_export_symlink_is_rejected_before_writing(self):
        destination = self.root / "report"
        destination.mkdir()
        victim = self.root / "original.txt"
        victim.write_text("preserve me")
        (destination / "amr-review.tsv").symlink_to(victim)
        with self.assertRaisesRegex(ValueError, "symlink"):
            write_report({"report_view": "screening", "options": {"deep": False}}, [self.sample], [self.record], [], destination)
        self.assertEqual(victim.read_text(), "preserve me")
        self.assertFalse((destination / "report.html").exists())

    def test_empty_failed_missing_and_completed_sources_are_distinct(self):
        self.assertEqual(build_review_summary([])["status"], "not_assessed")
        for state in ("failed", "not_assessed", "completed"):
            summary = build_review_summary([dict(self.record, findings=[], execution_status=state)])
            self.assertEqual(summary["status"], "no_reported_amr_findings" if state == "completed" else "not_assessed")


if __name__ == "__main__":
    unittest.main()
