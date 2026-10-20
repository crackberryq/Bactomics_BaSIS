"""Pure workflow routing audit with synthetic names and no biological sequences."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from basis.deep_workflow import (SCHEMA, STAGES, candidate_workflow,
                                 record_family_routes, summarize)
from basis.references import profile_for_candidate, validate_profiles


class DeepWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profiles_dir = self.root / "profiles"
        self.profiles_dir.mkdir()
        panels = []
        for name in ("synthetic_alpha", "synthetic_beta"):
            profile = {"schema_version": 1, "profile_id": name + "_profile",
                       "validation_status": "unvalidated_template", "interpretation_policy": "exploratory_only",
                       "candidate_aliases": [name, "shared_synthetic"],
                       "required_comparison_groups": [], "anchor_reference_ids": [],
                       "analyses": ["integrity", "context", "references", "domains", "phylogeny"]}
            (self.profiles_dir / (name + ".yaml")).write_text(json.dumps(profile), encoding="utf-8")
            panels.append({"panel_id": name + "_panel", "candidate_aliases": [name, "shared_synthetic"],
                           "groups": [{"group_id": "synthetic_target", "role": "amr", "required": True},
                                      {"group_id": "synthetic_alternative", "role": "alternative", "required": True}],
                           "references": []})
        self.registry = self.root / "registry.json"
        self.registry.write_text(json.dumps({"schema_version": 1, "registry_id": "synthetic_registry",
                                             "panels": panels}), encoding="utf-8")
        self.config = {"profiles_dir": str(self.profiles_dir), "family_placement": True,
                       "placement_registry": str(self.registry)}
        self.profiles, errors = validate_profiles(self.profiles_dir)
        self.assertEqual(errors, [])

    def candidate(self, number, family, *, selected=True, qualified=True):
        item = {"candidate_id": "candidate-" + str(number), "protein_id": "P" + str(number),
                "sample_id": "sample1", "gene": family, "family": family, "family_labels": [family],
                "selected": selected, "selection_queue": "native_screen" if selected else "exploratory",
                "execution": "completed" if selected else "deferred", "analyses": {},
                "assessment": {"interpretation": "unresolved", "followup": "review_existing_evidence"},
                "source_evidence": [{"gene": family, "tool": "synthetic", "attributes": {"passes_threshold": qualified}}]}
        item["profile"] = profile_for_candidate(item, self.profiles)
        return item

    def test_two_arbitrary_families_route_without_native_or_download(self):
        items = [self.candidate(1, "synthetic_alpha"), self.candidate(2, "synthetic_beta")]
        before = copy.deepcopy(items)
        files_before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch("subprocess.Popen", side_effect=AssertionError("native process attempted")), \
             patch("basis.placement_panels.urlopen", side_effect=AssertionError("download attempted")), \
             patch("basis.placement_panels.prepare_panel", side_effect=AssertionError("reference preparation attempted")):
            inventory = record_family_routes(items, self.config)
        for old, current in zip(before, items):
            self.assertEqual(old, {key: value for key, value in current.items() if key != "family_routing"})
        self.assertEqual(len(inventory["profiles"]), 2)
        self.assertEqual(len(inventory["placement_registry"]["panels"]), 2)
        self.assertTrue(all(panel["reference_count"] == 0 for panel in inventory["placement_registry"]["panels"]))
        self.assertEqual(inventory["placement_registry"]["sha256"], hashlib.sha256(self.registry.read_bytes()).hexdigest())
        for item, name in zip(items, ("synthetic_alpha", "synthetic_beta")):
            row = candidate_workflow(item)
            self.assertEqual(row["routing_status"], "configured")
            self.assertEqual(row["profile"]["matched_profiles"], [name + "_profile"])
            self.assertEqual(row["profile"]["validation_status"], "unvalidated_template")
            self.assertEqual(row["placement"]["matched_panels"], [name + "_panel"])
            self.assertEqual(row["placement"]["route_status"], "matched")
            self.assertEqual(row["placement"]["status"], "not_recorded")
        self.assertEqual(files_before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_unmatched_ambiguous_deferred_and_unqualified_remain_explicit(self):
        unmatched = self.candidate(1, "unknown_synthetic")
        ambiguous = self.candidate(2, "shared_synthetic")
        deferred = self.candidate(3, "synthetic_alpha", selected=False)
        unqualified = self.candidate(4, "synthetic_alpha", qualified=False)
        items = [unmatched, ambiguous, deferred, unqualified]
        record_family_routes(items, self.config)
        unknown, shared, held, loose = [candidate_workflow(item) for item in items]
        self.assertEqual(unknown["routing_status"], "no_configured_family")
        self.assertEqual(unknown["placement"]["matched_panels"], [])
        self.assertEqual(shared["routing_status"], "ambiguous")
        self.assertEqual(len(shared["profile"]["matched_profiles"]), 2)
        self.assertEqual(len(shared["placement"]["matched_panels"]), 2)
        self.assertEqual(shared["placement"]["status"], "not_recorded")
        self.assertEqual(held["routing_status"], "deferred")
        self.assertEqual(held["profile"]["status"], "deferred")
        self.assertEqual(held["placement"]["route_status"], "deferred")
        self.assertEqual(loose["profile"]["status"], "matched")
        self.assertEqual(loose["placement"]["route_status"], "no_configured_family")
        self.assertEqual(loose["placement"]["matched_panels"], [])

    def test_disabled_placement_does_not_load_registry(self):
        item = self.candidate(1, "synthetic_alpha")
        with patch("basis.placement_panels.load_registry", side_effect=AssertionError("disabled registry loaded")):
            inventory = record_family_routes([item], {**self.config, "family_placement": False})
        row = candidate_workflow(item)
        self.assertFalse(inventory["placement_enabled"])
        self.assertIsNone(inventory["placement_registry"])
        self.assertEqual(row["placement"]["route_status"], "not_requested")
        self.assertEqual(row["placement"]["status"], "not_requested")

    def test_historical_candidate_is_not_reconstructed_from_labels(self):
        item = self.candidate(1, "synthetic_alpha")
        before = copy.deepcopy(item)
        row = candidate_workflow(item)
        self.assertEqual(row["schema"], SCHEMA)
        self.assertEqual(row["routing_status"], "not_recorded")
        self.assertEqual(row["profile"]["status"], "not_recorded")
        self.assertEqual(row["placement"]["route_status"], "not_recorded")
        self.assertEqual([stage["name"] for stage in row["stages"]], list(STAGES))
        self.assertTrue(all(stage["status"] == "not_recorded" for stage in row["stages"]))
        self.assertEqual(item, before)

    def test_completed_and_held_stages_preserve_interpretation_and_no_probability(self):
        item = self.candidate(1, "synthetic_alpha")
        record_family_routes([item], self.config)
        item["analyses"] = {"domains": {"status": "completed", "reason": "synthetic_record"},
                            "phylogeny": {"status": "not_assessed", "reason": "synthetic_hold"},
                            "family_placement": {"status": "insufficient_evidence", "reason": "synthetic_panel_hold",
                                "panel": {"panel_id": "synthetic_alpha_panel",
                                          "adequacy": {"reasons": ["synthetic_missing_contrast"]}}}}
        before = copy.deepcopy(item)
        row = candidate_workflow(item)
        self.assertEqual(row["execution"], "completed")
        self.assertEqual(row["interpretation"], "unresolved")
        self.assertEqual(row["placement"]["route_status"], "matched")
        self.assertEqual(row["placement"]["status"], "insufficient_evidence")
        self.assertEqual(row["placement"]["adequacy_reasons"], ["synthetic_missing_contrast"])
        self.assertEqual({stage["name"]: stage["status"] for stage in row["stages"]}["phylogeny"], "not_assessed")
        self.assertIn("not calibrated functional probabilities", row["scope"])
        self.assertNotIn("probability", row)
        row["profile"]["matched_profiles"].append("tampered")
        row["placement"]["adequacy_reasons"].append("tampered")
        self.assertEqual(item, before)

    def test_summary_separates_selection_routing_execution_and_calibration(self):
        items = [self.candidate(1, "synthetic_alpha"), self.candidate(2, "unknown_synthetic"),
                 self.candidate(3, "shared_synthetic"), self.candidate(4, "synthetic_beta", selected=False)]
        record_family_routes(items, self.config)
        summary = summarize([candidate_workflow(item) for item in items])
        self.assertEqual((summary["candidates"], summary["selected"], summary["deferred"]), (4, 3, 1))
        self.assertEqual(summary["selected_routing"], {"configured": 1, "no_configured_family": 1, "ambiguous": 1})
        self.assertEqual(summary["selected_execution"], {"completed": 3})
        self.assertFalse(summary["function_probability_calibrated"])
        self.assertEqual(summarize([])["selected"], 0)

    def test_invalid_configured_profile_is_not_silently_routed(self):
        (self.profiles_dir / "invalid.yaml").write_text("profile_id: incomplete\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid profile registry"):
            record_family_routes([self.candidate(1, "synthetic_alpha")], self.config)

    def test_unselected_or_historical_rows_are_not_invented_deferrals(self):
        summary = summarize([{"selected": False, "execution": "not_assessed"},
                             {"execution": "not_recorded"},
                             {"selected": False, "execution": "deferred"}])
        self.assertEqual(summary["selected"], 0)
        self.assertEqual(summary["not_selected"], 3)
        self.assertEqual(summary["deferred"], 1)


if __name__ == "__main__":
    unittest.main()
