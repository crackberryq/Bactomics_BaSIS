"""Scheduler contracts using synthetic imports, without biological tools."""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
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
    def test_verbose_scheduler_output_is_logged_and_stderr_only(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "snakemake.log"
            stdout, stderr = io.StringIO(), io.StringIO()

            def child(command, **kwargs):
                kwargs["stdout"].write("[sample] native worker progress\nfinal scheduler line\n")
                kwargs["stdout"].flush()
                return subprocess.CompletedProcess(command, 0)

            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                    patch("basis.pipeline.subprocess.run", side_effect=child), verbosity(True):
                completed = _run_scheduler(["fixture"], log, {})
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), log.read_text())
            self.assertIn("native worker progress", stderr.getvalue())

    def test_scheduler_start_failure_still_writes_explicit_failed_report(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = make_plan(load_config(create_demo(directory)), False, False)
            with patch("basis.pipeline.subprocess.run", side_effect=OSError("synthetic launch failure")):
                result, code = run_pipeline(plan, engine="snakemake")
            self.assertEqual(code, 2)
            self.assertIn("could not start", result["run"]["engine_error"])
            evidence = json.loads(Path(result["artifacts"]["evidence_json"]).read_text())
            requested = [r for r in evidence["records"] if r["module_decision"]["requested"]]
            self.assertTrue(requested)
            self.assertEqual({r["execution_status"] for r in requested}, {"failed"})
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

            def scheduler(command, **kwargs):
                run_id = kwargs["env"]["BASIS_SCHEDULER_RUN_ID"]
                receipt = Path(plan["outdir"]) / "provenance/scheduler" / run_id / "DEMO_A/amrfinder.json"
                run_worker(plan["plan_path"], "DEMO_A", "amrfinder", receipt=str(receipt))
                return subprocess.CompletedProcess(command, 1)

            with patch("basis.pipeline.subprocess.run", side_effect=scheduler):
                result, code = run_pipeline(plan, engine="snakemake")
            self.assertEqual(code, 2)
            evidence = json.loads(Path(result["artifacts"]["evidence_json"]).read_text())
            requested = [r for r in evidence["records"] if r["module_decision"]["requested"]]
            completed = [(r["sample_id"], r["module"]) for r in requested if r["execution_status"] == "completed"]
            self.assertEqual(completed, [("DEMO_A", "amrfinder")])
            self.assertEqual(sum(r["execution_status"] == "failed" for r in requested), len(requested) - 1)

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
        first, code, evidence = self.run_plan(plan)
        self.assertEqual(code, 0, first["run"].get("engine_error"))
        self.assertEqual(first["run"]["engine"], "snakemake")
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
        self.assertNotIn("engine_error", result["run"])
        failed = next(r for r in evidence["records"] if r["sample_id"] == "DEMO_A"
                      and r["module"] == "pathogenfinder2")
        self.assertEqual(failed["execution_status"], "failed")
        self.assertEqual(failed["interpretation"], "inconclusive")
        deep = {r["sample_id"]: r for r in evidence["deep_records"]}
        self.assertEqual(deep["DEMO_A"]["upstream_incomplete"], ["pathogenfinder2"])
        self.assertEqual(deep["DEMO_B"]["execution_status"], "completed")
        self.assertEqual(deep["DEMO_B"]["upstream_incomplete"], [])
        self.assertTrue(Path(result["artifacts"]["html"]).is_file())
        self.assertTrue(Path(result["run"]["evidence_snapshot"]).is_file())


if __name__ == "__main__":
    unittest.main()
