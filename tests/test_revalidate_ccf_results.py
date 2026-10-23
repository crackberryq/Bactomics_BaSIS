"""Saved-result migration uses only synthetic artifacts, never native programs."""
from __future__ import annotations

import contextlib
import copy
import errno
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor"), str(ROOT / "scripts")]
import revalidate_ccf_results as recovery
from safety import ccf_assembly as assembly, resources, result_store, runtime, setup
from basis.config import ALL_MODULES
from tests.test_ccf_assembly import fixture, write


class SavedResultRevalidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ccf saved recovery ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.isolate = self.root / "sample"
        self.base = self.isolate / "safety"
        self.store = self.base / "results-cache/crisprcasfinder"
        self.target = self.root / "linux-results/crisprcasfinder"
        self.destination = self.base / "native/crisprcasfinder"
        self.receipt = self.base / "completion/crisprcasfinder.json"
        self.sample = {"sample_id": "synthetic", "assembly": str(self.isolate / "assembly.fna")}
        self.sequences = {"synthetic": "A" * 12}
        write(Path(self.sample["assembly"]), ">synthetic\n" + self.sequences["synthetic"] + "\n")
        environment, resource = self.root / "environment.json", self.root / "resource.json"
        runtime.write_json(environment, {"schema": "basis-snake-environment-v1", "status": "completed",
            "packages": [{"name": "synthetic", "version": "1"}]})
        runtime.write_json(resource, {"schema": resources.SCHEMA, "resource": "crisprcasfinder",
            "status": "completed", "artifacts": {"synthetic-resource": {"bytes": 1, "sha256": "synthetic"}},
            "result": {"version": "synthetic"}})
        self.config = {"modules": {"crisprcasfinder": {"enabled": True, "threads": 2,
            "executable": sys.executable, "min_evidence_level": 3, "genetic_code": 11}},
            "resource_receipts": [str(resource)], "environment_receipts": [str(environment)]}
        self.config_path = self.base / "metadata/configs/crisprcasfinder.json"
        runtime.write_json(self.base / "metadata/sample.json", self.sample)
        runtime.write_json(self.config_path, self.config)
        self.raw = self.root / "synthetic-staging"
        fixture(self.raw, self.sequences, cas_records=("CCF000001",), with_array=True)
        self.macsyfinder = self.root / "synthetic-macsyfinder"
        self.macsyfinder.chmod(0o755)
        write(self.raw / "CRISPRCasFinder.pl", "synthetic source; never execute\n")
        write(self.raw / "sel392v2.so", "synthetic library\n")
        models = self.root / "models"
        models.mkdir()
        (self.raw / "models").symlink_to(models, target_is_directory=True)
        self.pin = patch.object(assembly.ccf, "SOURCE_SHA256", result_store.sha256(self.raw / "CRISPRCasFinder.pl"))
        self.pin.start()
        self.addCleanup(self.pin.stop)
        self.guard = patch.object(recovery, "active_workflows", return_value=[])
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.forbid = patch.object(runtime.DirectRunner, "run", side_effect=AssertionError("native launch forbidden"))
        self.forbid.start()
        self.addCleanup(self.forbid.stop)
        self.current = result_store.execution_identity(recovery.MODULE, self.sample, self.config,
            sources=recovery.sources(), extra_inputs=[sys.executable, self.macsyfinder], executable=sys.executable)
        self.original = copy.deepcopy(self.current)
        self.original["sources"].update({str(ROOT / path): value for path, value in recovery.LEGACY_HASHES.items()})
        self.command = {"argv": ["python", "basis_ccf_launch.py", sys.executable,
            "CRISPRCasFinder.pl", "-in", "input.fna", "-out", "native", "-so", "./sel392v2.so",
            "-cas", "-keep", "-log", "-cpuM", "2", "-gcode", "11"], "cwd": str(self.raw),
            "status": "completed", "returncode": 0, "started_at": "synthetic-start", "finished_at": "synthetic-finish"}

    def saved(self, *, completed=False, identity=None):
        identity = identity or self.original
        key = result_store.digest(identity)
        attempt = self.store / key / ("attempt-" + "1" * 32)
        shutil.copytree(self.raw, attempt / "data", symlinks=True)
        started = {"schema": result_store.SCHEMA, "status": "started", "name": recovery.MODULE,
            "key": key, "identity": identity, "started_at": "2026-01-01T00:00:00+00:00"}
        runtime.write_json(attempt / "started.json", started)
        runtime.write_json(attempt / "commands.json", [self.command])
        write(attempt / "commands.log", "synthetic successful command\n")
        if completed:
            (attempt / "data/models").unlink()
            runtime.write_json(attempt / "data/parsed.json", {"legacy": "unchanged original"})
            row = {"schema": result_store.SCHEMA, "status": "completed", "name": recovery.MODULE,
                "key": key, "identity": identity, "payload_root": str(attempt / "data"),
                "destination": str(self.destination), "artifacts": result_store.output_inventory(attempt / "data"),
                "started_at": started["started_at"], "finished_at": "2026-01-01T01:00:00+00:00"}
            runtime.write_json(attempt / "completed.json", row)
            runtime.write_json(attempt.parent / "cache.json", row)
            result_store._publish_link(self.destination, attempt / "data")
            runtime.write_json(self.receipt, row)
        else:
            # Invented single-base terminal discrepancy, exactly the legacy
            # parser failure supported by this migration.
            path = attempt / "data/native/result.json"
            data = runtime.read_json(path)
            array = data["Sequences"][0]["Crisprs"][0]
            array["End"] = 5
            array["Regions"][-1].update(Start=6, End=6)
            runtime.write_json(path, data)
            runtime.write_json(attempt / "failure.json", {**started, "status": "failed", "error": recovery.OLD_ERROR})
        return attempt

    def inspect(self, **kwargs):
        with contextlib.redirect_stderr(io.StringIO()):
            return recovery.inspect_sample(self.isolate, **kwargs)

    def apply(self, **kwargs):
        with contextlib.redirect_stderr(io.StringIO()):
            return recovery.apply_sample(self.isolate, **kwargs)

    def snapshot(self, attempt):
        return {str(path.relative_to(attempt)): ("link", os.readlink(path)) if path.is_symlink()
                else ("file", path.read_bytes()) for path in attempt.rglob("*") if path.is_file() or path.is_symlink()}

    def test_legacy_failure_revalidated_with_originals_unchanged_and_cache_resume(self):
        attempt = self.saved()
        before = self.snapshot(attempt)
        self.assertEqual(self.inspect()["status"], "ready_to_revalidate")
        self.assertFalse(self.receipt.exists())
        with patch("subprocess.Popen", side_effect=AssertionError("native launch forbidden")):
            self.apply()
            row = result_store.verify_receipt(self.receipt, self.destination, self.current)
            self.assertEqual(self.inspect()["status"], "current_verified_result")
            self.apply()
        self.assertEqual(result_store.verify_receipt(self.receipt), row)
        self.assertEqual(self.snapshot(attempt), before)
        self.assertTrue((attempt / "data/models").is_symlink())
        self.assertFalse(os.path.lexists(self.destination / "models"))
        self.assertTrue(runtime.read_json(self.destination / "parsed.json")["scope_incomplete"])
        self.assertEqual((attempt / "data/stdout.txt").stat().st_ino, (self.destination / "stdout.txt").stat().st_ino)
        self.receipt.unlink()
        binaries = self.root / "bin"
        binaries.mkdir()
        (binaries / "python").symlink_to(sys.executable)
        (binaries / "macsyfinder").symlink_to(self.macsyfinder)
        with patch.dict(os.environ, {"PATH": str(binaries) + os.pathsep + os.environ["PATH"]}), \
                contextlib.redirect_stdout(io.StringIO()):
            resumed, reused = assembly.run_assembly(self.sample, self.config, self.destination,
                self.receipt, self.store, threads=2, sources=recovery.sources())
        self.assertTrue(reused)
        self.assertEqual(resumed, row)

    def test_legacy_completed_migrates_to_configured_store_and_preserves_parsed_inode(self):
        attempt = self.saved(completed=True)
        before = self.snapshot(attempt)
        configured = {"storage": {"native_results_directory": str(self.root / "configured-linux")}}
        selected = recovery.native_result_store(ROOT, configured, self.isolate, recovery.MODULE)
        self.apply(workflow_config=configured)
        row = result_store.verify_receipt(self.receipt, self.destination, self.current)
        self.assertTrue(Path(row["payload_root"]).is_relative_to(selected))
        self.assertEqual(self.snapshot(attempt), before)
        self.assertNotEqual((attempt / "data/parsed.json").stat().st_ino, (self.destination / "parsed.json").stat().st_ino)
        self.assertEqual(runtime.read_json(attempt / "data/parsed.json"), {"legacy": "unchanged original"})

    def test_absolute_saved_executables_work_without_native_path(self):
        self.saved()
        with patch.dict(os.environ, {"PATH": ""}):
            self.apply(store=self.target)
        result_store.verify_receipt(self.receipt, self.destination, self.current)

    def test_current_receipt_is_relocated_when_configured_store_changes(self):
        self.saved()
        self.apply()
        old = result_store.verify_receipt(self.receipt, self.destination)
        old_payload = Path(old["payload_root"])
        before = self.snapshot(old_payload.parent)
        self.apply(store=self.target)
        new = result_store.verify_receipt(self.receipt, self.destination, self.current)
        self.assertTrue(Path(new["payload_root"]).is_relative_to(self.target))
        self.assertNotEqual(old["payload_root"], new["payload_root"])
        self.assertEqual(self.snapshot(old_payload.parent), before)

    def test_unknown_mixed_and_other_source_changes_refused(self):
        for change in ("unknown", "mixed", "other", "missing"):
            with self.subTest(change=change):
                identity = copy.deepcopy(self.original)
                parser = str(ROOT / "workflow/lib/safety/ccf_assembly.py")
                if change == "unknown":
                    identity["sources"][parser] = "0" * 64
                elif change == "mixed":
                    identity["sources"][parser] = self.current["sources"][parser]
                elif change == "other":
                    identity["sources"][str(Path(runtime.__file__).resolve())] = "0" * 64
                else:
                    identity["sources"].pop(parser)
                attempt = self.saved(identity=identity)
                with self.assertRaisesRegex(ValueError, "identity changed|eight original"):
                    self.apply()
                shutil.rmtree(attempt.parent)
        self.assertFalse(self.receipt.exists())

    def test_config_environment_resource_and_input_drift_refused(self):
        self.saved()
        changes = [(self.config_path, lambda row: row["modules"][recovery.MODULE].update(genetic_code=4)),
                   (self.root / "environment.json", lambda row: row["packages"][0].update(version="2")),
                   (self.root / "resource.json", lambda row: row["result"].update(version="2")),
                   (self.base / "metadata/sample.json", lambda row: row.update(sample_id="different"))]
        for path, mutate in changes:
            with self.subTest(path=path):
                original = path.read_bytes()
                row = runtime.read_json(path)
                mutate(row)
                runtime.write_json(path, row)
                with self.assertRaisesRegex(ValueError, "identity changed"):
                    self.apply()
                path.write_bytes(original)
        write(Path(self.sample["assembly"]), ">synthetic\nCCCCCCCCCCCC\n")
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.apply()
        self.assertFalse(self.receipt.exists())

    def test_execution_executable_extras_and_thread_drift_refused(self):
        for field in ("execution", "executable", "extra_inputs"):
            with self.subTest(field=field):
                identity = copy.deepcopy(self.original)
                if field == "execution":
                    identity[field] = {"changed": True}
                elif field == "executable":
                    identity[field]["sha256"] = "0" * 64
                else:
                    identity[field][str(self.macsyfinder)] = "0" * 64
                attempt = self.saved(identity=identity)
                with self.assertRaisesRegex(ValueError, "identity changed"):
                    self.apply()
                shutil.rmtree(attempt.parent)
        self.saved()
        self.config["modules"][recovery.MODULE]["threads"] = 1
        runtime.write_json(self.config_path, self.config)
        with self.assertRaisesRegex(ValueError, "configured threads"):
            self.apply()

    def test_failed_commands_logs_wrong_failures_and_incomplete_payload_refused(self):
        attempt = self.saved()
        changes = [(attempt / "commands.json", [{**self.command, "returncode": 1}], "successful invocation"),
                   (attempt / "commands.json", [{**self.command, "argv": self.command["argv"] + ["extra"]}], "successful invocation"),
                   (attempt / "failure.json", {**runtime.read_json(attempt / "failure.json"), "error": "other"}, "legacy array-bounds")]
        for path, value, error in changes:
            with self.subTest(path=path, value=value):
                before = path.read_bytes()
                runtime.write_json(path, value)
                with self.assertRaisesRegex(ValueError, error):
                    self.apply()
                path.write_bytes(before)
        write(attempt / "commands.log", "Traceback (most recent call last):\n")
        with self.assertRaisesRegex(ValueError, "native error"):
            self.apply()
        write(attempt / "commands.log", "successful\n")
        (attempt / "data/native/rawFASTA/analyzedSequences/CCF000001.fna").unlink()
        with self.assertRaisesRegex(ValueError, "complete supplied assembly"):
            self.apply()
        self.assertFalse(self.receipt.exists())

    def test_interrupted_copy_is_retained_and_retry_uses_original(self):
        attempt = self.saved()
        before = self.snapshot(attempt)
        with patch.object(recovery, "_link_or_copy", side_effect=OSError("synthetic disk failure")):
            with self.assertRaisesRegex((OSError, shutil.Error), "synthetic disk failure"):
                self.apply()
        partials = list(self.store.glob("*/attempt-*/failure.json"))
        self.assertEqual(len(partials), 2)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(self.inspect()["attempt"], attempt)
        self.apply()
        self.assertTrue(all(path.exists() for path in partials))
        self.assertEqual(self.snapshot(attempt), before)

    def test_cache_survives_interruption_before_receipt_publication(self):
        self.saved(completed=True)
        old_receipt = self.receipt.read_bytes()
        with patch.object(result_store, "_publish_link", side_effect=OSError("publication interrupted")):
            with self.assertRaisesRegex(OSError, "publication interrupted"):
                self.apply(store=self.target)
        self.assertEqual(self.receipt.read_bytes(), old_receipt)
        self.assertEqual(self.inspect(store=self.target)["status"], "current_verified_cache")
        with patch.object(recovery.shutil, "copytree", side_effect=AssertionError("copy should be reused")):
            self.apply(store=self.target)
        result_store.verify_receipt(self.receipt, self.destination, self.current)

    def test_retry_after_alias_changed_before_legacy_receipt_replacement(self):
        self.saved(completed=True)
        original_write = runtime.write_json
        def interrupted(path, row):
            if Path(path) == self.receipt:
                raise OSError("receipt write interrupted")
            return original_write(path, row)
        with patch.object(runtime, "write_json", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "receipt write interrupted"):
                self.apply(store=self.target)
        self.assertTrue(self.destination.resolve().is_relative_to(self.target))
        with patch.object(recovery.shutil, "copytree", side_effect=AssertionError("copy should be reused")):
            self.apply(store=self.target)
        result_store.verify_receipt(self.receipt, self.destination, self.current)

    def test_cross_filesystem_fallback_and_active_workflow_guard(self):
        self.saved()
        with patch.object(recovery, "active_workflows", return_value=[{"pid": 123}]):
            with self.assertRaisesRegex(ValueError, "Stop the workflow"):
                self.apply()
        with patch.object(recovery.os, "link", side_effect=OSError(errno.EXDEV, "cross-device")):
            self.apply(store=self.target)
        result_store.verify_receipt(self.receipt, self.destination)

    def test_all_batch_preflight_precedes_publication_and_same_store_locked_once(self):
        self.saved()
        real_inspect = recovery.inspect_sample
        missing = self.root / "another-sample"
        def inspect(isolate, **kwargs):
            if Path(isolate) == missing:
                raise ValueError("second sample failed preflight")
            return real_inspect(isolate, **kwargs)
        with patch.object(recovery, "inspect_sample", side_effect=inspect), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "second sample failed"):
                recovery.apply_samples([self.isolate, missing])
        self.assertFalse(self.receipt.exists())
        with patch.object(result_store, "_lock", wraps=result_store._lock) as locked:
            self.apply()
        self.assertEqual(locked.call_count, 1)

    def test_explicit_wrong_sample_store_is_rejected(self):
        attempt = self.saved()
        other = self.root / "other-sample"
        runtime.write_json(other / "safety/metadata/sample.json", {**self.sample, "sample_id": "other"})
        runtime.write_json(other / "safety/metadata/configs/crisprcasfinder.json", self.config)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(ValueError, "identity changed"):
            recovery.apply_sample(other, store=self.store)
        self.assertTrue(attempt.exists())
        self.assertFalse(self.receipt.exists())

    def test_cli_ordered_overlays_allow_targeted_batch_selection(self):
        class Context:
            samples = {str(self.isolate): {}, str(self.root / "other"): {}}
            def module_enabled(self, isolate, module):
                return True
        with patch.object(recovery, "load_configuration", return_value=({"storage": {}}, [])) as load, \
                patch.object(recovery, "SafetyContext", return_value=Context()), \
                patch.object(recovery, "inspect_sample", return_value={"isolate": str(self.isolate), "status": "no_saved_attempt"}) as inspect, \
                contextlib.redirect_stdout(io.StringIO()):
            recovery.main(["--configfile", "first.yaml", "second.yaml", "--configfile", "third.yaml",
                           "--isolate", str(self.isolate), "--store", str(self.target)])
        self.assertEqual(load.call_args.args[1], ["first.yaml", "second.yaml", "third.yaml"])
        self.assertEqual(inspect.call_count, 1)
        self.assertEqual(inspect.call_args.args[0], str(self.isolate))
        self.assertEqual(inspect.call_args.kwargs["store"], str(self.target))

    def test_real_batch_configuration_reconstructs_frozen_execution_identity(self):
        proteins, gff = self.isolate / "proteins.faa", self.isolate / "annotation.gff"
        write(proteins, ">synthetic_1\nMMM\n")
        write(gff, "##gff-version 3\n")
        modules = {name: {"enabled": False} for name in ALL_MODULES}
        modules[recovery.MODULE] = {**self.config["modules"][recovery.MODULE], "enabled": True}
        first, second = self.root / "batch.yaml", self.root / "linux.yaml"
        first.write_text(yaml.safe_dump({"threads": 2, "deep": {"enabled": False},
            "isolates": [{**self.sample, "isolate_dir": str(self.isolate), "proteins": str(proteins), "gff": str(gff)}],
            "modules": modules, "resources": {"directory": str(self.root / "resources")}}))
        second.write_text(yaml.safe_dump({"storage": {"native_results_directory": str(self.root / "linux")}}))
        config, _ = recovery.load_configuration(ROOT, [first, second])
        context = recovery.SafetyContext(config, ROOT)
        resource = Path(context.resource_inputs(recovery.MODULE)[0])
        artifact = self.root / "synthetic-resource"
        write(artifact, "synthetic resource\n")
        runtime.write_json(resource, {"schema": resources.SCHEMA, "resource": recovery.MODULE,
            "status": "completed", "artifacts": {str(artifact): {"bytes": artifact.stat().st_size,
                "sha256": result_store.sha256(artifact)}},
            "result": {"module_updates": {recovery.MODULE: {"database": str(self.root / "native-source")}}}})
        environment = Path(context.module_environment_receipts(recovery.MODULE)[0])
        runtime.write_json(environment, runtime.read_json(self.root / "environment.json"))
        self.sample = context.samples[str(self.isolate)]
        self.config = setup.resolved_config(context.module_document(str(self.isolate), recovery.MODULE),
            context.resource_inputs(recovery.MODULE), context.module_environment_receipts(recovery.MODULE))
        runtime.write_json(self.base / "metadata/sample.json", self.sample)
        runtime.write_json(self.config_path, self.config)
        self.current = result_store.execution_identity(recovery.MODULE, self.sample, self.config,
            sources=recovery.sources(), extra_inputs=[sys.executable, self.macsyfinder], executable=sys.executable)
        self.original = copy.deepcopy(self.current)
        self.original["sources"].update({str(ROOT / path): value for path, value in recovery.LEGACY_HASHES.items()})
        self.saved()
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            recovery.main(["--configfile", str(first), str(second), "--apply"])
        row = result_store.verify_receipt(self.receipt, self.destination, self.current)
        self.assertTrue(Path(row["payload_root"]).is_relative_to(
            recovery.native_result_store(ROOT, config, self.isolate, recovery.MODULE)))


if __name__ == "__main__":
    unittest.main()
