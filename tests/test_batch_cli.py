"""Installed-facing batch contracts with synthetic imports; no native tools."""
import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from basis.cli import check, main
from basis.config import load_config
from basis.demo import create_demo
from basis.inputs import load_samples


class BatchCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.original = self.root / "original"
        self.base = create_demo(self.original)
        self.sheet = self.root / "batch.csv"
        self.study = self.root / "new study"
        fields = ["sample_id", "assembly", "proteins", "gff", "input_mode"]
        with self.sheet.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for sample in load_samples(self.original / "samples.tsv"):
                writer.writerow({**{k: sample[k] for k in fields[:-1]}, "input_mode": "external"})

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_import_run_and_report_preserve_source_and_honor_sample_exclusion(self):
        cfg = yaml.safe_load(self.base.read_text())
        cfg["sample_module_overrides"] = {"DEMO_B": {
            "pathogenfinder2": {"enabled": False, "reason": "Synthetic scope exclusion"}}}
        self.base.write_text(yaml.safe_dump(cfg))
        before = {p: p.read_bytes() for p in self.original.rglob("*") if p.is_file()}
        code, output, error = self.call("import-samples", "--file", str(self.sheet),
                                        "--directory", str(self.study), "--base-config", str(self.base))
        self.assertEqual(code, 0, error)
        summary = json.loads(output)
        self.assertEqual(summary["sample_count"], 2)
        imported = load_config(self.study / "config.yaml")
        self.assertEqual(imported["outdir"], str(self.study / "results"))
        self.assertEqual(imported["deep"]["placement_cache_dir"], str(self.study / "placement-cache"))
        self.assertEqual(imported["deep"]["reference_fasta"], str(self.original / "inputs/references.faa"))
        code, _, error = self.call("run", "-c", str(self.study / "config.yaml"), "-d", "-e")
        self.assertEqual(code, 0, error)
        evidence = json.loads((self.study / "results/evidence.json").read_text())
        pf2 = {r["sample_id"]: r for r in evidence["records"] if r["module"] == "pathogenfinder2"}
        self.assertEqual(pf2["DEMO_A"]["execution_status"], "completed")
        self.assertEqual(pf2["DEMO_B"]["execution_status"], "not_assessed")
        self.assertEqual(pf2["DEMO_B"]["module_decision"]["requested"], False)
        self.assertEqual(evidence["run"]["completeness"], "completed_requested_scope")
        self.assertEqual({p: p.read_bytes() for p in self.original.rglob("*") if p.is_file()}, before)
        html = (self.study / "results/report.html").read_text()
        self.assertIn("Inputs and module scope", html)
        self.assertIn("Synthetic scope exclusion", html)

    def test_invalid_base_config_leaves_no_study(self):
        code, _, _ = self.call("import-samples", "--file", str(self.sheet),
                              "--directory", str(self.study), "--base-config", str(self.root / "absent.yaml"))
        self.assertEqual(code, 2)
        self.assertFalse(self.study.exists())

    def test_sample_topology_cannot_be_silently_transferred_from_base(self):
        cfg = yaml.safe_load(self.base.read_text())
        cfg["deep"]["circular_contigs"] = ["1"]
        self.base.write_text(yaml.safe_dump(cfg))
        code, _, error = self.call("import-samples", "--file", str(self.sheet),
                                   "--directory", str(self.study), "--base-config", str(self.base))
        self.assertEqual(code, 2)
        self.assertIn("sample-specific circular topology", error)
        self.assertFalse(self.study.exists())

    def test_existing_study_is_never_replaced(self):
        self.study.mkdir()
        sentinel = self.study / "keep.txt"
        sentinel.write_text("unchanged")
        code, _, error = self.call("import-samples", "--file", str(self.sheet),
                                   "--directory", str(self.study))
        self.assertEqual(code, 2)
        self.assertTrue(error)
        self.assertEqual(list(self.study.iterdir()), [sentinel])

    def test_doctor_checks_only_runnable_sample_modules_and_records_holds(self):
        cfg = load_config(self.base)
        for mod in cfg["modules"].values():
            mod["enabled"] = False
        cfg["modules"]["icescreen"].update(enabled=True, mode="native")
        cfg["sample_module_overrides"] = {"DEMO_A": {
            "icescreen": {"enabled": False, "reason": "Out of declared study scope"}}}
        with patch("basis.cli.shutil.which", side_effect=AssertionError("Withheld executable should not be probed")):
            result = check(cfg, executables=True)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["module_decisions"]["DEMO_A"]["icescreen"]["requested"])
        self.assertTrue(result["module_decisions"]["DEMO_B"]["icescreen"]["scope_incomplete"])
        self.assertTrue(any("DEMO_B:icescreen" in e for e in result["errors"]))
        self.assertFalse(any("DEMO_A:icescreen" in e for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()
