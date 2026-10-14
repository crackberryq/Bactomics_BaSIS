"""Setup failure/reuse contracts; fake native manager, real Snakemake graph."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from basis import environment_setup as cli
from basis import environment_workflow as workflow


FAKE_MANAGER = r'''
import json,os,subprocess,sys
from pathlib import Path
args=sys.argv[1:]
with Path(os.environ['BASIS_FAKE_CALLS']).open('a') as f:
 f.write(json.dumps({'args':args,'strict':os.environ.get('CONDA_CHANNEL_PRIORITY'),
 'pythonpath':os.environ.get('PYTHONPATH'),'virtualenv':os.environ.get('VIRTUAL_ENV'),
 'pythonhome':os.environ.get('PYTHONHOME'),'pythonuserbase':os.environ.get('PYTHONUSERBASE'),
 'path':os.environ.get('PATH')})+'\n')
if args == ['info','--json']:
 print(json.dumps({'conda_prefix':str(Path(sys.argv[0]).parent.parent/'fake-base'),'platform':'linux-64'}));raise SystemExit(0)
if args == ['--version']:
 print('conda 24.9.2');raise SystemExit(0)
if args[:2] == ['config','--get']:
 print(json.dumps({'get':{'channel_priority':'strict'}}));raise SystemExit(0)
if args and args[0] == 'clean':
 print('fake package cleanup');raise SystemExit(0)
prefix=Path(args[args.index('--prefix')+1])
if args[0]=='run':
 raise SystemExit(subprocess.run(args[args.index('--prefix')+2:]).returncode)
prefix.mkdir(parents=True,exist_ok=True)
if os.environ.get('BASIS_FAKE_FAIL')=='1':
 (prefix/'partial-download').write_text('kept')
 print('simulated interrupted download',flush=True)
 raise SystemExit(1)
meta=prefix/'conda-meta';meta.mkdir(exist_ok=True)
(meta/'history').write_text('fake transaction for software test\n')
binary=prefix/'bin';binary.mkdir(exist_ok=True)
recipe=Path(args[args.index('--file')+1]).read_text()
if 'basis-minced' in recipe:
 packages={'minced':'0.4.2','openjdk':'17.0.17'}
 tools={'minced':'minced 0.4.2','java':'openjdk version 17.0.17'}
else:
 packages={'diamond':'2.2.8'};tools={'diamond':'diamond version 2.2.8'}
for name,version in packages.items():
 (meta/(name+'.json')).write_text(json.dumps({'name':name,'version':version,'build':'test_0'}))
for name,output in tools.items():
 p=binary/name;p.write_text('#!/bin/sh\nprintf "%s\\n" "'+output+'"\n');p.chmod(0o755)
print('fake environment created',flush=True)
'''


class EnvironmentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace with spaces"
        self.bin = self.root / "manager-bin"
        self.bin.mkdir()
        for manager in ("conda", "mamba", "micromamba"):
            path = self.bin / manager
            path.write_text("#!" + sys.executable + "\n" + FAKE_MANAGER)
            path.chmod(0o755)
        activate = self.root / "fake-base/bin/activate"
        activate.parent.mkdir(parents=True)
        activate.write_text('export CONDA_PREFIX="$1"\nexport CONDA_EXE="' + str(self.bin / 'conda') + '"\nexport PATH="$CONDA_PREFIX/bin:$PATH"\n')
        self.calls = self.root / "calls.jsonl"
        self.environment = patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                                                  "BASIS_FAKE_CALLS": str(self.calls)})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def run_setup(self, **kwargs):
        kwargs.setdefault("engine", "local")
        names = kwargs.pop("names", ("minced",))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return workflow.run_setup(self.workspace, self.workspace / ".basis-envs", names, **kwargs)

    def read_calls(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def test_numeric_constraint_checks_fail_closed(self):
        self.assertTrue(workflow.version_matches("3.11.17", "=3.11"))
        self.assertFalse(workflow.version_matches("3.12.1", "=3.11"))
        self.assertTrue(workflow.version_matches("2.2.8", ">=2.1,<3"))
        self.assertFalse(workflow.version_matches("3.0.0", ">=2.1,<3"))
        self.assertTrue(workflow.version_matches("1.7.3", "==1.7.3"))
        self.assertFalse(workflow.version_matches("1.7.30", "==1.7.3"))
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            workflow.version_matches("3.0rc1", ">=2,<3")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            workflow.version_matches("3.0", ">=2|<3")

    def test_plan_only_never_writes_or_calls_manager(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["--workspace", str(self.workspace), "--env", "minced"])
        self.assertEqual(code, 0)
        self.assertFalse(self.workspace.exists())
        self.assertEqual(self.read_calls(), [])

    def test_all_supported_managers_create_verified_tools_only(self):
        for manager in ("conda", "mamba", "micromamba"):
            self.workspace = self.root / manager
            result, code = self.run_setup(manager=manager)
            self.assertEqual(code, 0, result)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["scope"], "tool_environments_only")
            self.assertEqual(result["databases"], "not_assessed")
            self.assertEqual(result["models"], "not_assessed")
            self.assertEqual(result["analysis_readiness"], "not_assessed")
            record = json.loads(Path(result["environments"][0]["record"]).read_text())
            self.assertEqual(len(record["verification"]["probes"]), 2)
            self.assertEqual(len(record["verification"]["checked_dependencies"]), 2)
            self.assertEqual(record["operation"], "created")
        self.assertTrue(all(call["strict"] == "strict" for call in self.read_calls()))

    def test_reuse_rechecks_executables_without_installing(self):
        self.run_setup()
        count = len(self.read_calls())
        result, code = self.run_setup()
        self.assertEqual(code, 0)
        self.assertEqual(result["environments"][0]["operation"], "reused")
        calls = self.read_calls()[count:]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call["args"][0] == "run" for call in calls))

    def test_existing_arbitrary_directory_is_failed_and_preserved(self):
        prefix = self.workspace / ".basis-envs/basis-minced"
        prefix.mkdir(parents=True)
        sentinel = prefix / "keep"
        sentinel.write_text("untouched")
        result, code = self.run_setup(retry_failed=True)
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("no Conda transaction history", result["environments"][0]["error"])
        self.assertEqual(sentinel.read_text(), "untouched")
        self.assertEqual(self.read_calls(), [])

    def test_failed_creation_is_logged_and_repair_requires_explicit_retry(self):
        with patch.dict(os.environ, {"BASIS_FAKE_FAIL": "1"}):
            failed, code = self.run_setup()
        self.assertEqual(code, 2)
        row = json.loads(Path(failed["environments"][0]["record"]).read_text())
        self.assertEqual(row["status"], "failed")
        self.assertIn("simulated interrupted download", Path(row["log"]).read_text())
        prefix = Path(row["prefix"])
        self.assertTrue((prefix / "partial-download").is_file())
        count = len(self.read_calls())
        blocked, code = self.run_setup()
        self.assertEqual(code, 2)
        self.assertEqual(len(self.read_calls()), count)
        recovered, code = self.run_setup(retry_failed=True)
        self.assertEqual(code, 0, recovered)
        self.assertEqual(recovered["environments"][0]["operation"], "retry")
        self.assertEqual(self.read_calls()[count]["args"][:2], ["env", "update"])
        self.assertTrue((prefix / "partial-download").is_file())
        self.assertEqual(json.loads(Path(failed["environments"][0]["record"]).read_text())["status"], "failed")

    def test_missing_executable_never_reuses_ready_inventory(self):
        result, _ = self.run_setup()
        prefix = Path(result["environments"][0]["prefix"])
        (prefix / "bin/minced").unlink()
        count = len(self.read_calls())
        result, code = self.run_setup(retry_failed=True)
        self.assertEqual(code, 2)
        self.assertIn("executable missing", result["environments"][0]["error"])
        self.assertEqual(len(self.read_calls()), count)

    def test_version_mismatch_preserves_existing_package_metadata(self):
        result, _ = self.run_setup()
        meta = Path(result["environments"][0]["prefix"]) / "conda-meta/minced.json"
        data = json.loads(meta.read_text()); data["version"] = "0.3.0"
        meta.write_text(json.dumps(data))
        count = len(self.read_calls())
        result, code = self.run_setup(retry_failed=True)
        self.assertEqual(code, 2)
        self.assertIn("does not satisfy minced=0.4.2", result["environments"][0]["error"])
        self.assertEqual(len(self.read_calls()), count)
        self.assertEqual(json.loads(meta.read_text())["version"], "0.3.0")

    def test_attempt_report_directory_cannot_overwrite_previous_evidence(self):
        outdir = self.root / "explicit report"
        result, _ = self.run_setup(report_dir=outdir)
        original = (outdir / "plan.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.run_setup(report_dir=outdir)
        self.assertEqual((outdir / "plan.json").read_bytes(), original)
        self.assertEqual(result["summary_path"], str(outdir / "summary.json"))

    def test_manager_child_drops_launcher_python_routing(self):
        fake_venv = self.root / "launcher-venv"
        contamination = self.root / "contamination"
        contamination.mkdir()
        (contamination / "json.py").write_text("raise RuntimeError('launcher polluted native tools')")
        with patch.dict(os.environ, {"PYTHONPATH": str(contamination), "VIRTUAL_ENV": str(fake_venv),
                                     "PYTHONHOME": str(self.root / "invalid-python-home"),
                                     "PYTHONUSERBASE": str(contamination),
                                     "PATH": str(fake_venv / "bin") + os.pathsep + os.environ["PATH"]}):
            result, code = self.run_setup(manager="micromamba")
        self.assertEqual(code, 0, result)
        for call in self.read_calls():
            for name in ("pythonpath", "virtualenv", "pythonhome", "pythonuserbase"):
                self.assertIsNone(call[name])
            self.assertNotIn(str(fake_venv / "bin"), call["path"].split(os.pathsep))

    def test_plan_and_recipe_tampering_are_rejected(self):
        result, _ = self.run_setup()
        plan_path = Path(result["summary_path"]).parent / "plan.json"
        plan = json.loads(plan_path.read_text())
        plan["cores"] = 99
        plan_path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, "plan changed"):
            workflow.load_plan(plan_path)

    @unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake not installed")
    def test_real_snakemake_runs_environment_workers_before_summary(self):
        result, code = self.run_setup(engine="snakemake", names=("minced", "diamond"), cores=2)
        self.assertEqual(code, 0, result)
        self.assertEqual({row["status"] for row in result["environments"]}, {"ready"})
        self.assertEqual(len(result["environments"]), 2)
        directory = Path(result["summary_path"]).parent
        log = (directory / "snakemake.log").read_text()
        self.assertIn("rule setup_environment", log)
        self.assertIn("Creating conda environment", log)
        self.assertTrue(all(row["backend"] == "managed" for row in result["environments"]))
        self.assertTrue(all(Path(row["prefix"]).name != "basis-" + row["name"] for row in result["environments"]))
        self.assertIn("rule setup_summary", log)
        self.assertIn("100%", log)
        plan = workflow.load_plan(directory / "plan.json")
        for entry in plan["environments"]:
            self.assertEqual(Path(entry["recipe"]).read_bytes(), cli.recipe_resource(entry["name"]).read_bytes())
        self.assertTrue(all(call["pythonpath"] is None for call in self.read_calls()))
        count = len(self.read_calls())
        repeated, repeated_code = self.run_setup(engine="snakemake", names=("minced", "diamond"), cores=2)
        self.assertEqual(repeated_code, 0, repeated)
        self.assertEqual([row["prefix"] for row in repeated["environments"]],
                         [row["prefix"] for row in result["environments"]])
        self.assertFalse(any(call["args"][:2] == ["env", "create"] for call in self.read_calls()[count:]))

    @unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake not installed")
    def test_snakemake_verifies_existing_named_environment_without_deployment(self):
        original, _ = self.run_setup()
        prefix = original["environments"][0]["prefix"]
        count = len(self.read_calls())
        result, code = self.run_setup(engine="snakemake")
        self.assertEqual(code, 0, result)
        row = result["environments"][0]
        self.assertEqual(row["backend"], "external")
        self.assertEqual(row["prefix"], prefix)
        self.assertTrue(all(call["args"][0] == "run" for call in self.read_calls()[count:]))

    @unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake not installed")
    def test_native_conda_deployment_failure_is_not_reported_as_ready(self):
        with patch.dict(os.environ, {"BASIS_FAKE_FAIL": "1"}):
            result, code = self.run_setup(engine="snakemake")
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["environments"][0]["status"], "failed")
        self.assertIn("Snakemake exited", result["scheduler_error"])
        directory = Path(result["summary_path"]).parent
        self.assertIn("simulated interrupted download", (directory / "snakemake.log").read_text())
        self.assertFalse(Path(result["environments"][0]["record"]).exists())


if __name__ == "__main__":
    unittest.main()
