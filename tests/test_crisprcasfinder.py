"""Pinned-schema fixtures; these are adapter tests, not biological validation."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from basis.adapters import crisprcasfinder as ccf


HEADER = "# macsyfinder 2.0\n# models : CASFinder-3.1.0\n# macsyfinder --models CASFinder\n"
FIELDS = ["replicon", "hit_id", "gene_name", "hit_pos", "model_fqn", "sys_id", "sys_loci",
          "locus_num", "sys_wholeness", "sys_score", "sys_occ", "hit_gene_ref", "hit_status",
          "hit_seq_len", "hit_i_eval", "hit_score", "hit_profile_cov", "hit_seq_cov",
          "hit_begin_match", "hit_end_match", "counterpart", "used_in"]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def isolation_fixture(work):
    hashes = {"launcher_sha256": hashlib.sha256(ccf._CCF_LAUNCHER.encode()).hexdigest(),
              "wrapper_sha256": hashlib.sha256(ccf._MACSY_SHIM.encode()).hexdigest(),
              "config_sha256": hashlib.sha256((work / "macsyfinder.conf").read_bytes()).hexdigest()}
    manifest = {**hashes, "policy": "disable_system_and_user_config_readers", "home_environment_unchanged": True,
                "python": "/native/bin/python", "macsyfinder_executable": "/native/bin/macsyfinder"}
    write(work / "runtime_isolation.json", json.dumps(manifest))
    write(work / "macsyfinder_isolation.json", json.dumps({**hashes, "policy": manifest["policy"],
          "python": manifest["python"], "exit_status": 0, "macsyfinder_version": "2.0",
          "actual_executable": manifest["macsyfinder_executable"]}))


def native_fixture(native, sequence, safe="CCF000001", positive=False, arrays=None, no_cds=False):
    """Minimal native files with the verified 4.3.2 / 2.0 field schemas."""
    mode = "unordered" if len(sequence) < 100000 else "gembase"
    system_id = f"{safe}_CAS-TypeIIA_1"
    gene = {"Sub_type": "cas9", "Start": 10, "End": 69, "Orientation": "+"}
    systems = [{"Type": system_id, "Start": 10, "End": 69, "Genes": [gene]}] if positive and mode == "gembase" else []
    arrays = arrays or []
    result = {"Version": "4.3.2", "Date": "synthetic fixture", "Command": "perl CRISPRCasFinder.pl ",
              "Sequences": [{"Id": safe, "Version": safe, "Description": "Unknown", "Length": len(sequence),
                             "AT": 100, "Summary_CRISPR-Cas": "", "Crisprs": arrays, "Cas": systems}]}
    write(native / "result.json", json.dumps(result))
    write(native / "rawFASTA" / "analyzedSequences" / f"{safe}.fna", f">{safe}\n{sequence}\n")
    annotation = "##gff-version 3\n"
    if not no_cds:
        annotation += f"{safe}\tProdigal:002006\tCDS\t10\t69\t1\t+\t0\tID=1_1;partial=00;\n"
    write(native / "GFF" / f"annotation_{safe}.gff", annotation)
    write(native / "Prodigal" / f"prodigal_{safe}" / f"{safe}.faa", "" if no_cds else f">{safe}_1 # 10 # 69 # 1\n{'M' * 20}\n")
    stdout = f"All CRISPRs = {len(arrays)}\nAll Cas = {len(systems)}\nThank you for using CRISPRCasFinder.pl! Thank you for your patience!\n"
    if no_cds:
        return stdout + "No Cas search because no CDS was detected!\n"
    folder = native / "CasFinder" / f"casfinder_{safe}"
    write(folder / "macsyfinder.log", "INFO: Searching systems\nINFO: END\n")
    write(folder / "macsyfinder.conf", f"[base]\nsequence_db = /tmp/native/Prodigal/prodigal_{safe}/{safe}.faa\ndb_type = {mode}\nreplicon_topology = circular\n")
    if positive:
        values = ["Unordered" if mode == "unordered" else safe, f"{safe}_1", "cas9", "1", "CASFinder/CAS-TypeIIA",
                  system_id, "1", "1", "1.0", "10.0", "1", "cas9", "mandatory", "20", "1e-20", "80", "0.8", "1.0", "1", "20", "None", "None"]
        table = HEADER + "\t".join(FIELDS) + "\n" + "\t".join(values) + "\n"
    else:
        table = HEADER + ("# No Likely Systems found\n" if mode == "unordered" else "# No Systems found\n")
    write(folder / ("all_systems.tsv" if mode == "unordered" else "best_solution.tsv"), table)
    if mode == "unordered":
        write(folder / "uncomplete_systems.txt", HEADER + "# No Unlikely Systems found\n")
    else:
        write(folder / "best_solution_summary.tsv", HEADER + f"replicon\tCASFinder/CAS-TypeIIA\n{safe}\t{int(positive)}\n")
    return stdout


def array_fixture():
    repeat = "ACGTACGTACGTACGTACGTACG"
    content = (repeat + "T" * 25) * 4 + repeat
    sequence = "A" * 100 + content + "A" * (100000 - 100 - len(content))
    regions = [{"Type": "LeftFLANK", "Start": 1, "End": 100, "Sequence": "A" * 100, "Leader": 0, "AT": 100}]
    cursor = 101
    for index in range(5):
        regions.append({"Type": "DR", "Start": cursor, "End": cursor + len(repeat) - 1, "Sequence": repeat})
        cursor += len(repeat)
        if index < 4:
            regions.append({"Type": "Spacer", "Start": cursor, "End": cursor + 24, "Sequence": "T" * 25})
            cursor += 25
    regions.append({"Type": "RightFLANK", "Start": cursor, "End": cursor + 99, "Sequence": "A" * 100, "Leader": 1, "AT": 100})
    array = {"Name": "CCF000001_1", "Start": 101, "End": cursor - 1, "DR_Consensus": repeat,
             "Repeat_ID": "fixture", "DR_Length": len(repeat), "Spacers": 4, "Potential_Orientation": "-",
             "CRISPRDirection": "ND", "Evidence_Level": 4, "Conservation_DRs": 100,
             "Conservation_Spacers": 2, "Regions": regions}
    return sequence, array


class CrisprCasFinderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ccf tests ")
        self.root = Path(self.temp.name)
        self.native = self.root / "native"
        self.sequence = "A" * 100000
        self.stdout = native_fixture(self.native, self.sequence)

    def tearDown(self):
        self.temp.cleanup()

    def parse(self):
        return ccf.parse_contig(self.native, "CCF000001", "original|contig.1", self.sequence, self.stdout)

    def test_verified_zero_system_result_requires_all_native_evidence(self):
        findings, status = self.parse()
        self.assertEqual(findings, [])
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["system_count"], 0)

    def test_swallowed_cas_failure_is_not_a_zero_result(self):
        path = self.native / "CasFinder/casfinder_CCF000001/best_solution.tsv"
        path.unlink()
        self.stdout += "No Cas results\n"
        with self.assertRaisesRegex(ValueError, "missing"):
            self.parse()

    def test_incomplete_or_error_log_rejected(self):
        path = self.native / "CasFinder/casfinder_CCF000001/macsyfinder.log"
        for text in ["INFO: Searching systems\n", "INFO: END\nINFO: New unfinished job\n", "ERROR: hmmsearch failed\nINFO: END\n"]:
            with self.subTest(text=text):
                path.write_text(text)
                with self.assertRaisesRegex(ValueError, "completion"):
                    self.parse()

    def test_unsupported_versions_and_wrong_identity_fail_closed(self):
        path = self.native / "result.json"
        original = json.loads(path.read_text())
        for key, value in [("Version", "4.4.0"), ("Sequences", [])]:
            with self.subTest(key=key):
                path.write_text(json.dumps({**original, key: value}))
                with self.assertRaises(ValueError):
                    self.parse()
        original["Sequences"][0]["Id"] = "wrong"
        path.write_text(json.dumps(original))
        with self.assertRaisesRegex(ValueError, "identity"):
            self.parse()

    def test_saved_assembly_sequence_is_verified(self):
        path = self.native / "rawFASTA/analyzedSequences/CCF000001.fna"
        path.write_text(f">CCF000001\n{'C' * len(self.sequence)}\n")
        with self.assertRaisesRegex(ValueError, "differs"):
            self.parse()

    def test_ordered_positive_system_reconciles_native_components(self):
        self.stdout = native_fixture(self.native, self.sequence, positive=True)
        findings, status = self.parse()
        self.assertEqual(status["system_count"], 1)
        self.assertEqual(findings[0]["contig"], "original|contig.1")
        self.assertEqual((findings[0]["start"], findings[0]["end"]), (10, 69))
        self.assertIsNone(findings[0]["protein_id"])
        path = self.native / "result.json"
        data = json.loads(path.read_text())
        data["Sequences"][0]["Cas"][0]["Genes"][0]["Start"] = 11
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "coordinates disagree"):
            self.parse()

    def test_native_summary_and_hit_count_must_agree(self):
        path = self.native / "CasFinder/casfinder_CCF000001/best_solution_summary.tsv"
        path.write_text(path.read_text().replace("CCF000001\t0", "CCF000001\t1"))
        with self.assertRaisesRegex(ValueError, "counts disagree"):
            self.parse()

    def test_unordered_native_hits_preserved_as_untyped_components(self):
        self.sequence = "A" * 500
        self.stdout = native_fixture(self.native, self.sequence, positive=True)
        findings, status = self.parse()
        self.assertEqual(status["mode"], "unordered")
        self.assertIsNone(status["system_count"])
        self.assertEqual(status["candidate_model_count"], 1)
        self.assertEqual(findings[0]["attributes"]["feature_type"], "cas_component_candidate")
        self.assertEqual(findings[0]["attributes"]["typed_system_assessment"], "not_assessed_unordered_mode")

    def test_unordered_zero_requires_explicit_native_no_hit_markers(self):
        self.sequence = "A" * 500
        self.stdout = native_fixture(self.native, self.sequence)
        self.assertEqual(self.parse()[0], [])
        path = self.native / "CasFinder/casfinder_CCF000001/all_systems.tsv"
        path.write_text(HEADER)
        with self.assertRaisesRegex(ValueError, "neither hits"):
            self.parse()

    def test_no_cds_is_not_assessed_not_zero_systems(self):
        self.sequence = "A" * 500
        self.stdout = native_fixture(self.native, self.sequence, no_cds=True)
        findings, status = self.parse()
        self.assertEqual(findings, [])
        self.assertEqual(status["status"], "not_assessed_no_predicted_cds")
        self.assertIsNone(status["system_count"])

    def test_arrays_validate_regions_and_keep_confidence(self):
        self.sequence, array = array_fixture()
        self.stdout = native_fixture(self.native, self.sequence, arrays=[array])
        findings, _ = self.parse()
        self.assertEqual(findings[0]["strand"], "-")
        self.assertEqual(findings[0]["attributes"]["spacer_count"], 4)
        self.assertTrue(findings[0]["attributes"]["passes_threshold"])
        array["Regions"][1]["Sequence"] = "T" * array["DR_Length"]
        self.stdout = native_fixture(self.native, self.sequence, arrays=[array])
        with self.assertRaisesRegex(ValueError, "sequence does not match"):
            self.parse()

    def test_native_stdout_count_and_errors_are_checked(self):
        for stdout in [self.stdout.replace("All CRISPRs = 0", "All CRISPRs = 1"),
                       self.stdout + "An error occurred in CasFinder function\n"]:
            with self.subTest(stdout=stdout):
                with self.assertRaises(ValueError):
                    ccf.parse_contig(self.native, "CCF000001", "orig", self.sequence, stdout)

    def test_low_evidence_arrays_retained_in_native_completion_count(self):
        self.sequence, array = array_fixture()
        array["Evidence_Level"] = 2
        array["Conservation_DRs"] = 50
        self.stdout = native_fixture(self.native, self.sequence, arrays=[array])
        findings, _ = self.parse()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["evidence_level"], "exploratory")
        self.assertFalse(findings[0]["attributes"]["passes_threshold"])

    def resources(self):
        root = self.root / "resource directory with spaces"
        source = b"# synthetic adapter fixture, not upstream software\n"
        write(root / "CRISPRCasFinder.pl", source.decode())
        write(root / "sel392v2.so", "fixture")
        write(root / "models/CASFinder/metadata.yml", "vers: 3.1.0\n")
        write(root / "models/CASFinder/profiles/cas.hmm", "fixture")
        write(root / "models/CASFinder/definitions/cas.xml", "fixture")
        return root, hashlib.sha256(source).hexdigest()

    def test_pinned_source_rejected_before_execution(self):
        resource, _ = self.resources()
        class NoRun:
            def run(self, *args, **kwargs):
                raise AssertionError("Must reject source before native execution")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            ccf.run("crisprcasfinder", {}, {"database": str(resource)}, self.root / "result", NoRun())

    def test_per_contig_staging_supports_spaces_and_restores_diagnostics(self):
        resource, digest = self.resources()
        assembly = self.root / "input assembly.fna"
        assembly.write_text(f">one|contig.7 description\n{'A' * 100000}\n>two.8\n{'C' * 500}\n")
        calls = []
        class Runner:
            def run(inner, argv, cwd, stdout_path):
                cwd = Path(cwd)
                calls.append((argv, cwd))
                self.assertNotIn(" ", str(cwd))
                self.assertEqual(argv[argv.index("-so") + 1], "./sel392v2.so")
                self.assertTrue((cwd / "models/CASFinder/metadata.yml").exists())
                records = ccf.read_fasta(cwd / "input.fna")
                safe, sequence = next(iter(records.items()))
                stdout_path.write_text(native_fixture(cwd / "native", sequence, safe))
                isolation_fixture(cwd)
                return subprocess.CompletedProcess(argv, 0)
        with patch.object(ccf, "SOURCE_SHA256", digest):
            result = ccf.run("crisprcasfinder", {"assembly": str(assembly)}, {"database": str(resource)}, self.root / "output with spaces", Runner())
        self.assertEqual(result["metrics"]["assessed_contigs"], ["one|contig.7", "two.8"])
        self.assertEqual(result["metrics"]["typed_cas_assessed_contig_count"], 1)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(not path.exists() for _, path in calls))
        self.assertTrue((self.root / "output with spaces/native/CCF000001/native/result.json").is_file())
        self.assertEqual(json.loads((self.root / "output with spaces/contig_map.json").read_text())["CCF000001"]["contig"], "one|contig.7")

    def test_failed_execution_keeps_native_diagnostics(self):
        resource, digest = self.resources()
        assembly = self.root / "input.fna"
        assembly.write_text(">original\nAAAA\n")
        class Runner:
            def run(inner, argv, cwd, stdout_path):
                stdout_path.write_text("native error fixture")
                raise RuntimeError("native failed")
        with patch.object(ccf, "SOURCE_SHA256", digest):
            with self.assertRaisesRegex(RuntimeError, "native failed"):
                ccf.run("crisprcasfinder", {"assembly": str(assembly)}, {"database": str(resource)}, self.root / "failed", Runner())
        self.assertEqual((self.root / "failed/native/CCF000001/stdout.txt").read_text(), "native error fixture")

    def test_runtime_isolation_audit_rejects_wrong_version_or_wrapper(self):
        work = self.root / "isolated"
        write(work / "macsyfinder.conf", "[directories]\nmodels_dir=models\n")
        isolation_fixture(work)
        ccf._verify_runtime_isolation(work, {"status": "completed"})
        path = work / "macsyfinder_isolation.json"
        data = json.loads(path.read_text())
        for field, wrong in [("macsyfinder_version", "2.1"), ("wrapper_sha256", "modified"), ("python", "/controller/python")]:
            with self.subTest(field=field):
                path.write_text(json.dumps({**data, field: wrong}))
                with self.assertRaisesRegex(ValueError, "isolation"):
                    ccf._verify_runtime_isolation(work, {"status": "completed"})

    def test_launcher_and_wrapper_ignore_hostile_ambient_readers_without_recursion(self):
        work = self.root / "shim execution"
        write(work / "basis_ccf_launch.py", ccf._CCF_LAUNCHER)
        write(work / "bin/macsyfinder", ccf._MACSY_SHIM)
        (work / "bin/macsyfinder").chmod(0o700)
        write(work / "macsyfinder.conf", "[directories]\nmodels_dir=models\n")
        write(work / "macsypy/__init__.py", '__version__ = "2.0"\n')
        # Both readers would fail if the shim allowed ambient configuration.
        write(work / "macsypy/config.py", '''class Config:
    def _set_system_wide_config(self, path):
        raise RuntimeError("hostile system configuration was loaded")
    def _set_user_wide_config(self, path):
        raise RuntimeError("hostile user configuration was loaded")
''')
        write(work / "realbin/macsyfinder", '''import json, os, sys
from pathlib import Path
from macsypy.config import Config
c = Config()
c._set_system_wide_config("/hostile/system.conf")
c._set_user_wide_config("/hostile/user.conf")
assert sys.argv.count("--cfg-file") == 1
assert Path(sys.argv[sys.argv.index("--cfg-file") + 1]).read_text().startswith("[directories]")
Path("native-observed.json").write_text(json.dumps({"home": os.environ.get("HOME"), "python": sys.executable}))
''')
        (work / "realbin/macsyfinder").chmod(0o700)
        write(work / "fake_ccf.py", '''import subprocess
subprocess.run(["macsyfinder", "--models", "CASFinder"], check=True)
''')
        env = os.environ.copy()
        env["PATH"] = str(work / "realbin") + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        env["PYTHONPATH"] = str(work)
        subprocess.run([sys.executable, "basis_ccf_launch.py", sys.executable, "fake_ccf.py"], cwd=work, env=env, check=True, capture_output=True, text=True)
        ccf._verify_runtime_isolation(work, {"status": "completed"})
        audit = json.loads((work / "macsyfinder_isolation.json").read_text())
        self.assertEqual([x["scope"] for x in audit["ignored_configurations"]], ["system", "user"])
        self.assertEqual(audit["actual_executable"], str((work / "realbin/macsyfinder").resolve()))
        self.assertEqual(json.loads((work / "native-observed.json").read_text())["home"], os.environ.get("HOME"))
        self.assertEqual(audit["macsyfinder_version"], "2.0")
        # Native source stays untouched; the separately saved wrapper supplies isolation.
        self.assertIn("hostile user configuration was loaded", (work / "macsypy/config.py").read_text())


if __name__ == "__main__":
    unittest.main()
