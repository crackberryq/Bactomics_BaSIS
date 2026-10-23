"""Synthetic post-command recovery; no native program or database is run."""
from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor"), str(ROOT / "scripts")]
import recover_ccf_results as recovery
from safety import ccf_assembly as assembly, resources, result_store, runtime
from tests.test_ccf_assembly import fixture, write


class CompletedCommandRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="recovery synthetic ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = Path(tempfile.mkdtemp(prefix="basis-ccf-assembly-", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(self.raw, ignore_errors=True))
        staged = self.root / "native-fixture"
        self.sequences = {"synthetic": "A" * 12}
        fixture(staged, self.sequences, cas_records=("CCF000001",))
        shutil.copytree(staged, self.raw, dirs_exist_ok=True)
        self.macsyfinder = self.root / "synthetic-macsyfinder"
        self.macsyfinder.chmod(0o755)
        manifest = runtime.read_json(self.raw / "runtime_isolation.json")
        manifest["working_directory"] = str(self.raw)
        runtime.write_json(self.raw / "runtime_isolation.json", manifest)
        # Bind the synthetic invocation's original absolute paths to the /tmp
        # source before recovery; recovery must preserve these audit bytes.
        audit_path = self.raw / "macsyfinder-audits/CCF000001.json"
        audit_path.write_text(audit_path.read_text().replace(str(staged), str(self.raw)))
        write(self.raw / "CRISPRCasFinder.pl", "synthetic source; never execute\n")
        write(self.raw / "sel392v2.so", "synthetic library identity\n")
        self.models = self.root / "models"
        self.models.mkdir()
        (self.raw / "models").symlink_to(self.models, target_is_directory=True)
        self.pin = patch.object(assembly.ccf, "SOURCE_SHA256", result_store.sha256(self.raw / "CRISPRCasFinder.pl"))
        self.pin.start()
        self.addCleanup(self.pin.stop)
        self.stopped = patch.object(recovery, "active_workflows", return_value=[])
        self.stopped.start()
        self.addCleanup(self.stopped.stop)
        self.safety = self.root / "sample/safety"
        self.sample_path = self.safety / "metadata/sample.json"
        self.config_path = self.safety / "metadata/configs/crisprcasfinder.json"
        assembly_input = self.root / "sample/assembly.fna"
        write(assembly_input, ">synthetic\n" + self.sequences["synthetic"] + "\n")
        self.sample = {"sample_id": "synthetic", "assembly": str(assembly_input)}
        runtime.write_json(self.sample_path, self.sample)
        environment = self.root / "environment.json"
        runtime.write_json(environment, {"schema": "basis-snake-environment-v1", "status": "completed",
            "packages": [{"name": "synthetic", "version": "1"}]})
        resource = self.root / "resource.json"
        runtime.write_json(resource, {"schema": resources.SCHEMA, "resource": "crisprcasfinder", "status": "completed",
            "artifacts": {"synthetic-resource": {"bytes": 1, "sha256": "synthetic"}}, "result": {"version": "synthetic"}})
        self.config = {"modules": {"crisprcasfinder": {"enabled": True, "threads": 2,
            "executable": sys.executable, "min_evidence_level": 3, "genetic_code": 11}},
            "resource_receipts": [str(resource)], "environment_receipts": [str(environment)]}
        runtime.write_json(self.config_path, self.config)
        modules = (assembly, assembly.ccf, recovery.inputs, recovery.normalize, recovery.util,
                   recovery.extend, result_store, runtime)
        self.identity = result_store.execution_identity("crisprcasfinder", self.sample, self.config,
            sources=[module.__file__ for module in modules],
            extra_inputs=[sys.executable, self.macsyfinder], executable=sys.executable)
        self.old_store = self.safety / "results-cache/crisprcasfinder"
        self.key = result_store.digest(self.identity)
        self.attempt = self.old_store / self.key / ("attempt-" + "1" * 32)
        self.attempt.mkdir(parents=True)
        (self.old_store / ".lock").touch()
        runtime.write_json(self.attempt / "started.json", {"schema": result_store.SCHEMA, "name": "crisprcasfinder",
            "status": "started", "key": self.key, "identity": self.identity, "started_at": "synthetic-start"})
        self.command = {"argv": ["python", "basis_ccf_launch.py", sys.executable,
            "CRISPRCasFinder.pl", "-in", "input.fna", "-out", "native", "-so", "./sel392v2.so",
            "-cas", "-keep", "-log", "-cpuM", "2", "-gcode", "11"], "cwd": str(self.raw),
            "status": "completed", "returncode": 0, "started_at": "synthetic-start", "finished_at": "synthetic-finish"}
        runtime.write_json(self.attempt / "commands.json", [self.command])
        write(self.attempt / "commands.log", "synthetic successful command\n")
        write(self.attempt / "data/partial.txt", "preserve interrupted Windows copy\n")
        self.store = self.root / "linux-results/crisprcasfinder"
        self.native = self.safety / "native/crisprcasfinder"
        self.receipt = self.safety / "completion/crisprcasfinder.json"

    def plan(self):
        return recovery.build_plan(self.attempt, self.sample_path, self.config_path,
                                   self.store, self.native, self.receipt)

    def apply(self, plan=None):
        with contextlib.redirect_stderr(io.StringIO()):
            return recovery.apply_plan(plan or self.plan())

    def test_plan_is_read_only_and_does_not_scan_native_tree(self):
        with patch("os.walk", side_effect=AssertionError("unexpected recursive traversal")):
            plan = self.plan()
        self.assertEqual(plan["status"], "ready_for_validation")
        self.assertFalse(plan["native_command_will_run"])
        self.assertFalse(self.store.exists())
        self.assertFalse(self.receipt.exists())
        self.assertFalse((self.raw / "parsed.json").exists())

    def test_recovery_preserves_originals_and_never_launches_native_command(self):
        before = (self.raw / "native/result.json").read_bytes()
        cas_audit = (self.raw / "macsyfinder-audits/CCF000001.json").read_bytes()
        with patch("subprocess.Popen", side_effect=AssertionError("native execution forbidden")):
            result = self.apply()
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["native_command_ran"])
        self.assertEqual((self.raw / "native/result.json").read_bytes(), before)
        self.assertTrue((self.raw / "models").is_symlink())
        self.assertFalse((self.raw / "parsed.json").exists())
        self.assertEqual((self.attempt / "data/partial.txt").read_text(), "preserve interrupted Windows copy\n")
        self.assertTrue(self.native.is_symlink())
        row = result_store.verify_receipt(self.receipt, self.native, self.identity)
        self.assertEqual((self.native / "macsyfinder-audits/CCF000001.json").read_bytes(), cas_audit)
        self.assertEqual(runtime.read_json(self.native / "parsed.json")["metrics"]
                         ["cas_contig_assessments"]["synthetic"]["status"], "completed")
        self.assertTrue(Path(row["payload_root"]).is_relative_to(self.store))
        self.assertFalse(os.path.lexists(self.native / "models"))
        audit = runtime.read_json(Path(row["payload_root"]).parent / "recovery.json")
        self.assertEqual(audit["original_attempt"], str(self.attempt))
        self.assertEqual(audit["native_command"], self.command)
        self.assertEqual((Path(row["payload_root"]).parent / "commands.log").read_bytes(),
                         (self.attempt / "commands.log").read_bytes())
        binaries = self.root / "bin"
        binaries.mkdir()
        (binaries / "python").symlink_to(sys.executable)
        (binaries / "macsyfinder").symlink_to(self.macsyfinder)
        with patch.dict(os.environ, {"PATH": str(binaries) + os.pathsep + os.environ["PATH"]}), \
                patch.object(runtime.DirectRunner, "run", side_effect=AssertionError("native rerun forbidden")), \
                contextlib.redirect_stdout(io.StringIO()):
            resumed, reused = assembly.run_assembly(self.sample, self.config, self.native,
                self.receipt, self.store, threads=2, sources=sorted(self.identity["sources"]))
        self.assertTrue(reused)
        self.assertEqual(resumed, row)
        receipt_before = self.receipt.read_bytes()
        shutil.rmtree(self.raw)
        self.assertEqual(self.apply()["status"], "already_completed")
        self.assertEqual(self.receipt.read_bytes(), receipt_before)

    def test_failed_native_command_cannot_be_recovered(self):
        self.command["returncode"] = 1
        runtime.write_json(self.attempt / "commands.json", [self.command])
        with self.assertRaisesRegex(ValueError, "did not complete successfully"):
            self.plan()
        self.assertFalse(self.store.exists())

    def test_changed_input_or_configuration_is_rejected(self):
        write(Path(self.sample["assembly"]), ">synthetic\nACGTACGA\n")
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.plan()
        write(Path(self.sample["assembly"]), ">synthetic\n" + self.sequences["synthetic"] + "\n")
        self.config["modules"]["crisprcasfinder"]["genetic_code"] = 4
        runtime.write_json(self.config_path, self.config)
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.plan()

    def test_disabled_cache_cannot_accidentally_rerun_recovered_native_work(self):
        self.config["modules"]["crisprcasfinder"]["cache"] = False
        runtime.write_json(self.config_path, self.config)
        with self.assertRaisesRegex(ValueError, "cache reuse enabled"):
            self.plan()

    def test_current_writer_and_original_store_lock_both_block_apply(self):
        plan = self.plan()
        with patch.object(recovery, "active_workflows", return_value=[{"pid": 123, "program": "snakemake"}]):
            with self.assertRaisesRegex(ValueError, "still running"):
                self.apply(plan)
        with (self.old_store / ".lock").open("r+") as locked:
            fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "still locked"):
                self.apply(plan)
        self.assertFalse(self.store.exists())

    def test_missing_raw_output_cannot_fall_back_to_partial_windows_copy(self):
        shutil.rmtree(self.raw)
        with self.assertRaisesRegex(ValueError, "temporary output is unavailable"):
            self.plan()
        self.assertTrue((self.attempt / "data/partial.txt").exists())

    def test_raw_output_corruption_never_creates_completion_receipt(self):
        (self.raw / "native/rawFASTA/analyzedSequences/CCF000001.fna").unlink()
        with self.assertRaisesRegex(ValueError, "complete supplied assembly"):
            self.apply()
        self.assertFalse(self.receipt.exists())
        self.assertFalse(self.native.exists())
        self.assertFalse(self.store.exists())

    def test_native_stderr_error_is_not_hidden_by_zero_exit_status(self):
        write(self.attempt / "commands.log", "Traceback (most recent call last):\n")
        with self.assertRaisesRegex(ValueError, "native error"):
            self.apply()
        self.assertFalse(self.receipt.exists())

    def test_existing_native_directory_is_never_replaced_or_archived(self):
        write(self.native / "keep.txt", "existing data")
        with self.assertRaisesRegex(ValueError, "no overwrite allowed"):
            self.plan()
        self.assertEqual((self.native / "keep.txt").read_text(), "existing data")
        self.assertFalse((self.native.parent / ".previous-results").exists())

    def test_windows_store_and_unbound_temporary_output_are_rejected(self):
        with patch.object(recovery, "_filesystem", return_value="9p"):
            with self.assertRaisesRegex(ValueError, "local Linux filesystem"):
                self.plan()
        manifest = runtime.read_json(self.raw / "runtime_isolation.json")
        manifest["working_directory"] = "/tmp/basis-ccf-assembly-other"
        runtime.write_json(self.raw / "runtime_isolation.json", manifest)
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.plan()

    def test_copy_corruption_fails_validation_and_preserves_raw_source(self):
        original_copy = shutil.copytree
        def corrupt(source, target, *args, **kwargs):
            value = original_copy(source, target, *args, **kwargs)
            if Path(source) == self.raw:
                (Path(target) / "stdout.txt").write_text("incomplete copied output\n")
            return value
        with patch.object(recovery.shutil, "copytree", side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, "successful completion"):
                self.apply()
        self.assertIn("Thank you", (self.raw / "stdout.txt").read_text())
        self.assertFalse(self.receipt.exists())
        self.assertFalse(self.native.exists())


class MountParsingTests(unittest.TestCase):
    def test_longest_mount_and_escaped_spaces(self):
        mounts = "1 0 0:1 / / rw - ext4 /dev/root rw\n2 1 0:2 / /mnt/c rw - 9p drvfs rw\n3 1 0:3 / /home/space\\040name rw - xfs /dev/local rw\n"
        self.assertEqual(recovery._filesystem("/mnt/c/results", mounts), "9p")
        self.assertEqual(recovery._filesystem("/home/space name/results", mounts), "xfs")
        self.assertEqual(recovery._filesystem("/home/jimi/results", mounts), "ext4")


if __name__ == "__main__":
    unittest.main()
