"""Native contract regression tests; synthetic files, not biological validation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety.native import MODULES, command_argv, command_shell, normalized_record, parse_payload, verify


class NativeContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sample = {"sample_id": "fixture", "assembly": str(self.root / "assembly.fna"),
                       "proteins": str(self.root / "proteins.faa"), "gff": str(self.root / "features.gff"),
                       "genbank": str(self.root / "assembly.gbk")}
        Path(self.sample["assembly"]).write_text(">c1\n" + "ATG" * 100 + "\n")
        self.settings = {"database": str(self.root / "database"), "phylum": "bacillota"}
        self.native = self.root / "native"
        self.native.mkdir()

    def test_all_commands_use_tool_and_allocated_threads_without_activation(self):
        for module in MODULES:
            with self.subTest(module=module):
                argv = command_argv(module, self.sample, self.settings, self.native, 2)
                self.assertNotIn("conda", argv)
                self.assertNotEqual("basis", argv[0])
                self.assertNotIn("run", argv)
                if module != "minced":
                    self.assertIn("2", argv)
        self.assertIn("--local", command_argv("rgi", self.sample, self.settings, self.native, 1))
        self.assertNotIn("--nudge", command_argv("rgi", self.sample, self.settings, self.native, 1))

    def test_scope_gate_does_not_access_absent_native_outputs(self):
        config = {"modules": {"icescreen": self.settings}, "module_decisions": {
            "icescreen": {"action": "hold", "reason": "phylum_scope_requires_review"}}}
        result = normalized_record("icescreen", self.sample, config, {"input_hashes": {}}, self.root / "absent")
        self.assertEqual(result["execution_status"], "not_assessed")
        self.assertEqual(result["reason"], "phylum_scope_requires_review")
        self.assertEqual(result["interpretation"], "inconclusive")

    def test_diamond_missing_vs_verified_empty_and_normalization(self):
        with self.assertRaises(ValueError):
            verify("vfdb", self.sample, self.settings, self.native)
        self.assertFalse((self.native / "parsed.json").exists())
        (self.native / "vfdb.diamond.tsv").write_text("")
        verify("vfdb", self.sample, self.settings, self.native)
        validation = {"input_hashes": {}, "protein_coordinates": {}, "contig_lengths": {"c1": 300}}
        result = normalized_record("vfdb", self.sample, {"modules": {"vfdb": self.settings}}, validation, self.native)
        self.assertEqual(result["execution_status"], "completed")
        self.assertEqual(result["interpretation"], "not_detected")
        self.assertEqual(result["findings"], [])
        self.assertIn(str(self.native / "vfdb.diamond.tsv"), result["artifact_hashes"])

    def test_homology_threshold_and_coordinates_preserved(self):
        (self.native / "vfdb.diamond.tsv").write_text(
            "p1\tref1\t85\t80\t100\t100\t1\t80\t1\t80\t1e-30\t100\t80\t80\tsynthetic reference\n")
        settings = {**self.settings, "min_identity": 90}
        verify("vfdb", self.sample, settings, self.native)
        validation = {"input_hashes": {}, "protein_coordinates": {
            "p1": {"contig": "c1", "start": 1, "end": 300, "strand": "+"}}, "contig_lengths": {"c1": 300}}
        result = normalized_record("vfdb", self.sample, {"modules": {"vfdb": settings}}, validation, self.native)
        hit = result["findings"][0]
        self.assertEqual(hit["evidence_level"], "exploratory")
        self.assertEqual((hit["contig"], hit["start"], hit["end"]), ("c1", 1, 300))
        self.assertTrue(hit["finding_id"].startswith("finding_"))
        self.assertFalse(hit["attributes"]["passes_threshold"])

    def test_minced_error_exit_zero_cannot_be_negative(self):
        (self.native / "arrays.gff").write_text("##gff-version 3\n")
        (self.native / "arrays.txt").write_text("")
        (self.native / "stdout.txt").write_text("")
        (self.native / "stderr.txt").write_text("java.lang.RuntimeException: synthetic failure\n")
        with self.assertRaisesRegex(ValueError, "MinCED reported an error"):
            verify("minced", self.sample, self.settings, self.native)
        (self.native / "stderr.txt").write_text("")
        self.assertEqual(verify("minced", self.sample, self.settings, self.native)["interpretation"], "not_detected")

    def test_mlst_no_matching_scheme_is_completed_inconclusive(self):
        (self.native / "mlst.json").write_text(json.dumps([{
            "filename": self.sample["assembly"], "id": "fixture", "scheme": "-",
            "sequence_type": "-", "alleles": None}]))
        (self.native / "mlst.tsv").write_text("FILE\tSCHEME\tST\tSTATUS\tSCORE\tALLELES\nfixture\t-\t-\tNONE\t0\t\n")
        payload = verify("mlst", self.sample, self.settings, self.native)
        self.assertEqual(payload["interpretation"], "inconclusive")
        self.assertEqual(payload["metrics"]["typing_status"], "no_matching_scheme")

    def test_shell_quotes_paths_and_retains_failure_log(self):
        native = self.root / "space and ' quote"
        executable = self.root / "tool ' space"
        executable.write_text("#!/bin/sh\nprintf 'synthetic failure\\n' >&2\nexit 7\n")
        executable.chmod(0o755)
        body = command_shell("vfdb", self.sample, {**self.settings, "executable": str(executable)}, native, 1)
        result = subprocess.run(["bash", "-euo", "pipefail", "-c", body], capture_output=True, text=True)
        self.assertEqual(result.returncode, 7)
        self.assertIn("synthetic failure", result.stdout)
        self.assertEqual((native / "stderr.txt").read_text(), "synthetic failure\n")

    def test_ice_scope_guard_has_no_default_taxon(self):
        with self.assertRaisesRegex(ValueError, "explicit supported phylum"):
            command_argv("icescreen", self.sample, {}, self.native, 1)


if __name__ == "__main__":
    unittest.main()
