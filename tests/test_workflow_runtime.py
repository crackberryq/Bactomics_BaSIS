"""Checks for staged scientific evidence, conservative failure and DAG ownership."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.config import ALL_MODULES, load_config
from basis.deep_stages import STAGES, run_stage as scientific_stage
from basis.demo import create_demo
from basis.pipeline import (_base_record, _read_scheduler_result, execute_deep,
                            make_plan, record_path, run_worker)
from basis.util import atomic_json, hash_object, now
from basis.workflow_runtime import (check_resources, read_document, run_deep_stage,
                                    run_module, validate_inputs)


class StagedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis-stage-")
        self.plan = make_plan(load_config(create_demo(self.temp.name)), True, True)
        self.root = Path(self.plan["outdir"]) / "provenance/scheduler/fixture"
        self.sid = "DEMO_A"
        self.sample = self.plan["samples"][0]
        for module in ALL_MODULES:
            run_worker(self.plan["plan_path"], self.sid, module,
                       receipt=str(self.root / self.sid / (module + ".json")))
        validate_inputs(self.plan, self.root, self.sid)
        check_resources(self.plan, self.root, self.sid, "deepsafe")

    def tearDown(self):
        self.temp.cleanup()

    def stage(self, name):
        # Each worker reads the previous JSON file, reproducing process boundaries.
        return run_deep_stage(self.plan, self.root, self.sid, name)

    def test_each_stage_commits_independently_and_final_result_retains_evidence(self):
        for stage in STAGES:
            value = self.stage(stage)
            self.assertEqual(value["status"], "completed")
            saved = read_document(self.root / self.sid / "deep-stages" / (stage + ".json"))
            self.assertEqual(saved["stage"], stage)
            if stage != "prepare":
                self.assertTrue(saved["previous_sha256"])
        result = _read_scheduler_result(self.plan, self.sid, "deepsafe", self.root)
        self.assertEqual(result["execution_status"], "completed")
        self.assertEqual(result["upstream_incomplete"], [])
        self.assertTrue(any(c.get("protein_id") == "DEMO_A_0002" and c.get("selected") for c in result["candidates"]))
        self.assertTrue(all("consolidated_evidence" in c for c in result["candidates"] if c["selected"]))

    def test_native_stage_failure_is_retained_and_later_science_is_not_run(self):
        seen = []
        def stage(name, *args, **kwargs):
            seen.append(name)
            if name == "domains":
                raise RuntimeError("synthetic native domain failure")
            return scientific_stage(name, *args, **kwargs)
        with patch("basis.deep_stages.run_stage", side_effect=stage):
            for name in STAGES:
                self.stage(name)
        self.assertEqual(seen, ["prepare", "domains"])
        result = _read_scheduler_result(self.plan, self.sid, "deepsafe", self.root)
        self.assertEqual(result["execution_status"], "failed")
        self.assertIn("synthetic native domain failure", result["error"])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(read_document(self.root / self.sid / "deep-stages/references.json")["status"], "blocked")

    def test_valid_whole_result_cache_makes_all_stage_workers_explicit_noops(self):
        previous = execute_deep(self.plan, self.sample)
        previous["cache_hit"] = True
        cached = {"result": previous, "records": [], "config": {}, "work": None,
                  "cache_allowed": True, "stopped": True, "cached": True, "commands": []}
        with patch("basis.workflow_runtime.prepare_deep_execution", return_value=cached), \
                patch("basis.deep_stages.run_stage") as native:
            for name in STAGES:
                self.assertEqual(self.stage(name)["status"], "cached")
            native.assert_not_called()
        result = _read_scheduler_result(self.plan, self.sid, "deepsafe", self.root)
        self.assertTrue(result["cache_hit"])
        self.assertEqual(result["candidates"], json.loads(json.dumps(previous["candidates"])))

    def test_changed_stage_document_is_rejected_before_downstream_science(self):
        self.stage("prepare")
        path = self.root / self.sid / "deep-stages/prepare.json"
        value = json.loads(path.read_text())
        value["envelope"]["config"]["threads"] = 9000
        atomic_json(path, value)
        with patch("basis.deep_stages.run_stage") as native:
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                self.stage("domains")
            native.assert_not_called()

    def test_managed_prefix_and_iqtree_alias_survive_stage_process_boundaries(self):
        prefix = ["/opt/conda/bin/conda", "run", "--prefix", "/managed/tool-env"]
        def resolved(plan, sid, module, cfg):
            cfg = copy.deepcopy(cfg)
            cfg["command_prefix"] = prefix
            cfg.setdefault("executables", {})["iqtree"] = "/managed/tool-env/bin/iqtree2"
            return cfg
        seen = []
        def stage(name, sample, records, cfg, outdir, runner, state=None):
            seen.append((name, list(runner.prefix), cfg["executables"]["iqtree"]))
            return {"completed": name}
        with patch("basis.workflow_environments.execution_config", side_effect=resolved), \
                patch("basis.deep_stages.run_stage", side_effect=stage):
            for name in STAGES[:3]:
                value = self.stage(name)
                self.assertEqual(value["envelope"]["config"]["command_prefix"], prefix)
                self.assertEqual(value["envelope"]["result"]["effective_config"]["command_prefix"], prefix)
        self.assertEqual([name for name, _, _ in seen], list(STAGES[:3]))
        self.assertTrue(all(p == prefix and alias == "/managed/tool-env/bin/iqtree2" for _, p, alias in seen))

    def test_interrupted_prosite_directory_is_preserved_before_stage_retry(self):
        for stage in STAGES[:4]:
            value = self.stage(stage)
        work = Path(value["envelope"]["work"])
        interrupted = work / "prosite_patterns"
        interrupted.mkdir()
        (interrupted / "partial.log").write_text("saved partial analysis")
        value = self.stage("patterns")
        retry = value["envelope"]["stage_retries"][0]
        archived = Path(retry["preserved_output"])
        self.assertEqual((archived / "partial.log").read_text(), "saved partial analysis")
        self.assertEqual(retry["reason"], "output_exists_without_committed_stage_receipt")
        self.assertEqual(value["status"], "completed")

    def test_resource_failure_records_failed_module_without_invoking_adapter(self):
        source = Path(self.plan["config"]["modules"]["amrfinder"]["result"].replace("{sample_id}", self.sid))
        source.unlink()
        check = check_resources(self.plan, self.root, self.sid, "amrfinder")
        self.assertEqual(check["status"], "failed")
        with patch("basis.workflow_runtime.execute_module") as execute:
            result = run_module(self.plan, self.root, self.sid, "amrfinder")
            execute.assert_not_called()
        self.assertEqual(result["execution_status"], "failed")
        self.assertIn("Configured resource does not exist", result["error"])
        self.assertEqual(_read_scheduler_result(self.plan, self.sid, "amrfinder", self.root)["execution_status"], "failed")

    def test_changed_input_gate_blocks_native_execution(self):
        protein = Path(self.sample["proteins"])
        protein.write_text(protein.read_text() + "\n")
        check = validate_inputs(self.plan, self.root, self.sid)
        self.assertEqual(check["status"], "failed")
        check_resources(self.plan, self.root, self.sid, "amrfinder")
        with patch("basis.workflow_runtime.execute_module") as execute:
            result = run_module(self.plan, self.root, self.sid, "amrfinder")
            execute.assert_not_called()
        self.assertEqual(result["execution_status"], "failed")
        self.assertIn("changed after planning", result["error"])

    def test_scheduled_thread_allocation_is_the_native_worker_limit(self):
        check_resources(self.plan, self.root, self.sid, "amrfinder")
        self.plan["config"]["modules"]["amrfinder"]["threads"] = 64
        observed = []
        def execute(plan, sample, module, force):
            observed.append(plan["config"]["modules"][module]["threads"])
            result = _base_record(plan, sample, module)
            result["execution_status"] = "completed"
            atomic_json(record_path(plan, self.sid, module), result)
            return result
        with patch("basis.workflow_runtime.execute_module", side_effect=execute):
            run_module(self.plan, self.root, self.sid, "amrfinder", allocated_threads=2)
        self.assertEqual(observed, [2])


if __name__ == "__main__":
    unittest.main()
