"""Saved ISEScan recovery contracts; synthetic files, no native analysis."""
import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor"), str(ROOT / "scripts")]
import recover_isescan_results as recovery
from safety import native, native_execution, result_store, runtime
from test_isescan import write_fixture


class ISEScanRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="isescan recovery's ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.isolate = self.root / "isolate"
        self.base = self.isolate / "safety"
        self.assembly = self.isolate / "annotation/fixture.fna"
        self.assembly.parent.mkdir(parents=True)
        self.assembly.write_text("".join(f">{c}\n" + "A" * 60 + "\n" for c in ("1", "2", "3")))
        self.sample = {"sample_id": "fixture", "assembly": str(self.assembly), "input_mode": "external"}
        self.tool = self.root / "synthetic-isescan"
        self.tool.write_text("#!/bin/sh\nexit 99\n")
        self.tool.chmod(0o755)
        prefix = self.root / "environment"
        prefix.mkdir()
        environment = self.root / "environment.json"
        runtime.write_json(environment, {"schema": "basis-snake-environment-v1", "status": "completed",
            "prefix": str(prefix), "packages": [{"name": "fixture", "version": "1"}],
            "recipe_sha256": "fixture", "post_deploy_sha256": "fixture"})
        self.config = {"modules": {"isescan": {"enabled": True, "executable": str(self.tool),
            "threads": 7, "cache": True}}, "environment_receipts": [str(environment)]}
        self.validation = {"input_hashes": {"assembly": result_store.sha256(self.assembly)},
                           "protein_coordinates": {}, "contig_lengths": {c: 60 for c in ("1", "2", "3")}}
        for name, data in (("sample.json", self.sample), ("configs/isescan.json", self.config),
                           ("validation.json", self.validation),
                           ("config.json", {"version": "0.4.0a1", "deep": {"enabled": False},
                            "modules": {"isescan": {"enabled": True}, "minced": {"enabled": True}}})):
            runtime.write_json(self.base / "metadata" / name, data)
        self.other_evidence = self.base / "evidence/minced.json"
        runtime.write_json(self.other_evidence, {"sample_id": "fixture", "module": "minced",
            "execution_status": "completed", "interpretation": "not_detected", "findings": [],
            "input_hashes": self.validation["input_hashes"]})
        self.other_native = self.base / "native/minced/keep.txt"
        self.other_native.parent.mkdir(parents=True)
        self.other_native.write_text("preserve this completed result\n")
        self.identity = result_store.execution_identity("isescan", self.sample, self.config,
                                                        sources=recovery.sources(), executable=str(self.tool))
        self.legacy = copy.deepcopy(self.identity)
        self.legacy["sources"][str(ROOT / recovery.PARSER)] = recovery.LEGACY_PARSER
        key = result_store.digest(self.legacy)
        self.store = self.base / "results-cache/isescan"
        self.attempt = self.store / key / ("attempt-" + "a" * 32)
        self.payload = self.attempt / "data"
        write_fixture(self.payload, self.assembly)
        stamp = datetime.now(timezone.utc).isoformat()
        common = {"schema": result_store.SCHEMA, "name": "isescan", "key": key, "identity": self.legacy,
                  "started_at": stamp, "payload_root": str(self.payload)}
        runtime.write_json(self.attempt / "started.json", {**common, "status": "started"})
        runtime.write_json(self.attempt / "failure.json", {**common, "status": "failed", "error": recovery.OLD_ERROR})
        argv = ["bash", "-euo", "pipefail", "-c",
                native.command_shell("isescan", self.sample, self.config["modules"]["isescan"], self.payload, 7)]
        runtime.write_json(self.attempt / "commands.json", [{"argv": argv, "status": "completed",
            "returncode": 0, "started_at": stamp, "finished_at": stamp}])
        (self.attempt / "commands.log").write_text("Synthetic successful native command\n")
        self.receipt = self.base / "completion/isescan.json"
        self.destination = self.base / "native/isescan"
        self.evidence = self.base / "evidence/isescan.json"
        self.addCleanup(patch.stopall)
        patch.object(recovery, "active_workflows", return_value=[]).start()
        self.no_native = patch("safety.runtime.DirectRunner.run", side_effect=AssertionError("No native commands allowed")).start()

    def files(self, root=None):
        return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in (root or self.root).rglob("*")
                if p.is_file() and p.name != ".lock"}

    def test_preview_is_read_only_and_validates_report_scope(self):
        before = self.files()
        result = recovery.recover(self.isolate, refresh_report=True)
        self.assertEqual(result["status"], "ready_to_revalidate")
        self.assertFalse(result["applied"])
        self.assertEqual(before, self.files())
        self.no_native.assert_not_called()

    def test_apply_preserves_original_attempt_other_modules_and_metadata(self):
        original = self.files(self.attempt)
        protected = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in
                     [self.other_evidence, self.other_native, self.base / "metadata/sample.json",
                      self.base / "metadata/config.json", self.base / "metadata/configs/isescan.json"]}
        result = recovery.recover(self.isolate, apply=True, refresh_report=True)
        self.assertEqual(result["insertion_sequence_count"], 0)
        self.assertEqual(result["interpretation"], "not_detected")
        self.assertTrue((self.base / "report.html").is_file())
        self.assertEqual(original, self.files(self.attempt))
        self.assertEqual(protected, {path: (Path(path).read_bytes(), Path(path).stat().st_mtime_ns) for path in protected})
        record = result_store.verify_receipt(self.receipt, self.destination, self.identity)
        new_payload = Path(record["payload_root"])
        for path in self.payload.rglob("*"):
            if path.is_file():
                self.assertEqual(path.read_bytes(), (new_payload / path.relative_to(self.payload)).read_bytes())
        self.assertFalse(runtime.read_json(new_payload / "recovery.json")["native_commands_run"])
        self.assertEqual(runtime.read_json(self.evidence)["execution_status"], "completed")
        self.no_native.assert_not_called()

    def test_repeat_and_worker_reuse_launch_no_native_commands(self):
        recovery.recover(self.isolate, apply=True)
        first = runtime.read_json(self.receipt)
        count = len(list(self.store.glob("*/attempt-*")))
        recovery.recover(self.isolate, apply=True)
        self.assertEqual(runtime.read_json(self.receipt), first)
        self.assertEqual(len(list(self.store.glob("*/attempt-*"))), count)
        with patch("sys.stdout", new=io.StringIO()):
            record, reused = native_execution.execute("isescan", self.sample, self.config,
                self.destination, self.receipt, self.store, threads=7, sources=recovery.sources())
        self.assertTrue(reused)
        self.assertEqual(record["payload_root"], first["payload_root"])
        self.no_native.assert_not_called()

    def test_audit_remains_available_after_old_failed_attempt_is_pruned(self):
        recovery.recover(self.isolate, apply=True)
        row = runtime.read_json(self.receipt)
        payload = Path(row["payload_root"])
        audit = runtime.read_json(payload / "recovery.json")
        for name, checksum in audit["original_record_hashes"].items():
            self.assertEqual(result_store.sha256(payload / "recovery-source" / name), checksum)
        shutil.rmtree(self.attempt)  # Simulate later native failed-attempt retention, in this fixture only.
        recovery.recover(self.isolate, apply=True, refresh_report=True)
        self.assertEqual(runtime.read_json(payload / "recovery-source/commands.json")[0]["returncode"], 0)
        result_store.verify_receipt(self.receipt, self.destination, self.identity)

    def test_bad_native_exit_or_invocation_is_refused_without_writes(self):
        path = self.attempt / "commands.json"
        original = runtime.read_json(path)
        for change in ({"returncode": 7}, {"status": "failed"}, {"argv": ["different", "command"]}):
            with self.subTest(change=change):
                runtime.write_json(path, [{**original[0], **change}])
                before = self.files()
                with self.assertRaisesRegex(ValueError, "successful invocation"):
                    recovery.recover(self.isolate, apply=True)
                self.assertEqual(before, self.files())

    def test_different_failure_is_not_reclassified(self):
        path = self.attempt / "failure.json"
        row = runtime.read_json(path)
        row["error"] = "Unrelated native failure"
        runtime.write_json(path, row)
        with self.assertRaisesRegex(ValueError, "legacy parser failure"):
            recovery.recover(self.isolate, apply=True)
        self.assertFalse(self.receipt.exists())

    def test_changed_input_or_configuration_is_refused(self):
        for target, content in ((self.assembly, self.assembly.read_text() + "A\n"),
                                (self.tool, "#!/bin/sh\nexit 98\n"),
                                (self.base / "metadata/configs/isescan.json",
                                 json.dumps({**self.config, "execution": {"changed": True}}))):
            with self.subTest(target=target):
                old = target.read_bytes()
                target.write_text(content)
                with self.assertRaisesRegex(ValueError, "changed"):
                    recovery.recover(self.isolate, apply=True)
                self.assertFalse(self.receipt.exists())
                target.write_bytes(old)

    def test_unfinished_search_is_refused(self):
        path = next((self.payload / "output/hmm").rglob("clusters.single.*"))
        path.write_text(path.read_text().replace("# [ok]\n", ""))
        with self.assertRaisesRegex(ValueError, "incomplete"):
            recovery.recover(self.isolate, apply=True)
        self.assertFalse(self.receipt.exists())

    def test_missing_or_stale_other_evidence_blocks_report_before_publication(self):
        old = self.other_evidence.read_bytes()
        self.other_evidence.unlink()
        with self.assertRaises(FileNotFoundError):
            recovery.recover(self.isolate, apply=True, refresh_report=True)
        self.assertFalse(self.receipt.exists())
        self.other_evidence.write_bytes(old)
        row = runtime.read_json(self.other_evidence)
        row["input_hashes"] = {"assembly": "wrong"}
        runtime.write_json(self.other_evidence, row)
        with self.assertRaisesRegex(ValueError, "different inputs"):
            recovery.recover(self.isolate, apply=True, refresh_report=True)
        self.assertFalse(self.receipt.exists())

    def test_symlinked_receipt_parent_and_payload_are_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.receipt.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            recovery.recover(self.isolate, apply=True)
        self.assertEqual(list(outside.iterdir()), [])
        self.receipt.parent.unlink()
        (self.payload / "linked").symlink_to(self.assembly)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            recovery.recover(self.isolate, apply=True)

    def test_unrelated_native_destination_is_not_overwritten(self):
        self.destination.mkdir()
        marker = self.destination / "keep"
        marker.write_text("existing unrelated output")
        with self.assertRaisesRegex(ValueError, "destination"):
            recovery.recover(self.isolate, apply=True)
        self.assertEqual(marker.read_text(), "existing unrelated output")

    def test_interrupted_receipt_publication_can_resume_from_verified_cache(self):
        write = runtime.write_json
        def interrupt(path, value):
            if Path(path) == self.receipt:
                raise OSError("simulated interruption")
            write(path, value)
        with patch.object(runtime, "write_json", side_effect=interrupt):
            with self.assertRaisesRegex(OSError, "interruption"):
                recovery.recover(self.isolate, apply=True)
        self.assertFalse(self.receipt.exists())
        self.assertTrue(self.destination.is_symlink())
        recovery.recover(self.isolate, apply=True, refresh_report=True)
        result_store.verify_receipt(self.receipt, self.destination, self.identity)
        self.assertTrue((self.base / "report.html").is_file())
        self.no_native.assert_not_called()

    def test_raw_change_during_copy_prevents_publication(self):
        copytree = recovery.shutil.copytree
        def change(source, target, *args, **kwargs):
            result = copytree(source, target, *args, **kwargs)
            if Path(source) == self.payload:
                (self.payload / "stderr.txt").write_text("changed during recovery")
            return result
        with patch.object(recovery.shutil, "copytree", side_effect=change):
            with self.assertRaisesRegex(ValueError, "changed"):
                recovery.recover(self.isolate, apply=True)
        self.assertFalse(self.receipt.exists())
        self.assertFalse(self.destination.exists())

    def test_active_workflow_blocks_recovery(self):
        with patch.object(recovery, "active_workflows", return_value=[{"pid": 123}]):
            with self.assertRaisesRegex(ValueError, "workers to stop"):
                recovery.recover(self.isolate, apply=True)

    def test_added_empty_result_table_cannot_bypass_no_hit_verification(self):
        (self.payload / "output/fixture.fna.tsv").write_text("seqID\tfamily\tisBegin\tisEnd\tstrand\n")
        with self.assertRaisesRegex(ValueError, "restricted to verified native zero"):
            recovery.recover(self.isolate, apply=True)
        self.assertFalse(self.receipt.exists())

    def test_partial_assessment_remains_inconclusive_in_report(self):
        write_fixture(self.payload, self.assembly, translated=("1", "2"))
        result = recovery.recover(self.isolate, apply=True, refresh_report=True)
        self.assertEqual(result["interpretation"], "inconclusive")
        self.assertTrue(runtime.read_json(self.evidence)["scope_incomplete"])


if __name__ == "__main__":
    unittest.main()
