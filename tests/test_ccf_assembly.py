"""Whole-assembly orchestration with synthetic artifacts; no biological tools."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety import ccf_assembly as assembly, resources, result_store, runtime


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def fixture_array():
    return {"Name": "synthetic-array", "Start": 2, "End": 4, "DR_Consensus": "A",
            "DR_Length": 1, "Spacers": 1, "Evidence_Level": 3,
            "Potential_Orientation": "+", "CRISPRDirection": "+",
            "Conservation_DRs": 100, "Conservation_Spacers": 0,
            "Regions": [{"Type": kind, "Start": n, "End": n, "Sequence": "A", "Leader": 0, "AT": 100}
                        for n, kind in enumerate(("LeftFLANK", "DR", "Spacer", "DR", "RightFLANK"), 1)]}


def fixture(work, sequences, *, cas_records=(), with_array=False):
    """Synthetic native schema with original relative MacSyFinder argv paths."""
    mapping = assembly.contig_map(sequences)
    runtime.write_json(work / "contig_map.json", mapping)
    write(work / "input.fna", "".join(f">{safe}\n{sequences[row['contig']]}\n" for safe, row in mapping.items()))
    write(work / "basis_ccf_launch.py", assembly.ASSEMBLY_LAUNCHER)
    write(work / "bin/macsyfinder", assembly.ASSEMBLY_MACSY_SHIM)
    write(work / "macsyfinder.conf", assembly._CONFIG)
    actual = work.parent / "synthetic-macsyfinder"
    write(actual, "# synthetic executable identity, never executed by this fixture\n")
    manifest = {"policy": assembly._POLICY, "python": sys.executable,
                "home_environment_unchanged": True, "working_directory": str(work),
                "macsyfinder_executable": str(actual), "macsyfinder_executable_sha256": result_store.sha256(actual),
                "launcher_sha256": result_store.sha256(work / "basis_ccf_launch.py"),
                "wrapper_sha256": result_store.sha256(work / "bin/macsyfinder"),
                "config_sha256": result_store.sha256(work / "macsyfinder.conf")}
    runtime.write_json(work / "runtime_isolation.json", manifest)
    records, stdout = [], []
    for index, (safe, row) in enumerate(mapping.items()):
        sequence = sequences[row["contig"]]
        arrays = [fixture_array()] if with_array and index == 0 else []
        records.append({"Id": safe, "Version": safe, "Length": len(sequence), "Crisprs": arrays, "Cas": []})
        write(work / f"native/rawFASTA/analyzedSequences/{safe}.fna", f">{safe}\n{sequence}\n")
        stdout.extend([f"  ( Input file: {safe}.fna, Sequence ID: {safe}, Sequence name =  )",
                       f"Nb of CRISPRs in this sequence = {len(arrays)}"])
        proteome = work / f"native/Prodigal/prodigal_{safe}/{safe}.faa"
        if safe not in cas_records:
            write(work / f"native/GFF/annotation_{safe}.gff", "##gff-version 3\n")
            write(proteome, "")
            stdout.append("No Cas search because no CDS was detected!")
            continue
        write(work / f"native/GFF/annotation_{safe}.gff", f"{safe}\tProdigal\tCDS\t1\t9\t.\t+\t0\tID=1_1\n")
        write(proteome, f">{safe}_1\nMMM\n")
        cas = work / f"native/CasFinder/casfinder_{safe}"
        write(cas / "macsyfinder.log", "synthetic completion\nEND\n")
        write(cas / "macsyfinder.conf", f"[general]\ndb_type = unordered\nreplicon_topology = circular\nsequence_db = {safe}.faa\n")
        write(cas / "all_systems.tsv", "# macsyfinder 2.0\n# models : CASFinder-3.1.0\n# No Likely Systems found\n")
        write(cas / "uncomplete_systems.txt", "# No Unlikely Systems found\n")
        runtime.write_json(work / f"macsyfinder-audits/{safe}.json", {
            "safe_id": safe, "exit_status": 0, "policy": assembly._POLICY,
            "macsyfinder_version": "2.0", "python": manifest["python"],
            "actual_executable": str(actual), "explicit_config": str(work / "macsyfinder.conf"),
            "wrapper_sha256": manifest["wrapper_sha256"], "config_sha256": manifest["config_sha256"],
            "sequence_db": str(work / f"native/prodigal_{safe}/{safe}.faa"),
            "out_dir": str(work / f"native/casfinder_{safe}"),
            "sequence_db_sha256": result_store.sha256(proteome),
            "argv": [str(actual), "--sequence-db", f"native/prodigal_{safe}/{safe}.faa",
                     "--out-dir", f"native/casfinder_{safe}/", "--cfg-file", str(work / "macsyfinder.conf")]})
    stdout.extend([f"All CRISPRs = {int(with_array)}", "Thank you for using CRISPRCasFinder.pl!"])
    write(work / "stdout.txt", "\n".join(stdout) + "\n")
    runtime.write_json(work / "native/result.json", {"Version": "4.3.2", "Sequences": records})
    return mapping


class AssemblyParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ccf assembly fixtures ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.sequences = {"original|one": "A" * 12, "second": "A" * 16, "third": "A" * 20}
        fixture(self.work, self.sequences, cas_records=("CCF000001", "CCF000002"), with_array=True)

    def parse(self):
        return assembly.parse_assembly(self.work, self.sequences)

    def test_every_record_parsed_with_existing_coordinates_and_semantics(self):
        row = self.parse()
        self.assertEqual(row["metrics"]["assessed_contigs"], list(self.sequences))
        self.assertEqual(row["metrics"]["array_count"], 1)
        self.assertEqual(row["metrics"]["repeat_count"], 2)
        self.assertEqual(row["metrics"]["native_invocation_count"], 1)
        self.assertEqual(row["findings"][0]["contig"], "original|one")
        self.assertEqual(row["findings"][0]["start"], 2)
        self.assertEqual(row["metrics"]["cas_contig_assessments"]["second"]["status"], "completed")
        self.assertEqual(row["metrics"]["cas_contig_assessments"]["third"]["status"], "not_assessed_no_predicted_cds")

    def test_absent_duplicate_unexpected_reordered_result_records_rejected(self):
        original = runtime.read_json(self.work / "native/result.json")
        for rows in (original["Sequences"][:-1], original["Sequences"] + original["Sequences"][:1],
                     list(reversed(original["Sequences"])),
                     original["Sequences"] + [{"Id": "CCF999999"}]):
            with self.subTest(ids=[row.get("Id") for row in rows]):
                runtime.write_json(self.work / "native/result.json", {**original, "Sequences": rows})
                with self.assertRaisesRegex(ValueError, "every assembly record"):
                    self.parse()

    def test_wrong_coordinate_and_raw_sequence_rejected(self):
        path = self.work / "native/result.json"
        original = runtime.read_json(path)
        wrong = copy.deepcopy(original)
        wrong["Sequences"][0]["Crisprs"][0]["End"] = 999
        runtime.write_json(path, wrong)
        with self.assertRaisesRegex(ValueError, "coordinates"):
            self.parse()
        runtime.write_json(path, original)
        write(self.work / "native/rawFASTA/analyzedSequences/CCF000002.fna", ">CCF000002\nCCCCCCCCCCCCCCCC\n")
        with self.assertRaisesRegex(ValueError, "analyzed sequence differs"):
            self.parse()

    def test_mapping_or_staged_sequence_drift_rejected(self):
        mapping = runtime.read_json(self.work / "contig_map.json")
        mapping["CCF000001"]["contig"] = "second"
        runtime.write_json(self.work / "contig_map.json", mapping)
        with self.assertRaisesRegex(ValueError, "contig map"):
            self.parse()
        runtime.write_json(self.work / "contig_map.json", assembly.contig_map(self.sequences))
        write(self.work / "input.fna", ">CCF000001\nAAAA\n")
        with self.assertRaisesRegex(ValueError, "staged assembly"):
            self.parse()

    def test_record_and_aggregate_counts_and_missing_stdout_block_rejected(self):
        path = self.work / "stdout.txt"
        original = path.read_text()
        for changed in (original.replace("All CRISPRs = 1", "All CRISPRs = 0"),
                        original.replace("Nb of CRISPRs in this sequence = 1", "Nb of CRISPRs in this sequence = 0"),
                        original.replace("Sequence ID: CCF000002", "Sequence ID: CCF000001"),
                        original.replace("Thank you for using CRISPRCasFinder.pl!", "interrupted")):
            write(path, changed)
            with self.subTest(change=changed), self.assertRaises(ValueError):
                self.parse()

    def test_no_cds_evidence_on_another_record_cannot_bless_missing_cas(self):
        path = self.work / "stdout.txt"
        text = path.read_text().replace("No Cas search because no CDS was detected!", "")
        text = text.replace("Nb of CRISPRs in this sequence = 1", "Nb of CRISPRs in this sequence = 1\nNo Cas search because no CDS was detected!")
        write(path, text)
        with self.assertRaisesRegex(ValueError, "explicit skipped-Cas evidence"):
            self.parse()

    def test_every_cas_call_needs_its_own_valid_audit(self):
        path = self.work / "macsyfinder-audits/CCF000001.json"
        original = path.read_text()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "cover completed contigs exactly"):
            self.parse()
        write(path, original)
        audit = json.loads(original)
        audit["sequence_db"] = str(self.work / "native/prodigal_CCF000002/CCF000002.faa")
        runtime.write_json(path, audit)
        with self.assertRaisesRegex(ValueError, "verified isolation"):
            self.parse()
        write(path, original)
        write(self.work / "macsyfinder-audits/CCF000003.json", original)
        with self.assertRaisesRegex(ValueError, "cover completed contigs exactly"):
            self.parse()

    def test_changed_proteome_and_duplicate_argument_rejected(self):
        path = self.work / "macsyfinder-audits/CCF000001.json"
        row = runtime.read_json(path)
        row["argv"].extend(["--sequence-db", "native/prodigal_CCF000001/CCF000001.faa"])
        runtime.write_json(path, row)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.parse()
        row["argv"] = row["argv"][:-2]
        row["sequence_db_sha256"] = "changed"
        runtime.write_json(path, row)
        with self.assertRaisesRegex(ValueError, "verified isolation"):
            self.parse()

    def test_valid_empty_proteomes_are_inconclusive(self):
        shutil.rmtree(self.work)
        fixture(self.work, self.sequences)
        row = self.parse()
        self.assertEqual(row["findings"], [])
        self.assertEqual(row["interpretation"], "inconclusive")


FAKE_NATIVE = r'''import json, pathlib, sys
def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
with CALLS.open("a") as stream:
    stream.write(json.dumps({"argv": sys.argv[1:], "cwd": str(pathlib.Path.cwd()),
                             "input": pathlib.Path("input.fna").read_text()}) + "\n")
if CONTROL.exists():
    write(pathlib.Path("native/partial.txt"), "synthetic interrupted diagnostics")
    print("synthetic command interrupted", file=sys.stderr)
    raise SystemExit(23)
records = []
lines = pathlib.Path("input.fna").read_text().splitlines()
for index in range(0, len(lines), 2):
    safe, sequence = lines[index][1:], lines[index+1]
    records.append({"Id": safe, "Version": safe, "Length": len(sequence), "Crisprs": [], "Cas": []})
    write(pathlib.Path(f"native/rawFASTA/analyzedSequences/{safe}.fna"), f">{safe}\n{sequence}\n")
    write(pathlib.Path(f"native/GFF/annotation_{safe}.gff"), "##gff-version 3\n")
    write(pathlib.Path(f"native/Prodigal/prodigal_{safe}/{safe}.faa"), "")
    print(f"  ( Input file: {safe}.fna, Sequence ID: {safe}, Sequence name =  )")
    print("Nb of CRISPRs in this sequence = 0")
    print("No Cas search because no CDS was detected!")
write(pathlib.Path("native/result.json"), json.dumps({"Version": "4.3.2", "Sequences": records}))
print("All CRISPRs = 0")
print("Thank you for using CRISPRCasFinder.pl!")
'''


class AssemblyExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ccf durable execution ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls_path = self.root / "invocations.jsonl"
        self.control = self.root / "interrupt"
        self.database = self.root / "database"
        source = "import pathlib\nCALLS = pathlib.Path(" + repr(str(self.calls_path)) + ")\nCONTROL = pathlib.Path(" + repr(str(self.control)) + ")\n" + FAKE_NATIVE
        write(self.database / "CRISPRCasFinder.pl", source)
        write(self.database / "sel392v2.so", "synthetic library fixture")
        write(self.database / "models/CASFinder/metadata.yml", "vers: 3.1.0\n")
        write(self.database / "models/CASFinder/profiles/fixture.hmm", "synthetic model fixture")
        write(self.database / "models/CASFinder/definitions/fixture.xml", "synthetic model fixture")
        fake_bin = self.root / "bin"
        write(fake_bin / "macsyfinder", "#!/bin/sh\nexit 99\n")
        (fake_bin / "macsyfinder").chmod(0o755)
        (fake_bin / "python").symlink_to(sys.executable)
        self.path_patch = patch.dict(os.environ, {"PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]})
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.pin_patch = patch.object(assembly.ccf, "SOURCE_SHA256", result_store.sha256(self.database / "CRISPRCasFinder.pl"))
        self.pin_patch.start()
        self.addCleanup(self.pin_patch.stop)
        self.input = self.root / "assembly.fna"
        self.original = ">arbitrary|name description\nACGTACGT\n>second\nNNACGT\n>third\nAAAA\n"
        write(self.input, self.original)
        self.sample = {"sample_id": "synthetic", "assembly": str(self.input)}
        self.environment = self.root / "environments/crisprcasfinder.json"
        self.prefix = self.root / "prefix"
        self.prefix.mkdir()
        runtime.write_json(self.environment, {"schema": "basis-snake-environment-v1", "status": "completed",
            "prefix": str(self.prefix), "packages": [{"name": "fixture", "version": "1"}],
            "recipe_sha256": "fixture", "post_deploy_sha256": "fixture"})
        self.resource = self.root / "resources/crisprcasfinder/provenance.json"
        runtime.write_json(self.resource, {"schema": resources.SCHEMA, "resource": "crisprcasfinder", "status": "completed",
            "artifacts": {str(self.database / "CRISPRCasFinder.pl"): {"bytes": len(source.encode()), "sha256": assembly.ccf.SOURCE_SHA256}},
            "result": {"version": "synthetic"}})
        self.config = {"modules": {"crisprcasfinder": {"database": str(self.database),
            "executable": sys.executable, "timeout_seconds": 10, "heartbeat_seconds": 1,
            "min_evidence_level": 3, "genetic_code": 11}},
            "resource_receipts": [str(self.resource)], "environment_receipts": [str(self.environment)]}
        self.destination = self.root / "native/crisprcasfinder"
        self.receipt = self.root / "completion/crisprcasfinder.json"
        self.store = self.root / "results-cache/crisprcasfinder"

    def run_job(self, config=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return assembly.run_assembly(self.sample, config or self.config, self.destination,
                                          self.receipt, self.store, threads=2)

    def calls(self):
        return [json.loads(line) for line in self.calls_path.read_text().splitlines()] if self.calls_path.exists() else []

    def test_one_process_full_assembly_unchanged_flags_durable_cache(self):
        with patch.object(assembly, "parse_assembly", wraps=assembly.parse_assembly) as parser:
            record, reused = self.run_job()
            self.assertFalse(reused)
            self.assertTrue(self.destination.is_symlink())
            self.assertFalse((self.destination / "models").exists())
            self.assertEqual(self.input.read_text(), self.original)
            calls = self.calls()
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["input"].count(">"), 3)
            self.assertEqual(calls[0]["argv"], ["-in", "input.fna", "-out", "native", "-so", "./sel392v2.so",
                                               "-cas", "-keep", "-log", "-cpuM", "2", "-gcode", "11"])
            self.assertNotIn(" ", calls[0]["cwd"])
            self.receipt.unlink()
            self.destination.unlink()
            repeated, reused = self.run_job()
            self.assertTrue(reused)
            self.assertEqual(record, repeated)
            self.assertEqual(parser.call_count, 1)
        self.assertEqual(len(self.calls()), 1)
        result_store.verify_receipt(self.receipt, self.destination)
        parsed = runtime.read_json(self.destination / "parsed.json")
        self.assertTrue(all(Path(path).exists() for path in parsed["artifacts"]))
        self.assertFalse(any("/tmp/basis-ccf-assembly-" in path for path in parsed["artifacts"]))

    def test_failure_retains_diagnostics_no_receipt_and_retry_completes(self):
        self.control.touch()
        with self.assertRaisesRegex(RuntimeError, "exited 23"):
            self.run_job()
        self.assertFalse(self.receipt.exists())
        self.assertFalse(self.destination.exists())
        failures = list(self.store.glob("*/attempt-*/failure.json"))
        self.assertEqual(len(failures), 1)
        attempt = failures[0].parent
        self.assertEqual((attempt / "data/native/partial.txt").read_text(), "synthetic interrupted diagnostics")
        self.assertTrue((attempt / "commands.json").is_file())
        self.control.unlink()
        self.run_job()
        self.assertEqual(len(self.calls()), 2)
        self.assertTrue((attempt / "data/native/partial.txt").is_file())

    def test_changed_input_resource_environment_and_executable_invalidate_reuse(self):
        self.run_job()
        write(self.input, self.original.replace("ACGTACGT", "ACGTACGA"))
        self.assertFalse(self.run_job()[1])
        resource = runtime.read_json(self.resource)
        resource["result"]["version"] = "changed synthetic identity"
        runtime.write_json(self.resource, resource)
        self.assertFalse(self.run_job()[1])
        environment = runtime.read_json(self.environment)
        environment["packages"][0]["version"] = "2"
        runtime.write_json(self.environment, environment)
        self.assertFalse(self.run_job()[1])
        write(self.root / "bin/macsyfinder", "#!/bin/sh\n# changed synthetic executable\nexit 99\n")
        self.assertFalse(self.run_job()[1])
        self.assertEqual(len(self.calls()), 5)

    def test_command_record_write_failure_still_retains_native_diagnostics(self):
        original_write = runtime.write_json
        def fail_command_record(path, value):
            if Path(path).name == "commands.json":
                raise OSError("synthetic command-record write failure")
            return original_write(path, value)
        with patch.object(runtime, "write_json", side_effect=fail_command_record):
            with self.assertRaisesRegex(OSError, "command-record write failure"):
                self.run_job()
        self.assertFalse(self.receipt.exists())
        failures = list(self.store.glob("*/attempt-*/failure.json"))
        self.assertEqual(len(failures), 1)
        self.assertTrue((failures[0].parent / "data/native/result.json").is_file())

    def test_corrupt_completed_output_is_rejected_and_legacy_folder_preserved(self):
        write(self.destination / "legacy.txt", "previous per-contig outputs")
        self.run_job()
        old = list(self.destination.parent.glob(".previous-results/crisprcasfinder-*/legacy.txt"))
        self.assertEqual(len(old), 1)
        self.assertEqual(old[0].read_text(), "previous per-contig outputs")
        write(self.destination / "stdout.txt", "changed output")
        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            self.run_job()
        self.assertEqual(len(self.calls()), 1)

    def test_missing_provenance_and_invalid_threads_stop_before_invocation(self):
        config = copy.deepcopy(self.config)
        config["resource_receipts"] = []
        with self.assertRaisesRegex(ValueError, "verified CRISPR resource"):
            self.run_job(config)
        with self.assertRaisesRegex(ValueError, "threads"):
            assembly.run_assembly(self.sample, self.config, self.destination, self.receipt, self.store, threads=True)
        self.assertEqual(self.calls(), [])


class AssemblyShimTests(unittest.TestCase):
    def test_multiple_relative_invocations_are_isolated_and_duplicate_rejected(self):
        with tempfile.TemporaryDirectory(prefix="ccf-shim-fixture-") as directory:
            root = Path(directory)
            # Fake MacSyFinder Python API exercises the shim itself, without
            # importing or executing any biological tool or native model.
            write(root / "modules/macsypy/__init__.py", '__version__ = "2.0"\n')
            write(root / "modules/macsypy/config.py", 'class Config:\n    def _set_system_wide_config(self, path):\n        raise RuntimeError("ambient system config read")\n    def _set_user_wide_config(self, path):\n        raise RuntimeError("ambient user config read")\n')
            actual = root / "actual-macsyfinder"
            write(actual, 'from macsypy.config import Config\nc = Config()\nc._set_system_wide_config("system")\nc._set_user_wide_config("user")\n')
            shim = root / "bin/macsyfinder"
            write(shim, assembly.ASSEMBLY_MACSY_SHIM)
            write(root / "macsyfinder.conf", assembly._CONFIG)
            runtime.write_json(root / "runtime_isolation.json", {"macsyfinder_executable": str(actual),
                "macsyfinder_executable_sha256": result_store.sha256(actual)})
            env = {**os.environ, "PYTHONPATH": str(root / "modules")}
            for safe in ("CCF000001", "CCF000002"):
                write(root / f"native/prodigal_{safe}/{safe}.faa", f">{safe}_1\nMMM\n")
                argv = [sys.executable, str(shim), "--sequence-db", f"native/prodigal_{safe}/{safe}.faa",
                        "--out-dir", f"native/casfinder_{safe}/"]
                process = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(process.returncode, 0, process.stderr)
                row = runtime.read_json(root / f"macsyfinder-audits/{safe}.json")
                self.assertEqual(row["exit_status"], 0)
                self.assertEqual({entry["scope"] for entry in row["ignored_configurations"]}, {"system", "user"})
                self.assertEqual(row["safe_id"], safe)
            original = (root / "macsyfinder-audits/CCF000002.json").read_bytes()
            duplicate = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True)
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertEqual((root / "macsyfinder-audits/CCF000002.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
