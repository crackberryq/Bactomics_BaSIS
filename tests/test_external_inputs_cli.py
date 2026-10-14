"""External annotation command contracts with tiny artificial input files."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from basis.cli import main


class ExternalInputCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.annotation = self.root / "external_case" / "annotation"
        self.annotation.mkdir(parents=True)
        for suffix, content in (("fna", ">ctg\nATGAAATAA\n"),
                                ("faa", ">p1\nMK\n"),
                                ("gff", "##gff-version 3\nctg\tfixture\tCDS\t1\t9\t.\t+\t0\tID=p1\n")):
            (self.annotation / f"external_case.{suffix}").write_text(content)
        metadata = self.annotation.parent / "metadata"
        metadata.mkdir()
        (metadata / "run_summary.json").write_text("invalid adjacent metadata must not be consulted")
        self.sheet = self.root / "samples.csv"
        self.sheet.write_text("sample_id\nexternal_case\n")
        self.study = self.root / "study"

    def call(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict("os.environ", {}, clear=False), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def imported_config(self):
        code, output, error = self.call("import-samples", "--file", str(self.sheet),
                                        "--root", str(self.root), "--input-mode", "external",
                                        "--directory", str(self.study))
        self.assertEqual(code, 0, error)
        return Path(json.loads(output)["config"])

    def test_preview_and_validation_expose_external_unknown_context(self):
        code, output, error = self.call("import-samples", "--file", str(self.sheet),
                                        "--root", str(self.root), "--input-mode", "external", "--dry-run")
        self.assertEqual(code, 0, error)
        preview = json.loads(output)["samples"][0]["input_context"]
        self.assertEqual(preview["taxon_status"], "not_recorded")
        self.assertEqual(preview["hybas_metadata_status"], "not_consulted")
        config = self.imported_config()
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with patch("basis.hybas._json_summary", side_effect=AssertionError("Metadata consulted")):
            code, output, error = self.call("validate", "-c", str(config), "-v")
        self.assertEqual(code, 0, error)
        result = json.loads(output)
        context = result["input_context"]["external_case"]
        self.assertEqual(context["input_mode"], "external")
        self.assertEqual(context["taxon_status"], "not_recorded")
        self.assertEqual(context["hybas_metadata_status"], "not_consulted")
        self.assertFalse(context["taxonomic_classification_performed"])
        self.assertEqual(result["checks"][0]["protein_count"], 1)
        self.assertIn("Checking annotation file consistency", error)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_doctor_reports_same_context_without_probing_excluded_tools(self):
        config = self.imported_config()
        settings = yaml.safe_load(config.read_text())
        for module in settings["modules"].values():
            module["enabled"] = False
        config.write_text(yaml.safe_dump(settings))
        with patch("basis.cli.shutil.which", side_effect=AssertionError("Unexpected tool probe")):
            code, output, error = self.call("doctor", "-c", str(config), "-v")
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output)["input_context"]["external_case"]["hybas_metadata_status"], "not_consulted")
        self.assertIn("Check inputs and configured resources", error)

    def test_raw_missing_marker_is_preserved_and_explained_in_preview(self):
        self.sheet.write_text("sample_id,taxon\nexternal_case,NA\n")
        code, output, error = self.call("import-samples", "--file", str(self.sheet),
                                        "--root", str(self.root), "--input-mode", "external", "--dry-run")
        self.assertEqual(code, 0, error)
        context = json.loads(output)["samples"][0]["input_context"]
        self.assertEqual(context["taxon"], "NA")
        self.assertEqual(context["taxon_status"], "not_recorded")

    def test_invalid_mode_remains_structured_blocked_preview(self):
        self.sheet.write_text("sample_id,input_mode\nexternal_case,invalid-mode\n")
        code, output, _ = self.call("import-samples", "--file", str(self.sheet),
                                   "--root", str(self.root), "--dry-run")
        self.assertEqual(code, 2)
        result = json.loads(output)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocked_samples"], 1)


if __name__ == "__main__":
    unittest.main()
