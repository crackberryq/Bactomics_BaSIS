"""Recover synthetic completed commands; never launch biological programs."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import recover_integron_results as recovery
from safety import integron, native_execution, result_store, runtime
from test_integron import make_fixture


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="integron recovery's ")
        self.addCleanup(self.temp.cleanup)
        self.isolate = Path(self.temp.name)
        self.base = self.isolate / "safety"
        self.sample, self.config = make_fixture(self.isolate)
        runtime.write_json(self.base / "metadata/sample.json", self.sample)
        self.save_config()
        self.destination = self.base / "native/integronfinder"
        self.receipt = self.base / "completion/integronfinder.json"
        self.store = self.base / "results-cache/integronfinder"
        self.guard = patch.object(recovery, "active_workflows", return_value=[])
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def save_config(self):
        runtime.write_json(self.base / "metadata/configs/integronfinder.json", self.config)

    def legacy_run(self, partial=True):
        if not partial:
            path = Path(self.config["modules"]["integronfinder"]["executable"])
            path.write_text(path.read_text().replace("write_payload(p)", "write_payload(p, partial=False)"))
        with contextlib.redirect_stdout(io.StringIO()):
            if partial:
                with self.assertRaisesRegex(ValueError, recovery.OLD_ERROR):
                    native_execution.execute("integronfinder", self.sample, self.config,
                        self.destination, self.receipt, self.store, threads=1, sources=recovery.sources())
            else:
                native_execution.execute("integronfinder", self.sample, self.config,
                    self.destination, self.receipt, self.store, threads=1, sources=recovery.sources())
        return next(self.store.glob("*/attempt-*/started.json")).parent

    def test_saved_failure_recovery_preserves_originals_and_resume_reuses(self):
        attempt = self.legacy_run()
        before = {p.relative_to(attempt): p.read_bytes() for p in attempt.rglob("*") if p.is_file()}
        plan = recovery.inspect_sample(self.isolate)
        self.assertEqual(plan["coverage"]["not_assessed_contigs"], 1)
        self.assertFalse(self.receipt.exists())
        with patch.object(runtime.DirectRunner, "run", side_effect=AssertionError("native launch forbidden")):
            recovery.apply_sample(self.isolate)
            first = result_store.verify_receipt(self.receipt, self.destination)
            parsed = runtime.read_json(self.destination / "parsed.json")
            self.assertTrue(parsed["scope_incomplete"])
            recovery.apply_sample(self.isolate)
            self.assertEqual(result_store.verify_receipt(self.receipt), first)
            self.receipt.unlink()
            with contextlib.redirect_stdout(io.StringIO()):
                _, reused = integron.execute(self.sample, self.config, self.destination, self.receipt,
                    self.store, threads=1, sources=recovery.sources() + [Path(integron.__file__)])
        self.assertTrue(reused)
        self.assertEqual({p.relative_to(attempt): p.read_bytes() for p in attempt.rglob("*") if p.is_file()}, before)

    def test_legacy_completed_result_is_migrated_without_native_execution(self):
        attempt = self.legacy_run(partial=False)
        original = (attempt / "completed.json").read_bytes()
        with patch.object(runtime.DirectRunner, "run", side_effect=AssertionError("native launch forbidden")):
            recovery.apply_sample(self.isolate)
        self.assertEqual((attempt / "completed.json").read_bytes(), original)
        self.assertFalse(runtime.read_json(self.destination / "parsed.json")["scope_incomplete"])

    def test_changed_inputs_and_failed_command_are_rejected(self):
        attempt = self.legacy_run()
        command_file = attempt / "commands.json"
        commands = runtime.read_json(command_file)
        commands[0]["returncode"] = 1
        runtime.write_json(command_file, commands)
        with self.assertRaisesRegex(ValueError, "successful invocation"):
            recovery.apply_sample(self.isolate)
        commands[0]["returncode"] = 0
        runtime.write_json(command_file, commands)
        Path(self.sample["assembly"]).write_text(">different\nAAAA\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            recovery.apply_sample(self.isolate)
        self.assertFalse(self.receipt.exists())

    def test_unknown_failure_and_active_workflow_do_not_publish(self):
        attempt = self.legacy_run()
        with patch.object(recovery, "active_workflows", return_value=[{"pid": 123}]):
            with self.assertRaisesRegex(ValueError, "Stop the workflow"):
                recovery.apply_sample(self.isolate)
        failure = runtime.read_json(attempt / "failure.json")
        failure["error"] = "unrelated failure"
        runtime.write_json(attempt / "failure.json", failure)
        with self.assertRaisesRegex(ValueError, "supported summary-coverage error"):
            recovery.apply_sample(self.isolate)
        self.assertFalse(self.receipt.exists())

    def test_copy_failure_leaves_originals_and_does_not_publish_receipt(self):
        attempt = self.legacy_run()
        before = (attempt / "data/stdout.txt").read_bytes()
        with patch.object(recovery.shutil, "copy2", side_effect=OSError("synthetic disk failure")):
            with self.assertRaisesRegex(OSError, "synthetic disk failure"):
                recovery.apply_sample(self.isolate)
        self.assertEqual((attempt / "data/stdout.txt").read_bytes(), before)
        self.assertFalse(self.receipt.exists())
        recovery.apply_sample(self.isolate)
        result_store.verify_receipt(self.receipt, self.destination)


if __name__ == "__main__":
    unittest.main()
