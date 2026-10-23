"""Durable native-result lifecycle contracts using synthetic files only."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from safety.result_store import (execution_identity, output_inventory, receipt_inputs,
                                 run_cached, sha256, verify_receipt)
from safety.resources import SCHEMA as RESOURCE_SCHEMA


class ResultStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / "native/example"
        self.receipt = self.root / "completion/example.json"
        self.store = self.root / "results-cache/example"
        self.input = self.root / "input.txt"
        self.input.write_text("synthetic input")
        self.source = self.root / "parser.py"
        self.source.write_text("# synthetic command/parser identity\n")
        self.environment = self.root / "environment.json"
        self.environment.write_text(json.dumps({"schema": "basis-snake-environment-v1",
            "status": "completed", "prefix": "/synthetic/environment", "created_at": "before",
            "packages": [{"name": "synthetic", "version": "1"}]}))
        self.sample = {"sample_id": "synthetic", "assembly": str(self.input)}
        self.config = {"modules": {"example": {"executable": sys.executable, "threads": 2}},
                       "environment_receipts": [str(self.environment)], "resource_receipts": []}
        self.executed = 0
        self.verified = 0

    def identity(self, config=None):
        return execution_identity("example", self.sample, config or self.config, sources=[self.source])

    def execute(self, payload):
        self.executed += 1
        (payload / "result.txt").write_text("verified output")
        (payload / "nested").mkdir()
        (payload / "nested/empty.txt").write_text("")

    def verify(self, payload):
        self.verified += 1
        self.assertEqual((payload / "result.txt").read_text(), "verified output")
        (payload / "parsed.json").write_text('{"completed":true}\n')

    def run_job(self, identity=None, execute=None, verify=None, **kwargs):
        return run_cached("example", identity or self.identity(), self.destination, self.receipt,
                          self.store, execute or self.execute, verify or self.verify, **kwargs)

    def test_verified_cache_survives_scheduler_receipt_removal(self):
        first, reused = self.run_job()
        self.assertFalse(reused)
        original = self.receipt.read_bytes()
        self.receipt.unlink()
        second, reused = self.run_job()
        self.assertTrue(reused)
        self.assertEqual(self.executed, 1)
        self.assertEqual(self.verified, 1)
        self.assertEqual(first, second)
        self.assertEqual(self.receipt.read_bytes(), original)
        self.assertTrue(self.destination.is_symlink())
        self.assertEqual(verify_receipt(self.receipt, self.destination), first)
        paths = receipt_inputs(self.receipt, self.destination)
        self.assertIn(str(Path(first["payload_root"]) / "result.txt"), paths)

    def test_missing_alias_is_restored_without_reexecuting(self):
        first, _ = self.run_job()
        self.destination.unlink()
        with self.assertRaisesRegex(ValueError, "link is missing"):
            receipt_inputs(self.receipt, self.destination)
        verify_receipt(self.receipt, self.destination, repair_link=True)
        self.assertEqual(self.destination.resolve(), Path(first["payload_root"]))
        self.assertEqual(self.executed, 1)

    def test_disabled_reuse_executes_new_attempt_then_updates_matching_cache(self):
        first, _ = self.run_job()
        second, reused = self.run_job(allow_reuse=False)
        self.assertFalse(reused)
        self.assertEqual(self.executed, 2)
        self.assertNotEqual(first["payload_root"], second["payload_root"])
        self.assertEqual(first["identity"], second["identity"])
        self.assertTrue(Path(first["payload_root"]).is_dir())
        third, reused = self.run_job()
        self.assertTrue(reused)
        self.assertEqual(self.executed, 2)
        self.assertEqual(second, third)

    def test_failed_forced_attempt_keeps_last_matching_completion(self):
        first, _ = self.run_job()
        def fail(payload):
            (payload / "partial.txt").write_text("partial")
            raise RuntimeError("synthetic forced attempt failure")
        with self.assertRaisesRegex(RuntimeError, "forced attempt failure"):
            self.run_job(allow_reuse=False, execute=fail)
        self.assertEqual(verify_receipt(self.receipt, self.destination), first)
        restored, reused = self.run_job()
        self.assertTrue(reused)
        self.assertEqual(restored, first)

    def test_deleted_payload_blocks_normalization_and_reuse(self):
        first, _ = self.run_job()
        (Path(first["payload_root"]) / "result.txt").unlink()
        with self.assertRaisesRegex(ValueError, "missing output"):
            receipt_inputs(self.receipt, self.destination)
        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            verify_receipt(self.receipt, self.destination)
        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            self.run_job()
        self.assertEqual(self.executed, 1)

    def test_changed_payload_or_new_file_never_reused(self):
        first, _ = self.run_job()
        payload = Path(first["payload_root"])
        (payload / "result.txt").write_text("tampered output")
        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            self.run_job()
        (payload / "result.txt").write_text("verified output")
        (payload / "unexpected.txt").write_text("added")
        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            self.run_job()
        self.assertEqual(self.executed, 1)

    def test_changed_settings_or_inputs_produce_new_snapshot_preserving_prior(self):
        first, _ = self.run_job()
        original = Path(first["payload_root"])
        self.config["modules"]["example"]["threshold"] = 2
        second, reused = self.run_job()
        self.assertFalse(reused)
        self.assertNotEqual(first["key"], second["key"])
        self.input.write_text("changed synthetic input")
        third, reused = self.run_job()
        self.assertFalse(reused)
        self.assertNotEqual(second["key"], third["key"])
        self.assertEqual((original / "result.txt").read_text(), "verified output")

    def test_changed_environment_source_and_executable_change_identity(self):
        first = self.identity()
        env = json.loads(self.environment.read_text())
        env["created_at"] = "later"
        self.environment.write_text(json.dumps(env))
        self.assertEqual(first, self.identity())
        env["packages"][0]["version"] = "2"
        self.environment.write_text(json.dumps(env))
        self.assertNotEqual(first, self.identity())
        second = self.identity()
        self.source.write_text("# changed parser\n")
        self.assertNotEqual(second, self.identity())
        exe = self.root / "fake-tool"
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(0o755)
        config = deepcopy(self.config)
        config["modules"]["example"]["executable"] = str(exe)
        third = self.identity(config)
        exe.write_text("#!/bin/sh\nexit 1\n")
        self.assertNotEqual(third, self.identity(config))

    def test_runtime_only_settings_do_not_invalidate_result(self):
        first = self.identity()
        self.config["modules"]["example"].update(timeout_seconds=100, heartbeat_seconds=1,
                                                failed_retention=2, failed_attempts_to_keep=4,
                                                cache=True)
        self.assertEqual(first, self.identity())
        self.config["modules"]["example"]["threads"] = 3
        self.assertNotEqual(first, self.identity())

    def test_unverified_environment_or_changed_resource_receipt_is_rejected(self):
        bad = deepcopy(self.config)
        bad["environment_receipts"] = []
        with self.assertRaisesRegex(ValueError, "verified environment"):
            self.identity(bad)
        resource = self.root / "resource.json"
        resource.write_text(json.dumps({"schema": RESOURCE_SCHEMA, "status": "completed", "resource": "example",
            "artifacts": {"/immutable/database": "checksum"}, "result": {"version": 1}}))
        bad["environment_receipts"] = [str(self.environment)]
        bad["resource_receipts"] = [{"path": str(resource), "sha256": sha256(resource)}]
        self.identity(bad)
        resource.write_text(resource.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "receipt changed"):
            self.identity(bad)

    def test_resource_loader_can_explicitly_omit_sample_inputs(self):
        resource = self.root / "resource.json"
        resource.write_text(json.dumps({"schema": RESOURCE_SCHEMA, "status": "completed", "resource": "example",
                                       "artifacts": {"/database": "checksum"}}))
        config = deepcopy(self.config)
        config["resource_receipts"] = [str(resource)]
        identity = execution_identity("example", {}, config, sources=[self.source],
                                      require_sample_inputs=False)
        self.assertFalse(identity["inputs"])
        self.assertTrue(identity["resources"])
        with self.assertRaisesRegex(ValueError, "declared input files"):
            execution_identity("example", {}, config, sources=[self.source])

    def test_failed_verification_never_publishes_success_or_replaces_previous(self):
        first, _ = self.run_job()
        original = self.receipt.read_bytes()
        self.config["modules"]["example"]["threshold"] = 2
        def fail(payload):
            raise ValueError("synthetic invalid result")
        with self.assertRaisesRegex(ValueError, "synthetic invalid result"):
            self.run_job(verify=fail)
        self.assertEqual(self.receipt.read_bytes(), original)
        self.assertEqual(self.destination.resolve(), Path(first["payload_root"]))
        failures = list(self.store.glob("*/attempt-*/failure.json"))
        self.assertEqual(len(failures), 1)
        self.assertTrue((failures[0].parent / "data/result.txt").is_file())
        self.assertFalse((failures[0].parent / "completed.json").exists())

    def test_failed_attempt_retention_is_bounded_without_pruning_success(self):
        first, _ = self.run_job()
        self.config["modules"]["example"]["threshold"] = 2
        def fail(payload):
            (payload / "partial.txt").write_text("diagnostic")
            raise RuntimeError("synthetic interrupted command")
        for _ in range(4):
            with self.assertRaises(RuntimeError):
                self.run_job(execute=fail, failed_retention=2)
        self.assertEqual(len(list(self.store.glob("*/attempt-*/failure.json"))), 2)
        self.assertTrue(Path(first["payload_root"]).is_dir())

    def test_input_change_during_run_never_publishes_success(self):
        identity = self.identity()
        def mutate(payload):
            self.execute(payload)
            self.input.write_text("changed while process was running")
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.run_job(identity=identity, execute=mutate)
        self.assertFalse(self.receipt.exists())

    def test_existing_unmanaged_outputs_are_archived_without_deletion(self):
        self.destination.mkdir(parents=True)
        (self.destination / "old.txt").write_text("legacy result")
        self.run_job()
        files = list((self.destination.parent / ".previous-results").glob("*/old.txt"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_text(), "legacy result")

    def test_output_links_are_bound_to_targets_and_directory_membership(self):
        def linked(payload):
            self.execute(payload)
            (payload / "alias.txt").symlink_to("result.txt")
        first, _ = self.run_job(execute=linked)
        payload = Path(first["payload_root"])
        alias = payload / "alias.txt"
        alias.unlink()
        alias.symlink_to("nested/empty.txt")
        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            verify_receipt(self.receipt, self.destination)
        alias.unlink()
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Unsupported native output symlink"):
            output_inventory(payload)

    def test_completion_record_and_destination_tampering_rejected(self):
        self.run_job()
        row = json.loads(self.receipt.read_text())
        row["identity"]["module"] = "other"
        self.receipt.write_text(json.dumps(row))
        with self.assertRaisesRegex(ValueError, "Invalid native completion"):
            verify_receipt(self.receipt, self.destination)
        self.receipt.unlink()
        self.run_job()
        self.destination.unlink()
        self.destination.symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "different payload"):
            receipt_inputs(self.receipt, self.destination)


if __name__ == "__main__":
    unittest.main()
