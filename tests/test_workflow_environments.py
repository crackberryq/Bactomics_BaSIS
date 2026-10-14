"""Deployment selection and activated native-environment identity contracts."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.config import ALL_MODULES
from basis import workflow_environments as envs
from basis.sample_policy import plan_sample_modules


class WorkflowEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config = {"modules": {name: {"enabled": name == "minced", "mode": "native", "command_prefix": []}
                              for name in ALL_MODULES}, "deep": {"mode": "native", "command_prefix": []},
                  "execution": {"environment_backend": "auto"}}
        self.plan = {"config": config, "outdir": str(self.root / "results"), "plan_hash": "stable-plan",
                     "samples": [{"sample_id": "sample1"}], "enabled_modules": ["minced"],
                     "options": {"deep": False, "extend": False}}

    def tearDown(self):
        self.temp.cleanup()

    def test_auto_deploys_supported_native_recipe_only(self):
        self.assertTrue(envs.environment_for_job(self.plan, "sample1", "minced").endswith("/minced.yml"))
        self.assertIsNone(envs.environment_for_job(self.plan, "sample1", "amrfinder"))
        self.assertIsNone(envs.environment_for_job(self.plan, "sample1", "deepsafe"))
        self.assertTrue(envs.has_managed_environments(self.plan))
        self.assertEqual(envs.conda_storage(self.plan), str(self.root / ".basis-envs"))
        self.plan["config"]["execution"]["conda_prefix"] = str(self.root / "shared cache")
        self.assertEqual(envs.conda_storage(self.plan), str(self.root / "shared cache"))

    def test_synthetic_import_and_external_need_no_conda(self):
        cfg = self.plan["config"]["modules"]["minced"]
        with patch.dict(os.environ, {}, clear=True):
            self.plan["config"]["validation_mode"] = "synthetic_fixture"
            self.assertFalse(envs.has_managed_environments(self.plan))
            self.plan["config"].pop("validation_mode")
            cfg["mode"] = "import"
            self.assertIsNone(envs.environment_for_job(self.plan, "sample1", "minced"))
            cfg["mode"] = "native"
            cfg["command_prefix"] = ["conda", "run", "-p", "/existing"]
            self.assertIsNone(envs.environment_for_job(self.plan, "sample1", "minced"))
            cfg["command_prefix"] = []
            self.plan["config"]["execution"]["environment_backend"] = "external"
            self.assertIsNone(envs.environment_for_job(self.plan, "sample1", "minced"))
            self.assertEqual(envs.execution_config(self.plan, "sample1", "minced", cfg), cfg)

    def test_explicit_prefix_cannot_be_silently_replaced(self):
        self.plan["config"]["execution"]["environment_backend"] = "managed"
        self.plan["config"]["modules"]["minced"]["command_prefix"] = ["conda", "run", "-p", "/existing"]
        with self.assertRaisesRegex(ValueError, "conflicts"):
            envs.environment_for_job(self.plan, "sample1", "minced")

    def test_unsupported_adapters_are_withheld_without_generic_recipe(self):
        self.plan["config"]["modules"]["mlst"]["enabled"] = True
        self.plan["module_decisions"] = {"sample1": plan_sample_modules(self.plan["config"], self.plan["samples"][0])}
        self.assertIsNone(envs.environment_for_job(self.plan, "sample1", "mlst"))
        self.plan["module_decisions"]["sample1"]["mlst"]["action"] = "run"
        with self.assertRaisesRegex(ValueError, "unsupported module"):
            envs.environment_for_job(self.plan, "sample1", "mlst")

    def test_activation_requires_managed_location_and_completed_transaction(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "activate"):
            envs.managed_prefix()
        with patch.dict(os.environ, {"CONDA_PREFIX": str(self.root / "outside")}):
            with self.assertRaisesRegex(ValueError, "outside"):
                envs.managed_prefix(self.root / "cache")
        with patch.dict(os.environ, {"CONDA_PREFIX": str(self.root / "cache/hash")}):
            with self.assertRaisesRegex(ValueError, "transaction"):
                envs.managed_prefix(self.root / "cache")

    def test_execution_verifies_native_environment_and_keeps_cache_identity_stable(self):
        prefix = self.root / ".basis-envs/hash"
        (prefix / "conda-meta").mkdir(parents=True)
        (prefix / "conda-meta/history").write_text("fixture transaction")
        manager = self.root / "conda"
        manager.write_text("fixture executable")
        cfg = self.plan["config"]["modules"]["minced"]
        before = copy.deepcopy(cfg)
        verification = {"metadata_sha256": {"minced.json": "stable"}, "probes": []}
        with patch.dict(os.environ, {"CONDA_PREFIX": str(prefix), "CONDA_EXE": str(manager)}), \
             patch("basis.environment_workflow.verify", return_value=verification) as verify:
            with patch.dict(os.environ, {"BASIS_SCHEDULER_RUN_ID": "first"}):
                first = envs.execution_config(self.plan, "sample1", "minced", cfg)
            with patch.dict(os.environ, {"BASIS_SCHEDULER_RUN_ID": "second"}):
                second = envs.execution_config(self.plan, "sample1", "minced", cfg)
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(cfg, before)
        self.assertEqual(first, second)
        self.assertEqual(first["command_prefix"][-1], str(prefix))
        for run in ("first", "second"):
            record = json.loads((self.root / "results/provenance/environments" / run / "sample1/minced.json").read_text())
            self.assertEqual(record["prefix"], str(prefix))
            self.assertEqual(record["verification"], verification)

    def test_managed_routing_rejects_external_executable(self):
        prefix = self.root / ".basis-envs/hash"
        (prefix / "conda-meta").mkdir(parents=True)
        (prefix / "conda-meta/history").write_text("fixture")
        manager = self.root / "conda"; manager.write_text("fixture")
        cfg = {"executable": "/external/bin/minced"}
        with patch.dict(os.environ, {"CONDA_PREFIX": str(prefix), "CONDA_EXE": str(manager)}):
            with self.assertRaisesRegex(ValueError, "outside managed"):
                envs.execution_config(self.plan, "sample1", "minced", cfg)


if __name__ == "__main__":
    unittest.main()
