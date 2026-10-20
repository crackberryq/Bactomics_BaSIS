"""Synthetic family workflow records render without native analysis or inference."""
from __future__ import annotations

import copy
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from basis.report_deep_workflow import (candidate_workflow_details, recorded_workflow,
                                       render_workflow_overview, workflow_search_terms)
from basis.report_links import candidate_anchor, candidate_section_anchor
from safety.reporting import export_workflow_coverage, render


def candidate(number, family="synthetic_alpha"):
    ident = "P" + str(number)
    return {"candidate_id": "candidate-" + str(number), "protein_id": ident,
            "sample_id": "sample1", "selected": True, "selection_queue": "native_screen",
            "execution": "completed", "analyses": {},
            "assessment": {"interpretation": "unresolved", "followup": "review_existing_evidence"},
            "workflow_coverage": {
                "schema": "basis-deep-workflow-v1", "candidate_id": "candidate-" + str(number),
                "protein_id": ident, "selected": True, "selection_queue": "native_screen",
                "routing_status": "recorded", "routing_reason": "configured_profile",
                "profile": {"id": family + "_profile", "status": "matched", "reason": "recorded_match",
                            "matched_profiles": [family + "_profile"]},
                "placement": {"status": "not_assessed", "reason": "missing_comparison_groups",
                              "route_status": "matched", "route_reason": "recorded_match",
                              "panel_id": family + "_panel", "matched_panels": [family + "_panel"],
                              "registry_id": "synthetic_registry", "registry_sha256": "a" * 64,
                              "adequacy_reasons": ["synthetic_comparator_gap"]},
                "stages": [{"name": "domains", "status": "completed", "reason": "recorded_result"},
                           {"name": "phylogeny", "status": "not_assessed", "reason": "missing_comparison_groups"}],
                "execution": "completed", "interpretation": "unresolved",
                "followup": "hold_for_comparator_review"}}


def parent(candidates, status="completed"):
    return {"sample_id": "sample1", "module": "deepsafe", "execution_status": status,
            "candidates": candidates, "findings": [], "artifacts": [], "metrics": {},
            "upstream_incomplete": [], "complete": False}


class WorkflowReportTests(unittest.TestCase):
    def test_historical_metadata_is_not_reconstructed(self):
        historical = candidate(1)
        del historical["workflow_coverage"]
        historical["family"] = "synthetic_alpha"
        historical["analyses"]["family_placement"] = {"status": "completed", "panel_id": "old_panel"}
        self.assertIsNone(recorded_workflow(historical))
        body = candidate_workflow_details(historical, "completed")
        self.assertIn("Routing metadata: not recorded", body)
        self.assertNotIn("old_panel", body)
        self.assertEqual(workflow_search_terms(historical), "workflow routing not recorded")

    def test_unknown_schema_and_mismatched_candidate_are_unrecorded(self):
        item = candidate(1)
        item["workflow_coverage"]["schema"] = "future_schema"
        self.assertIsNone(recorded_workflow(item))
        item["workflow_coverage"]["schema"] = "basis-deep-workflow-v1"
        item["workflow_coverage"]["protein_id"] = "other"
        self.assertIsNone(recorded_workflow(item))
        item = candidate(1)
        item["workflow_coverage"]["sample_id"] = "other_sample"
        self.assertIsNone(recorded_workflow(item))
        item = candidate(1)
        item["workflow_coverage"]["selected"] = False
        self.assertIsNone(recorded_workflow(item))
        item = candidate(1)
        del item["workflow_coverage"]["candidate_id"]
        self.assertIsNone(recorded_workflow(item))

    def test_multiple_arbitrary_families_holds_and_legacy_are_explicit(self):
        alpha, beta, unmatched, ambiguous, old = [candidate(n, family) for n, family in
                [(1, "synthetic_alpha"), (2, "synthetic_beta"), (3, "unmatched"),
                 (4, "ambiguous"), (5, "historical")]]
        unmatched["workflow_coverage"].update(routing_status="held", routing_reason="no_configured_family",
                                              profile={}, placement={})
        ambiguous["workflow_coverage"].update(routing_status="held", routing_reason="ambiguous_family_match",
                                              profile={"status": "ambiguous", "matched_profiles": ["one", "two"]},
                                              placement={})
        del old["workflow_coverage"]
        items = [alpha, beta, unmatched, ambiguous, old]
        body = render_workflow_overview([("sample1", item) for item in items], [parent(items)])
        for text in ("synthetic_alpha_profile", "synthetic_beta_panel", "no configured family",
                     "ambiguous family match", "not recorded", "4 / 5"):
            self.assertIn(text, body)
        self.assertNotIn("Workflow coverage · JSON", body)
        self.assertIn('data-navigation-candidate="' + candidate_anchor("sample1", "P4") + '"', body)
        self.assertIn("synthetic_comparator_gap", workflow_search_terms(alpha))
        self.assertIn("Routing: matched", body)

    def test_labels_reasons_and_registry_are_escaped(self):
        item = candidate(1, '<script>alert("family")</script>')
        item["workflow_coverage"]["routing_reason"] = '<img src=x onerror="bad()">'
        item["workflow_coverage"]["placement"]["registry_id"] = "<svg onload=bad()>"
        body = render_workflow_overview([("sample1", item)], [parent([item])])
        details = candidate_workflow_details(item, "completed")
        self.assertNotIn("<script>", body + details)
        self.assertNotIn("<img src=x", body + details)
        self.assertNotIn("<svg onload", details)
        self.assertIn("&lt;script&gt;", body)

    def test_all_rows_shown_deferred_separate_and_links_are_existing_sections(self):
        items = [candidate(n) for n in range(45)]
        deferred = {"protein_id": "D1", "selected": False, "execution": "deferred"}
        body = render_workflow_overview([("sample1", item) for item in items], [parent(items + [deferred])])
        self.assertEqual(body.count('data-navigation-candidate="'), 45)
        self.assertIn("1 deferred candidates", body)
        self.assertNotIn("D1", body)
        self.assertIn('href="#' + candidate_section_anchor("sample1", "P44", "trees") + '"', body)
        self.assertIn('href="#' + candidate_section_anchor("sample1", "P44", "placement") + '"', body)
        self.assertNotIn("Open placement tree", body)

    def test_inventory_and_matched_routes_do_not_count_generic_as_supported(self):
        generic = candidate(1)
        generic["workflow_coverage"]["profile"] = {"id": "generic", "status": "no_configured_family"}
        generic["workflow_coverage"]["placement"] = {"route_status": "no_configured_family", "matched_panels": []}
        record = parent([generic])
        record["workflow_coverage"] = {"family_inventory": {
            "profiles": [{"id": "one"}, {"id": "two"}],
            "placement_registry": {"panels": [{"id": "one_panel"}]}}}
        body = render_workflow_overview([("sample1", generic)], [record])
        self.assertIn("2 configured profile definitions; 1 configured placement panels", body)
        self.assertIn('Matched profiles</span><strong class="stat-value">0</strong>', body)
        self.assertIn('Matched panels</span><strong class="stat-value">0</strong>', body)
        self.assertIn("distinct configured profiles matched", body)
        self.assertNotIn("Profile identifiers", body)

    def test_failed_parent_keeps_saved_routing_provisional(self):
        item = candidate(1)
        body = render_workflow_overview([("sample1", item)], [parent([item], "failed")])
        self.assertIn("Parent execution: failed", body)
        self.assertIn("not successful completion", candidate_workflow_details(item, "failed"))
        self.assertNotIn("placement-view-", body)

    def test_optional_downloads_are_report_relative(self):
        item = candidate(1)
        pairs, records = [("sample1", item)], [parent([item])]
        body = render_workflow_overview(pairs, records, {"json": "metadata/deep-workflow.json",
                                                       "tsv": "metadata/deep-workflow.tsv"})
        self.assertIn('href="metadata/deep-workflow.json"', body)
        self.assertIn('href="metadata/deep-workflow.tsv"', body)
        for unsafe in ("https://example.org/file", "javascript:bad()", "../escape.json",
                       "%2e%2e/escape.json", "\\windows\\share", "/absolute.json"):
            with self.subTest(unsafe=unsafe):
                body = render_workflow_overview(pairs, records, {"json": unsafe})
                self.assertNotIn("Workflow coverage · JSON", body)

    def test_offline_report_integration_preserves_evidence_and_searches_family(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "safety"
            root.mkdir()
            item = candidate(1, "unrelated_synthetic_family")
            original = copy.deepcopy(item)
            sample = {"sample_id": "sample1", "input_mode": "external", "taxon": ""}
            config = {"version": "0.4.0a1", "modules": {}, "deep": {"enabled": True}}
            paths = []
            for name, value in (("sample.json", sample), ("config.json", config),
                                ("deep.json", parent([item]))):
                path = root / name
                path.write_text(json.dumps(value), encoding="utf-8")
                paths.append(path)
            before = {path: path.read_bytes() for path in paths}
            with patch("subprocess.Popen", side_effect=AssertionError("native analysis attempted")):
                render(paths[0], paths[1], [paths[2]], root)
            for path, contents in before.items():
                self.assertEqual(path.read_bytes(), contents)
            self.assertEqual(item, original)
            body = (root / "report.html").read_text(encoding="utf-8")
            self.assertIn('id="deep-workflow"', body)
            self.assertIn("Automated workflow coverage", body)
            self.assertIn("unrelated_synthetic_family_profile", body)
            self.assertIn('id="' + candidate_section_anchor("sample1", "P1", "trees") + '"', body)
            self.assertIn('id="' + candidate_section_anchor("sample1", "P1", "placement") + '"', body)

    def test_export_includes_all_selected_and_deferred_without_mutating_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "safety"
            root.mkdir()
            items = [candidate(n, "synthetic_alpha" if n % 2 else "synthetic_beta") for n in range(45)]
            for n in range(45, 100):
                item = candidate(n)
                item.update(selected=False, execution="deferred", selection_queue="exploratory")
                item["workflow_coverage"].update(selected=False, execution="deferred", selection_queue="exploratory",
                                                  routing_status="deferred", routing_reason="candidate_deferred")
                items.append(item)
            sample = {"sample_id": "sample1", "input_mode": "external", "taxon": ""}
            config = {"version": "0.4.0a1", "modules": {}, "deep": {"enabled": True}}
            paths = []
            for name, value in (("sample.json", sample), ("config.json", config), ("deep.json", parent(items))):
                path = root / name
                path.write_text(json.dumps(value), encoding="utf-8")
                paths.append(path)
            before = {path: path.read_bytes() for path in paths}
            with patch("subprocess.Popen", side_effect=AssertionError("native analysis attempted")):
                artifacts = render(paths[0], paths[1], [paths[2]], root)
            self.assertEqual(before, {path: path.read_bytes() for path in paths})
            bundle = json.loads((root / "metadata/deep-workflow.json").read_text(encoding="utf-8"))
            self.assertEqual(len(bundle["candidates"]), 100)
            self.assertEqual(bundle["summary"]["selected"], 45)
            self.assertEqual(bundle["summary"]["deferred"], 55)
            self.assertFalse(bundle["summary"]["function_probability_calibrated"])
            self.assertEqual({row["protein_id"] for row in bundle["candidates"]}, {"P" + str(n) for n in range(100)})
            with (root / "metadata/deep-workflow.tsv").open(encoding="utf-8", newline="") as stream:
                table_rows = list(csv.DictReader(stream, delimiter="\t"))
            self.assertEqual(len(table_rows), 100)
            self.assertEqual({row["protein_id"] for row in table_rows}, {"P" + str(n) for n in range(100)})
            body = (root / "report.html").read_text(encoding="utf-8")
            self.assertIn('href="metadata/deep-workflow.json"', body)
            self.assertIn('href="metadata/deep-workflow.tsv"', body)
            self.assertTrue(Path(artifacts["deep_workflow_json"]).is_file())
            self.assertTrue(Path(artifacts["deep_workflow_tsv"]).is_file())

    def test_export_rejects_mismatched_saved_routing_consistently_with_html(self):
        for field, wrong in (("candidate_id", "other"), ("protein_id", "other"),
                             ("sample_id", "other"), ("selected", False), ("selected", "False")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "metadata").mkdir()
                item = candidate(1)
                item["workflow_coverage"][field] = wrong
                before = copy.deepcopy(item)
                artifacts = export_workflow_coverage([parent([item])], root)
                bundle = json.loads(Path(artifacts["json"]).read_text(encoding="utf-8"))
                row = bundle["candidates"][0]
                self.assertEqual(row["candidate_id"], item["candidate_id"])
                self.assertEqual(row["protein_id"], item["protein_id"])
                self.assertEqual(row["sample_id"], "sample1")
                self.assertTrue(row["selected"])
                self.assertEqual(row["routing_status"], "not_recorded")
                self.assertIsNone(recorded_workflow(item))
                self.assertEqual(item, before)


if __name__ == "__main__":
    unittest.main()
