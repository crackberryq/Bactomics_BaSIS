"""Public CLI dry-run contracts using file-identity fixtures only."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.cli import main


class BatchPreviewCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        folder = self.inputs / "S01" / "annotation"
        folder.mkdir(parents=True)
        for suffix, text in ((".fna", ">contig\nATGAAATAA\n"),
                             (".faa", ">protein\nMK\n"),
                             (".gff", "##gff-version 3\n")):
            (folder / ("S01" + suffix)).write_text(text)
        self.sheet = self.root / "samples.csv"
        self.sheet.write_text("sample_id\nS01\n")

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["import-samples", "--file", str(self.sheet),
                         "--root", str(self.inputs), *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def test_preview_without_directory_is_read_only_and_not_preflight(self):
        before = self.snapshot()
        with patch("basis.sample_import.import_samples", side_effect=AssertionError("Import called")):
            code, output, error = self.invoke("--dry-run")
        self.assertEqual(code, 0, error)
        result = json.loads(output)
        self.assertEqual(result["status"], "ready_for_import")
        self.assertEqual(result["sample_count"], 1)
        self.assertFalse(result["sequence_consistency_checked"])
        self.assertFalse(result["resources_checked"])
        self.assertFalse(result["native_analyses_run"])
        self.assertEqual(before, self.snapshot())

    def test_invalid_rows_return_nonzero_and_multiple_issues(self):
        self.sheet.write_text("sample_id\nS01\nMISSING_A\nMISSING_B\n")
        code, output, _ = self.invoke("--dry-run")
        self.assertEqual(code, 2)
        result = json.loads(output)
        self.assertEqual(result["status"], "blocked")
        blocked = [r for r in result["samples"] if r["status"] == "blocked"]
        self.assertEqual({r["sample_id"] for r in blocked}, {"MISSING_A", "MISSING_B"})
        self.assertTrue(all(r["errors"] for r in blocked))

    def test_existing_proposed_destination_is_blocked_and_preserved(self):
        destination = self.root / "existing"
        destination.mkdir()
        sentinel = destination / "preserve.txt"
        sentinel.write_text("historical data")
        code, output, _ = self.invoke("--dry-run", "--directory", str(destination))
        self.assertEqual(code, 2)
        self.assertIn("already exists", " ".join(json.loads(output)["errors"]))
        self.assertEqual(sentinel.read_text(), "historical data")

    def test_normal_import_still_requires_explicit_destination(self):
        before = self.snapshot()
        code, output, error = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("--directory is required", error)
        self.assertEqual(before, self.snapshot())

    def test_verbose_preview_keeps_stdout_machine_readable(self):
        with patch.dict("os.environ", {}, clear=False):
            code, output, error = self.invoke("--dry-run", "-v")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["status"], "ready_for_import")
        self.assertIn("Preview batch inputs", error)

    def test_preview_and_real_import_resolve_identical_paths(self):
        destination = self.root / "new-study"
        code, output, _ = self.invoke("--dry-run", "--directory", str(destination))
        self.assertEqual(code, 0)
        self.assertFalse(destination.exists())
        preview = json.loads(output)
        code, output, error = self.invoke("--directory", str(destination))
        self.assertEqual(code, 0, error)
        audit = json.loads(Path(json.loads(output)["audit"]).read_text())
        expected = {k: v["path"] for k, v in audit["samples"][0]["inputs"].items()}
        self.assertEqual(preview["samples"][0]["resolved_inputs"], expected)


if __name__ == "__main__":
    unittest.main()
