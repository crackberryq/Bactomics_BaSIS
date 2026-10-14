"""Independent row diagnostics and per-sample prerequisite metadata only."""
from __future__ import annotations

import copy
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.cli import check
from basis.config import default_config
from basis.inputs import load_samples, load_samples_diagnostic


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = []
        for sid in ("S01", "S02"):
            folder = self.root / sid
            folder.mkdir()
            row = {"sample_id": sid, "input_mode": "external", "taxon": "Declared species",
                   "amrfinder_organism": "declared-organism", "genbank": "", "phenotypes": ""}
            for key, suffix, text in (("assembly", ".fna", ">ctg\nATGAAATAA\n"),
                                      ("proteins", ".faa", ">p1\nMK\n"),
                                      ("gff", ".gff", "##gff-version 3\nctg\tfixture\tCDS\t1\t9\t.\t+\t0\tID=p1;transl_table=11\n")):
                path = folder / (sid + suffix)
                path.write_text(text)
                row[key] = str(path)
            self.rows.append(row)
        self.manifest = self.root / "samples.tsv"
        self.config = default_config()
        self.config["samples"] = str(self.manifest)
        self.config["outdir"] = str(self.root / "results")
        for module in self.config["modules"].values():
            module["enabled"] = False
        self.config["modules"]["amrfinder"].update(enabled=True, executable="fixture-amrfinder")
        self.write_manifest()

    def write_manifest(self):
        fields = ["sample_id", "assembly", "proteins", "gff", "input_mode", "taxon", "amrfinder_organism", "genbank", "phenotypes"]
        with self.manifest.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(self.rows)

    def test_diagnostic_loader_resolves_good_rows_and_strict_loader_still_rejects(self):
        self.rows[0]["genbank"] = str(self.root / "missing.gbk")
        self.write_manifest()
        result = load_samples_diagnostic(self.manifest)
        self.assertEqual([row["sample_id"] for row in result], ["S01", "S02"])
        self.assertEqual([row["source_row"] for row in result], [2, 3])
        self.assertIsNone(result[0]["sample"])
        self.assertIn("GenBank file does not exist", result[0]["errors"][0])
        self.assertEqual(result[1]["sample"]["sample_id"], "S02")
        self.assertEqual(result[1]["errors"], [])
        with self.assertRaisesRegex(ValueError, "GenBank file does not exist"):
            load_samples(self.manifest)
        self.rows.pop(0)
        self.write_manifest()
        self.assertEqual(load_samples(self.manifest), [load_samples_diagnostic(self.manifest)[0]["sample"]])

    def test_row_resolution_failure_retained_while_other_sample_is_validated_and_probed(self):
        self.rows[0]["phenotypes"] = str(self.root / "bad.json")
        Path(self.rows[0]["phenotypes"]).write_text("invalid JSON")
        self.write_manifest()
        with patch("basis.cli.shutil.which", return_value="/fixture/bin/amrfinder") as which:
            result = check(self.config, executables=True)
        self.assertEqual(result["schema"], "basis-preflight-v1")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["global_checks"]["errors"], [])
        bad, good = result["sample_checks"]
        self.assertEqual((bad["status"], bad["input_status"]), ("failed", "failed"))
        self.assertEqual(bad["module_checks"], {})
        self.assertNotIn("S01", result["module_decisions"])
        self.assertEqual(bad["metrics"], {})
        self.assertEqual(good["input_status"], "passed")
        self.assertEqual(good["metrics"], {"contigs": 1, "assembly_bp": 9, "protein_count": 1})
        self.assertEqual([row["sample_id"] for row in result["checks"]], ["S02"])
        self.assertEqual(good["module_checks"]["amrfinder"]["status"], "passed")
        which.assert_called_once_with("fixture-amrfinder")
        self.assertTrue(result["resource_checks_performed"])

    def test_sequence_failure_does_not_remove_later_valid_sample(self):
        Path(self.rows[0]["proteins"]).write_text(">p1\nMQ\n")
        with patch("basis.cli.shutil.which", side_effect=AssertionError("validate must not probe tools")):
            result = check(self.config)
        bad, good = result["sample_checks"]
        self.assertEqual(bad["input_status"], "failed")
        self.assertTrue(bad["errors"])
        self.assertEqual(bad["metrics"], {})
        self.assertEqual(good["input_status"], "passed")
        self.assertEqual(good["input_hashes"], result["checks"][0]["input_hashes"])
        self.assertEqual(good["module_checks"]["amrfinder"]["status"], "not_checked")
        self.assertFalse(result["resource_checks_performed"])

    def test_structural_error_prevents_all_resolution_validation_and_probes(self):
        self.rows[1]["sample_id"] = "s01"
        self.write_manifest()
        with patch("basis.inputs._resolve_sample_declaration", side_effect=AssertionError("structural invalidity must abort resolution")), \
             patch("basis.cli.validate_sample", side_effect=AssertionError("unexpected sequence check")), \
             patch("basis.cli.shutil.which", side_effect=AssertionError("unexpected resource probe")):
            result = check(self.config, deep=True, extend=True, executables=True)
        self.assertEqual(result["status"], "failed")
        self.assertIn("duplicate sample ID", result["global_checks"]["errors"][0])
        self.assertEqual(result["sample_checks"], [])
        self.assertEqual(result["checks"], [])
        self.assertEqual(result["module_decisions"], {})
        self.assertFalse(result["resource_checks_performed"])

    def test_unknown_or_malformed_override_fails_globally_before_checks(self):
        cases = [{"Absent": {"amrfinder": {"enabled": False, "reason": "declared"}}},
                 {"S01": {"amrfinder": {"enabled": True, "reason": "invalid"}}},
                 {"S01": {"amrfinder": {"enabled": False, "reason": " "}}}]
        for overrides in cases:
            self.config["sample_module_overrides"] = overrides
            with self.subTest(overrides=overrides), \
                 patch("basis.cli.validate_sample", side_effect=AssertionError("unexpected sequence check")), \
                 patch("basis.cli.shutil.which", side_effect=AssertionError("unexpected resource probe")):
                result = check(self.config, deep=True, executables=True)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["global_checks"]["errors"])
            self.assertEqual(result["checks"], [])
            self.assertEqual(result["module_decisions"], {})
            self.assertTrue(all(row["input_status"] == "not_checked" and not row["metrics"] for row in result["sample_checks"]))
            self.assertFalse(result["resource_checks_performed"])

    def test_override_for_unresolved_sample_is_known_but_does_not_invent_decisions(self):
        self.rows[0]["genbank"] = str(self.root / "missing.gbk")
        self.write_manifest()
        self.config["sample_module_overrides"] = {"S01": {"amrfinder": {"enabled": False, "reason": "explicit scope"}}}
        with patch("basis.cli.shutil.which", return_value="/fixture/amrfinder"):
            result = check(self.config, executables=True)
        self.assertFalse(result["global_checks"]["errors"])
        self.assertTrue(result["sample_checks"][0]["errors"])
        self.assertEqual(result["sample_checks"][0]["module_checks"], {})
        self.assertNotIn("S01", result["module_decisions"])
        self.assertEqual(result["sample_checks"][1]["input_status"], "passed")

    def test_failed_resource_checks_are_scoped_and_keep_existing_limits(self):
        module = self.config["modules"]["amrfinder"]
        module.update(database=str(self.root / "missing.db"), memory_mb=64000, gpus=1)
        with patch("basis.cli.shutil.which", return_value=None):
            result = check(self.config, executables=True)
        for sample in result["sample_checks"]:
            detail = sample["module_checks"]["amrfinder"]
            self.assertEqual(detail["status"], "failed")
            self.assertEqual(len(detail["errors"]), 4)
            self.assertTrue(any("executable/prefix not found" in error for error in detail["errors"]))
            self.assertTrue(any("database does not exist" in error for error in detail["errors"]))
            self.assertTrue(any("memory requirement" in error for error in detail["errors"]))
            self.assertTrue(any("GPU requirement" in error for error in detail["errors"]))
            self.assertEqual(sample["status"], "failed")
            self.assertEqual(sample["input_status"], "passed")
        self.assertFalse(result["global_checks"]["errors"])
        self.assertEqual(len(result["errors"]), 8)

    def test_prefix_check_is_advisory_not_native_environment_verification(self):
        self.config["modules"]["amrfinder"]["command_prefix"] = ["conda", "run", "-p", "/not-probed"]
        with patch("basis.cli.shutil.which", return_value="/fixture/conda") as which:
            result = check(self.config, executables=True)
        self.assertEqual(which.call_count, 2)
        self.assertEqual({call.args for call in which.call_args_list}, {("conda",)})
        for row in result["sample_checks"]:
            detail = row["module_checks"]["amrfinder"]
            self.assertEqual(detail["status"], "passed_with_warnings")
            self.assertIn("native acceptance testing", " ".join(detail["warnings"]))
        self.assertEqual(result["status"], "passed_with_warnings")

    def test_explicit_exclusion_disabled_extension_and_requested_hold_are_separate(self):
        self.config["modules"]["icescreen"]["enabled"] = True
        self.config["modules"]["pathogenfinder2"]["enabled"] = True
        self.config["sample_module_overrides"] = {"S01": {
            "amrfinder": {"enabled": False, "reason": "intentional test scope"},
            "rgi": {"enabled": False, "reason": "inactive because globally disabled"},
            "pathogenfinder2": {"enabled": False, "reason": "inactive because extension not requested"}}}
        with patch("basis.cli.shutil.which", return_value="/fixture/amrfinder"):
            result = check(self.config, executables=True)
        modules = result["sample_checks"][0]["module_checks"]
        self.assertEqual(modules["amrfinder"]["status"], "excluded")
        self.assertEqual(modules["rgi"]["status"], "disabled")
        self.assertEqual(modules["pathogenfinder2"]["status"], "disabled")
        self.assertEqual(modules["pathogenfinder2"]["decision"]["reason"], "extension_not_requested")
        self.assertEqual(modules["icescreen"]["status"], "held")
        self.assertEqual(modules["icescreen"]["errors"], ["matching_genbank_required"])
        self.assertTrue(modules["icescreen"]["decision"]["scope_incomplete"])
        for name in ("amrfinder", "rgi", "pathogenfinder2"):
            self.assertEqual(modules[name]["declared_exclusion"], self.config["sample_module_overrides"]["S01"][name])
        self.assertFalse(modules["amrfinder"]["decision"]["scope_incomplete"])

    def test_disabled_reason_text_alone_cannot_create_an_explicit_exclusion(self):
        self.config["modules"]["rgi"].update(enabled=False, disabled_reason="sample_scope_exclusion")
        result = check(self.config)
        for row in result["sample_checks"]:
            detail = row["module_checks"]["rgi"]
            self.assertEqual(detail["status"], "disabled")
            self.assertIsNone(detail["declared_exclusion"])

    def test_sequence_warnings_are_visible_without_changing_input_or_configuration(self):
        from basis.inputs import validate_sample
        original = copy.deepcopy(self.config)
        files = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        def warned_validation(sample):
            result = validate_sample(sample)
            result["warnings"] = ["fixture: unresolved initiation alternative"]
            return result
        with patch("basis.cli.validate_sample", side_effect=warned_validation):
            result = check(self.config)
        self.assertEqual(result["status"], "passed_with_warnings")
        for row in result["sample_checks"]:
            self.assertEqual(row["input_status"], "passed_with_warnings")
            self.assertEqual(row["input_warnings"], ["fixture: unresolved initiation alternative"])
            self.assertIn("fixture: unresolved initiation alternative", row["warnings"])
            self.assertTrue(any(row["sample_id"] + ": fixture:" in warning for warning in result["warnings"]))
        self.assertEqual(self.config, original)
        self.assertEqual(files, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})
        self.assertFalse((self.root / "results").exists())

    def test_shared_deep_resource_issues_are_global_even_for_validate_only(self):
        self.config["deep"]["reference_fasta"] = str(self.root / "missing.faa")
        with patch("basis.cli.shutil.which", side_effect=AssertionError("validate does not inspect executable availability")):
            result = check(self.config, deep=True, extend=False, executables=False)
        self.assertTrue(result["resource_checks_performed"])
        self.assertEqual(result["check_options"], {"deep": True, "extend": False, "executables": False})
        self.assertIn("DeepSafe reference_fasta does not exist", result["global_checks"]["errors"])
        self.assertTrue(any("reference_metadata is unset" in warning for warning in result["global_checks"]["warnings"]))
        self.assertTrue(all(row["module_checks"]["amrfinder"]["status"] == "not_checked" for row in result["sample_checks"]))
        self.assertEqual(result["status"], "failed")

    def test_imported_result_presence_has_its_own_resource_status(self):
        self.config["modules"]["amrfinder"].update(mode="import", result=str(self.root / "{sample_id}.json"))
        (self.root / "S02.json").write_text("{}")
        with patch("basis.cli.shutil.which", side_effect=AssertionError("import resource check must not probe native tools")):
            result = check(self.config, executables=True)
        first, second = result["sample_checks"]
        self.assertEqual(first["module_checks"]["amrfinder"]["errors"], ["imported result missing"])
        self.assertEqual(first["module_checks"]["amrfinder"]["status"], "failed")
        self.assertEqual(second["module_checks"]["amrfinder"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
