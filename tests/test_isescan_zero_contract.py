"""Synthetic ISEScan empty-result contract regressions; no native tools needed.

Run from the workspace with:
  PYTHONPATH=basis python -m unittest discover -s isescan36-work -p 'test_isescan_zero_contract.py' -v
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from basis.adapters.context import _isescan_native_zero


PREFIX = "Warning: no significant hit with E-value <= 1e-10 found for "


class NativeFixture:
    def __init__(self, root, contigs=("1", "2", "3")):
        self.contigs = tuple(contigs)
        self.assembly = root / "annotation" / "isolate6.fna"
        self.assembly.parent.mkdir(parents=True)
        self.assembly.write_text("".join(f">{cid}\nATGGCTGAATAA\n" for cid in contigs))
        self.native = root / "attempt" / "native"
        self.stdout_path = root / "attempt" / "isescan_stdout.txt"
        self.proteins = self.native / "proteome" / "annotation" / "isolate6.fna.faa"
        self.proteins.parent.mkdir(parents=True)
        self.proteins.write_text("".join(f">{cid}_1_9_+\nMAE\n" for cid in contigs))
        self.searches = []
        for name, program in (("clusters.single.faa", "phmmer"),
                              ("clusters.faa.hmm", "hmmsearch")):
            p = self.native / "hmm" / "annotation" / (name + ".isolate6.fna.faa")
            p.parent.mkdir(parents=True, exist_ok=True)
            self.searches.append(p)
            body = [
                "# target name accession query name accession full sequence and best domain",
                *[self.row(f"{cid}_1_9_+") for cid in contigs],
                f"# Program:         {program}",
                f"# Query file:      /synthetic/pHMMs/{name}",
                f"# Target file:     {self.proteins}",
                "# [ok]",
            ]
            p.write_text("\n".join(body) + "\n")
        self.lines = [
            "ISEScan starts at Sun Oct 11 23:52:06 2026",
            "Begin to translate genome into proteome.",
            f"Translating genome into proteome for {self.assembly} , return  0",
            "Finish translating genome into proteome. Sun Oct 11 23:52:07 2026",
            "Begin to phmmer search against proteome database. Sun Oct 11 23:52:07 2026",
            f"Finish phmmer searching /synthetic/pHMMs/clusters.single.faa  against {self.proteins} , output {self.searches[0]}",
            "Finish phmmer searching against proteome database. Sun Oct 11 23:52:46 2026",
            "Begin to profile HMM search against proteome database. Sun Oct 11 23:52:46 2026",
            f"Finish Profile HMM searching /synthetic/pHMMs/clusters.faa.hmm  against {self.proteins} , output {self.searches[1]}",
            "Finish profile HMM searching against proteome database. Sun Oct 11 23:53:16 2026",
            "pred begins at Sun Oct 11 23:53:16 2026",
            *[PREFIX + cid for cid in contigs],
            PREFIX + ",".join(contigs),
            "End in pred Sun Oct 11 23:53:16 2026",
            "Both complete and partial IS elements are reported.",
            "ISEScan ends at Sun Oct 11 23:53:16 2026",
        ]

    @staticmethod
    def row(protein_id, domain_evalue="1e-5"):
        return f"{protein_id} - cluster001 - 1e-6 25.0 0.0 {domain_evalue} 22.0 0.0 1.0 1 0 0 1 1 1 1 synthetic"

    def run(self):
        stdout = "\n".join(self.lines) + "\n"
        self.stdout_path.write_text(stdout)
        return _isescan_native_zero(self.native, self.assembly, stdout, self.stdout_path)

    def mutate_table(self, index, transform):
        path = self.searches[index]
        path.write_text(transform(path.read_text()))


class ISEScanZeroContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = NativeFixture(Path(self.temp.name))

    def test_complete_three_contig_native_early_return(self):
        f = self.fixture
        result = f.run()
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["interpretation"], "not_detected")
        self.assertEqual(result["metrics"]["insertion_sequence_count"], 0)
        self.assertEqual(result["metrics"]["assessed_contigs"], ["1", "2", "3"])
        self.assertEqual(result["metrics"]["assessed_contig_count"], 3)
        self.assertEqual(result["metrics"]["native_no_hit_format"],
                         "isescan_1.7.3_all_contigs_no_significant_hits")
        self.assertEqual(set(result["artifacts"]),
                         {str(f.stdout_path), str(f.proteins), *map(str, f.searches)})

    def test_single_contig_has_identical_individual_and_aggregate_warning(self):
        with TemporaryDirectory() as folder:
            f = NativeFixture(Path(folder), ("contig_with_underscores",))
            result = f.run()
            self.assertEqual(result["metrics"]["assessed_contigs"], ["contig_with_underscores"])

    def test_missing_contig_warning_is_not_negative(self):
        self.fixture.lines.remove(PREFIX + "2")
        with self.assertRaises(ValueError):
            self.fixture.run()

    def test_mixed_contig_warning_is_not_negative(self):
        f = self.fixture
        f.lines[f.lines.index(PREFIX + "2")] = PREFIX + "different_contig"
        with self.assertRaises(ValueError):
            f.run()

    def test_duplicate_individual_warning_is_rejected(self):
        f = self.fixture
        f.lines[f.lines.index(PREFIX + "2")] = PREFIX + "1"
        with self.assertRaises(ValueError):
            f.run()

    def test_incomplete_or_duplicate_aggregate_is_rejected(self):
        f = self.fixture
        original = list(f.lines)
        for aggregate in ("1,2", "1,2,2", "1,2,unknown"):
            with self.subTest(aggregate=aggregate):
                f.lines = [PREFIX + aggregate if line == PREFIX + "1,2,3" else line
                           for line in original]
                with self.assertRaises(ValueError):
                    f.run()

    def test_repeated_complete_warning_block_is_ambiguous(self):
        f = self.fixture
        at = f.lines.index(PREFIX + "1")
        f.lines[at:at] = [PREFIX + cid for cid in ("1", "2", "3", "1,2,3")]
        with self.assertRaises(ValueError):
            f.run()

    def test_missing_terminal_or_prediction_marker_is_rejected(self):
        f = self.fixture
        original = list(f.lines)
        for prefix in ("ISEScan starts at ", "pred begins at ", "End in pred ", "ISEScan ends at "):
            with self.subTest(marker=prefix):
                f.lines = [line for line in original if not line.startswith(prefix)]
                with self.assertRaises(ValueError):
                    f.run()

    def test_error_or_trailing_output_is_not_completed_negative(self):
        f = self.fixture
        original = list(f.lines)
        for error in ("ERROR processing genome", "Error: cannot open output", "Traceback (most recent call last):"):
            with self.subTest(error=error):
                f.lines = [*original[:-1], error, original[-1]]
                with self.assertRaises(ValueError):
                    f.run()
        f.lines = original + ["unexpected trailing data"]
        with self.assertRaises(ValueError):
            f.run()

    def test_search_completion_line_is_required_for_each_search(self):
        f = self.fixture
        original = list(f.lines)
        for prefix in ("Finish phmmer searching /", "Finish Profile HMM searching /"):
            with self.subTest(search=prefix):
                f.lines = [line for line in original if not line.startswith(prefix)]
                with self.assertRaises(ValueError):
                    f.run()

    def test_translation_success_must_identify_exact_input(self):
        f = self.fixture
        original = list(f.lines)
        for replacement in (str(f.assembly) + ".other", "wrong-input.fna"):
            with self.subTest(input=replacement):
                f.lines = [line.replace(str(f.assembly), replacement)
                           if line.startswith("Translating genome into proteome for ") else line
                           for line in original]
                with self.assertRaises(ValueError):
                    f.run()
        f.lines = [line.replace(" , return  0", " , return  1") for line in original]
        with self.assertRaises(ValueError):
            f.run()

    def test_empty_or_wrong_generated_proteome_is_rejected(self):
        f = self.fixture
        for data in ("", ">1_1_9_+\nMAE\n", ">unknown_1_9_+\nMAE\n", ">1\nMAE\n"):
            with self.subTest(proteome=data):
                f.proteins.write_text(data)
                with self.assertRaises(ValueError):
                    f.run()

    def test_search_footer_required_for_both_searches(self):
        f = self.fixture
        for index in (0, 1):
            with self.subTest(search=index):
                original = f.searches[index].read_text()
                f.mutate_table(index, lambda data: data.replace("# [ok]\n", ""))
                with self.assertRaises(ValueError):
                    f.run()
                f.searches[index].write_text(original)

    def test_wrong_or_duplicated_target_header_is_rejected(self):
        f = self.fixture
        original = f.searches[0].read_text()
        for altered in (original.replace(str(f.proteins), str(f.proteins) + ".other"),
                        original.replace("# [ok]", f"# Target file: {f.proteins}\n# [ok]")):
            with self.subTest(table=altered):
                f.searches[0].write_text(altered)
                with self.assertRaises(ValueError):
                    f.run()

    def test_program_and_query_database_binding_are_required(self):
        f = self.fixture
        original = f.searches[0].read_text()
        for altered in (original.replace("# Program:         phmmer", "# Program: hmmsearch"),
                        original.replace("/synthetic/pHMMs/clusters.single.faa", "/synthetic/pHMMs/wrong.faa")):
            with self.subTest(table=altered):
                f.searches[0].write_text(altered)
                with self.assertRaises(ValueError):
                    f.run()

    def test_significant_hit_contradicts_no_hit_transcript(self):
        f = self.fixture
        original = f.searches[0].read_text()
        for evalue in ("1e-12", "1e-10", "nan", "inf", "not-a-number", "-1"):
            with self.subTest(evalue=evalue):
                f.searches[0].write_text(original.replace(f.row("1_1_9_+"), f.row("1_1_9_+", evalue)))
                with self.assertRaises(ValueError):
                    f.run()

    def test_unknown_hit_target_or_malformed_row_is_rejected(self):
        f = self.fixture
        original = f.searches[0].read_text()
        for row in (f.row("unrelated_1_9_+"), "1_1_9_+ - truncated"):
            with self.subTest(row=row):
                f.searches[0].write_text(original.replace(f.row("1_1_9_+"), row))
                with self.assertRaises(ValueError):
                    f.run()

    def test_missing_raw_hit_contig_is_not_assessed_negative(self):
        f = self.fixture
        for index in (0, 1):
            f.mutate_table(index, lambda data: data.replace(f.row("3_1_9_+") + "\n", ""))
        with self.assertRaises(ValueError):
            f.run()

    def test_existing_terminal_zero_marker_remains_supported(self):
        f = self.fixture
        warnings = [index for index, line in enumerate(f.lines) if line.startswith(PREFIX)]
        f.lines[warnings[0]:warnings[-1] + 1] = ["No IS element was found for ['annotation/isolate6.fna']"]
        result = f.run()
        self.assertEqual(result["metrics"]["insertion_sequence_count"], 0)
        self.assertEqual(result["metrics"]["native_no_hit_format"], "isescan_1.7.3_terminal_marker")

    def test_wrong_existing_terminal_marker_not_accepted(self):
        f = self.fixture
        warnings = [index for index, line in enumerate(f.lines) if line.startswith(PREFIX)]
        f.lines[warnings[0]:warnings[-1] + 1] = ["No IS element was found for ['annotation/wrong.fna']"]
        with self.assertRaises(ValueError):
            f.run()


if __name__ == "__main__":
    unittest.main()
