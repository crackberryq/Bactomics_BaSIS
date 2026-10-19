"""Real Snakemake acceptance using explicitly synthetic tools and annotations.

The fake MinCED executable exists only in each test's temporary PATH.  These
checks establish orchestration/restart/report contracts, not biological accuracy.
Real Conda deployment is tested separately by check_environment_recovery.py.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.metadata
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

import yaml

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "workflow/vendor", ROOT / "workflow/lib"):
    sys.path.insert(0, str(directory))

from safety.runtime import DirectRunner


class DirectRunnerTests(unittest.TestCase):
    def test_already_selected_environment_and_argument_boundaries_are_preserved(self):
        with tempfile.TemporaryDirectory(prefix="basis-direct-") as temp:
            root = Path(temp)
            runner = DirectRunner({"threads": 2}, root / "command.log")
            script = "import json, os, sys; print(json.dumps({'argv': sys.argv[1:], 'prefix': os.environ.get('CONDA_PREFIX'), 'path': os.environ['PATH'], 'pythonpath': os.environ.get('PYTHONPATH')}))"
            arguments = ["space in value", "", "literal;$HOME", "quote'and\"double"]
            with patch.dict(os.environ, {"CONDA_PREFIX": "/synthetic/already-activated", "PYTHONPATH": "/controller-only"}):
                expected_path = os.environ["PATH"]
                result = runner.run([sys.executable, "-c", script, *arguments])
            value = json.loads(result.stdout)
            self.assertEqual(value["argv"], arguments)
            self.assertEqual(value["prefix"], "/synthetic/already-activated")
            self.assertEqual(value["path"], expected_path)
            self.assertIsNone(value["pythonpath"])
            self.assertEqual(runner.commands[0]["argv"][0], sys.executable)
            self.assertEqual(len(runner.commands), 1)

    def test_nested_activation_and_workflow_launchers_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "command_prefix"):
                DirectRunner({"command_prefix": ["conda", "run"]}, Path(temp) / "commands.log")
            runner = DirectRunner(logfile=Path(temp) / "commands.log")
            for launcher in ("conda", "mamba", "micromamba", "snakemake", "basis"):
                with self.subTest(launcher=launcher), self.assertRaisesRegex(ValueError, "launcher"):
                    runner.run([launcher, "--version"])


class StandaloneWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="basis-snake-acceptance-"))
        self.workflow_root = ROOT
        if os.environ.get("BASIS_TEST_NO_BENCHMARK") == "1":
            # Explicit local fallback for containers whose /proc PID namespace
            # is inconsistent with child PIDs. CI always tests production files
            # unchanged; this switch affects only a temporary test copy.
            self.workflow_root = self.root / "workflow-fixture"
            (self.workflow_root / "workflow/rules").mkdir(parents=True)
            for name in ("BaSIS.smk", "config.yaml"):
                shutil.copy2(ROOT / name, self.workflow_root / name)
            for name in ("lib", "vendor"):
                (self.workflow_root / "workflow" / name).symlink_to(ROOT / "workflow" / name, target_is_directory=True)
            (self.workflow_root / "envs").symlink_to(ROOT / "envs", target_is_directory=True)
            for source in (ROOT / "workflow/rules").glob("*.smk"):
                lines, skipped_indent = [], None
                for line in source.read_text().splitlines():
                    indentation = len(line) - len(line.lstrip())
                    if re.match(r"\s*benchmark:", line):
                        skipped_indent = indentation
                        continue
                    if skipped_indent is not None:
                        if not line.strip() or indentation > skipped_indent:
                            continue
                        skipped_indent = None
                    lines.append(line)
                (self.workflow_root / "workflow/rules" / source.name).write_text("\n".join(lines) + "\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "native-calls.jsonl"
        self.allow = self.root / "allow-sample-b"
        self.bad_setup = self.root / "fail-environment-check"
        self.isolates = [self.root / "isolateA", self.root / "isolateB"]
        for path in self.isolates:
            self._annotation(path)
        self._binary("minced", r'''
            import json, os, sys
            from pathlib import Path
            if "--version" in sys.argv:
                if Path(os.environ["BASIS_FIXTURE_BAD_SETUP"]).exists():
                    print("Synthetic environment probe failure", file=sys.stderr)
                    raise SystemExit(31)
                print("minced 0.4.2")
                raise SystemExit(0)
            assert sys.argv[1] == "-gffFull", sys.argv
            assembly, text, gff = map(Path, sys.argv[2:5])
            sample = assembly.stem
            metadata = assembly.parent.parent / "safety/metadata"
            event = {"sample": sample, "argv": sys.argv[1:], "metadata_files": sorted(p.name for p in metadata.glob("*.json")),
                     "ready_isolates": sorted(p.parents[2].name for p in assembly.parent.parent.parent.glob("isolate*/safety/metadata/setup.ready.json"))}
            with open(os.environ["BASIS_FIXTURE_CALLS"], "a") as handle:
                handle.write(json.dumps(event) + "\n")
            if sample == "isolateB" and not Path(os.environ["BASIS_FIXTURE_ALLOW"]).exists():
                print("Intentional synthetic native task failure", file=sys.stderr)
                raise SystemExit(29)
            text.write_text("")
            count = sum(line.startswith(">") for line in assembly.read_text().splitlines())
            gff.write_text("##gff-version 3\n" * count)
            print("Synthetic no-array output; not a biological test.")
        ''')
        self._binary("java", 'print(\'openjdk version "17.0.0" synthetic test executable\')')
        names = ("amrfinder", "rgi", "vfdb", "antismash", "genomad", "mobsuite", "integronfinder",
                 "bacmet", "isescan", "icescreen", "crisprcasfinder", "minced", "mlst", "pathogenfinder2")
        self.config = self.root / "config.yaml"
        self.config.write_text(yaml.safe_dump({
            "isolates": [str(path) for path in self.isolates],
            "project": "Synthetic orchestration acceptance; no biological validation",
            "modules": {name: {"enabled": name == "minced"} for name in names},
            "deep": {"enabled": False}, "extend": False, "threads": 1,
            "resources": {"directory": str(self.root / "resources"), "policy": "auto"},
        }), encoding="utf-8")
        self.environment = dict(os.environ)
        self.environment.update(PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                                CONDA_PREFIX=str(self.root),
                                BASIS_FIXTURE_CALLS=str(self.calls), BASIS_FIXTURE_ALLOW=str(self.allow),
                                BASIS_FIXTURE_BAD_SETUP=str(self.bad_setup))
        self.invocations = 0

    def tearDown(self):
        if os.environ.get("BASIS_TEST_KEEP_WORKDIR"):
            print("Synthetic acceptance workspace:", self.root)
        else:
            shutil.rmtree(self.root)

    def _binary(self, name, body):
        path = self.bin / name
        path.write_text("#!" + sys.executable + "\n" + textwrap.dedent(body).strip() + "\n", encoding="utf-8")
        path.chmod(0o755)

    def _annotation(self, path):
        annotation = path / "annotation"
        annotation.mkdir(parents=True)
        sample = path.name
        (annotation / f"{sample}.fna").write_text(">contig1\nATGAAATTTTAA\n", encoding="utf-8")
        (annotation / f"{sample}.faa").write_text(">synthetic_cds\nMKF\n", encoding="utf-8")
        (annotation / f"{sample}.gff").write_text("##gff-version 3\ncontig1\tfixture\tCDS\t1\t12\t.\t+\t0\tID=synthetic_cds;locus_tag=synthetic_cds;transl_table=11\n", encoding="utf-8")
        (annotation / f"{sample}.gbk").write_text(
            "LOCUS       contig1                   12 bp    DNA     linear   BCT 01-JAN-2000\n"
            "DEFINITION  Synthetic infrastructure test fixture.\nACCESSION   contig1\nVERSION     contig1\n"
            "FEATURES             Location/Qualifiers\n     source          1..12\n"
            "                     /organism=\"synthetic fixture\"\n     CDS             1..12\n"
            "                     /locus_tag=\"synthetic_cds\"\n                     /translation=\"MKF\"\n"
            "                     /transl_table=11\nORIGIN\n        1 atgaaattttaa\n//\n", encoding="utf-8")

    def snake(self, *arguments, success=True):
        self.invocations += 1
        command = [sys.executable, "-m", "snakemake", "--snakefile", str(self.workflow_root / "BaSIS.smk"),
                   "--configfile", str(self.config), "--directory", str(self.root),
                   "--cores", "2", "--rerun-incomplete", "--keep-going", *map(str, arguments)]
        result = subprocess.run(command, cwd=ROOT, env=self.environment, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        (self.root / f"invocation-{self.invocations}.log").write_text(result.stdout, encoding="utf-8")
        if (result.returncode == 0) != success:
            failures = []
            for log in self.root.glob("isolate*/safety/logs/*.log"):
                text = log.read_text(errors="replace")
                if "Traceback" in text or "Error" in text:
                    failures.append(f"\n{log}:\n{text[-3000:]}")
            self.fail(f"Unexpected Snakemake exit {result.returncode}\n{result.stdout[-8000:]}\n" + "".join(failures))
        return result

    def events(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def native_snapshot(self, isolate):
        return {str(path.relative_to(isolate)): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                for path in (isolate / "safety/native").rglob("*") if path.is_file()}

    def test_direct_failure_resume_noop_and_report_only_refresh(self):
        self.assertEqual(importlib.metadata.version("snakemake"), "9.6.2")
        preview = self.snake("--dry-run")
        self.assertIn("minced", preview.stdout)
        self.assertFalse(self.calls.exists(), "Dry-run invoked a native analysis")
        failed = self.snake(success=False)
        self.assertIn("minced", failed.stdout)
        counts = Counter(event["sample"] for event in self.events())
        self.assertEqual(counts, {"isolateA": 1, "isolateB": 1})
        self.assertTrue(all("setup.ready.json" in event["metadata_files"] for event in self.events()),
                        "A native job started before the setup barrier was written")
        self.assertTrue(all(event["ready_isolates"] == ["isolateA", "isolateB"] for event in self.events()),
                        "A native job started before the complete batch setup barrier")
        first = self.native_snapshot(self.isolates[0])
        self.assertTrue(first, "Successful sibling's native files were not retained")
        self.assertFalse((self.isolates[1] / "safety/evidence/minced.json").exists(), "Failed task was normalized as success")
        self.allow.touch()
        self.snake()
        self.assertEqual(self.native_snapshot(self.isolates[0]), first, "Resume changed a successful sibling's native files")
        self.assertEqual(Counter(event["sample"] for event in self.events()), {"isolateA": 1, "isolateB": 2})
        for isolate in self.isolates:
            self.assertTrue((isolate / "safety/report.html").is_file())
            record = json.loads((isolate / "safety/evidence/minced.json").read_text())
            self.assertEqual(record["execution_status"], "completed")
            self.assertEqual(record["metrics"]["assessed_contig_count"], 1)
            self.assertEqual(record["metrics"]["array_count"], 0)
        snapshots = [self.native_snapshot(isolate) for isolate in self.isolates]
        events = self.events()
        idle = self.snake()
        self.assertIn("Nothing to be done", idle.stdout)
        self.assertEqual(self.events(), events)
        report = self.isolates[0] / "safety/report.html"
        before_report = report.stat().st_mtime_ns
        self.snake(str(report), "--forcerun", "safety_report")
        self.assertGreater(report.stat().st_mtime_ns, before_report)
        self.assertEqual(self.events(), events, "Report redraw ran an analysis tool")
        self.assertEqual([self.native_snapshot(isolate) for isolate in self.isolates], snapshots)
        # Each invocation publishes stable isolate-local outputs, not timestamped
        # attempt trees which prevent Snakemake itself from deciding what resumes.
        for isolate in self.isolates:
            self.assertFalse(list((isolate / "safety").rglob("attempts")))

    def test_failed_setup_blocks_native_analysis(self):
        self.bad_setup.touch()
        result = self.snake(success=False)
        diagnostics = result.stdout + "\n" + "\n".join(
            path.read_text(errors="replace") for path in self.root.rglob("*.log"))
        self.assertIn("Synthetic environment probe failure", diagnostics,
                      "Failure did not exercise the intended deployment/resource check")
        self.assertFalse(self.events(), "An analysis started before its environment/resource check passed")
        for isolate in self.isolates:
            self.assertFalse((isolate / "safety/native/minced").exists())

    def test_invalid_sample_blocks_batch_analyses(self):
        self.allow.touch()
        protein = self.isolates[1] / "annotation/isolateB.faa"
        protein.write_text(">synthetic_cds\nMQF\n", encoding="utf-8")
        result = self.snake(success=False)
        diagnostics = result.stdout + "\n" + "\n".join(
            path.read_text(errors="replace") for path in self.root.rglob("*.log"))
        self.assertIn("residue 2", diagnostics,
                      "Failure did not exercise the intended annotation mismatch")
        self.assertFalse(self.events(), "A sample analysis started before every batch input passed validation")


if __name__ == "__main__":
    unittest.main()
