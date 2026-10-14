"""Immutable resource administration: no live network or biological analyses."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import yaml

from basis import resource_setup as resources
from basis.util import atomic_json


class ResourceSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.json"
        self.source.write_text('{"version": "1.2"}\n')
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.config = self.root / "resources.yaml"
        self.row = {"backend": "pinned_files", "destination": "snapshots/test-v1", "version": "1.2",
                    "files": [{"source": "source.json", "path": "data/source.json", "sha256": self.digest}]}
        self.write_config()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self, row=None, name="source"):
        self.config.write_text(yaml.safe_dump({"schema_version": 1, "resources": {name: row or self.row}}))

    def plan(self):
        plan = resources.build_resource_plan(self.config, ["source"])
        folder = self.root / "attempt"
        folder.mkdir(exist_ok=True)
        plan.update(directory=str(folder), summary_path=str(folder / "summary.json"))
        path = folder / "plan.json"
        atomic_json(path, plan)
        return path

    def test_plan_is_read_only_and_requires_selection(self):
        before = sorted(p.relative_to(self.root) for p in self.root.rglob("*"))
        with contextlib.redirect_stdout(io.StringIO()), patch("basis.environment_workflow._command") as native:
            code = resources.main(["-c", str(self.config), "--resource", "source"])
        self.assertEqual(code, 0)
        self.assertEqual(before, sorted(p.relative_to(self.root) for p in self.root.rglob("*")))
        native.assert_not_called()
        with self.assertRaisesRegex(ValueError, "explicitly"):
            resources.build_resource_plan(self.config, [])

    def test_exact_pinned_copy_reuses_and_detects_changed_bytes(self):
        plan = self.plan()
        first = resources.prepare_resource(plan, "source")
        self.assertEqual(first["status"], "ready")
        self.assertEqual(first["scope"], "source_files_only")
        self.assertEqual(first["analysis_readiness"], "not_assessed")
        self.assertFalse(first["reused"])
        snapshot = self.root / "snapshots/test-v1/data/source.json"
        self.assertEqual(snapshot.read_bytes(), self.source.read_bytes())
        self.assertTrue(resources.prepare_resource(plan, "source")["reused"])
        self.assertEqual(resources.summarize_resources(plan)["status"], "completed")
        snapshot.write_text("tampered")
        self.assertEqual(resources.summarize_resources(plan)["status"], "incomplete")
        failed = resources.prepare_resource(plan, "source")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(snapshot.read_text(), "tampered")

    def test_wrong_pin_preserves_partial_but_never_marks_ready(self):
        self.row["files"][0]["sha256"] = "0" * 64
        self.write_config()
        plan = self.plan()
        result = resources.prepare_resource(plan, "source")
        self.assertEqual(result["status"], "failed")
        self.assertIn("checksum differs", result["error"])
        self.assertFalse((self.root / "snapshots/test-v1/provenance.json").exists())
        self.assertTrue((self.root / "snapshots/test-v1/data/source.json").exists())
        self.assertEqual(resources.summarize_resources(plan)["status"], "incomplete")

    def test_existing_unowned_destination_is_never_overwritten(self):
        root = self.root / "snapshots/test-v1"
        root.mkdir(parents=True)
        (root / "mine").write_text("preserved")
        result = resources.prepare_resource(self.plan(), "source")
        self.assertEqual(result["status"], "failed")
        self.assertEqual((root / "mine").read_text(), "preserved")

    def test_invalid_resource_recipes_fail_before_any_work(self):
        cases = [dict(self.row, backend="arbitrary_shell"), dict(self.row, version="latest"),
                 dict(self.row, command="echo hello"), dict(self.row, required_paths=["../outside"]),
                 dict(self.row, files=[{"path": "data", "url": "http://example.test/data", "sha256": self.digest}]),
                 dict(self.row, files=[{"path": "data", "url": "https://example.test/data", "sha256": "bad"}]),
                 dict(self.row, files=[{"path": "provenance.json", "source": "source.json", "sha256": self.digest}])]
        for row in cases:
            with self.subTest(row=row):
                self.write_config(row)
                with self.assertRaises(ValueError):
                    resources.build_resource_plan(self.config, ["source"])
        self.assertFalse((self.root / "snapshots").exists())

    def test_native_requires_explicit_upstream_acknowledgement_and_release_check(self):
        native = {"backend": "genomad", "destination": "snapshots/genomad-v1", "version": "1.9",
                  "required_paths": ["genomad_db/database"]}
        self.write_config(native)
        with self.assertRaisesRegex(ValueError, "allow_upstream_current"):
            resources.load_resource_config(self.config)
        native["allow_upstream_current"] = True
        self.write_config(native)
        with self.assertRaisesRegex(ValueError, "version_file or expected_files"):
            resources.load_resource_config(self.config)
        native["version_file"] = "genomad_db/version.txt"
        self.write_config(native)
        self.assertEqual(resources.load_resource_config(self.config)["resources"]["source"]["version"], "1.9")

    def test_native_worker_verifies_version_and_captures_all_files(self):
        native = {"backend": "amrfinder", "destination": "snapshots/amr", "version": "2026-08-07.1",
                  "required_paths": ["latest/AMRProt.fa"], "allow_upstream_current": True,
                  "version_file": "latest/version.txt"}
        self.write_config(native)
        plan = self.plan()

        def fake_prepare(row, root, log, threads=None):
            release = root / "2026-08-07.1"
            release.mkdir()
            (release / "version.txt").write_text(row["version"] + "\n")
            (release / "AMRProt.fa").write_text(">ref\nACDE\n")
            (root / "latest").symlink_to(release.name, target_is_directory=True)
            return {"commands": [{"argv": ["amrfinder_update", "--database", str(root)]}]}

        with patch.object(resources, "_prepare_native", side_effect=fake_prepare):
            result = resources.prepare_resource(plan, "source")
        self.assertEqual(result["status"], "ready")
        manifest = json.loads(Path(result["manifest"]).read_text())
        self.assertEqual(manifest["symlinks"], {"latest": "2026-08-07.1"})
        self.assertIn("2026-08-07.1/AMRProt.fa", manifest["sha256"])

    def test_native_wrong_version_and_external_symlink_are_rejected(self):
        row = resources.load_resource_config(self.config)["resources"]["source"]
        row["files"] = []
        root = self.root / "test"
        root.mkdir()
        (root / "version.txt").write_text("2.0")
        with self.assertRaisesRegex(ValueError, "version differs"):
            resources._verify_expected(dict(row, version_file="version.txt"), root)
        (root / "escape").symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "external symlink"):
            resources._inventory(root)

    def test_resource_plan_and_receipt_integrity(self):
        plan = self.plan()
        resources.prepare_resource(plan, "source")
        receipt = plan.parent / "resources/source.json"
        data = json.loads(receipt.read_text())
        data["plan_hash"] = "tampered"
        atomic_json(receipt, data)
        self.assertEqual(resources.summarize_resources(plan)["status"], "incomplete")
        data = json.loads(plan.read_text())
        data["resources"][0]["destination"] = str(self.root / "injected")
        atomic_json(plan, data)
        with self.assertRaisesRegex(ValueError, "integrity"):
            resources.prepare_resource(plan, "source")

    def test_overlapping_resource_destinations_are_rejected(self):
        other = dict(self.row, destination="snapshots/test-v1/nested")
        self.config.write_text(yaml.safe_dump({"schema_version": 1, "resources": {"a": self.row, "b": other}}))
        with self.assertRaisesRegex(ValueError, "overlap"):
            resources.load_resource_config(self.config)

    def test_scheduler_failure_does_not_claim_ready(self):
        with patch("basis.environment_workflow._command", return_value=SimpleNamespace(returncode=1)), contextlib.redirect_stdout(io.StringIO()):
            status = resources.main(["-c", str(self.config), "--resource", "source", "--execute",
                                     "--report-dir", str(self.root / "failed-attempt")])
        self.assertEqual(status, 2)
        summary = json.loads((self.root / "failed-attempt/summary.json").read_text())
        self.assertEqual(summary["status"], "incomplete")
        self.assertEqual(summary["scheduler_error"], "Snakemake exited with code 1")

    def test_native_backends_use_fixed_commands_and_allocated_threads(self):
        expected = {
            "amrfinder": ["amrfinder_update", "--database"],
            "genomad": ["genomad", "download-database"],
            "antismash": ["download-antismash-databases", "--database-dir"],
            "mobsuite": ["mob_init", "--database_directory"],
        }
        for backend, argv in expected.items():
            with self.subTest(backend=backend):
                row = {"backend": backend, "threads": 8, "timeout_seconds": 600}
                with patch.object(resources, "_command_prefix", return_value=(["conda", "run", "--prefix", "/env"], Path("/env"))), \
                     patch("basis.environment_workflow.verify", return_value={"probes": []}), \
                     patch.object(resources, "CommandRunner") as runner:
                    output = resources._prepare_native(row, self.root / "db", self.root / "setup.log", threads=2)
                self.assertEqual(runner.call_args.args[0]["threads"], 2)
                self.assertEqual(output["allocated_threads"], 2)
                runner.return_value.run.assert_called_once_with(argv + [str(self.root / "db")])

    def test_changed_manifest_cannot_override_a_pinned_source(self):
        plan = self.plan()
        record = resources.prepare_resource(plan, "source")
        root = Path(record["destination"])
        path = root / "data/source.json"
        path.write_text("changed")
        manifest = json.loads((root / "provenance.json").read_text())
        manifest["sha256"]["data/source.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
        atomic_json(root / "provenance.json", manifest)
        failed = resources.prepare_resource(plan, "source")
        self.assertEqual(failed["status"], "failed")
        self.assertIn("Pinned source checksum differs", failed["error"])

    def test_malformed_yaml_has_clean_cli_error(self):
        self.config.write_text("resources: [\n")
        with contextlib.redirect_stderr(io.StringIO()) as error:
            status = resources.main(["-c", str(self.config), "--resource", "source"])
        self.assertEqual(status, 2)
        self.assertIn("Resource preparation stopped:", error.getvalue())

    @unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake is not installed")
    def test_real_snakemake_pinned_resource_graph(self):
        # Runs only local byte-copy fixtures. No Conda, network or biology.
        with contextlib.redirect_stdout(io.StringIO()):
            status = resources.main(["-c", str(self.config), "--resource", "source", "--execute",
                                     "--report-dir", str(self.root / "real-attempt")])
        self.assertEqual(status, 0)
        summary = json.loads((self.root / "real-attempt/summary.json").read_text())
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["resources"][0]["scope"], "source_files_only")


if __name__ == "__main__":
    unittest.main()
