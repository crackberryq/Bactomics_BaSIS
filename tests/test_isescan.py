"""ISEScan 1.7.3 output contracts using synthetic files, not biological runs."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety import native, native_execution, runtime

WARNING = "Warning: no significant hit with E-value <= 1e-10 found for "


def write_fixture(payload, assembly, *, translated=("1", "2", "3"), hits=("1",), terminal=False):
    """Reproduce upstream's warning inventory: raw-hit contigs, not all contigs."""
    payload, assembly = Path(payload), Path(assembly)
    proteins = payload / "output/proteome" / assembly.parent.name / (assembly.name + ".faa")
    proteins.parent.mkdir(parents=True, exist_ok=True)
    proteins.write_text("".join(f">{cid}_1_30_+\nMMMMMMMMMM\n" for cid in translated))
    lines = ["ISEScan starts at synthetic time",
             f"Translating genome into proteome for {assembly} , return  0"]
    searches = []
    for name, program, label in (("clusters.single.faa", "phmmer", "phmmer"),
                                 ("clusters.faa.hmm", "hmmsearch", "Profile HMM")):
        path = payload / "output/hmm" / assembly.parent.name / (name + "." + assembly.name + ".faa")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Program: {program}\n# Query file: /synthetic/{name}\n# Target file: {proteins}\n"
                        + "".join(f"{cid}_1_30_+ - fixture - 1e-4 20 0 1e-4 20 0 1 1 0 0 1 1 1 1 fixture\n"
                                  for cid in hits) + "# [ok]\n")
        lines.append(f"Finish {label} searching /synthetic/{name}  against {proteins} , output {path}")
        searches.append(path)
    lines.append("pred begins at synthetic time")
    if terminal:
        lines.append(f"No IS element was found for {[str(Path(assembly.parent.name) / assembly.name)]!r}")
    else:
        if not hits:
            lines.extend(f"Warning: no hit returned by HMM search in {path}" for path in searches)
        lines.extend(WARNING + cid for cid in sorted(hits))
        lines.append(WARNING + ",".join(sorted(hits)))
    lines.extend(["End in pred synthetic time", "Both complete and partial IS elements are reported.",
                  "ISEScan ends at synthetic time"])
    (payload / "stdout.txt").write_text("\n".join(lines) + "\n")
    (payload / "stderr.txt").write_text("")
    return proteins, searches


class ISEScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="isescan fixture's ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assembly = self.root / "annotation/fixture.fna"
        self.assembly.parent.mkdir()
        self.assembly.write_text("".join(f">{cid}\n" + "A" * 60 + "\n" for cid in ("1", "2", "3")))
        self.sample = {"sample_id": "fixture", "assembly": str(self.assembly)}
        self.payload = self.root / "data"
        self.proteins, self.searches = write_fixture(self.payload, self.assembly)

    def parse(self):
        return native.parse_payload("isescan", self.sample, {}, self.payload)

    def edit_stdout(self, before, after):
        path = self.payload / "stdout.txt"
        path.write_text(path.read_text().replace(before, after))

    def test_contigs_without_raw_hits_do_not_need_warnings(self):
        result = native.verify("isescan", self.sample, {}, self.payload)
        self.assertEqual(result["interpretation"], "not_detected")
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["metrics"]["insertion_sequence_count"], 0)
        self.assertEqual(result["metrics"]["no_hit_warning_contigs"], ["1"])
        self.assertEqual(result["metrics"]["assessed_contigs"], ["1", "2", "3"])
        record = native.normalized_record("isescan", self.sample, {"modules": {"isescan": {}}},
            {"input_hashes": {}, "protein_coordinates": {}, "contig_lengths": {c: 60 for c in ("1", "2", "3")}},
            self.payload)
        self.assertEqual(record["execution_status"], "completed")
        self.assertEqual(record["interpretation"], "not_detected")
        self.assertEqual(json.loads((self.payload / "parsed.json").read_text()), result)

    def test_no_raw_hits_in_either_completed_search_is_valid(self):
        write_fixture(self.payload, self.assembly, hits=())
        result = self.parse()
        self.assertEqual(result["interpretation"], "not_detected")
        self.assertEqual(result["metrics"]["no_hit_warning_contigs"], [])

    def test_all_contigs_with_nonsignificant_hits_remain_valid(self):
        write_fixture(self.payload, self.assembly, hits=("1", "2", "3"))
        self.assertEqual(self.parse()["interpretation"], "not_detected")

    def test_untranslated_contig_is_visible_without_failing_execution(self):
        write_fixture(self.payload, self.assembly, translated=("1", "2"))
        result = self.parse()
        self.assertTrue(result["scope_incomplete"])
        self.assertEqual(result["interpretation"], "inconclusive")
        self.assertEqual(result["metrics"]["assessed_contigs"], ["1", "2"])
        self.assertEqual(result["metrics"]["contigs_without_predicted_proteins"], ["3"])

    def test_missing_or_inconsistent_warning_cannot_be_a_negative(self):
        for replacement in ("", WARNING + "2\n", WARNING + "1,1\n", WARNING + "unknown\n"):
            with self.subTest(replacement=replacement):
                write_fixture(self.payload, self.assembly)
                self.edit_stdout(WARNING + "1\n" + WARNING + "1\n", WARNING + "1\n" + replacement)
                with self.assertRaises(ValueError):
                    self.parse()

    def test_incomplete_searches_are_not_negative(self):
        for path in self.searches:
            with self.subTest(path=path.name):
                write_fixture(self.payload, self.assembly)
                path.write_text(path.read_text().replace("# [ok]\n", ""))
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    self.parse()

    def test_wrong_search_provenance_is_not_negative(self):
        for before, after in ((str(self.proteins), "/other/proteome.faa"),
                              ("# Program: phmmer", "# Program: hmmsearch"),
                              ("# Query file: /synthetic/clusters.single.faa", "# Query file: /other/model")):
            with self.subTest(before=before):
                write_fixture(self.payload, self.assembly)
                path = self.searches[0]
                path.write_text(path.read_text().replace(before, after))
                with self.assertRaises(ValueError):
                    self.parse()

    def test_unreported_raw_hit_contig_is_not_negative(self):
        write_fixture(self.payload, self.assembly, hits=("1", "2"))
        self.edit_stdout(WARNING + "2\n", "")
        self.edit_stdout(WARNING + "1,2\n", WARNING + "1\n")
        with self.assertRaisesRegex(ValueError, "raw search hits"):
            self.parse()

    def test_significant_unknown_and_malformed_rows_are_not_negative(self):
        for before, after in (("1e-4 20 0 1e-4", "1e-4 20 0 1e-20"),
                              ("1_1_30_+ -", "unknown_1_30_+ -"),
                              ("1_1_30_+ - fixture - 1e-4 20 0 1e-4 20 0 1 1 0 0 1 1 1 1 fixture", "broken")):
            with self.subTest(after=after):
                write_fixture(self.payload, self.assembly)
                path = self.searches[0]
                path.write_text(path.read_text().replace(before, after))
                with self.assertRaises(ValueError):
                    self.parse()

    def test_unknown_or_invalid_generated_protein_is_not_negative(self):
        for identifier in ("unknown_1_30_+", "1_bad_30_+", "1_1_30_?", "1_1_99_+"):
            with self.subTest(identifier=identifier):
                write_fixture(self.payload, self.assembly)
                self.proteins.write_text(self.proteins.read_text().replace("1_1_30_+", identifier))
                with self.assertRaises(ValueError):
                    self.parse()

    def test_incomplete_or_conflicting_transcript_is_not_negative(self):
        for before, after in (("ISEScan ends at synthetic time\n", ""),
                              ("pred begins at synthetic time", "pred begins at synthetic time\nERROR synthetic"),
                              (str(self.assembly) + " , return  0", "/other/input.fna , return  0"),
                              ("ISEScan ends at synthetic time\n", "ISEScan ends at synthetic time\nTraceback synthetic\n")):
            with self.subTest(after=after):
                write_fixture(self.payload, self.assembly)
                self.edit_stdout(before, after)
                with self.assertRaises(ValueError):
                    self.parse()

    def test_empty_proteome_is_not_negative(self):
        self.proteins.write_text("")
        with self.assertRaises(ValueError):
            self.parse()

    def test_terminal_no_is_marker_remains_supported(self):
        write_fixture(self.payload, self.assembly, terminal=True)
        self.assertEqual(self.parse()["interpretation"], "not_detected")

    def test_native_table_findings_and_header_only_table_are_preserved(self):
        path = self.payload / "output/fixture.fna.tsv"
        header = "seqID\tfamily\tisBegin\tisEnd\tisLen\tstrand\n"
        path.write_text(header + "1\tfixture\t1\t30\t30\t+\n")
        self.assertEqual(self.parse()["metrics"]["insertion_sequence_count"], 1)
        path.write_text(header)
        self.assertEqual(self.parse()["interpretation"], "not_detected")

    def execution_fixture(self, returncode=0):
        tool = self.root / "synthetic-isescan"
        tool.write_text("#!" + sys.executable + "\n"
            "import pathlib, sys\n"
            f"sys.path.insert(0, {str(ROOT / 'tests')!r})\n"
            "from test_isescan import write_fixture\n"
            "root = pathlib.Path(__file__).parent\n"
            "with (root / 'invocations.txt').open('a') as handle: handle.write('invoked\\n')\n"
            "args = sys.argv[1:]\n"
            "assembly = pathlib.Path(args[args.index('--seqfile') + 1])\n"
            "payload = pathlib.Path(args[args.index('--output') + 1]).parent\n"
            "write_fixture(payload, assembly)\n"
            "sys.stdout.write((payload / 'stdout.txt').read_text())\n"
            f"raise SystemExit({returncode})\n")
        tool.chmod(0o755)
        prefix = self.root / "environment"
        prefix.mkdir()
        receipt = self.root / "environment.json"
        runtime.write_json(receipt, {"schema": "basis-snake-environment-v1", "status": "completed",
            "prefix": str(prefix), "packages": [{"name": "synthetic-fixture", "version": "1"}],
            "recipe_sha256": "fixture", "post_deploy_sha256": "fixture"})
        config = {"modules": {"isescan": {"enabled": True, "executable": str(tool),
                    "threads": 1, "cache": True, "timeout_seconds": 10}},
                  "environment_receipts": [str(receipt)]}
        return config

    def execute(self, config):
        with contextlib.redirect_stdout(io.StringIO()):
            return native_execution.execute("isescan", self.sample, config,
                self.root / "native/isescan", self.root / "completion/isescan.json", self.root / "cache/isescan",
                threads=1, sources=[ROOT / "workflow/vendor/basis/adapters/context.py"])

    def test_worker_accepts_zero_then_reuses_result_without_running_again(self):
        config = self.execution_fixture()
        record, reused = self.execute(config)
        self.assertFalse(reused)
        payload = Path(record["payload_root"])
        original = {str(path): path.read_bytes() for path in payload.rglob("*") if path.is_file()}
        self.assertEqual(json.loads((payload / "parsed.json").read_text())["interpretation"], "not_detected")
        second, reused = self.execute(config)
        self.assertTrue(reused)
        self.assertEqual(second["payload_root"], record["payload_root"])
        self.assertEqual((self.root / "invocations.txt").read_text(), "invoked\n")
        self.assertEqual(original, {str(path): path.read_bytes() for path in payload.rglob("*") if path.is_file()})

    def test_nonzero_process_exit_is_not_reclassified_as_zero_findings(self):
        config = self.execution_fixture(returncode=7)
        with self.assertRaisesRegex(RuntimeError, "exited 7"):
            self.execute(config)
        self.assertFalse((self.root / "completion/isescan.json").exists())
        failures = list((self.root / "cache/isescan").glob("*/attempt-*/failure.json"))
        self.assertEqual(len(failures), 1)
        commands = json.loads((failures[0].parent / "commands.json").read_text())
        self.assertEqual(commands[0]["returncode"], 7)


if __name__ == "__main__":
    unittest.main()
