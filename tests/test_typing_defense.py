"""Pinned MLST output consistency, unresolved typing, and native argv contract."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from basis.adapters.typing_defense import parse_mlst, run


class MlstTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assembly = self.root / "assembly with spaces.fna"
        self.assembly.write_text(">contig1\nACGTACGT\n")
        self.json = self.root / "mlst.json"
        self.tsv = self.root / "mlst.tsv"

    def tearDown(self):
        self.temp.cleanup()

    def output(self, scheme="saureus", st="239", alleles=None, status="PERFECT", score=100):
        if alleles is None and scheme != "-":
            alleles = {"arcC": "2", "aroE": "3"}
        row = {"id": "sample1", "filename": str(self.assembly), "scheme": scheme,
               "sequence_type": st, "alleles": alleles}
        self.json.write_text(json.dumps([row]))
        codes = ";".join(f"{key}({value})" for key, value in (alleles or {}).items())
        self.tsv.write_text("FILE\tSCHEME\tST\tSTATUS\tSCORE\tALLELES\n" +
                            f"sample1\t{scheme}\t{st}\t{status}\t{score}\t{codes}\n")

    def parse(self, **kwargs):
        return parse_mlst(self.json, self.tsv, self.assembly, "sample1", **kwargs)

    def test_assigned_st_is_typing_context(self):
        self.output()
        result = self.parse()
        self.assertEqual(result["metrics"]["typing_status"], "sequence_type_assigned")
        self.assertEqual(result["findings"][0]["category"], "typing")
        self.assertIsNone(result["findings"][0]["protein_id"])

    def test_no_scheme_is_completed_inconclusive_not_failed_or_negative(self):
        self.output(scheme="-", st="-", status="NONE", score=0)
        result = self.parse()
        self.assertEqual(result["interpretation"], "inconclusive")
        self.assertEqual(result["metrics"]["typing_status"], "no_matching_scheme")
        self.assertEqual(result["findings"], [])

    def test_partial_and_mixed_alleles_remain_unresolved(self):
        self.output(st="-", alleles={"arcC": "2,3", "aroE": "~17?"}, status="MIXED", score=70)
        result = self.parse()
        self.assertEqual(result["metrics"]["typing_status"], "sequence_type_unresolved")
        self.assertEqual(result["findings"][0]["attributes"]["alleles"]["aroE"], "~17?")

    def test_forced_scheme_without_alleles_is_distinct_from_no_scheme(self):
        self.output(st="-", alleles={"arcC": "-", "aroE": "-"}, status="NONE", score=0)
        self.assertEqual(self.parse(requested_scheme="saureus")["metrics"]["typing_status"], "sequence_type_unresolved")

    def test_missing_or_duplicate_native_rows_rejected(self):
        self.output()
        self.json.write_text("[]")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.parse()
        self.output()
        data = json.loads(self.json.read_text()); self.json.write_text(json.dumps(data + data))
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.parse()

    def test_identity_and_two_formats_must_agree(self):
        self.output()
        self.tsv.write_text(self.tsv.read_text().replace("sample1\t", "wrong\t"))
        with self.assertRaisesRegex(ValueError, "identity"):
            self.parse()
        self.output()
        self.tsv.write_text(self.tsv.read_text().replace("arcC(2)", "arcC(7)"))
        with self.assertRaisesRegex(ValueError, "allele observations"):
            self.parse()

    def test_invalid_score_and_status_cannot_pass(self):
        self.output(score="NaN")
        with self.assertRaisesRegex(ValueError, "score"):
            self.parse()
        self.output(st="-", status="PERFECT", score=90)
        with self.assertRaisesRegex(ValueError, "contradicts"):
            self.parse()

    def test_native_argv_explicit_snapshot_and_source_identity(self):
        database = self.root / "database with spaces"
        (database / "blast").mkdir(parents=True)
        (database / "pubmlst/test").mkdir(parents=True)
        (database / "pubmlst/test/test.txt").write_text("ST\tx\n1\t1\n")
        for suffix in ("nhr", "nin", "nsq"):
            (database / ("blast/mlst.fa." + suffix)).write_bytes(b"native index fixture")
        outer = self
        class Runner:
            def __init__(self): self.commands = []
            def run(self, argv, cwd):
                self.commands.append(argv)
                if argv[-1] == "--version":
                    return subprocess.CompletedProcess(argv, 0, "mlst 2.35.0\n")
                if argv[0] == "blastdbcmd":
                    return subprocess.CompletedProcess(argv, 0, "Database: PubMLST\n1 sequences; 4 total bases\n")
                outer.output()
                Path(argv[argv.index("--json") + 1]).write_bytes(outer.json.read_bytes())
                Path(argv[argv.index("--outfile") + 1]).write_bytes(outer.tsv.read_bytes())
                return subprocess.CompletedProcess(argv, 0, "")
        runner = Runner()
        result = run("mlst", {"assembly": str(self.assembly), "sample_id": "sample1"},
                     {"database": str(database), "threads": 3}, self.root, runner)
        self.assertEqual(result["metrics"]["sequence_type"], "239")
        self.assertIn(["blastdbcmd", "-db", str(database / "blast/mlst.fa"), "-dbtype", "nucl", "-info"],
                      runner.commands)
        self.assertFalse((database / "blast/mlst.fa").exists())
        self.assertIn(str(database / "pubmlst"), runner.commands[-1])
        self.assertIn(str(database / "blast/mlst.fa"), runner.commands[-1])
        self.assertEqual(runner.commands[-1][-1], str(self.assembly))
        with self.assertRaises(FileExistsError):
            run("mlst", {"assembly": str(self.assembly), "sample_id": "sample1"},
                {"database": str(database)}, self.root, runner)


if __name__ == "__main__": unittest.main()
