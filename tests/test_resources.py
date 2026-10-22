"""Resource publication and deploy-time failure contracts, without downloads."""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from safety.resources import SCHEMA, external_inputs, preparation_spec, prepare, sha256, source_inputs, verify_receipt


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _provider(spec, data, runner):
        database = data / "vfdb_setA.dmnd"
        database.write_text("resource bytes")
        metadata = data / "metadata.json"
        metadata.write_text(json.dumps({"database": str(database)}))
        return {"module_updates": {"vfdb": {"database": str(database), "metadata": str(metadata)}}}

    def test_resource_failure_does_not_publish_success(self):
        def fail(spec, data, runner):
            (data / "partial.txt").write_text("partial download")
            raise RuntimeError("interrupted")
        with patch("safety.resources._provider", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                prepare("vfdb", {}, self.root, self.root / "job.log", runner=object())
        self.assertFalse((self.root / "vfdb/provenance.json").exists())
        failures = list((self.root / "snapshots/vfdb").glob("*/attempt-*/failure.json"))
        self.assertEqual(len(failures), 1)
        failure = json.loads(failures[0].read_text())
        self.assertEqual(failure["status"], "failed")
        self.assertEqual((Path(failure["payload_preserved"]) / "partial.txt").read_text(), "partial download")
        self.assertFalse(list((self.root / "snapshots").rglob("completed.json")))

    def test_completed_resource_inventory_detects_changed_payload(self):
        def provider(spec, data, runner):
            database = data / "database.txt"
            database.write_text("resource bytes")
            return {"module_updates": {"vfdb": {"database": str(database)}}}
        with patch("safety.resources._provider", side_effect=provider):
            result = prepare("vfdb", {}, self.root, self.root / "job.log", runner=object())
        receipt = self.root / "vfdb/provenance.json"
        self.assertEqual(verify_receipt(receipt)["status"], "completed")
        self.assertEqual(result["module_updates"], result["result"]["module_updates"])
        Path(result["module_updates"]["vfdb"]["database"]).write_text("changed! bytes")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            verify_receipt(receipt)

    def test_verified_snapshot_survives_workflow_receipt_deletion_and_reuses_exact_record(self):
        with patch("safety.resources._provider", side_effect=self._provider) as provider:
            first = prepare("vfdb", {}, self.root, self.root / "job.log", runner=object())
            receipt = self.root / "vfdb/provenance.json"
            original = receipt.read_bytes()
            receipt.unlink()  # Snakemake can remove a scheduled output before rerunning.
            second = prepare("vfdb", {"threads": 8, "module_config": {"min_identity": 90}},
                             self.root, self.root / "job.log", threads=8, runner=object())
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(original, receipt.read_bytes())
        self.assertTrue((self.root / "vfdb/data").is_symlink())
        self.assertEqual((self.root / "vfdb/data").resolve(), Path(second["payload_root"]))
        self.assertTrue(Path(second["payload_root"]).is_dir())
        metadata = Path(second["module_updates"]["vfdb"]["metadata"])
        self.assertEqual(json.loads(metadata.read_text())["database"],
                         second["module_updates"]["vfdb"]["database"])

    def test_failed_changed_request_preserves_last_completed_snapshot_and_receipt(self):
        with patch("safety.resources._provider", side_effect=self._provider):
            previous = prepare("vfdb", {}, self.root, self.root / "job.log", runner=object())
        receipt = self.root / "vfdb/provenance.json"
        before = receipt.read_bytes()
        def fail(spec, data, runner):
            (data / "partial").write_text("retained")
            raise RuntimeError("failed new version")
        with patch("safety.resources._provider", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "failed new version"):
                prepare("vfdb", {"options": {"expected_version": "new"}},
                        self.root, self.root / "job.log", runner=object())
        self.assertEqual(before, receipt.read_bytes())
        self.assertEqual(previous, verify_receipt(receipt))
        self.assertEqual((self.root / "vfdb/data").resolve(), Path(previous["payload_root"]))

    def test_dag_time_module_database_path_resolves_to_verified_snapshot(self):
        from safety.context import SafetyContext
        repository = Path(__file__).resolve().parents[1]
        context = SafetyContext({"isolates": [], "resources": {"directory": str(self.root)}}, repository)
        path = Path(context.module_config("unused", "vfdb")["database"])
        self.assertEqual(path, self.root / "vfdb/data/vfdb_setA.dmnd")
        with patch("safety.resources._provider", side_effect=self._provider):
            receipt = prepare("vfdb", {}, self.root, self.root / "job.log", runner=object())
        self.assertEqual(path.read_text(), "resource bytes")
        self.assertEqual(path.resolve(), Path(receipt["module_updates"]["vfdb"]["database"]))

    def test_retry_uses_new_attempt_and_keeps_failed_attempt(self):
        def fail(spec, data, runner):
            (data / "partial").write_text("retained")
            raise KeyboardInterrupt()
        with patch("safety.resources._provider", side_effect=fail):
            with self.assertRaises(KeyboardInterrupt):
                prepare("vfdb", {}, self.root, self.root / "job.log", runner=object())
        partial = next((self.root / "snapshots").rglob("partial"))
        with patch("safety.resources._provider", side_effect=self._provider):
            ready = prepare("vfdb", {}, self.root, self.root / "job.log", runner=object())
        self.assertEqual(partial.read_text(), "retained")
        self.assertNotEqual(partial.parent, Path(ready["payload_root"]))

    def test_corrupt_cached_payload_never_silently_reuses_or_redownloads(self):
        with patch("safety.resources._provider", side_effect=self._provider):
            receipt = prepare("vfdb", {}, self.root, self.root / "job.log", runner=object())
        payload = Path(receipt["module_updates"]["vfdb"]["database"])
        payload.write_text("changed! bytes")
        with patch("safety.resources._provider", side_effect=AssertionError("redownload attempted")):
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                prepare("vfdb", {}, self.root, self.root / "job.log", runner=object())
        self.assertEqual(payload.read_text(), "changed! bytes")

    def test_added_payload_file_invalidates_snapshot(self):
        with patch("safety.resources._provider", side_effect=self._provider):
            receipt = prepare("vfdb", {}, self.root, self.root / "job.log", runner=object())
        (Path(receipt["payload_root"]) / "unexpected.txt").write_text("extra")
        with self.assertRaisesRegex(ValueError, "changed inventory"):
            verify_receipt(self.root / "vfdb/provenance.json")

    def test_retargeted_internal_symlink_invalidates_snapshot_even_when_bytes_match(self):
        def provider(spec, data, runner):
            for name in ("a", "b"):
                (data / name).write_text("same bytes")
            (data / "alias").symlink_to("a")
            return {"module_updates": {"vfdb": {"database": str(data)}}}
        with patch("safety.resources._provider", side_effect=provider):
            receipt = prepare("vfdb", {}, self.root, self.root / "job.log", runner=object())
        alias = Path(receipt["payload_root"]) / "alias"
        alias.unlink()
        alias.symlink_to("b")
        with self.assertRaisesRegex(ValueError, "symlink targets"):
            verify_receipt(self.root / "vfdb/provenance.json")

    def test_environment_timestamp_does_not_redownload_but_package_change_does(self):
        environment = self.root / "environment.json"
        record = {"schema": "basis-snake-environment-v1", "status": "completed",
                  "prefix": "/example/environment", "packages": [{"name": "tool", "version": "1"}],
                  "created_at": "earlier"}
        environment.write_text(json.dumps(record))
        with patch("safety.resources._provider", side_effect=self._provider) as provider:
            first = prepare("vfdb", {}, self.root, self.root / "job.log",
                            environment_receipt=environment, runner=object())
            record["created_at"] = "later"
            environment.write_text(json.dumps(record))
            unchanged = prepare("vfdb", {}, self.root, self.root / "job.log",
                                environment_receipt=environment, runner=object())
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(first, unchanged)
            record["packages"][0]["version"] = "2"
            environment.write_text(json.dumps(record))
            changed = prepare("vfdb", {}, self.root, self.root / "job.log",
                              environment_receipt=environment, runner=object())
        self.assertEqual(provider.call_count, 2)
        self.assertNotEqual(first["cache_key"], changed["cache_key"])
        self.assertTrue(Path(first["payload_root"]).is_dir())

    def test_explicit_source_content_change_invalidates_preparation_identity(self):
        source = self.root / "input.txt"
        source.write_text("source A")
        spec = {"options": {"reference_sources": [{"path": str(source)}]}}
        with patch("safety.resources._provider", side_effect=self._provider) as provider:
            first = prepare("vfdb", spec, self.root / "resources", self.root / "job.log", runner=object())
            source.write_text("source B")
            second = prepare("vfdb", spec, self.root / "resources", self.root / "job.log", runner=object())
        self.assertEqual(provider.call_count, 2)
        self.assertNotEqual(first["cache_key"], second["cache_key"])

    def test_dependency_source_declaration_change_invalidates_without_content_change(self):
        source = self.root / "existing.txt"
        source.write_text("fixed source bytes")
        resources = self.root / "resources"
        spec = {"mode": "external", "result": {
            "module_updates": {"vfdb": {"database": str(source)}},
            "provenance": {"reference_sources": [{"path": str(source), "review_status": "first"}]}}}
        dependency = resources / "vfdb/provenance.json"
        prepare("vfdb", spec, resources, self.root / "job.log")
        with patch("safety.resources._provider", side_effect=self._provider) as provider:
            first = prepare("deep_catalogue", {}, resources, self.root / "job.log",
                            dependency_receipts=[dependency], runner=object())
            # The same evidence bytes with an updated source declaration are new
            # preparation inputs for a provider that consumes that declaration.
            spec["result"]["provenance"]["reference_sources"][0]["review_status"] = "second"
            prepare("vfdb", spec, resources, self.root / "job.log")
            second = prepare("deep_catalogue", {}, resources, self.root / "job.log",
                             dependency_receipts=[dependency], runner=object())
        self.assertEqual(provider.call_count, 2)
        self.assertNotEqual(first["cache_key"], second["cache_key"])

    def test_legacy_receipt_and_payload_are_preserved_without_guessing_compatibility(self):
        data = self.root / "vfdb/data"
        data.mkdir(parents=True)
        payload = data / "database.txt"
        payload.write_text("existing resource")
        previous = {"schema": SCHEMA, "status": "completed", "provider": "vfdb",
                    "artifacts": {str(payload): {"bytes": payload.stat().st_size, "sha256": sha256(payload)}},
                    "result": {"module_updates": {"vfdb": {"database": str(payload)}}}}
        receipt = self.root / "vfdb/provenance.json"
        receipt.write_text(json.dumps(previous))
        before = receipt.read_bytes()
        with patch("safety.resources._provider", side_effect=AssertionError("download attempted")):
            with self.assertRaisesRegex(ValueError, "Legacy resource receipt and data were preserved"):
                prepare("vfdb", {}, self.root, self.root / "job.log", runner=object())
        self.assertEqual(before, receipt.read_bytes())
        self.assertEqual(payload.read_text(), "existing resource")
        archived = list((self.root / "vfdb/legacy-receipts").glob("*.json"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(json.loads(archived[0].read_text()), previous)

    def test_legacy_data_without_receipt_is_not_deleted_or_overwritten(self):
        data = self.root / "vfdb/data"
        data.mkdir(parents=True)
        payload = data / "keep"
        payload.write_text("untracked prior data")
        with patch("safety.resources._provider", side_effect=AssertionError("download attempted")):
            with self.assertRaisesRegex(ValueError, "Existing legacy resource data were preserved"):
                prepare("vfdb", {}, self.root, self.root / "job.log", runner=object())
        self.assertEqual(payload.read_text(), "untracked prior data")

    def test_external_resource_verified_without_provider_or_modification(self):
        database = self.root / "external/database.txt"
        database.parent.mkdir()
        database.write_text("existing verified resource")
        before = sha256(database)
        spec = {"mode": "external", "module_updates": {"vfdb": {
            "database": str(database), "database_version": "recorded-snapshot"}}}
        self.assertEqual(external_inputs(spec), [str(database)])
        with patch("safety.resources._provider", side_effect=AssertionError("download attempted")):
            result = prepare("vfdb", spec, self.root / "resources", self.root / "job.log")
        self.assertEqual(before, sha256(database))
        self.assertEqual(result["mode"], "external")
        self.assertIn(str(database), result["artifacts"])
        receipt = self.root / "resources/vfdb/provenance.json"
        before_receipt = receipt.read_bytes()
        receipt.unlink()
        with patch("safety.resources._provider", side_effect=AssertionError("download attempted")):
            repeated = prepare("vfdb", spec, self.root / "resources", self.root / "job.log")
        self.assertEqual(result, repeated)
        self.assertEqual(before_receipt, receipt.read_bytes())

    def test_external_directory_cannot_enclose_generated_receipts_or_cache(self):
        resources = self.root / "resources"
        resources.mkdir()
        (resources / "payload").write_text("input")
        spec = {"mode": "external", "module_updates": {"vfdb": {"database": str(resources)}}}
        with patch("safety.resources._provider", side_effect=AssertionError("download attempted")):
            with self.assertRaisesRegex(ValueError, "encloses its generated cache"):
                prepare("vfdb", spec, resources, self.root / "job.log")

    def test_source_inputs_cover_provider_code_and_packaged_catalogue(self):
        root = Path(__file__).resolve().parents[1]
        files = source_inputs({"provider": "deep_catalogue"}, root)
        self.assertIn(str(root / "workflow/vendor/basis/bootstrap_deep.py"), files)
        self.assertIn(str(root / "workflow/vendor/basis/resources/comparators/reviewed-v1/checksums.json"), files)
        self.assertIn(str(root / "workflow/vendor/basis/resources/motifs/rules-v1.json"), files)
        self.assertTrue(all(Path(path).is_file() for path in files))

    def test_analysis_changes_do_not_change_resource_preparation_parameters(self):
        original = {"id": "vfdb", "provider": "vfdb", "recipe": "diamond",
                    "targets": ["vfdb"], "threads": 4,
                    "module_config": {"min_identity": 80, "threads": 4}}
        changed = {**original, "targets": ["vfdb", "deepsafe"], "threads": 8,
                   "module_config": {"min_identity": 90, "threads": 8}}
        self.assertEqual(preparation_spec(original), preparation_spec(changed))

    def test_failed_dependency_never_runs_dependent_provider(self):
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"status": "failed"}))
        with patch("safety.resources._provider", side_effect=AssertionError("provider ran")):
            with self.assertRaisesRegex(ValueError, "completed receipt"):
                prepare("deep_catalogue", {}, self.root / "resources", self.root / "job.log",
                        dependency_receipts=[bad], runner=object())

    def test_output_symlink_does_not_delete_external_directory(self):
        external = self.root / "external"
        external.mkdir()
        marker = external / "keep.txt"
        marker.write_text("keep")
        resource = self.root / "resources/vfdb"
        resource.mkdir(parents=True)
        (resource / "data").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            prepare("vfdb", {}, self.root / "resources", self.root / "job.log", runner=object())
        self.assertEqual(marker.read_text(), "keep")

    def _postdeploy(self, body):
        prefix = self.root / "environment"
        binary = prefix / "bin/diamond"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/usr/bin/env bash\n" + body + "\n")
        binary.chmod(0o755)
        script = Path(__file__).resolve().parents[1] / "envs/diamond.post-deploy.sh"
        environment = {**os.environ, "CONDA_PREFIX": str(prefix),
                       "PATH": str(binary.parent) + os.pathsep + os.environ["PATH"]}
        return subprocess.run(["bash", str(script)], env=environment, text=True, capture_output=True)

    def test_post_deploy_fails_on_crashed_executable_even_with_version_text(self):
        result = self._postdeploy("echo 'diamond version 2.1.10'; exit 139")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Deployment probe failed", result.stderr)

    def test_post_deploy_accepts_valid_executable(self):
        result = self._postdeploy("echo 'diamond version 2.1.10'; exit 0")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
