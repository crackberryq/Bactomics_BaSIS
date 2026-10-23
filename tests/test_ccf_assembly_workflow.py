"""Exercise production scheduling with synthetic whole-assembly output only.

The fake worker replaces the native command boundary; receipt storage and
normalization are real. No biological tool, database download or Conda install
is used. Native command/parser contracts have separate worker tests.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety.result_store import sha256


WORKER = r'''import argparse, json, pathlib, sys
from basis.inputs import read_fasta
from safety.extend import main as extend_main, write_json
from safety.result_store import execution_identity, run_cached

root = pathlib.Path(__file__).resolve().parent.parent
if sys.argv[1:3] == ["-m", "safety.extend"]:
    raise SystemExit(extend_main(sys.argv[3:]))
if sys.argv[1:3] != ["-m", "safety.ccf_assembly"]:
    raise SystemExit("unexpected synthetic worker invocation")
parser = argparse.ArgumentParser()
for key in ("sample", "config", "native", "receipt", "store"):
    parser.add_argument("--" + key, required=True)
parser.add_argument("--threads", type=int, required=True)
parser.add_argument("--sources", nargs="+", required=True)
args = parser.parse_args(sys.argv[3:])
sample = json.loads(pathlib.Path(args.sample).read_text())
config = json.loads(pathlib.Path(args.config).read_text())
records = read_fasta(sample["assembly"])
identity = execution_identity("crisprcasfinder", sample, config,
    sources=args.sources, executable=sys.executable)

def execute(payload):
    with (root / "invocations.jsonl").open("a") as stream:
        stream.write(json.dumps({"sample": sample["sample_id"],
            "records": list(records), "threads": args.threads}) + "\n")
    (payload / "synthetic-output.txt").write_text("synthetic whole-assembly output\n")
    if sample["sample_id"] == "isolateB" and not (root / "allow-isolateB").exists():
        raise RuntimeError("synthetic interrupted assembly")
    write_json(payload / "parsed.json", {"findings": [],
        "interpretation": "inconclusive", "artifacts": [],
        "metrics": {"assessed_contig_count": len(records),
                    "assessed_contigs": list(records), "execution_mode": "whole_assembly"}})

def verify(payload):
    row = json.loads((payload / "parsed.json").read_text())
    if row["metrics"]["assessed_contigs"] != list(records):
        raise ValueError("synthetic coverage incomplete")

run_cached("crisprcasfinder", identity, args.native, args.receipt, args.store,
           execute, verify)
'''


@unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake is not installed")
class WholeAssemblySchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis-assembly-workflow-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.samples = {}
        self.settings = {"enabled": True, "threads": 1, "memory_mb": 128,
                         "execution_scope": "assembly", "cache": True}
        self.environment = self.root / "environment.json"
        self.write_json(self.environment, {"schema": "basis-snake-environment-v1",
            "status": "completed", "packages": [{"name": "synthetic-fixture", "version": "1"}]})
        self.database = self.root / "synthetic-database.txt"
        self.database.write_text("synthetic resource identity only\n")
        self.resource = self.root / "resource.json"
        self.write_json(self.resource, {"schema": "basis-snakemake-resource-v1",
            "resource": "crisprcasfinder", "status": "completed",
            "artifacts": {str(self.database): {"bytes": self.database.stat().st_size,
                                             "sha256": sha256(self.database)}},
            "result": {"version": "synthetic-v1"}})
        for name, count in (("isolateA", 1001), ("isolateB", 3)):
            isolate = self.root / name
            metadata = isolate / "safety/metadata"
            metadata.mkdir(parents=True)
            assembly = isolate / "assembly.fna"
            assembly.write_text("".join(f">synthetic{n:04d}\nACGT\n" for n in range(count)))
            sample = {"sample_id": name, "assembly": str(assembly)}
            self.samples[str(isolate)] = sample
            self.write_json(metadata / "sample.json", sample)
            self.write_json(metadata / "validation.json", {"protein_coordinates": {},
                "contig_lengths": {f"synthetic{n:04d}": 4 for n in range(count)}})
            self.write_json(metadata / "config.json", self.config())
            (metadata / "setup.ready").touch()
        binary = self.root / "bin/python"
        binary.parent.mkdir()
        binary.write_text("#!" + sys.executable + "\n" + WORKER)
        binary.chmod(0o755)
        self.rules = ROOT / "workflow/rules/extend.smk"
        # Some managed test hosts deny process inspection used by Snakemake's
        # benchmark sampler. CI runs the unmodified production benchmark rules.
        if os.environ.get("BASIS_TEST_NO_BENCHMARK") == "1":
            self.rules = self.root / "extend.smk"
            self.rules.write_text(re.sub(r"(?m)^    benchmark:\n        [^\n]+\n", "",
                (ROOT / "workflow/rules/extend.smk").read_text()))
        self.snakefile = self.root / "Snakefile"
        self.snakefile.write_text(textwrap.dedent(f'''\
            import json
            import sys
            from pathlib import Path
            ROOT = Path({str(ROOT)!r})
            sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
            CODE_PATH = str(ROOT / "workflow/lib") + ":" + str(ROOT / "workflow/vendor")
            PYTHON = {str(binary)!r}
            class FixtureContext:
                samples = {self.samples!r}
                def setup_target(self, isolate):
                    return str(Path(isolate) / "safety/metadata/setup.ready")
                def module_config_path(self, isolate, module):
                    return str(Path(isolate) / "safety/metadata/config.json")
                def module_config(self, isolate, module):
                    return json.loads(Path(self.module_config_path(isolate, module)).read_text())["modules"][module]
                def module_enabled(self, isolate, module):
                    return self.module_config(isolate, module).get("enabled", True)
                def resource_inputs(self, module):
                    return [{str(self.resource)!r}]
                def env(self, module):
                    return str(ROOT / "envs" / (module + ".yml"))
            CTX = FixtureContext()
            rule all:
                input:
                    lambda w: [str(Path(i) / "safety/evidence/crisprcasfinder.json") for i in CTX.samples] + extend_completion_targets()
            include: {str(self.rules)!r}
            '''))

    @staticmethod
    def write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n")

    def config(self):
        return {"modules": {"crisprcasfinder": dict(self.settings)},
                "module_decisions": {"crisprcasfinder": {"action": "run"}},
                "environment_receipts": [str(self.environment)],
                "resource_receipts": [{"path": str(self.resource)}]}

    def run_workflow(self, *extra, success=True):
        result = subprocess.run([sys.executable, "-m", "snakemake", "--snakefile",
            str(self.snakefile), "--cores", "2", "--resources", "mem_mb=1024",
            "--rerun-incomplete", "--keep-going", *extra], cwd=self.root,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + self.logs())
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result.stdout

    def logs(self):
        return "\n".join(p.read_text() for p in self.root.glob("isolate*/safety/logs/*.log"))

    def calls(self):
        path = self.root / "invocations.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def path(self, name, suffix):
        return self.root / name / "safety" / suffix

    def receipt(self, name):
        return self.path(name, "completion/crisprcasfinder.json")

    def allow_all(self):
        (self.root / "allow-isolateB").touch()

    def test_one_job_per_isolate_failure_resume_noop_and_missing_receipt(self):
        dryrun = self.run_workflow("--dry-run")
        self.assertRegex(dryrun, r"crisprcasfinder_assembly\s+2\b")
        self.assertNotIn("crisprcasfinder_contig", dryrun)
        self.assertNotIn("crisprcasfinder_batch", dryrun)
        self.assertEqual(self.calls(), [])
        self.run_workflow(success=False)
        successful_receipt = self.receipt("isolateA").read_bytes()
        self.assertFalse(self.receipt("isolateB").exists())
        failed = list(self.path("isolateB", "results-cache/crisprcasfinder").glob("*/attempt-*/failure.json"))
        self.assertEqual(len(failed), 1)
        self.assertTrue((failed[0].parent / "data/synthetic-output.txt").is_file())
        self.allow_all()
        self.run_workflow()
        self.assertEqual(self.receipt("isolateA").read_bytes(), successful_receipt)
        calls = self.calls()
        self.assertEqual([row["sample"] for row in calls].count("isolateA"), 1)
        self.assertEqual([row["sample"] for row in calls].count("isolateB"), 2)
        for name, count in (("isolateA", 1001), ("isolateB", 3)):
            event = next(row for row in calls if row["sample"] == name)
            self.assertEqual(len(event["records"]), count)
            evidence = json.loads(self.path(name, "evidence/crisprcasfinder.json").read_text())
            self.assertEqual(evidence["execution_status"], "completed")
            self.assertEqual(evidence["metrics"]["assessed_contig_count"], count)
        self.assertIn("Nothing to be done", self.run_workflow())
        self.assertEqual(self.calls(), calls)
        self.receipt("isolateA").unlink()
        self.run_workflow()
        self.assertEqual(self.receipt("isolateA").read_bytes(), successful_receipt)
        self.assertEqual(self.calls(), calls)
        self.assertIn("Nothing to be done", self.run_workflow())

    def test_changed_assembly_reruns_only_its_isolate_and_retains_prior_success(self):
        self.allow_all()
        self.run_workflow()
        before = json.loads(self.receipt("isolateA").read_text())
        other = self.receipt("isolateB").read_bytes()
        assembly = self.root / "isolateA/assembly.fna"
        assembly.write_text(assembly.read_text().replace("ACGT", "ACGA", 1))
        self.run_workflow()
        calls = self.calls()
        self.assertEqual([row["sample"] for row in calls].count("isolateA"), 2)
        self.assertEqual([row["sample"] for row in calls].count("isolateB"), 1)
        after = json.loads(self.receipt("isolateA").read_text())
        self.assertNotEqual(before["key"], after["key"])
        self.assertTrue(Path(before["payload_root"]).is_dir())
        self.assertEqual(self.receipt("isolateB").read_bytes(), other)

    def test_corrupt_payload_stops_before_any_native_execution(self):
        self.allow_all()
        self.run_workflow()
        calls = self.calls()
        self.path("isolateA", "native/crisprcasfinder/synthetic-output.txt").write_text("corrupted\n")
        self.assertIn("integrity", self.run_workflow(success=False).lower())
        self.assertEqual(self.calls(), calls)
        self.receipt("isolateA").unlink()
        self.run_workflow(success=False)
        self.assertIn("integrity", self.logs().lower())
        self.assertEqual(self.calls(), calls)

    def test_disabled_module_normalizes_scope_without_native_execution(self):
        for isolate in self.samples:
            config = self.config()
            config["modules"]["crisprcasfinder"]["enabled"] = False
            config["module_decisions"]["crisprcasfinder"] = {"action": "skip", "reason": "synthetic_disabled"}
            self.write_json(Path(isolate) / "safety/metadata/config.json", config)
        self.run_workflow()
        self.assertEqual(self.calls(), [])
        for name in ("isolateA", "isolateB"):
            evidence = json.loads(self.path(name, "evidence/crisprcasfinder.json").read_text())
            self.assertEqual(evidence["execution_status"], "not_assessed")
            self.assertEqual(evidence["reason"], "synthetic_disabled")
            self.assertFalse(self.receipt(name).exists())


if __name__ == "__main__":
    unittest.main()
