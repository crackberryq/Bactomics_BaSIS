"""Synthetic saved-output and process-status tests; no biological tools run."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety import integron, native, result_store, runtime


def write_payload(payload, *, partial=True, positive=False):
    payload = Path(payload)
    output = payload / "output/Results_Integron_Finder_fixture"
    output.mkdir(parents=True, exist_ok=True)
    (output / "fixture.summary").write_text("ID_replicon\tCALIN\tcomplete\tIn0\n"
        + ("1\t1\t0\t0\n" if positive else "1\t0\t0\t0\n")
        + ("" if partial else "4117\t0\t0\t0\n"))
    (output / "fixture.integrons").write_text(
        "ID_integron\tID_replicon\telement\tpos_beg\tpos_end\tstrand\ttype\n"
        "fixture\t1\tattC\t1\t10\t+\tCALIN\n" if positive else "# No Integron found\n")
    warning = (f"WARNING  :  The protein file: '{output}/tmp_4117/4117.prt' is empty cannot perform hmmsearch on it.\n"
               "WARNING  :  ############ Skip replicon 4117 ############\n")
    (payload / "stdout.txt").write_text(
        "INFO : ############ Processing replicon 1 (1/2) ############\n"
        "INFO : Writing out results for replicon 1\n"
        "INFO : ############ Processing replicon 4117 (2/2) ############\n"
        + (warning if partial else "INFO : Writing out results for replicon 4117\n")
        + "INFO : Merging integrons results.\n")
    (payload / "stderr.txt").write_text("")


def make_fixture(root):
    root = Path(root)
    assembly = root / "input.fna"
    assembly.write_text(">1\n" + "A" * 60 + "\n>4117\n" + "A" * 60 + "\n")
    tool = root / "fixture-tool"
    tool.write_text("#!" + sys.executable + "\n" +
        "import pathlib, sys\n" +
        f"sys.path[:0] = {[str(ROOT / 'tests'), str(ROOT / 'workflow/lib'), str(ROOT / 'workflow/vendor')]!r}\n" +
        "from test_integron import write_payload\n" +
        "args = sys.argv[1:]\n"
        "p = pathlib.Path(args[args.index('--outdir') + 1]).parent\n"
        "write_payload(p)\n"
        "sys.stdout.write((p / 'stdout.txt').read_text())\n")
    tool.chmod(0o755)
    prefix = root / "environment"
    prefix.mkdir()
    environment = root / "environment.json"
    runtime.write_json(environment, {"schema": "basis-snake-environment-v1", "status": "completed",
        "prefix": str(prefix), "packages": [{"name": "synthetic-fixture", "version": "1"}],
        "recipe_sha256": "fixture", "post_deploy_sha256": "fixture"})
    sample = {"sample_id": "fixture", "assembly": str(assembly)}
    config = {"modules": {"integronfinder": {"enabled": True, "executable": str(tool),
        "threads": 1, "cache": True, "timeout_seconds": 10}},
        "environment_receipts": [str(environment)]}
    return sample, config


class IntegronTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="integron fixture's ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sample, self.config = make_fixture(self.root)
        self.payload = self.root / "data"
        write_payload(self.payload)

    def parse(self):
        return integron.parse_payload(self.sample, {}, self.payload)

    def test_explicit_skip_is_unassessed_and_not_a_negative(self):
        parsed = integron.verify(self.sample, {}, self.payload)
        self.assertTrue(parsed["scope_incomplete"])
        self.assertEqual(parsed["interpretation"], "inconclusive")
        coverage = parsed["metrics"]["contig_assessment"]
        self.assertEqual((coverage["input_contigs"], coverage["assessed_contigs"], coverage["not_assessed_contigs"]), (2, 1, 1))
        self.assertEqual(coverage["unassessed"][0]["contig"], "4117")
        self.assertEqual(coverage["unassessed"][0]["execution_status"], "not_assessed")
        record = native.normalized_record("integronfinder", self.sample, self.config,
            {"input_hashes": {}, "protein_coordinates": {}, "contig_lengths": {"1": 60, "4117": 60}}, self.payload)
        self.assertTrue(record["scope_incomplete"])
        self.assertEqual(record["execution_status"], "completed")
        self.assertEqual(record["interpretation"], "inconclusive")

    def test_complete_output_and_positive_partial_output_retain_findings(self):
        write_payload(self.payload, partial=False)
        self.assertFalse(self.parse()["scope_incomplete"])
        write_payload(self.payload, positive=True)
        parsed = self.parse()
        self.assertTrue(parsed["scope_incomplete"])
        self.assertEqual(len(parsed["findings"]), 1)
        self.assertNotEqual(parsed["interpretation"], "not_detected")

    def test_bad_diagnostics_and_unexplained_omissions_are_rejected(self):
        original = (self.payload / "stdout.txt").read_text()
        for broken in (
            original.replace("WARNING  :  ############ Skip replicon 4117 ############\n", ""),
            original.replace("is empty cannot perform hmmsearch on it.", "is missing."),
            original.replace("tmp_4117/4117.prt", "tmp_other/4117.prt"),
            original.replace("Skip replicon 4117", "Skip replicon unknown"),
            original.replace("(2/2)", "(1/2)"),
            original.replace("Writing out results for replicon 1", "Writing out results for replicon unknown"),
            original + "ERROR : synthetic error\n",
            original + "WARNING : Skipping unsupported record\n",
            original + "INFO : Writing out results for replicon 4117\n",
        ):
            with self.subTest(broken=broken):
                (self.payload / "stdout.txt").write_text(broken)
                with self.assertRaises(ValueError):
                    self.parse()

    def test_summary_and_log_duplicates_or_unexpected_ids_are_rejected(self):
        summary = next(self.payload.rglob("*.summary"))
        original = summary.read_text()
        for extra in ("1\t0\t0\t0\n", "unknown\t0\t0\t0\n", "4117\t0\t0\t0\n"):
            summary.write_text(original + extra)
            with self.assertRaises(ValueError):
                self.parse()

    def test_worker_reuses_completed_payload_without_launching(self):
        paths = [self.root / name for name in ("native", "receipt.json", "store")]
        with contextlib.redirect_stdout(io.StringIO()):
            first, reused = integron.execute(self.sample, self.config, *paths, threads=1,
                                            sources=[integron.__file__])
            self.assertFalse(reused)
            paths[1].unlink()
            with patch.object(runtime.DirectRunner, "run", side_effect=AssertionError("unexpected launch")):
                second, reused = integron.execute(self.sample, self.config, *paths, threads=1,
                                                 sources=[integron.__file__])
        self.assertTrue(reused)
        self.assertEqual(first, second)
        result_store.verify_receipt(paths[1], paths[0])


if __name__ == "__main__":
    unittest.main()
