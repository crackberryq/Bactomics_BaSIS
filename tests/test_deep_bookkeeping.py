"""Saved-record invariants with synthetic analysis payloads; no native tools."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.config import ALL_MODULES
from basis.deep_cache import reusable
from basis.pipeline import execute_deep, record_path
from basis.util import atomic_json


class DeepBookkeepingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis_deep_record_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profiles = self.root / "profiles"
        self.profiles.mkdir()
        self.sample = {"sample_id": "sampleA"}
        self.plan = {
            "outdir": str(self.root), "source_hash": "synthetic-source",
            "config": {"validation_mode": "synthetic", "modules": {},
                       "execution": {"cores": 1},
                       "deep": {"profiles_dir": str(self.profiles), "motifs": False}},
            "options": {"deep": True, "extend": False},
            "validations": {"sampleA": {"input_hashes": {}}},
            "enabled_modules": [],
            "module_decisions": {"sampleA": {
                module: {"action": "disabled", "requested": False,
                         "scope_incomplete": False, "reason": "module_disabled"}
                for module in ALL_MODULES}},
        }
        self.payload = {
            "candidates": [{"selected": True, "execution": "completed"},
                           {"selected": False, "execution": "deferred"}],
            "notes": ["Synthetic analysis note"], "artifacts": [],
            "metrics": {"failed_candidates": 0}, "complete": False,
            # Deliberately represents the pre-pipeline summary returned by
            # run_deep, which cannot yet know final upstream execution scope.
            "completion_summary": {"upstream_incomplete": []},
        }

    def decision(self, module, action, requested):
        self.plan["module_decisions"]["sampleA"][module] = {
            "action": action, "requested": requested,
            "scope_incomplete": action == "withheld" and requested,
            "reason": "synthetic-declaration",
        }

    def upstream(self, module, execution_status):
        self.decision(module, "run", True)
        atomic_json(record_path(self.plan, "sampleA", module), {
            "module": module, "execution_status": execution_status, "findings": []})

    def saved(self):
        return json.loads(record_path(self.plan, "sampleA", "deepsafe").read_text())

    def test_engine_identity_failure_note_survives_payload_and_disables_cache(self):
        with patch("basis.deep_cache.engine_identity", side_effect=RuntimeError("synthetic identity failure")), \
             patch("basis.deep.run_deep", return_value=self.payload) as analysis, \
             patch("basis.deep_cache.seal") as seal:
            first = execute_deep(self.plan, self.sample)
            execute_deep(self.plan, self.sample)
        self.assertEqual(analysis.call_count, 2)
        seal.assert_not_called()
        self.assertEqual(first["execution_status"], "completed")
        self.assertIn("Deep cache disabled: synthetic identity failure", first["notes"])
        self.assertIn("Synthetic analysis note", first["notes"])
        self.assertIsNone(first["provenance"]["engine_identity"])
        self.assertFalse(reusable(first, first["cache_key"]))
        self.assertEqual(self.saved()["notes"], first["notes"])

    def test_custom_resource_warning_is_retained_without_identity_probe_or_seal(self):
        (self.profiles / "test.yaml").write_text("resources:\n  external: untracked\n")
        with patch("basis.deep_cache.engine_identity") as identity, \
             patch("basis.deep.run_deep", return_value=self.payload), \
             patch("basis.deep_cache.seal") as seal:
            result = execute_deep(self.plan, self.sample)
        identity.assert_not_called()
        seal.assert_not_called()
        self.assertEqual(result["notes"], ["Deep cache disabled for custom profile resources.",
                                         "Synthetic analysis note"])

    def test_final_summary_counts_failed_and_requested_withheld_but_not_exclusions(self):
        self.upstream("amrfinder", "failed")
        self.upstream("rgi", "completed")
        self.decision("icescreen", "withheld", True)
        self.decision("pathogenfinder2", "withheld", False)
        with patch("basis.deep_cache.engine_identity", return_value={"hash": "synthetic"}), \
             patch("basis.deep.run_deep", return_value=self.payload):
            result = execute_deep(self.plan, self.sample)
        self.assertEqual(set(result["upstream_incomplete"]), {"amrfinder", "icescreen"})
        summary = result["completion_summary"]
        self.assertEqual(summary["upstream_incomplete"], result["upstream_incomplete"])
        self.assertEqual(summary["selected_execution"], "completed")
        self.assertEqual(summary["selected_candidates"], 1)
        self.assertEqual(summary["completed_selected_candidates"], 1)
        self.assertEqual(summary["deferred_candidates"], 1)
        self.assertFalse(reusable(result, result["cache_key"]))
        self.assertEqual(self.saved()["completion_summary"], summary)

    def test_analysis_failure_still_records_upstream_scope_and_current_summary(self):
        self.upstream("amrfinder", "failed")
        self.decision("icescreen", "withheld", True)
        with patch("basis.deep_cache.engine_identity", side_effect=RuntimeError("synthetic identity failure")), \
             patch("basis.deep.run_deep", side_effect=RuntimeError("synthetic analysis failure")), \
             patch("basis.deep_cache.seal") as seal:
            result = execute_deep(self.plan, self.sample)
        seal.assert_not_called()
        self.assertEqual(result["execution_status"], "failed")
        self.assertEqual(result["error"], "synthetic analysis failure")
        self.assertIn("Deep cache disabled: synthetic identity failure", result["notes"])
        self.assertEqual(set(result["upstream_incomplete"]), {"amrfinder", "icescreen"})
        self.assertEqual(result["completion_summary"]["upstream_incomplete"], result["upstream_incomplete"])
        self.assertEqual(result["completion_summary"]["selected_candidates"], 0)
        self.assertEqual(self.saved()["execution_status"], "failed")


if __name__ == "__main__":
    unittest.main()
