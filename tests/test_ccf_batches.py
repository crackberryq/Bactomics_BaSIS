"""Synthetic batching/restart contracts; no native tools or biological data."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety import ccf_batches as batches, extend


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class ContigBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ccf batch fixtures ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.assembly = self.root / "assembly.fna"
        self.assembly.write_text(">first description\nACGT\n>second\nACGTACGT\n>third\nACGTACGTACGT\n")
        self.sample = {"sample_id": "synthetic", "assembly": str(self.assembly)}
        self.contigs = self.root / "contigs"
        self.native = self.root / "native"
        self.logs = self.root / "logs"
        self.db = self.root / "database"
        write(self.db / "fixture.txt", "synthetic-resource")
        self.resource = self.root / "resources/crisprcasfinder/provenance.json"
        self.resource_row = {"resource": "crisprcasfinder", "status": "completed", "created_at_utc": "first",
                             "artifacts": {str(self.db / "fixture.txt"): {"bytes": 18, "sha256": "fixture"}}}
        extend.write_json(self.resource, self.resource_row)
        self.environment = self.root / "environments/crisprcasfinder.json"
        self.environment_row = {"status": "completed", "created_at": "first", "packages": [{"name": "fixture", "version": "1"}]}
        extend.write_json(self.environment, self.environment_row)
        self.config = {"modules": {"crisprcasfinder": {"database": str(self.db), "batch_size": 3}},
                       "resource_receipts": [{"path": str(self.resource)}],
                       "environment_receipts": [str(self.environment)]}
        self.calls = []
        self.preparation_calls = []
        self.fail_on = None
        self.run_patch = patch.object(extend, "run_contig", self.fake_contig)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)
        self.prepare_patch = patch.object(extend, "prepare_ccf_execution", self.fake_prepare)
        self.prepare_patch.start()
        self.addCleanup(self.prepare_patch.stop)
        extend.prepare_contigs(self.sample, self.contigs, 3)

    def fake_prepare(self, config):
        self.preparation_calls.append(dict(config))
        return {"settings": dict(config)}

    def fake_contig(self, config, staged, safe, work, logfile, *, prepared=None, contig_name=None):
        """Write a parser-valid, explicitly synthetic no-CDS native fixture."""
        self.calls.append(safe)
        if safe == self.fail_on:
            raise RuntimeError("synthetic interrupted contig")
        sequence = extend.read_fasta(staged)[safe]
        native = work / "native"
        extend.write_json(native / "result.json", {"Version": "4.3.2", "Sequences": [{
            "Id": safe, "Version": safe, "Length": len(sequence), "Crisprs": [], "Cas": []}]})
        write(native / f"rawFASTA/analyzedSequences/{safe}.fna", f">{safe}\n{sequence}\n")
        write(native / f"GFF/annotation_{safe}.gff", "##gff-version 3\n")
        write(native / f"Prodigal/prodigal_{safe}/{safe}.faa", "")
        write(work / "stdout.txt", "All CRISPRs = 0\nNo Cas search because no CDS was detected!\nThank you for using CRISPRCasFinder.pl!\n")
        write(work / "macsyfinder.conf", "[directories]\nmodels_dir = models\n")
        extend.write_json(work / "runtime_isolation.json", {
            "launcher_sha256": hashlib.sha256(extend.ccf._CCF_LAUNCHER.encode()).hexdigest(),
            "wrapper_sha256": hashlib.sha256(extend.ccf._MACSY_SHIM.encode()).hexdigest(),
            "config_sha256": hashlib.sha256((work / "macsyfinder.conf").read_bytes()).hexdigest(),
            "policy": "disable_system_and_user_config_readers", "home_environment_unchanged": True})
        findings, status = extend.ccf.parse_contig(native, safe, contig_name or safe, sequence,
                                                  (work / "stdout.txt").read_text(),
                                                  config.get("min_evidence_level", 3))
        extend.ccf._verify_runtime_isolation(work, status)
        return {"findings": findings, "status": status}

    def receipt(self, batch="CCB000001"):
        return self.native / "batches" / (batch + ".json")

    def run_batch(self, batch="CCB000001", config=None):
        batches.run_batch(config or self.config, self.contigs, batch, self.native,
                          self.receipt(batch), self.logs, threads=2)

    def test_bounded_deterministic_batches_cover_every_record_once(self):
        mapping = {f"CCF{number:06d}": {} for number in range(1, 66)}
        plan = batches.make_plan(mapping)
        self.assertEqual([len(rows) for rows in plan["batches"].values()], [32, 32, 1])
        self.assertEqual([item for rows in plan["batches"].values() for item in rows], list(mapping))
        for size in (0, 1025, True, 1.0, "32"):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "integer"):
                batches.make_plan(mapping, size)
        malformed = extend.read_json(self.contigs / "batches.json")
        malformed["batches"]["CCB000001"][1] = "CCF000001"
        extend.write_json(self.contigs / "batches.json", malformed)
        with self.assertRaisesRegex(ValueError, "exactly"):
            self.run_batch()

    def test_interruption_preserves_completed_contigs_and_retry_runs_only_remaining(self):
        self.fail_on = "CCF000002"
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.run_batch()
        first = (self.native / "CCF000001" / batches.COMPLETION).read_bytes()
        self.assertFalse(self.receipt().exists())
        self.fail_on = None
        self.run_batch()
        self.assertEqual(self.calls, ["CCF000001", "CCF000002", "CCF000002", "CCF000003"])
        self.assertEqual((self.native / "CCF000001" / batches.COMPLETION).read_bytes(), first)
        row = extend.read_json(self.receipt())
        self.assertEqual(row["reused_contigs"], ["CCF000001"])
        audit = batches.verify_batches(self.config, self.contigs, self.native, [self.receipt()])
        self.assertEqual(audit["batch_count"], 1)
        parsed = extend.collect_ccf(self.sample, self.config["modules"]["crisprcasfinder"], self.contigs, self.native)
        self.assertEqual(parsed["metrics"]["assessed_contig_count"], 3)
        self.assertEqual(parsed["findings"], [])
        self.assertEqual(parsed["metrics"]["cas_assessment"], "partially_assessed")

    def test_regrouping_and_receipt_timestamp_changes_reuse_all_native_outputs(self):
        self.run_batch()
        original_calls = list(self.calls)
        updated = copy.deepcopy(self.config)
        updated["modules"]["crisprcasfinder"].update(batch_size=1, threads=8, memory_mb=8000)
        self.resource_row["created_at_utc"] = "second"
        self.environment_row["created_at"] = "second"
        extend.write_json(self.resource, self.resource_row)
        extend.write_json(self.environment, self.environment_row)
        extend.prepare_contigs(self.sample, self.contigs, 1)
        receipts = []
        for number in range(1, 4):
            batch = f"CCB{number:06d}"
            self.run_batch(batch, updated)
            receipts.append(self.receipt(batch))
        self.assertEqual(self.calls, original_calls)
        self.assertEqual(batches.verify_batches(updated, self.contigs, self.native, receipts)["batch_count"], 3)
        self.assertEqual(len(list((self.native / ".execution-identities").glob("*.json"))), 1)

    def test_changed_input_reruns_only_that_contig_and_retains_prior_directory(self):
        self.run_batch()
        self.assembly.write_text(self.assembly.read_text().replace("\nACGTACGT\n", "\nACGTACGA\n"))
        extend.prepare_contigs(self.sample, self.contigs, 3)
        self.run_batch()
        self.assertEqual(self.calls, ["CCF000001", "CCF000002", "CCF000003", "CCF000002"])
        self.assertEqual(len(list((self.native / ".previous-contigs").glob("CCF000002-*"))), 1)

    def test_changed_execution_resource_or_environment_invalidates_native_reuse(self):
        self.run_batch()
        changed = copy.deepcopy(self.config)
        changed["modules"]["crisprcasfinder"]["genetic_code"] = 4
        self.run_batch(config=changed)
        self.assertEqual(len(self.calls), 6)
        self.resource_row["artifacts"][str(self.db / "fixture.txt")]["sha256"] = "changed-fixture"
        extend.write_json(self.resource, self.resource_row)
        self.run_batch(config=changed)
        self.assertEqual(len(self.calls), 9)
        self.environment_row["packages"][0]["version"] = "2"
        extend.write_json(self.environment, self.environment_row)
        self.run_batch(config=changed)
        self.assertEqual(len(self.calls), 12)

    def test_corrupted_or_legacy_output_is_not_silently_adopted(self):
        self.run_batch()
        (self.native / "CCF000001" / batches.COMPLETION).unlink()
        write(self.native / "CCF000002" / "stdout.txt", "corrupted")
        self.run_batch()
        self.assertEqual(self.calls[3:], ["CCF000001", "CCF000002"])
        archived = list((self.native / ".previous-contigs").iterdir())
        self.assertEqual(len(archived), 2)
        self.assertTrue(any((path / "stdout.txt").read_text() == "corrupted" for path in archived))

    def test_collection_rejects_missing_duplicate_or_changed_completion(self):
        self.run_batch()
        with self.assertRaisesRegex(ValueError, "every supplied"):
            batches.verify_batches(self.config, self.contigs, self.native, [])
        with self.assertRaisesRegex(ValueError, "current batch plan"):
            batches.verify_batches(self.config, self.contigs, self.native, [self.receipt(), self.receipt()])
        (self.native / "CCF000003" / "stdout.txt").unlink()
        with self.assertRaisesRegex(ValueError, "inputs and outputs"):
            batches.verify_batches(self.config, self.contigs, self.native, [self.receipt()])

    def test_missing_provenance_stops_before_native_execution(self):
        config = copy.deepcopy(self.config)
        config["resource_receipts"] = []
        with self.assertRaisesRegex(ValueError, "Exactly one verified"):
            self.run_batch(config=config)
        self.assertEqual(self.calls, [])

    def test_static_preparation_once_and_cached_collection_never_reparses(self):
        with patch.object(extend.ccf, "parse_contig", wraps=extend.ccf.parse_contig) as parser:
            self.run_batch()
            self.assertEqual(len(self.preparation_calls), 1)
            self.assertEqual(parser.call_count, 3)
            self.run_batch()
            self.assertEqual(len(self.preparation_calls), 1)
            audit = batches.verify_batches(self.config, self.contigs, self.native, [self.receipt()])
            cached = extend.collect_ccf(self.sample, self.config["modules"]["crisprcasfinder"],
                                       self.contigs, self.native, verified_parsed=audit["parsed"])
            self.assertEqual(parser.call_count, 3)
        uncached = extend.collect_ccf(self.sample, self.config["modules"]["crisprcasfinder"],
                                     self.contigs, self.native)
        self.assertEqual(cached, uncached)

    def test_cached_result_and_extra_native_files_are_integrity_checked(self):
        self.run_batch()
        cached = self.native / "CCF000001" / batches.PARSED
        payload = extend.read_json(cached)
        payload["status"]["status"] = "completed"
        extend.write_json(cached, payload)
        with self.assertRaisesRegex(ValueError, "inputs and outputs"):
            batches.verify_batches(self.config, self.contigs, self.native, [self.receipt()])
        self.run_batch()
        self.assertEqual(self.calls[3:], ["CCF000001"])
        write(self.native / "CCF000002" / "unexpected.txt", "extra")
        with self.assertRaisesRegex(ValueError, "inputs and outputs"):
            batches.verify_batches(self.config, self.contigs, self.native, [self.receipt()])

    def test_cached_result_binds_original_identity_and_raw_inventory(self):
        self.run_batch()
        work = self.native / "CCF000001"
        row = extend.read_json(work / batches.COMPLETION)
        signature = row["execution_identity"]
        mapping, _ = batches.load_plan(self.contigs)
        info = mapping["CCF000001"]
        payload = extend.read_json(work / batches.PARSED)
        self.assertEqual(payload["input"]["contig"], "first")
        payload["input"]["contig"] = "another"
        extend.write_json(work / batches.PARSED, payload)
        row["artifacts"] = batches.output_inventory(work)
        extend.write_json(work / batches.COMPLETION, row)
        with self.assertRaisesRegex(ValueError, "verified native artifacts"):
            batches.verify_contig(work, "CCF000001", info, "ACGT", signature,
                                  self.config["modules"]["crisprcasfinder"])

    def test_contig_cli_does_not_read_unused_sample_metadata(self):
        config = self.root / "config.json"
        extend.write_json(config, self.config)
        extend.main(["ccf-batch", "--sample", str(self.root / "deliberately-absent-sample.json"),
                     "--config", str(config), "--contigs", str(self.contigs),
                     "--batch", "CCB000001", "--native", str(self.native),
                     "--output", str(self.receipt()), "--log-directory", str(self.logs)])
        self.assertTrue(self.receipt().is_file())


@unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake required for scheduler acceptance")
class ContigBatchSchedulerTests(unittest.TestCase):
    def test_production_rules_batch_restart_preserves_completed_native_work(self):
        fixture = ContigBatchTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.root
        isolate = root / "isolate"
        metadata = isolate / "safety/metadata"
        fixture.config["modules"]["crisprcasfinder"].update(batch_size=2, threads=1, memory_mb=128)
        extend.write_json(metadata / "sample.json", fixture.sample)
        extend.write_json(metadata / "config.json", fixture.config)
        extend.write_json(metadata / "setup.ready.json", {"status": "completed"})
        env_file = root / "fixture-env.yml"
        env_file.write_text("channels: []\ndependencies: []\n")
        calls = root / "calls.jsonl"
        allow = root / "allow-second"
        worker = root / "fixture_worker.py"
        worker.write_text(textwrap.dedent(f'''
            import json, sys
            from pathlib import Path
            sys.path.insert(0, {str(ROOT / "tests")!r})
            from test_ccf_batches import ContigBatchTests
            from safety import extend
            def fixture_runner(config, staged, safe, work, logfile, **kwargs):
                with Path({str(calls)!r}).open("a") as handle:
                    handle.write(json.dumps({{"contig": safe}}) + "\\n")
                if safe == "CCF000002" and not Path({str(allow)!r}).exists():
                    raise RuntimeError("synthetic native interruption")
                fixture = ContigBatchTests()
                fixture.calls = []
                fixture.fail_on = None
                return fixture.fake_contig(config, staged, safe, work, logfile, **kwargs)
            def fixture_prepare(config):
                return {{"settings": dict(config)}}
            assert sys.argv[1:3] == ["-m", "safety.extend"]
            extend.run_contig = fixture_runner
            extend.prepare_ccf_execution = fixture_prepare
            extend.main(sys.argv[3:])
        '''))
        binary = root / "bin/python"
        write(binary, f"#!{sys.executable}\nimport runpy, sys\nsys.argv[0] = {str(worker)!r}\nrunpy.run_path({str(worker)!r}, run_name='__main__')\n")
        binary.chmod(0o755)
        rules = ROOT / "workflow/rules/extend.smk"
        if os.environ.get("BASIS_TEST_NO_BENCHMARK") == "1":
            # Same explicit local PID-namespace workaround as test_workflow;
            # CI executes the production benchmark declarations unchanged.
            lines, skipped = [], None
            for line in rules.read_text().splitlines():
                indent = len(line) - len(line.lstrip())
                if re.match(r"\s*benchmark:", line):
                    skipped = indent
                    continue
                if skipped is not None:
                    if not line.strip() or indent > skipped:
                        continue
                    skipped = None
                lines.append(line)
            rules = root / "extend.smk"
            rules.write_text("\n".join(lines) + "\n")
        snakefile = root / "Snakefile"
        parsed = isolate / "safety/native/crisprcasfinder/parsed.json"
        snakefile.write_text(textwrap.dedent(f'''
            import json, os, sys
            from pathlib import Path
            ROOT = Path({str(ROOT)!r})
            sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
            CODE_PATH = os.pathsep.join([str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")])
            PYTHON = {str(binary)!r}
            class FixtureContext:
                samples = {{{str(isolate)!r}: {fixture.sample!r}}}
                def setup_target(self, isolate):
                    return str(Path(isolate) / "safety/metadata/setup.ready.json")
                def module_config(self, isolate, module):
                    return {fixture.config["modules"]["crisprcasfinder"]!r}
                def module_config_path(self, isolate, module):
                    return str(Path(isolate) / "safety/metadata/config.json")
                def resource_inputs(self, module):
                    return [{str(fixture.resource)!r}]
                def env(self, module):
                    return {str(env_file)!r}
            CTX = FixtureContext()
            rule all:
                input: {str(parsed)!r}
            include: {str(rules)!r}
        '''))
        environment = {**os.environ, "PATH": str(binary.parent) + os.pathsep + os.environ["PATH"]}
        command = [sys.executable, "-m", "snakemake", "--snakefile", str(snakefile),
                   "--directory", str(root), "--cores", "1", "--rerun-incomplete", "--printshellcmds"]
        first = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=120)
        self.assertNotEqual(first.returncode, 0, first.stdout + first.stderr)
        native = isolate / "safety/native/crisprcasfinder"
        complete = native / "CCF000001" / batches.COMPLETION
        self.assertTrue(complete.is_file(), first.stdout + first.stderr)
        prior = complete.read_bytes()
        self.assertFalse((native / "batches/CCB000001.json").exists())
        allow.touch()
        second = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=120)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(complete.read_bytes(), prior)
        observed = [json.loads(line)["contig"] for line in calls.read_text().splitlines()]
        self.assertEqual(observed.count("CCF000001"), 1)
        self.assertEqual(observed.count("CCF000002"), 2)
        self.assertEqual(observed.count("CCF000003"), 1)
        self.assertEqual(len(list((native / "batches").glob("CCB*.json"))), 2)
        payload = extend.read_json(parsed)
        self.assertEqual(payload["metrics"]["assessed_contig_count"], 3)
        self.assertEqual(payload["metrics"]["scheduler_batch_count"], 2)
        third = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=120)
        self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
        self.assertIn("Nothing to be done", third.stdout + third.stderr)
        self.assertEqual(len(calls.read_text().splitlines()), 4)
        shutil.rmtree(native / "CCF000001")
        fourth = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=120)
        self.assertNotEqual(fourth.returncode, 0)
        self.assertIn("missing native output", fourth.stdout + fourth.stderr)
        self.assertIn("CCB000001.json", fourth.stdout + fourth.stderr)
        (native / "batches/CCB000001.json").unlink()
        repaired = subprocess.run(command + ["--forcerun", "crisprcasfinder_collect"],
                                  env=environment, text=True, capture_output=True, timeout=120)
        self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
        repaired_calls = [json.loads(line)["contig"] for line in calls.read_text().splitlines()]
        self.assertEqual(repaired_calls.count("CCF000001"), 2)
        self.assertEqual(repaired_calls.count("CCF000002"), 2)
        self.assertEqual(repaired_calls.count("CCF000003"), 1)


if __name__ == "__main__":
    unittest.main()
