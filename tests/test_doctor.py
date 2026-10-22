"""Read-only preflight and honest verification claims on synthetic fixtures."""
from __future__ import annotations

import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from basis.config import ALL_MODULES
from safety.context import SafetyContext
from safety.doctor import inspect_configuration, load_configuration, main, render_text, write_report
from safety.resources import prepare


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="basis-doctor-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.isolate = self.root / "sample"
        annotation = self.isolate / "annotation"
        annotation.mkdir(parents=True)
        (annotation / "sample.fna").write_text(">contig1\nATGAAATTTTAA\n")
        (annotation / "sample.faa").write_text(">protein1\nMKF\n")
        (annotation / "sample.gff").write_text("##gff-version 3\ncontig1\tfixture\tCDS\t1\t12\t.\t+\t0\tID=protein1;locus_tag=protein1;transl_table=11\n")
        self.config = {"isolates": [str(self.isolate)], "extend": False,
            "deep": {"enabled": False}, "threads": 7,
            "modules": {name: {"enabled": False} for name in ALL_MODULES},
            "resources": {"directory": str(self.root / "resources"), "policy": "auto"}}
        (self.root / "config.yaml").write_text(yaml.safe_dump(self.config))

    def snapshot(self):
        return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.root.rglob("*") if path.is_file()}

    def test_audit_is_read_only_and_does_not_run_tools_or_network(self):
        before = self.snapshot()
        with patch("subprocess.Popen", side_effect=AssertionError("process launch")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("network")), \
             patch("socket.socket", side_effect=AssertionError("network")):
            report = inspect_configuration(self.config, self.root, validate_inputs=True,
                                           verify_resources=True)
        self.assertEqual(report["status"], "checked")
        self.assertFalse(report["downloads_performed"])
        self.assertEqual(report["samples"][0]["input_validation"]["contigs"], 1)
        self.assertEqual(report["samples"][0]["input_validation"]["assembly_bp"], 12)
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.root / "resources").exists())
        self.assertFalse((self.isolate / "safety").exists())

    def test_config_overlays_merge_recursively_in_supplied_order(self):
        first, second = self.root / "first.yaml", self.root / "second.yaml"
        first.write_text("threads: 2\nmodules:\n  vfdb:\n    enabled: true\n    memory_mb: 1234\n")
        second.write_text("threads: 3\nmodules:\n  vfdb:\n    memory_mb: 2345\n")
        cfg, paths = load_configuration(self.root, [first, second])
        self.assertEqual(cfg["threads"], 3)
        self.assertTrue(cfg["modules"]["vfdb"]["enabled"])
        self.assertEqual(cfg["modules"]["vfdb"]["memory_mb"], 2345)
        self.assertEqual(paths, list(map(str, [self.root / "config.yaml", first, second])))
        self.assertEqual(self.config["threads"], 7)

    def test_missing_input_blocks_report_without_creating_results(self):
        (self.isolate / "annotation/sample.fna").unlink()
        report = inspect_configuration(self.config, self.root)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("missing assembly", report["errors"][0])
        self.assertFalse((self.isolate / "safety").exists())

    def test_manual_policy_does_not_silently_plan_automatic_resources(self):
        cfg = copy.deepcopy(self.config)
        cfg["resources"]["policy"] = "manual"
        cfg["modules"]["vfdb"]["enabled"] = True
        report = inspect_configuration(cfg, self.root)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("Manual resources require", report["errors"][0])
        self.assertFalse((self.root / "resources").exists())

    def test_unprepared_auto_resource_reports_download_risk(self):
        cfg = copy.deepcopy(self.config)
        cfg["modules"]["vfdb"]["enabled"] = True
        report = inspect_configuration(cfg, self.root)
        self.assertEqual(report["status"], "needs_setup")
        resource = report["resources"][0]
        self.assertTrue(resource["preparation_required"])
        self.assertTrue(resource["download_possible_on_preparation"])
        self.assertFalse(resource["inventory_verified"])

    def test_external_presence_is_not_reported_as_verification(self):
        path = self.root / "database.dmnd"
        path.write_text("synthetic database")
        cfg = copy.deepcopy(self.config)
        cfg["resources"]["policy"] = "manual"
        cfg["modules"]["vfdb"].update(enabled=True, database=str(path))
        report = inspect_configuration(cfg, self.root, verify_resources=True)
        resource = report["resources"][0]
        self.assertEqual(resource["status"], "external_present_unverified")
        self.assertFalse(resource["inventory_verified"])
        self.assertFalse(resource["download_possible_on_preparation"])
        path.unlink()
        missing = inspect_configuration(cfg, self.root)
        self.assertEqual(missing["status"], "blocked")
        self.assertEqual(missing["resources"][0]["status"], "missing_external")

    def test_resource_inventory_hashing_is_explicit_and_detects_tampering(self):
        cfg = copy.deepcopy(self.config)
        cfg["modules"]["vfdb"]["enabled"] = True
        context = SafetyContext(cfg, self.root)
        def provider(spec, data, runner):
            path = data / "vfdb_setA.dmnd"
            path.write_text("synthetic database bytes")
            return {"module_updates": {"vfdb": {"database": str(path)}}}
        with patch("safety.resources._provider", side_effect=provider):
            receipt = prepare("vfdb", context.resource_specs()["vfdb"], context.resource_root,
                              self.root / "prepare.log", runner=object())
        before = self.snapshot()
        with patch("safety.doctor.verify_receipt", side_effect=AssertionError("implicit checksum scan")):
            quick = inspect_configuration(cfg, self.root)
        self.assertEqual(quick["resources"][0]["status"], "receipt_recorded_unverified")
        checked = inspect_configuration(cfg, self.root, verify_resources=True)
        self.assertTrue(checked["resources"][0]["inventory_verified"])
        self.assertEqual(checked["resources"][0]["request_match"], "spec_matches")
        self.assertEqual(before, self.snapshot())
        Path(receipt["result"]["module_updates"]["vfdb"]["database"]).write_text("changed database bytes")
        invalid = inspect_configuration(cfg, self.root, verify_resources=True)
        self.assertEqual(invalid["status"], "blocked")
        self.assertIn("checksum mismatch", invalid["resources"][0]["errors"][0])

    def test_json_cli_and_explicit_report_output(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--workflow-root", str(self.root), "--validate-inputs", "--json"])
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["checks"]["input_consistency"], True)
        directory = self.root / "doctor-report"
        write_report(report, directory)
        self.assertEqual({path.name for path in directory.iterdir()}, {"doctor.json", "doctor.tsv", "doctor.html"})
        with self.assertRaises(FileExistsError):
            write_report(report, directory)
        with self.assertRaisesRegex(ValueError, "outside"):
            write_report(report, self.isolate / "audit")
        self.assertIn("Input consistency: passed", render_text(report))

    def test_invalid_environment_receipt_and_missing_prefix_are_reported(self):
        cfg = copy.deepcopy(self.config)
        cfg["modules"]["minced"]["enabled"] = True
        folder = self.root / "resources/environments"
        folder.mkdir(parents=True)
        receipt = folder / "minced.json"
        receipt.write_text("[]")
        invalid = inspect_configuration(cfg, self.root)
        self.assertEqual(invalid["status"], "blocked")
        self.assertEqual(invalid["environments"][0]["status"], "invalid_receipt")
        receipt.write_text(json.dumps({"schema": "basis-snake-environment-v1",
            "status": "completed", "prefix": str(self.root / "absent-prefix")}))
        missing = inspect_configuration(cfg, self.root)
        self.assertEqual(missing["status"], "needs_setup")
        self.assertEqual(missing["environments"][0]["status"], "missing_prefix")


if __name__ == "__main__":
    unittest.main()
