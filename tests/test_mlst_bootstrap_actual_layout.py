"""The upstream 2.35.0 bundle contains BLAST indices, not blast/mlst.fa.

The filenames match the pinned upstream tree. Native command results are test
boundaries; these tests do not claim to execute BLAST or validate biology.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from basis import bootstrap, bootstrap_typing_defense
from basis.adapters.typing_defense import run as run_mlst
from basis.config import default_config
from basis.util import sha256_file


# https://github.com/tseemann/mlst/tree/v2.35.0/db/blast
UPSTREAM_INDICES = ("ndb", "nhd", "nhi", "nhr", "nin", "njs", "nog", "nos", "not", "nsq", "ntf", "nto")


class NativeBoundaryFixture:
    def __init__(self, source, *, probe_error=None, probe_returncode=0):
        self.source = source
        self.probe_error = probe_error
        self.probe_returncode = probe_returncode
        self.commands = []

    def run(self, argv, cwd):
        self.commands.append(argv)
        if argv == ["mlst", "--version"]:
            return subprocess.CompletedProcess(argv, 0, "mlst 2.35.0\n")
        if argv[0] == "perl":
            return subprocess.CompletedProcess(argv, 0, str(self.source) + "\n")
        if argv[0] != "blastdbcmd" or argv[1] != "-db" or argv[3:] != ["-dbtype", "nucl", "-info"]:
            raise AssertionError("Unexpected native command: " + repr(argv))
        if not Path(argv[2]).is_absolute():
            raise AssertionError("BLAST probe must use an absolute prefix")
        if self.probe_error:
            raise self.probe_error
        return subprocess.CompletedProcess(argv, self.probe_returncode,
                                           "Database: PubMLST\n1 sequences; 4 total bases\n")


class MlstActualLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "native installation/db"
        (self.source / "blast").mkdir(parents=True)
        (self.source / "pubmlst/test").mkdir(parents=True)
        (self.source / "pubmlst/test/test.txt").write_text("ST\tx\n1\t1\n")
        (self.source / "pubmlst/test/x.tfa").write_text(">x_1\nACGT\n")
        (self.source / "pubmlst/test/test_info.json").write_text('{"source":"PubMLST fixture"}\n')
        (self.source / "VERSION.txt").write_text("snapshot fixture\n")
        for suffix in UPSTREAM_INDICES:
            (self.source / ("blast/mlst.fa." + suffix)).write_bytes(b"native boundary fixture " + suffix.encode())
        self.destination = self.root / "prepared snapshot"

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, runner=None):
        return bootstrap_typing_defense.prepare_resource({"provider": "mlst"}, self.destination,
                                                        runner or NativeBoundaryFixture(self.source))

    def test_complete_compiled_bundle_is_copied_probed_and_hashed_without_a_fasta(self):
        runner = NativeBoundaryFixture(self.source)
        result = self.prepare(runner)
        self.assertFalse((self.source / "blast/mlst.fa").exists())
        self.assertFalse((self.destination / "db/blast/mlst.fa").exists())
        self.assertEqual(runner.commands[-1], ["blastdbcmd", "-db", str(self.destination / "db/blast/mlst.fa"),
                                              "-dbtype", "nucl", "-info"])
        expected = {str(path.relative_to(self.source)): sha256_file(path)
                    for path in self.source.rglob("*") if path.is_file()}
        saved = json.loads((self.destination / "provenance.json").read_text())
        self.assertEqual(saved["sha256"], expected)
        self.assertEqual(result["provenance"], saved)
        self.assertEqual(saved["source_path"], str(self.source))
        self.assertEqual(saved["source_url"], "https://github.com/tseemann/mlst/tree/v2.35.0/db")
        self.assertEqual(saved["software_license"], "GPL-2.0")
        self.assertEqual(saved["runtime_checks"]["blast_database"], "blastdbcmd_info")
        self.assertEqual(result["module_updates"]["mlst"]["database"], str(self.destination / "db"))
        self.assertEqual(result["module_updates"]["mlst"]["metadata"], str(self.destination / "provenance.json"))
        for relative in expected:
            self.assertEqual((self.destination / "db" / relative).read_bytes(),
                             (self.source / relative).read_bytes())
        self.assertEqual([cmd[0] for cmd in runner.commands], ["mlst", "perl", "blastdbcmd"])

    def test_each_missing_or_empty_core_index_prevents_publication(self):
        for suffix in ("nhr", "nin", "nsq"):
            path = self.source / ("blast/mlst.fa." + suffix)
            contents = path.read_bytes()
            for state in ("missing", "empty"):
                with self.subTest(suffix=suffix, state=state):
                    if state == "missing":
                        path.unlink()
                    else:
                        path.write_bytes(b"")
                    runner = NativeBoundaryFixture(self.source)
                    with self.assertRaisesRegex(ValueError, "nonempty BLAST index"):
                        self.prepare(runner)
                    self.assertFalse((self.destination / "provenance.json").exists())
                    self.assertNotIn("blastdbcmd", [cmd[0] for cmd in runner.commands])
                    shutil.rmtree(self.destination)
                    path.write_bytes(contents)

    def test_raw_fasta_without_compiled_indices_is_not_a_ready_database(self):
        for path in (self.source / "blast").iterdir():
            path.unlink()
        (self.source / "blast/mlst.fa").write_text(">test.x_1\nACGT\n")
        with self.assertRaisesRegex(ValueError, "BLAST index"):
            self.prepare()
        self.assertFalse((self.destination / "provenance.json").exists())

    def test_native_rejection_of_nonempty_corrupt_indices_prevents_publication(self):
        (self.source / "blast/mlst.fa.nin").write_bytes(b"not a BLAST index")
        runner = NativeBoundaryFixture(self.source, probe_error=RuntimeError("BLAST database error: corrupt index"))
        with self.assertRaisesRegex(RuntimeError, "corrupt index"):
            self.prepare(runner)
        self.assertEqual(runner.commands[-1][0], "blastdbcmd")
        self.assertFalse((self.destination / "provenance.json").exists())

    def test_unsuccessful_probe_result_is_rejected_even_with_an_unchecked_runner(self):
        with self.assertRaisesRegex(ValueError, "could not be opened"):
            self.prepare(NativeBoundaryFixture(self.source, probe_returncode=1))
        self.assertFalse((self.destination / "provenance.json").exists())

    def test_empty_or_misnamed_scheme_profiles_are_rejected(self):
        profile = self.source / "pubmlst/test/test.txt"
        profile.write_text("")
        with self.assertRaisesRegex(ValueError, "scheme definitions"):
            self.prepare()
        self.assertFalse((self.destination / "provenance.json").exists())
        shutil.rmtree(self.destination)
        profile.write_text("ST\tx\n1\t1\n")
        profile.rename(profile.with_name("readme.txt"))
        with self.assertRaisesRegex(ValueError, "scheme definitions"):
            self.prepare()
        self.assertFalse((self.destination / "provenance.json").exists())

    def test_adapter_refuses_native_blast_probe_failure_before_typing(self):
        assembly = self.root / "sample.fna"
        assembly.write_text(">contig1\nACGT\n")
        runner = NativeBoundaryFixture(self.source, probe_error=RuntimeError("BLAST database error: corrupt index"))
        with self.assertRaisesRegex(RuntimeError, "corrupt index"):
            run_mlst("mlst", {"assembly": str(assembly), "sample_id": "sample1"},
                     {"database": str(self.source)}, self.root, runner)
        self.assertEqual([cmd[0] for cmd in runner.commands], ["blastdbcmd"])
        self.assertFalse((self.root / "native").exists())

    def test_shared_validator_change_invalidates_cache_and_prepared_plan(self):
        config = default_config()
        config["outdir"] = str(self.root / "results")
        config["resources"] = {"policy": "auto", "directory": str(self.root / "cache")}
        config["execution"]["conda_prefix"] = str(self.root / "environments")
        for name, module in config["modules"].items():
            module["enabled"] = name == "mlst"
        resolved, original = bootstrap.plan_auto_resources(config)
        self.assertEqual(len(original), 1)
        plan = {"plan_hash": "test-plan", "source_hash": "test-source", "config": resolved,
                "options": {"deep": False, "extend": False}, "outdir": config["outdir"],
                "bootstrap_resources": original}

        def changed_helper(path):
            return "f" * 64 if Path(path).name == "mlst_database.py" else sha256_file(path)

        with patch.object(bootstrap, "sha256_file", side_effect=changed_helper):
            _, updated = bootstrap.plan_auto_resources(config)
            receipt = bootstrap.run_resource(plan, self.root / "run", "mlst")
        self.assertNotEqual(original[0]["provider_source_sha256"], updated[0]["provider_source_sha256"])
        self.assertNotEqual(original[0]["cache_key"], updated[0]["cache_key"])
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("Resource provider source changed after planning", receipt["error"])


if __name__ == "__main__":
    unittest.main()
