"""Resource publication and deploy-time failure contracts, without downloads."""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from safety.resources import external_inputs, preparation_spec, prepare, sha256, source_inputs, verify_receipt


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_resource_failure_does_not_publish_success(self):
        def fail(spec, data, runner):
            (data / "partial.txt").write_text("partial download")
            raise RuntimeError("interrupted")
        with patch("safety.resources._provider", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                prepare("vfdb", {}, self.root, self.root / "job.log", runner=object())
        self.assertFalse((self.root / "vfdb/provenance.json").exists())

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
        (self.root / "vfdb/data/database.txt").write_text("changed! bytes")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            verify_receipt(receipt)

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
