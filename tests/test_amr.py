"""Schema/command-contract tests with synthetic native-style output, not tool integration."""
import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from basis.adapters.amr import (
    DIAMOND_COLUMNS, OutputError, parse_amrfinder, parse_diamond, parse_rgi, run,
)


AMR_HEADER = [
    "Protein id", "Contig id", "Start", "Stop", "Strand", "Element symbol", "Element name",
    "Scope", "Type", "Subtype", "Class", "Subclass", "Method", "Target length",
    "Reference sequence length", "% Coverage of reference", "% Identity to reference",
    "Alignment length", "Closest reference accession", "Closest reference name", "HMM accession", "HMM description",
]
AMR_ROW = [
    "TEST_0001", "contig1", "101", "961", "+", "testA", "synthetic AMR model", "core", "AMR", "AMR",
    "TEST", "TEST", "ALLELEP", "286", "286", "100.00", "100.00", "286", "WP_TEST.1",
    "synthetic reference", "NF_TEST.1", "synthetic family",
]
RGI_HEADER = [
    "ORF_ID", "Contig", "Start", "Stop", "Orientation", "Cut_Off", "Pass_Bitscore", "Best_Hit_Bitscore",
    "Best_Hit_ARO", "Best_Identities", "ARO", "Model_type", "SNPs_in_Best_Hit_ARO", "Other_SNPs",
    "Drug Class", "Resistance Mechanism", "AMR Gene Family", "Predicted_DNA", "Predicted_Protein",
    "CARD_Protein_Sequence", "Percentage Length of Reference Sequence", "ID", "Model_ID", "Nudged",
    "Note", "Hit_Start", "Hit_End", "Antibiotic",
]
RGI_ROW = [
    "TEST_0001", "", "", "", "", "Strict", "50", "55", "testA", "85.0", "3000000",
    "protein homolog model", "n/a", "n/a", "test class", "test mechanism", "test family", "",
    "MMMMMMMMMMMM", "MMMMMMMMMM", "120.0", "hsp:0", "100", "FALSE", "", "1", "8", "test drug",
]
DIAMOND_ROW = [
    "TEST_0001", "REFERENCE1", "85", "80", "100", "100", "1", "80", "1", "80", "1e-30", "100", "80", "80", "synthetic reference title",
]


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "output.tsv"

    def table(self, header, rows):
        with self.path.open("w", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)
        return self.path

    def test_amrfinder_preserves_type_method_and_raw(self):
        rows = [AMR_ROW.copy() for _ in range(3)]
        for row, category in zip(rows, ["AMR", "VIRULENCE", "STRESS"]):
            row[8] = category
            row[9] = category
        rows[2][7] = "plus"
        result = parse_amrfinder(self.table(AMR_HEADER, rows))
        self.assertEqual([x["category"] for x in result], ["amr", "virulence", "stress"])
        self.assertEqual(result[0]["attributes"]["raw"]["Method"], "ALLELEP")
        self.assertEqual((result[0]["start"], result[0]["end"]), (101, 961))

    def test_amrfinder_legacy_header_and_hmm_unknown_identity(self):
        header = AMR_HEADER.copy()
        header[5], header[6] = "Gene symbol", "Sequence name"
        header[8], header[9] = "Element type", "Element subtype"
        header[18] = "Accession of closest sequence"
        row = AMR_ROW.copy()
        row[12], row[15], row[16] = "HMM", "-", "-"
        result = parse_amrfinder(self.table(header, [row]))[0]
        self.assertIsNone(result["identity"])
        self.assertIn("family_hmm_only", result["attributes"]["flags"])

    def test_amrfinder_headers_only_is_valid_no_hit(self):
        self.assertEqual(parse_amrfinder(self.table(AMR_HEADER, [])), [])

    def test_amrfinder_missing_empty_bad_header_bad_row_are_errors(self):
        with self.assertRaises(OutputError):
            parse_amrfinder(self.path)
        self.path.write_text("")
        with self.assertRaises(OutputError):
            parse_amrfinder(self.path)
        with self.assertRaises(OutputError):
            parse_amrfinder(self.table(["wrong"], []))
        with self.assertRaises(OutputError):
            parse_amrfinder(self.table(AMR_HEADER, [AMR_ROW[:-1]]))

    def test_amrfinder_invalid_numbers_and_new_category_fail_closed(self):
        for index, value in [(2, "0"), (16, "NaN"), (16, "101"), (8, "UNKNOWN")]:
            row = AMR_ROW.copy()
            row[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(OutputError):
                parse_amrfinder(self.table(AMR_HEADER, [row]))

    def test_amrfinder_disruptive_variant_is_exploratory(self):
        row = AMR_ROW.copy()
        row[9] = "POINT_DISRUPT"
        self.assertEqual(parse_amrfinder(self.table(AMR_HEADER, [row]))[0]["evidence_level"], "exploratory")

    def test_rgi_perfect_strict_loose_and_nudged(self):
        rows = [RGI_ROW.copy() for _ in range(4)]
        for row, level in zip(rows, ["Perfect", "Strict", "Loose", "Strict"]):
            row[5] = level
        rows[3][23] = "TRUE"
        result = parse_rgi(self.table(RGI_HEADER, rows))
        self.assertEqual([x["evidence_level"] for x in result], ["curated_model", "curated_model", "exploratory", "exploratory"])
        self.assertEqual(result[0]["protein_id"], "TEST_0001")
        self.assertIsNone(result[0]["contig"])

    def test_rgi_length_ratio_is_not_coverage(self):
        result = parse_rgi(self.table(RGI_HEADER, [RGI_ROW]))[0]
        self.assertEqual(result["coverage"], 80.0)
        self.assertEqual(result["attributes"]["reference_length_ratio_percent"], 120.0)
        row = RGI_ROW.copy()
        row[25], row[26] = "", ""
        self.assertIsNone(parse_rgi(self.table(RGI_HEADER, [row]))[0]["coverage"])

    def test_rgi_contig_coordinates_and_no_invented_protein_id(self):
        row = RGI_ROW.copy()
        row[1:5] = ["contig1", "10", "309", "-"]
        result = parse_rgi(self.table(RGI_HEADER, [row]), "contig")[0]
        self.assertEqual((result["start"], result["end"], result["strand"]), (10, 309, "-"))
        self.assertIsNone(result["protein_id"])
        self.assertEqual(result["attributes"]["protein_sequence"], RGI_ROW[18])
        self.assertEqual(result["attributes"]["protein_sequence_source"], "native_RGI_Predicted_Protein")

    def test_rgi_predicted_protein_missing_and_invalid(self):
        row = RGI_ROW.copy()
        row[18] = ""
        self.assertIsNone(parse_rgi(self.table(RGI_HEADER, [row]))[0]["attributes"]["protein_sequence"])
        row[18] = "MALFORMED-1"
        with self.assertRaises(OutputError):
            parse_rgi(self.table(RGI_HEADER, [row]))

    def test_rgi_valid_no_hit_and_failure_cases(self):
        self.assertEqual(parse_rgi(self.table(RGI_HEADER, [])), [])
        for index, value in [(5, "UNKNOWN"), (9, "garbage"), (23, "maybe"), (26, "999")]:
            row = RGI_ROW.copy()
            row[index] = value
            with self.subTest(index=index), self.assertRaises(OutputError):
                parse_rgi(self.table(RGI_HEADER, [row]))
        with self.assertRaises(OutputError):
            parse_rgi(self.table(RGI_HEADER, [RGI_ROW]), "contig")

    def diamond(self, rows):
        self.path.write_text("".join("\t".join(row) + "\n" for row in rows))
        return self.path

    def test_diamond_both_categories_and_thresholds_preserved(self):
        for module, category in [("vfdb", "virulence"), ("bacmet", "stress")]:
            result = parse_diamond(self.diamond([DIAMOND_ROW]), module)[0]
            self.assertEqual(result["category"], category)
            self.assertTrue(result["attributes"]["passes_threshold"])
            self.assertEqual(result["attributes"]["raw"]["sseqid"], "REFERENCE1")
        result = parse_diamond(self.path, "vfdb", min_identity=90)[0]
        self.assertFalse(result["attributes"]["passes_threshold"])
        self.assertEqual(result["evidence_level"], "exploratory")

    def test_diamond_empty_missing_malformed(self):
        with self.assertRaises(OutputError):
            parse_diamond(self.path, "vfdb")
        self.assertEqual(parse_diamond(self.diamond([]), "vfdb"), [])
        for row in [DIAMOND_ROW[:-1], list(DIAMOND_COLUMNS)]:
            with self.assertRaises(OutputError):
                parse_diamond(self.diamond([row]), "vfdb")
        row = DIAMOND_ROW.copy()
        row[7] = "101"
        with self.assertRaises(OutputError):
            parse_diamond(self.diamond([row]), "vfdb")

    def test_diamond_duplicate_hits_are_not_silently_collapsed(self):
        self.assertEqual(len(parse_diamond(self.diamond([DIAMOND_ROW, DIAMOND_ROW]), "bacmet")), 2)


class FakeRunner:
    def __init__(self, callback=None, fail=False):
        self.calls = []
        self.callback = callback
        self.fail = fail

    def run(self, argv, stdout_path=None, cwd=None):
        self.calls.append((argv, cwd))
        if self.fail:
            raise RuntimeError("Simulated native process failure")
        if self.callback:
            self.callback(argv, cwd)
        return subprocess.CompletedProcess(argv, 0, "", "")


class AdapterContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sample = {"sample_id": "TEST", "assembly": "/in/a.fna", "proteins": "/in/a.faa", "gff": "/in/a.gff"}
        self.db = self.root / "db"
        self.db.mkdir()

    def test_amrfinder_combined_native_defaults_and_missing_output_error(self):
        def callback(argv, cwd):
            Path(argv[argv.index("-o") + 1]).write_text("\t".join(AMR_HEADER) + "\n")
        runner = FakeRunner(callback)
        result = run("amrfinder", self.sample, {"database": str(self.db)}, self.root / "run", runner)
        self.assertEqual(result["interpretation"], "not_detected")
        argv = runner.calls[0][0]
        for flag in ["-n", "-p", "-g", "--plus"]:
            self.assertIn(flag, argv)
        self.assertNotIn("--ident_min", argv)
        self.assertNotIn("--coverage_min", argv)
        with self.assertRaises(OutputError):
            run("amrfinder", self.sample, {"database": str(self.db)}, self.root / "missing", FakeRunner())

    def test_failed_command_and_stale_output_never_become_no_hit(self):
        with self.assertRaises(RuntimeError):
            run("amrfinder", self.sample, {"database": str(self.db)}, self.root / "failed", FakeRunner(fail=True))
        out = self.root / "stale"
        out.mkdir()
        (out / "amrfinder.tsv").write_text("\t".join(AMR_HEADER) + "\n")
        with self.assertRaises(RuntimeError):
            run("amrfinder", self.sample, {"database": str(self.db)}, out, FakeRunner())

    def test_rgi_local_load_and_loose_only_inconclusive(self):
        database = self.db / "card.json"
        database.write_text(json.dumps({"_version": "synthetic-test"}))
        def callback(argv, cwd):
            if argv[1] == "main":
                prefix = argv[argv.index("--output_file") + 1]
                row = RGI_ROW.copy()
                row[5] = "Loose"
                Path(prefix + ".txt").write_text("\t".join(RGI_HEADER) + "\n" + "\t".join(row) + "\n")
                Path(prefix + ".json").write_text("{}")
        runner = FakeRunner(callback)
        result = run("rgi", self.sample, {"database": str(database)}, self.root / "rgi", runner)
        self.assertEqual(result["interpretation"], "inconclusive")
        self.assertEqual(runner.calls[0][0][1], "load")
        self.assertIn("--local", runner.calls[1][0])
        self.assertIn("--include_loose", runner.calls[1][0])
        self.assertNotIn("--include_nudge", runner.calls[1][0])

    def test_diamond_exact_schema_and_native_zero_hit(self):
        database = self.db / "vfdb.dmnd"
        database.write_bytes(b"synthetic")
        def callback(argv, cwd):
            Path(argv[argv.index("--out") + 1]).write_text("")
        runner = FakeRunner(callback)
        result = run("vfdb", self.sample, {"database": str(database)}, self.root / "diamond", runner)
        self.assertEqual(result["interpretation"], "not_detected")
        argv = runner.calls[0][0]
        self.assertEqual(argv[argv.index("--outfmt") + 1:], ["6", *DIAMOND_COLUMNS])

    def test_rgi_string_false_rejected_before_native_execution(self):
        database = self.db / "card.json"
        database.write_text(json.dumps({"_version": "synthetic-test"}))
        runner = FakeRunner()
        with self.assertRaises(ValueError):
            run("rgi", self.sample, {"database": str(database), "include_loose": "false"}, self.root / "bad-config", runner)
        self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
