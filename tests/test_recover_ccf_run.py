"""Batch selection and preflight regressions with synthetic metadata only."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import recover_ccf_run as batch


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class BatchRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="batch recovery ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.samples = [str(self.root / "sample-one"), str(self.root / "sample-two")]
        self.config = {"storage": {"native_results_directory": "local-results"}}
        self.overlays = [str(self.root / "study.yaml"), str(self.root / "local-results.yaml")]
        context = type("Context", (), {"samples": self.samples,
            "module_enabled": lambda self, isolate, module: True})()
        for target, kwargs in (
            ("load_configuration", {"return_value": (self.config, [str(self.root / "config.yaml"), *self.overlays])}),
            ("SafetyContext", {"return_value": context}),
            ("recovery.build_plan", {"side_effect": self.fake_plan}),
            ("recovery._require_stopped", {}),
        ):
            mock = patch.object(batch, target, **kwargs) if "." not in target else patch("recover_ccf_run." + target, **kwargs)
            setattr(self, target.split(".")[-1] + "_mock", mock.start())
            self.addCleanup(mock.stop)

    @staticmethod
    def fake_plan(attempt, sample, config, store, native, receipt):
        return {"status": "ready_for_validation", "attempt": str(attempt), "sample": str(sample),
                "config": str(config), "store": str(store), "native": str(native), "receipt": str(receipt)}

    def attempt(self, index=0, suffix="a", stamp="2026-10-23T10:25:51+00:00", status="completed"):
        attempt = Path(self.samples[index]) / "safety/results-cache/crisprcasfinder" / ("0" * 64) / ("attempt-" + suffix * 32)
        write_json(attempt / "started.json", {"schema": batch.result_store.SCHEMA,
            "name": "crisprcasfinder", "status": "started", "started_at": stamp})
        write_json(attempt / "commands.json", [{"status": status, "returncode": 0 if status == "completed" else 1}])
        return attempt

    def test_explicit_storage_required_before_any_recovery_plan(self):
        self.config.clear()
        with self.assertRaisesRegex(ValueError, "storage.native_results_directory"):
            batch.build_run_plan(self.root, self.overlays)
        self.build_plan_mock.assert_not_called()

    def test_latest_failed_attempt_does_not_fall_back_to_older_success(self):
        self.attempt()
        latest = self.attempt(suffix="b", stamp="2026-10-23T11:00:00+00:00", status="failed")
        plan = batch.build_run_plan(self.root, self.overlays)
        self.assertEqual(plan["results"][0]["status"], "no_recoverable_native_completion")
        self.assertEqual(plan["results"][0]["attempt"], str(latest))
        self.build_plan_mock.assert_not_called()

    def test_latest_success_uses_ordered_configuration_and_shared_storage_resolver(self):
        self.attempt()
        latest = self.attempt(suffix="b", stamp="2026-10-23T11:00:00+00:00")
        plan = batch.build_run_plan(self.root, self.overlays)
        row = plan["results"][0]
        expected = batch.native_result_store(self.root, self.config, self.samples[0], "crisprcasfinder")
        self.assertEqual(row["recovery"]["attempt"], str(latest))
        self.assertEqual(row["store"], str(expected))
        self.assertEqual(row["recovery"]["config"], str(Path(self.samples[0]) / "safety/metadata/configs/crisprcasfinder.json"))
        self.assertEqual(plan["configfiles"], self.overlays)
        self.load_configuration_mock.assert_called_once_with(self.root, self.overlays)

    def test_ambiguous_latest_timestamp_is_rejected(self):
        self.attempt()
        self.attempt(suffix="b")
        with self.assertRaisesRegex(ValueError, "Ambiguous latest"):
            batch.build_run_plan(self.root, self.overlays)

    def test_later_preflight_error_prevents_all_publication(self):
        self.attempt(index=0)
        self.attempt(index=1)
        original = batch.build_run_plan(self.root, self.overlays)
        self.build_plan_mock.side_effect = [self.fake_plan(*self.build_plan_mock.call_args_list[0].args), ValueError("second source absent")]
        with patch.object(batch.recovery, "apply_plan") as apply:
            with self.assertRaisesRegex(ValueError, "second source absent"):
                batch.apply_run_plan(original)
        apply.assert_not_called()

    def test_existing_completion_is_left_in_place_without_inventory_scan(self):
        receipt = Path(self.samples[0]) / "safety/completion/crisprcasfinder.json"
        write_json(receipt, {})
        payload = self.root / "existing-data"
        payload.mkdir()
        with patch.object(batch.result_store, "_record", return_value={"name": "crisprcasfinder", "payload_root": str(payload)}), \
             patch.object(batch.result_store, "_check_link"), \
             patch.object(batch.result_store, "output_inventory", side_effect=AssertionError("No full scan")):
            plan = batch.build_run_plan(self.root, self.overlays)
        self.assertEqual(plan["results"][0]["status"], "existing_completion_unchanged")
        self.build_plan_mock.assert_not_called()

    def test_apply_requires_stopped_even_when_no_attempt_needs_recovery(self):
        plan = batch.build_run_plan(self.root, self.overlays)
        self._require_stopped_mock.side_effect = ValueError("writer active")
        with self.assertRaisesRegex(ValueError, "writer active"):
            batch.apply_run_plan(plan)

    def test_apply_rebuilds_all_candidates_then_publishes_in_sample_order(self):
        self.attempt(index=0)
        self.attempt(index=1)
        plan = batch.build_run_plan(self.root, self.overlays)
        self.build_plan_mock.reset_mock()
        observed = []
        def apply(item):
            self.assertEqual(self.build_plan_mock.call_count, 2)
            observed.append(item["sample"])
            return {"status": "completed", "native_command_ran": False}
        with patch.object(batch.recovery, "apply_plan", side_effect=apply):
            result = batch.apply_run_plan(plan)
        self.assertEqual(observed, [str(Path(p) / "safety/metadata/sample.json") for p in self.samples])
        self.assertEqual(result["status"], "completed")
        self.assertTrue(all(row["status"] == "completed" for row in result["results"]))

    def test_multiple_native_commands_are_not_silently_marked_unrecoverable(self):
        attempt = self.attempt()
        write_json(attempt / "commands.json", [{"status": "completed", "returncode": 0}] * 2)
        with self.assertRaisesRegex(ValueError, "at most one"):
            batch.build_run_plan(self.root, self.overlays)


if __name__ == "__main__":
    unittest.main()
