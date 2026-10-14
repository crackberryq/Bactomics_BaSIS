"""Read-only batch import preview uses the same transaction checks as import."""
import contextlib
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.sample_import import import_samples, preview_samples
from basis.util import sha256_file

try:
    import openpyxl
except ImportError:
    openpyxl = None


class SamplePreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "inputs"
        self.root.mkdir()

    def annotations(self, sid, layout="annotation"):
        folder = self.root / sid / "annotation" if layout == "annotation" else self.root
        folder.mkdir(parents=True, exist_ok=True)
        result = {}
        for key, suffix, text in (("assembly", ".fna", ">contig\nATGAAATAA\n"),
                                  ("proteins", ".faa", ">protein\nMK\n"),
                                  ("gff", ".gff", "##gff-version 3\n")):
            path = folder / (sid + suffix)
            path.write_text(text)
            result[key] = str(path)
        return result

    def sheet(self, headers, rows):
        path = self.base / "samples.csv"
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)
        return path

    def snapshot(self):
        return {str(path.relative_to(self.base)): (path.stat().st_mtime_ns, sha256_file(path))
                for path in self.base.rglob("*") if path.is_file()}

    def test_aggregate_missing_and_ambiguous_rows_without_creating_any_output(self):
        self.annotations("S01")
        self.annotations("S02")
        self.annotations("S02", "flat")
        third = self.annotations("S03")
        Path(third["gff"]).unlink()
        source = self.sheet(["sample_id"], [["S01"], ["S02"], ["S03"], ["S04"]])
        before = self.snapshot()
        before_dirs = {str(path) for path in self.base.rglob("*") if path.is_dir()}
        with patch("basis.sample_import.tempfile.mkdtemp") as staging, patch.object(Path, "mkdir") as mkdir:
            result = preview_samples(source, root=self.root)
            staging.assert_not_called()
            mkdir.assert_not_called()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual((result["sample_count"], result["ready_samples"], result["blocked_samples"]), (4, 1, 3))
        self.assertIn("ambiguous discovery", result["samples"][1]["errors"][0])
        self.assertIn("missing gff", result["samples"][2]["errors"][0])
        self.assertIn("no annotation files", result["samples"][3]["errors"][0])
        self.assertEqual([row["source_row"] for row in result["samples"]], [2, 3, 4, 5])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(before_dirs, {str(path) for path in self.base.rglob("*") if path.is_dir()})
        self.assertFalse(result["sequence_consistency_checked"])
        self.assertFalse(result["resources_checked"])
        self.assertFalse(result["native_analyses_run"])
        with patch("basis.sample_import.tempfile.mkdtemp") as staging:
            with self.assertRaisesRegex(ValueError, "ambiguous discovery"):
                import_samples(source, self.base / "uncreated-parent" / "study", root=self.root)
            staging.assert_not_called()
        self.assertFalse((self.base / "uncreated-parent").exists())

    def test_ready_preview_import_agree_on_ids_paths_modes_and_hashes(self):
        first = self.annotations("001")
        second = self.annotations("002", "flat")
        source = self.sheet(["sample_id", "assembly", "proteins", "gff", "input_mode"], [
            ["001", "", "", "", "hybas"],
            ["002", *(str(Path(second[key]).relative_to(self.base)) for key in ("assembly", "proteins", "gff")), "external"]])
        before = self.snapshot()
        preview = preview_samples(source, root=self.root)
        self.assertEqual(preview["status"], "ready_for_import")
        self.assertEqual(preview["ready_samples"], 2)
        self.assertEqual(before, self.snapshot())
        imported = import_samples(source, self.base / "study", root=self.root)
        audit = json.loads(Path(imported["audit"]).read_text())
        self.assertEqual(preview["source"]["sha256"], audit["source"]["sha256"])
        for row, actual in zip(preview["samples"], audit["samples"]):
            self.assertEqual(row["sample_id"], actual["sample_id"])
            self.assertEqual(row["source_row"], actual["source_row"])
            self.assertEqual(row["input_mode"], actual["input_mode"])
            self.assertEqual(row["discovery_rule"], actual["discovery_rule"])
            self.assertEqual(row["resolved_inputs"], {key: record["path"] for key, record in actual["inputs"].items()})
            for key, record in actual["inputs"].items():
                self.assertEqual(record["sha256"], sha256_file(row["resolved_inputs"][key]))
        self.assertEqual(preview["samples"][0]["resolved_inputs"], first)
        self.assertTrue(all(self.snapshot()[name] == record for name, record in before.items()))

    def test_multiple_missing_explicit_files_in_one_row_are_reported(self):
        paths = self.annotations("S01")
        Path(paths["assembly"]).unlink()
        Path(paths["gff"]).unlink()
        source = self.sheet(["sample_id", "assembly", "proteins", "gff"], [["S01", *(paths[key] for key in ("assembly", "proteins", "gff"))]])
        result = preview_samples(source)
        self.assertEqual(len(result["samples"][0]["errors"]), 2)
        self.assertEqual(result["samples"][0]["resolved_inputs"], {"proteins": paths["proteins"]})
        self.assertEqual(result["blocked_samples"], 1)

    def test_all_shared_file_participants_blocked_including_hardlink_and_partial_row(self):
        first = self.annotations("S01")
        second = self.annotations("S02")
        alias = self.base / "hardlink.faa"
        alias.hardlink_to(first["proteins"])
        third = self.annotations("S03")
        source = self.sheet(["sample_id", "assembly", "proteins", "gff"], [
            ["S01", *(first[key] for key in ("assembly", "proteins", "gff"))],
            ["S02", second["assembly"], str(alias), ""],
            ["S03", third["assembly"], first["proteins"], third["gff"]]])
        result = preview_samples(source, input_mode="external")
        self.assertEqual(result["ready_samples"], 0)
        self.assertEqual(result["blocked_samples"], 3)
        self.assertTrue(all(any("shared required input" in error for error in sample["errors"]) for sample in result["samples"]))
        self.assertTrue(any("annotation_required" in error for error in result["samples"][1]["errors"]))
        with self.assertRaisesRegex(ValueError, "shared required input"):
            import_samples(source, self.base / "study", input_mode="external")
        self.assertFalse((self.base / "study").exists())

    def test_large_shared_input_group_has_linear_bounded_diagnostics(self):
        paths = self.annotations("owner")
        count = 150
        source = self.sheet(["sample_id", "assembly", "proteins", "gff"], [
            [f"S{number:03d}", *(paths[key] for key in ("assembly", "proteins", "gff"))]
            for number in range(count)])
        result = preview_samples(source, input_mode="external")
        self.assertEqual(result["blocked_samples"], count)
        self.assertEqual(result["ready_samples"], 0)
        diagnostics = [error for sample in result["samples"] for error in sample["errors"]]
        self.assertEqual(len(diagnostics), count)
        self.assertTrue(all("shared required input" in message for message in diagnostics))
        self.assertLess(sum(map(len, diagnostics)), count * 160)
        with self.assertRaises(ValueError) as error:
            import_samples(source, self.base / "study", input_mode="external")
        self.assertLess(len(str(error.exception)), count * 160)
        self.assertFalse((self.base / "study").exists())

    def test_reusing_required_file_for_two_fields_in_one_row_is_blocked(self):
        paths = self.annotations("S01")
        source = self.sheet(["sample_id", "assembly", "proteins", "gff"], [["S01", paths["assembly"], paths["assembly"], paths["gff"]]])
        result = preview_samples(source)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("S01:assembly and S01:proteins", " ".join(result["samples"][0]["errors"]))

    def test_structural_failures_return_consistent_empty_preview(self):
        cases = [(["sample_id"], [["S01"], ["s01"]]),
                 (["sample_id", "sample_id"], [["S01", "S02"]]),
                 (["sample_id", "typo"], [["S01", "x"]]),
                 (["sample_id"], [["S01", "extra"]]),
                 (["sample_id"], [["../escape"]])]
        for headers, rows in cases:
            with self.subTest(headers=headers, rows=rows):
                source = self.sheet(headers, rows)
                result = preview_samples(source, root=self.root)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual((result["sample_count"], result["ready_samples"], result["blocked_samples"]), (0, 0, 0))
                self.assertEqual(result["samples"], [])
                self.assertTrue(result["errors"])

    def test_missing_source_invalid_root_and_mode_return_errors(self):
        source = self.sheet(["sample_id"], [["S01"]])
        for kwargs in ({"sheet": self.base / "missing.csv"},
                       {"sheet": source, "root": self.base / "missing"},
                       {"sheet": source, "root": source},
                       {"sheet": source, "input_mode": "unknown"}):
            with self.subTest(kwargs=kwargs):
                result = preview_samples(**kwargs)
                self.assertEqual(result["status"], "blocked")
                self.assertTrue(result["errors"])

    def test_config_override_validity_matches_import_and_is_not_mutated(self):
        self.annotations("S01")
        source = self.sheet(["sample_id"], [["S01"]])
        config = {"sample_module_overrides": {"S01": {"pathogenfinder2": {"enabled": False, "reason": "out of requested scope"}}}}
        before = json.dumps(config, sort_keys=True)
        result = preview_samples(source, root=self.root, config=config)
        self.assertEqual(result["status"], "ready_for_import")
        self.assertEqual(json.dumps(config, sort_keys=True), before)
        import_samples(source, self.base / "good-study", root=self.root, config=config)
        config["sample_module_overrides"]["Absent"] = config["sample_module_overrides"]["S01"]
        result = preview_samples(source, root=self.root, config=config)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("unknown sample IDs", result["errors"][0])
        self.assertEqual(result["ready_samples"], 1)  # Input readiness is separate from global configuration.
        with self.assertRaisesRegex(ValueError, "unknown sample IDs"):
            import_samples(source, self.base / "bad-study", root=self.root, config=config)
        for bad_config in ([], {"sample_module_overrides": {"S01": {"pathogenfinder2": {"enabled": True, "reason": "x"}}}}):
            self.assertEqual(preview_samples(source, root=self.root, config=bad_config)["status"], "blocked")

    def test_source_mode_and_taxonomy_claims_are_identical_to_import(self):
        paths = self.annotations("S01")
        for headers, row in ((["sample_id", "input_mode", "hybas_dir"], ["S01", "external", str(self.root / "S01")]),
                             (["sample_id", "input_mode", "taxon_source"], ["S01", "external", "hybas_kraken_operational_target"]),
                             (["sample_id", "input_mode"], ["S01", "guess"])):
            source = self.sheet(headers, [row])
            result = preview_samples(source, root=self.root)
            self.assertEqual(result["status"], "blocked")
            with self.assertRaises(ValueError):
                import_samples(source, self.base / "study", root=self.root)

    def test_sheet_change_during_resolution_is_reported_as_global_block(self):
        self.annotations("S01")
        source = self.sheet(["sample_id"], [["S01"]])
        from basis.sample_import import _resolve_row
        def change_source(*args, **kwargs):
            result = _resolve_row(*args, **kwargs)
            source.write_text("sample_id\nchanged\n")
            return result
        with patch("basis.sample_import._resolve_row", side_effect=change_source):
            result = preview_samples(source, root=self.root)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("Source sheet changed", result["errors"][0])

    def test_preview_verbose_progress_only_on_stderr_when_enabled(self):
        self.annotations("S01")
        source = self.sheet(["sample_id"], [["S01"]])
        for enabled in ("0", "1"):
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {"BASIS_VERBOSE": enabled}), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = preview_samples(source, root=self.root)
            self.assertEqual(result["status"], "ready_for_import")
            self.assertEqual(stdout.getvalue(), "")
            if enabled == "1":
                self.assertIn("S01 (row 2): checking input files", stderr.getvalue())
                self.assertIn("S01: ready", stderr.getvalue())
            else:
                self.assertEqual(stderr.getvalue(), "")

    @unittest.skipIf(openpyxl is None, "openpyxl extra unavailable")
    def test_excel_text_ids_preserved_numeric_formula_and_error_cells_block(self):
        self.annotations("001")
        source = self.base / "samples.xlsx"
        for value in ("001", 1, "=1+1", "#DIV/0!"):
            workbook = openpyxl.Workbook()
            workbook.active.append(["sample_id"])
            workbook.active.append([value])
            workbook.save(source)
            workbook.close()
            with self.subTest(value=value):
                result = preview_samples(source, root=self.root)
                if value == "001":
                    self.assertEqual(result["status"], "ready_for_import")
                    self.assertEqual(result["samples"][0]["sample_id"], "001")
                else:
                    self.assertEqual(result["status"], "blocked")
                    self.assertTrue(result["errors"])

    def test_damaged_excel_returns_structured_blocked_preview(self):
        source = self.base / "damaged.xlsx"
        source.write_bytes(b"not a spreadsheet archive")
        result = preview_samples(source)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["errors"])


if __name__ == "__main__":
    unittest.main()
