"""Scheduler contracts using synthetic imports, without biological tools."""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import os
import signal
import sys
import time
import threading
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from basis.config import ALL_MODULES, load_config
from basis.demo import create_demo
from basis.pipeline import _run_scheduler, make_plan, record_path, run_pipeline, run_worker
from basis.progress import verbosity
from basis.util import atomic_json, hash_object


class SchedulerLauncherTests(unittest.TestCase):
    def test_scheduler_output_is_live_logged_and_stderr_only_without_verbose(self):
        for verbose in ("0", "1"):
            with self.subTest(verbose=verbose), tempfile.TemporaryDirectory() as directory:
                log = Path(directory) / "snakemake.log"
                acknowledged = Path(directory) / "progress-seen"

                class LiveStderr(io.StringIO):
                    def write(self, value):
                        count = super().write(value)
                        if "native progress" in value:
                            acknowledged.touch()
                        return count

                stdout, stderr = io.StringIO(), LiveStderr()
                child = ("import sys,time; from pathlib import Path; "
                         "print('native stdout'); print('native stderr',file=sys.stderr); "
                         "print('native progress',end='\\r',flush=True); "
                         "deadline=time.monotonic()+3\n"
                         "while not Path(%r).exists() and time.monotonic()<deadline: time.sleep(.01)\n"
                         "sys.exit(0 if Path(%r).exists() else 7)") % (str(acknowledged), str(acknowledged))
                command = [sys.executable, "-u", "-c", child]
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                        patch.dict(os.environ, {"BASIS_VERBOSE": verbose}):
                    completed = _run_scheduler(command, log, os.environ.copy())
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), log.read_text())
                self.assertIn("native stdout", stderr.getvalue())
                self.assertIn("native stderr", stderr.getvalue())

    def test_stalled_terminal_does_not_block_scheduler_completion(self):
        release = threading.Event()
        entered = threading.Event()

        class StalledStderr(io.StringIO):
            def write(self, value):
                entered.set()
                release.wait(5)
                return super().write(value)

        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "snakemake.log"
            started = time.monotonic()
            try:
                with contextlib.redirect_stderr(StalledStderr()):
                    result = _run_scheduler([sys.executable, "-u", "-c", "print('saved progress')"],
                                            log, os.environ.copy())
                self.assertEqual(result.returncode, 0)
                self.assertTrue(entered.is_set())
                self.assertLess(time.monotonic() - started, 3)
                self.assertIn("saved progress", log.read_text())
            finally:
                release.set()

    def test_scheduler_flags_follow_verbose_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = make_plan(load_config(create_demo(directory)), False, False)
            for verbose in ("0", "1"):
                with self.subTest(verbose=verbose), patch.dict(os.environ, {"BASIS_VERBOSE": verbose}), \
                        patch("basis.pipeline._run_scheduler", return_value=subprocess.CompletedProcess([], 1)) as scheduler, \
                        contextlib.redirect_stderr(io.StringIO()):
                    run_pipeline(plan, engine="snakemake")
                command, log, env = scheduler.call_args.args
                self.assertEqual(str(log.parent), env["BASIS_SCHEDULER_LAUNCH_DIR"])
                self.assertTrue(env["BASIS_SCHEDULER_RUN_ID"].startswith("state-"))
                self.assertEqual(log.name, "snakemake.log")
                self.assertIn("--printshellcmds", command)
                self.assertIn("--show-failed-logs", command)
                self.assertIn("--keep-going", command)
                self.assertEqual("--verbose" in command, verbose == "1")
                self.assertEqual(env["PYTHONUNBUFFERED"], "1")
                self.assertEqual(env["BASIS_VERBOSE"], verbose)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux process-tree cancellation")
    def test_cancel_terminates_scheduler_and_separate_session_native_child(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signum=signum), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                native = "import os,time; from pathlib import Path; Path(%r).write_text(str(os.getpid())); time.sleep(60)" % str(root / "native.pid")
                scheduler = ("import os,subprocess,sys,time; from pathlib import Path; "
                             "Path(%r).write_text(str(os.getpid())); "
                             "subprocess.Popen([sys.executable,'-c',%r], start_new_session=True); "
                             "print('scheduler started',flush=True); time.sleep(60)") % (str(root / "scheduler.pid"), native)
                launcher = ("import os,sys; from pathlib import Path; from basis.pipeline import _run_scheduler; "
                            "_run_scheduler([sys.executable,'-c',%r],Path(%r),os.environ.copy())") % (scheduler, str(root / "snakemake.log"))
                proc = subprocess.Popen([sys.executable, "-c", launcher], stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True)
                pids = []
                try:
                    deadline = time.monotonic() + 5
                    while not (root / "native.pid").is_file() and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue((root / "native.pid").is_file())
                    pids = [int((root / name).read_text()) for name in ("scheduler.pid", "native.pid")]
                    proc.send_signal(signum)
                    stdout, stderr = proc.communicate(timeout=10)
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertEqual(stdout, "")
                    self.assertIn("scheduler started", stderr)
                    for pid in pids:
                        path = Path(f"/proc/{pid}/stat")
                        self.assertTrue(not path.exists() or path.read_text().rsplit(")", 1)[1].split()[0] == "Z",
                                        f"Owned process {pid} survived cancellation")
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.communicate(timeout=10)
                    for pid in pids:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_scheduler_start_failure_still_writes_explicit_failed_report(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = make_plan(load_config(create_demo(directory)), False, False)
            with patch("basis.pipeline._run_scheduler", side_effect=OSError("synthetic launch failure")):
                result, code = run_pipeline(plan, engine="snakemake")
            self.assertEqual(code, 2)
            self.assertIn("could not start", result["run"]["engine_error"])
            self.assertEqual(result["run"]["reporting_mode"], "launcher_infrastructure_fallback")
            evidence = json.loads(Path(result["artifacts"]["evidence_json"]).read_text())
            requested = [r for r in evidence["records"] if r["module_decision"]["requested"]]
            self.assertTrue(requested)
            self.assertEqual({r["execution_status"] for r in requested}, {"not_started"})
            self.assertEqual({r["execution_status"] for r in evidence["deep_records"]}, {"not_assessed"})
            self.assertEqual({r["reason"] for r in evidence["deep_records"]}, {"deepsafe_not_requested"})
            self.assertTrue(Path(result["artifacts"]["html"]).is_file())

    def test_unknown_engine_cannot_silently_run_serially(self):
        with self.assertRaisesRegex(ValueError, "Unknown execution engine"):
            run_pipeline({}, engine="typo")

    def test_partial_scheduler_failure_preserves_only_current_receipted_success(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = make_plan(load_config(create_demo(directory)), False, False)
            _, code = run_pipeline(plan)
            self.assertEqual(code, 0)

            def scheduler(command, logfile, env):
                run_id = env["BASIS_SCHEDULER_RUN_ID"]
                receipt = Path(plan["outdir"]) / "provenance/scheduler" / run_id / "DEMO_A/amrfinder.json"
                run_worker(plan["plan_path"], "DEMO_A", "amrfinder", receipt=str(receipt))
                return subprocess.CompletedProcess(command, 1)

            with patch("basis.pipeline._run_scheduler", side_effect=scheduler):
                result, code = run_pipeline(plan, engine="snakemake")
            self.assertEqual(code, 2)
            evidence = json.loads(Path(result["artifacts"]["evidence_json"]).read_text())
            requested = [r for r in evidence["records"] if r["module_decision"]["requested"]]
            completed = [(r["sample_id"], r["module"]) for r in requested if r["execution_status"] == "completed"]
            self.assertEqual(completed, [("DEMO_A", "amrfinder")])
            self.assertEqual(sum(r["execution_status"] == "not_started" for r in requested), len(requested) - 1)

    def test_bootstrap_failure_fallback_reports_roots_and_blocked_resources(self):
        from basis.bootstrap import receipt_path
        from basis.workflow_runtime import _write
        with tempfile.TemporaryDirectory() as directory:
            plan = make_plan(load_config(create_demo(directory)), True, False)
            plan["bootstrap_resources"] = [
                {"id": "card", "targets": ["rgi", "deepsafe"], "dependencies": [], "cache_key": "card-test"},
                {"id": "deep_catalogue", "targets": ["deepsafe"], "dependencies": ["card"], "cache_key": "catalogue-test"},
            ]

            def scheduler(command, logfile, env):
                run_id = env["BASIS_SCHEDULER_RUN_ID"]
                root = Path(plan["outdir"]) / "provenance/scheduler" / run_id
                _write(receipt_path(root, "card"), {"schema": "basis-bootstrap-receipt-v1",
                    "run_id": run_id, "plan_hash": plan["plan_hash"], "source_hash": plan["source_hash"],
                    "resource_id": "card", "cache_key": "card-test", "status": "failed",
                    "error": "fixture database download interrupted"})
                run_worker(plan["plan_path"], "DEMO_A", "amrfinder", receipt=str(root / "DEMO_A/amrfinder.json"))
                return subprocess.CompletedProcess(command, 1)

            with patch("basis.pipeline._run_scheduler", side_effect=scheduler), \
                    patch("basis.bootstrap.bootstrap_uses_conda", return_value=False):
                result, code = run_pipeline(plan, engine="snakemake")
            self.assertEqual(code, 2)
            evidence = json.loads(Path(result["artifacts"]["evidence_json"]).read_text())
            records = {r["module"]: r for r in evidence["records"] if r["sample_id"] == "DEMO_A"}
            self.assertEqual(records["amrfinder"]["execution_status"], "completed")
            self.assertIn("fixture database download interrupted", records["rgi"]["error"])
            self.assertEqual(records["rgi"]["reason"], "resource_preparation_failed")
            deep = next(r for r in evidence["deep_records"] if r["sample_id"] == "DEMO_A")
            self.assertEqual(deep["candidates"], [])
            self.assertIn("rgi", deep["upstream_incomplete"])
            self.assertNotIn("amrfinder", deep["upstream_incomplete"])
            self.assertEqual({r["resource_id"] for r in deep["blocked_resources"]}, {"card", "deep_catalogue"})
            self.assertIn("fixture database download interrupted", Path(result["artifacts"]["html"]).read_text())
            audit = {r["resource_id"]: r for r in result["run"]["resource_preparation"]}
            self.assertEqual(audit["deep_catalogue"]["status"], "blocked")

    def test_deep_refuses_upstream_changed_after_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = make_plan(load_config(create_demo(directory)), True, True)
            root = Path(plan["outdir"]) / "provenance/scheduler/fixture-run"
            for module in ALL_MODULES:
                run_worker(plan["plan_path"], "DEMO_A", module,
                           receipt=str(root / "DEMO_A" / (module + ".json")))
            path = record_path(plan, "DEMO_A", "amrfinder")
            changed = json.loads(path.read_text())
            changed["metrics"]["tampered_after_receipt"] = True
            atomic_json(path, changed)
            with patch("basis.pipeline.execute_deep") as execute:
                with self.assertRaisesRegex(ValueError, "changed after worker completion"):
                    run_worker(plan["plan_path"], "DEMO_A", "deepsafe",
                               receipt=str(root / "DEMO_A/deepsafe.json"), upstream_receipts=str(root))
                execute.assert_not_called()
            self.assertFalse((root / "DEMO_A/deepsafe.json").exists())


@unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake integration dependency is unavailable")
class NativeSchedulerSyntheticTests(unittest.TestCase):
    def setUp(self):
        # Spaces also exercise quoting of plans, inputs and output destinations.
        self.temp = tempfile.TemporaryDirectory(prefix="basis scheduler ")
        self.root = Path(self.temp.name)
        self.cfg = load_config(create_demo(self.root))
        self.cfg["execution"]["cores"] = 4
        for module in self.cfg["modules"].values():
            module["threads"] = 1
            module["memory_mb"] = 32
        self.cfg["deep"].update(threads=1, memory_mb=64)

    def tearDown(self):
        self.temp.cleanup()

    def run_plan(self, plan):
        result, code = run_pipeline(plan, engine="snakemake")
        evidence = json.loads(Path(result["artifacts"]["evidence_json"]).read_text())
        return result, code, evidence

    def test_real_dag_waits_for_extend_and_rechecks_cached_source_identity(self):
        plan = make_plan(self.cfg, True, True)
        with patch("basis.pipeline.render_workflow_report", side_effect=AssertionError("launcher must not render normal reports")):
            first, code, evidence = self.run_plan(plan)
        self.assertEqual(code, 0, first["run"].get("engine_error"))
        self.assertEqual(first["run"]["engine"], "snakemake")
        self.assertEqual(first["run"]["reporting_mode"], "snakemake_dag")
        receipts = Path(first["run"]["scheduler_receipt_dir"])
        launch = Path(first["run"]["scheduler_launch_dir"])
        for name in ("consolidated.json", "report-artifacts.json", "outcome.json"):
            self.assertTrue((launch / name).is_file())
        for sid in ("DEMO_A", "DEMO_B"):
            self.assertTrue((receipts / sid / "deep-stages/prepare.ready.json").is_file())
            self.assertTrue(list((receipts / sid / "deep-candidates").rglob("*.ready.json")))
        scheduler_log = Path(first["run"]["scheduler_log"]).read_text()
        for name in ("validate_inputs", "amrfinder", "pathogenfinder2", "setup_ready", "capture_provenance"):
            self.assertIn(name, scheduler_log)
        for deep in evidence["deep_records"]:
            sid = deep["sample_id"]
            upstream = [r for r in evidence["records"] if r["sample_id"] == sid
                        and r["module_decision"]["action"] == "run"]
            self.assertEqual({r["module"] for r in upstream}, {"amrfinder", "rgi", "vfdb", "pathogenfinder2"})
            self.assertTrue(all(r["finished_at"] <= deep["started_at"] for r in upstream))
            self.assertTrue(any(c.get("protein_id") == sid + "_0002" and c.get("selected")
                                for c in deep["candidates"]), "Extend attention must be available before DeepSafe selection")
            self.assertEqual(deep["upstream_incomplete"], [])

        second, code, reused = self.run_plan(plan)
        self.assertEqual(code, 0, second["run"].get("engine_error"))
        requested = [r for r in reused["records"] if r["module_decision"]["action"] == "run"]
        self.assertTrue(all(r.get("cache_hit") for r in requested))
        keys = {(r["sample_id"], r["module"]): r["cache_key"] for r in requested}

        # Simulate a new saved source identity. Snakemake output timestamps
        # must never bypass the worker's content-based cache check.
        changed = copy.deepcopy(plan)
        changed.pop("plan_path")
        changed.pop("plan_hash")
        changed["source_hash"] = hash_object({"previous": plan["source_hash"], "synthetic_revision": 2})
        changed["plan_hash"] = hash_object(changed)
        path = Path(changed["outdir"]) / "provenance" / "plans" / (changed["plan_hash"] + ".json")
        atomic_json(path, changed)
        changed["plan_path"] = str(path)
        third, code, refreshed = self.run_plan(changed)
        self.assertEqual(code, 0, third["run"].get("engine_error"))
        requested = [r for r in refreshed["records"] if r["module_decision"]["action"] == "run"]
        self.assertTrue(all(r.get("cache_hit") is False for r in requested))
        self.assertTrue(all(r["cache_key"] != keys[(r["sample_id"], r["module"])] for r in requested))

    def test_real_dag_records_failed_upstream_then_reports_other_sample(self):
        # A malformed source record represents a checked analysis failure,
        # not an infrastructure crash or a zero-hit biological result.
        bad = self.root / "inputs" / "DEMO_A.pathogenfinder2.json"
        value = json.loads(bad.read_text())
        value["sample_id"] = "WRONG_SAMPLE"
        atomic_json(bad, value)
        result, code, evidence = self.run_plan(make_plan(self.cfg, True, True))
        self.assertEqual(code, 2)
        self.assertIn("engine_error", result["run"])
        self.assertEqual(result["run"]["reporting_mode"], "launcher_infrastructure_fallback")
        failed = next(r for r in evidence["records"] if r["sample_id"] == "DEMO_A"
                      and r["module"] == "pathogenfinder2")
        self.assertEqual(failed["execution_status"], "failed")
        self.assertEqual(failed["interpretation"], "inconclusive")
        deep = {r["sample_id"]: r for r in evidence["deep_records"]}
        self.assertEqual(deep["DEMO_A"]["upstream_incomplete"], ["pathogenfinder2"])
        self.assertEqual(deep["DEMO_A"]["execution_status"], "blocked")
        self.assertEqual(deep["DEMO_B"]["execution_status"], "completed")
        self.assertEqual(deep["DEMO_B"]["upstream_incomplete"], [])
        self.assertTrue(Path(result["artifacts"]["html"]).is_file())
        self.assertTrue(Path(result["run"]["evidence_snapshot"]).is_file())
        receipts = Path(result["run"]["scheduler_receipt_dir"])
        self.assertTrue((receipts / "DEMO_A/pathogenfinder2.json").is_file(), "Failure diagnostics must survive Snakemake cleanup")
        self.assertFalse((receipts / "DEMO_A/pathogenfinder2.ready.json").exists())
        self.assertTrue((receipts / "DEMO_A/amrfinder.ready.json").is_file(), "Independent jobs must complete")
        self.assertFalse((receipts / "DEMO_A/deep-stages/prepare.json").exists(), "Dependent DeepSafe must not start")
        self.assertFalse((Path(result["run"]["scheduler_launch_dir"]) / "outcome.json").exists(), "Normal DAG reporting target must remain blocked")
        self.assertIn(failed["error"], (Path(result["run"]["scheduler_launch_dir"]) / "DEMO_A/logs/pathogenfinder2.log").read_text())


if __name__ == "__main__":
    unittest.main()
