"""MinCED 0.4.2 fixtures and fail-closed native execution checks."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from basis.adapters.minced import parse_minced, run


REPEAT = "AGTTCACTGCCGTACAGGCAGCTTAGAAA"
GFF = """##gff-version 3
ctg1\tminced:0.4.2\trepeat_region\t200\t408\t4\t.\t.\tID=CRISPR1;rpt_type=direct;rpt_family=CRISPR;rpt_unit_seq=AGTTCACTGCCGTACAGGCAGCTTAGAAA
ctg1\tminced:0.4.2\trepeat_unit\t200\t228\t1\t.\t.\tParent=CRISPR1;ID=DR.CRISPR1.1
ctg1\tminced:0.4.2\trepeat_unit\t260\t288\t1\t.\t.\tParent=CRISPR1;ID=DR.CRISPR1.2
ctg1\tminced:0.4.2\trepeat_unit\t320\t348\t1\t.\t.\tParent=CRISPR1;ID=DR.CRISPR1.3
ctg1\tminced:0.4.2\trepeat_unit\t380\t408\t1\t.\t.\tParent=CRISPR1;ID=DR.CRISPR1.4
##gff-version 3
"""
TEXT = """CRISPR 1   Range: 200 - 408
POSITION\tREPEAT\t\t\t\tSPACER
--------\t-----------------------------\t-------------------------------
200\t\tAGTTCACTGCCGTACAGGCAGCTTAGAAA\tCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC\t[ 29, 31 ]
260\t\tAGTTCACTGCCGTACAGGCAGCTTAGAAA\tCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC\t[ 29, 31 ]
320\t\tAGTTCACTGCCGTACAGGCAGCTTAGAAA\tCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC\t[ 29, 31 ]
380\t\tAGTTCACTGCCGTACAGGCAGCTTAGAAA\t
--------\t-----------------------------\t-------------------------------
Repeats: 4\tAverage Length: 29\t\tAverage Length: 31

"""


class MincedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.assembly = self.root / "assembly.fna"
        seq = "A" * 199 + (REPEAT + "C" * 31) * 3 + REPEAT + "A" * 200
        self.assembly.write_text(f">ctg1 description\n{seq}\n>ctg2\n{'T' * 900}\n")
        self.gff = self.root / "arrays.gff"
        self.txt = self.root / "arrays.txt"
        self.gff.write_text(GFF)
        self.txt.write_text(TEXT)

    def tearDown(self):
        self.tmp.cleanup()

    def parse(self):
        return parse_minced(self.gff, self.txt, self.assembly)

    def test_positive_coordinates_repeat_units_and_no_cas_claim(self):
        result = self.parse()
        f = result["findings"][0]
        self.assertEqual((f["contig"], f["start"], f["end"], f["strand"]), ("ctg1", 200, 408, None))
        self.assertEqual(f["attributes"]["repeat_count"], 4)
        self.assertEqual(f["attributes"]["spacer_count"], 3)
        self.assertEqual(f["attributes"]["cas_assessment"], "not_assessed")
        self.assertEqual(result["metrics"]["assessed_contigs"], ["ctg1", "ctg2"])

    def test_completed_header_only_is_negative_for_all_contigs(self):
        self.gff.write_text("##gff-version 3\n##gff-version 3\n")
        self.txt.write_text("")
        r = self.parse()
        self.assertEqual(r["interpretation"], "not_detected")
        self.assertEqual(r["metrics"]["gff_contig_blocks"], 2)

    def test_empty_gff_is_failure(self):
        self.gff.write_text("")
        with self.assertRaisesRegex(ValueError, "empty"):
            self.parse()

    def test_missing_file_is_failure(self):
        self.txt.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.parse()

    def test_truncated_contig_output_is_failure(self):
        self.gff.write_text(GFF.rsplit("##gff-version 3", 1)[0])
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.parse()

    def test_contig_order_and_identity_are_checked(self):
        self.gff.write_text(GFF.replace("ctg1", "ctg2"))
        with self.assertRaisesRegex(ValueError, "order/identity"):
            self.parse()

    def test_duplicate_id_is_failure(self):
        self.gff.write_text(GFF.replace("DR.CRISPR1.2", "DR.CRISPR1.1"))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.parse()

    def test_missing_repeat_unit_is_failure(self):
        self.gff.write_text("\n".join(line for line in GFF.splitlines() if "DR.CRISPR1.4" not in line))
        with self.assertRaisesRegex(ValueError, "missing repeat"):
            self.parse()

    def test_bounds_and_parent_are_checked(self):
        for modified in (GFF.replace("\t380\t408\t1", "\t381\t409\t1"),
                         GFF.replace("Parent=CRISPR1", "Parent=CRISPR9")):
            with self.subTest(gff=modified):
                self.gff.write_text(modified)
                with self.assertRaises(ValueError):
                    self.parse()

    def test_native_version_change_is_failure(self):
        self.gff.write_text(GFF.replace("0.4.2", "0.5.0"))
        with self.assertRaisesRegex(ValueError, "version"):
            self.parse()

    def test_text_summary_disagreement_is_failure(self):
        self.txt.write_text(TEXT.replace("Repeats: 4", "Repeats: 5"))
        with self.assertRaisesRegex(ValueError, "disagree"):
            self.parse()

    def test_repeat_sequence_is_checked_against_input(self):
        self.gff.write_text(GFF.replace("rpt_unit_seq=A", "rpt_unit_seq=C"))
        with self.assertRaisesRegex(ValueError, "input assembly"):
            self.parse()

    def test_header_only_gff_requires_empty_text_report(self):
        self.gff.write_text("##gff-version 3\n##gff-version 3\n")
        self.txt.write_text("Interrupted")
        with self.assertRaisesRegex(ValueError, "Unexpected nonempty"):
            self.parse()

    def test_html_error_page_rejected(self):
        self.gff.write_text("<!doctype html><html>Gateway failure</html>")
        with self.assertRaisesRegex(ValueError, "HTML"):
            self.parse()

    def test_runner_detects_swallowed_native_exception(self):
        owner = self
        class Runner:
            logfile = owner.root / "commands.log"
            def run(self, argv, cwd):
                if argv[-1] == "--version":
                    self.logfile.touch()
                    return subprocess.CompletedProcess(argv, 0, "minced 0.4.2\n", "")
                Path(argv[-1]).write_text("##gff-version 3\n##gff-version 3\n")
                Path(argv[-2]).write_text("")
                with self.logfile.open("a") as fh:
                    fh.write("--Error writing to file--\njava.io.IOException: disk full\n")
                return subprocess.CompletedProcess(argv, 0, "", "")
        with self.assertRaisesRegex(ValueError, "despite successful exit"):
            run("minced", {"assembly": str(self.assembly)}, {}, self.root, Runner())

    def test_runner_accepts_complete_native_and_ignores_old_log_error(self):
        owner = self
        class Runner:
            logfile = owner.root / "commands.log"
            def run(self, argv, cwd):
                if argv[-1] == "--version":
                    self.logfile.write_text("Old unrelated Error\n")
                    return subprocess.CompletedProcess(argv, 0, "minced 0.4.2\n", "")
                owner.assertEqual(argv[1], "-gffFull")
                Path(argv[-1]).write_text(GFF)
                Path(argv[-2]).write_text(TEXT)
                return subprocess.CompletedProcess(argv, 0, "", "")
        result = run("minced", {"assembly": str(self.assembly)}, {}, self.root, Runner())
        self.assertEqual(result["metrics"]["array_count"], 1)
        self.assertEqual(result["metrics"]["completion_check"], "exit_log_and_all_contig_blocks_verified")


if __name__ == "__main__":
    unittest.main()
