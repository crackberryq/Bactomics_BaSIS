"""Execution-scope tests using artificial imports and mocked analysis only."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.config import ALL_MODULES, default_config, load_config
from basis.demo import create_demo
from basis.pipeline import execute_deep, execute_module, make_plan, module_config, record_path, run_pipeline
from basis.sample_policy import module_decision, plan_sample_modules, validate_sample_overrides


class ScopeDecisionTests(unittest.TestCase):
    def setUp(self):
        self.config = default_config()
        self.sample = {"sample_id": "sampleA", "taxon": ""}

    def ice(self, **sample_fields):
        self.config["modules"]["icescreen"]["enabled"] = True
        return plan_sample_modules(self.config, {**self.sample, **sample_fields})["icescreen"]

    def test_unknown_taxonomy_is_advisory_for_broad_native_screening(self):
        decisions = plan_sample_modules(self.config, self.sample)
        for module in ("amrfinder", "rgi", "vfdb", "genomad"):
            self.assertEqual(decisions[module]["action"], "run")
            self.assertIn("taxonomic_context_unavailable", decisions[module]["warnings"])
        self.assertIn("organism_specific_coverage_not_configured", decisions["amrfinder"]["warnings"])
        self.assertNotIn("organism_specific_coverage_not_configured", decisions["rgi"]["warnings"])

    def test_extension_flag_and_global_disable_remain_separate_from_withholding(self):
        self.config["sample_module_overrides"] = {"sampleA": {"pathogenfinder2": {"enabled": False, "reason": "study scope"}}}
        off = plan_sample_modules(self.config, self.sample, extend=False)["pathogenfinder2"]
        excluded = plan_sample_modules(self.config, self.sample, extend=True)["pathogenfinder2"]
        self.assertEqual((off["action"], off["reason"]), ("disabled", "extension_not_requested"))
        self.assertEqual((excluded["action"], excluded["requested"], excluded["scope_incomplete"]), ("withheld", False, False))
        self.assertEqual(excluded["declared_reason"], "study scope")

    def test_native_ice_requires_genbank_and_explicit_phylum(self):
        self.assertEqual(self.ice()["reason"], "matching_genbank_required")
        missing = self.ice(genbank="sample.gbk", taxon="A declared name")
        self.assertEqual(missing["reason"], "phylum_scope_requires_review")
        self.assertTrue(missing["requested"])
        self.assertTrue(missing["scope_incomplete"])
        outside = self.ice(genbank="sample.gbk", taxon_phylum="Actinomycetota")
        self.assertEqual(outside["reason"], "outside_declared_module_scope")
        accepted = self.ice(genbank="sample.gbk", taxon_phylum=" Firmicutes ")
        self.assertEqual(accepted["action"], "run")
        self.assertEqual(accepted["context"]["taxon_phylum"], "Firmicutes")
        self.assertEqual(module_config(self.config, "icescreen", {**self.sample, "taxon_phylum": "Firmicutes"})["phylum"], "bacillota")

    def test_conflicting_native_module_scope_is_held(self):
        self.config["modules"]["icescreen"]["phylum"] = "another_scope"
        decision = self.ice(genbank="sample.gbk", taxon_phylum="Bacillota")
        self.assertEqual(decision["reason"], "module_phylum_configuration_conflict")
        self.assertEqual(module_config(self.config, "icescreen", {**self.sample, "taxon_phylum": "Bacillota"})["phylum"], "another_scope")

    def test_imports_do_not_reapply_native_prerequisites(self):
        for module in ("icescreen", "mlst", "crisprcasfinder"):
            self.config["modules"][module].update(enabled=True, mode="import")
        decisions = plan_sample_modules(self.config, self.sample)
        for module in ("icescreen", "mlst", "crisprcasfinder"):
            self.assertEqual(decisions[module]["action"], "run")
            self.assertEqual(decisions[module]["reason"], "import_requested")
        self.config["modules"]["mlst"]["mode"] = "native"
        self.assertEqual(plan_sample_modules(self.config, self.sample)["mlst"]["action"], "run")

    def test_invalid_overrides_fail_instead_of_silently_changing_scope(self):
        cases = [None, [], {"absent": {}}, {"sampleA": []},
                 {"sampleA": {"unknown_tool": {"enabled": False, "reason": "declared"}}},
                 {"sampleA": {"amrfinder": {"enabled": True, "reason": "enable"}}},
                 {"sampleA": {"amrfinder": {"enabled": 0, "reason": "not boolean"}}},
                 {"sampleA": {"amrfinder": {"enabled": False, "reason": " "}}},
                 {"sampleA": {"amrfinder": {"enabled": False, "reason": "declared", "min_identity": 1}}}]
        for value in cases:
            with self.subTest(value=value):
                self.config["sample_module_overrides"] = value
                with self.assertRaises(ValueError):
                    validate_sample_overrides(self.config, [self.sample])

    def test_decisions_do_not_mutate_configuration(self):
        before = copy.deepcopy(self.config)
        plan_sample_modules(self.config, self.sample)
        self.assertEqual(self.config, before)

    def test_legacy_plan_fallback_uses_its_recorded_global_scope(self):
        plan = {"config": self.config, "enabled_modules": ["amrfinder"], "options": {"extend": False}}
        self.assertEqual(module_decision(plan, "sampleA", "amrfinder")["action"], "run")
        self.assertEqual(module_decision(plan, "sampleA", "pathogenfinder2")["reason"], "extension_not_requested")


class ScopePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis_scope_")
        self.root = Path(self.temp.name)
        self.config = load_config(create_demo(self.root))
        self.config["deep"]["cache"] = False

    def tearDown(self):
        self.temp.cleanup()

    def excluded(self):
        self.config["sample_module_overrides"] = {"DEMO_A": {"amrfinder": {"enabled": False, "reason": "Explicit test scope"}}}

    def run_quietly(self, plan):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_pipeline(plan)

    def test_two_samples_have_different_requested_scope_and_complete_run(self):
        self.excluded()
        plan = make_plan(self.config, deep=True, extend=False)
        self.assertNotIn("amrfinder", plan["enabled_modules_by_sample"]["DEMO_A"])
        self.assertIn("amrfinder", plan["enabled_modules_by_sample"]["DEMO_B"])
        result, code = self.run_quietly(plan)
        self.assertEqual(code, 0)
        evidence = json.loads(Path(result["artifacts"]["evidence_json"]).read_text())
        records = {r["sample_id"]: r for r in evidence["records"] if r["module"] == "amrfinder"}
        self.assertEqual(records["DEMO_A"]["reason"], "sample_scope_exclusion")
        self.assertEqual(records["DEMO_B"]["execution_status"], "completed")
        self.assertTrue(all(not r["upstream_incomplete"] for r in evidence["deep_records"]))
        self.assertFalse(result["run"]["module_decisions"]["DEMO_A"]["amrfinder"]["requested"])

    def test_requested_policy_hold_is_incomplete_in_run_and_deep(self):
        self.config["modules"]["icescreen"].update(enabled=True, mode="native")
        plan = make_plan(self.config, deep=True, extend=False)
        result, code = self.run_quietly(plan)
        self.assertEqual(code, 2)
        self.assertIn("DEMO_A:icescreen", result["run"]["incomplete_modules"])
        evidence = json.loads(Path(result["artifacts"]["evidence_json"]).read_text())
        for record in evidence["deep_records"]:
            self.assertEqual(record["upstream_incomplete"], ["icescreen"])
        ice = [r for r in evidence["records"] if r["module"] == "icescreen"]
        self.assertEqual({r["execution_status"] for r in ice}, {"not_assessed"})
        self.assertEqual({r["reason"] for r in ice}, {"matching_genbank_required"})

    def test_stale_cache_and_force_cannot_bypass_sample_exclusion(self):
        old = make_plan(self.config, deep=False, extend=False)
        sample = old["samples"][0]
        previous = execute_module(old, sample, "amrfinder")
        self.assertEqual(previous["execution_status"], "completed")
        self.excluded()
        new = make_plan(self.config, deep=False, extend=False)
        for force in (False, True):
            with self.subTest(force=force), patch("basis.pipeline._import_result", side_effect=AssertionError("held work executed")):
                result = execute_module(new, new["samples"][0], "amrfinder", force=force)
                self.assertEqual(result["execution_status"], "not_assessed")
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["reason"], "sample_scope_exclusion")

    def test_held_module_does_not_consume_resource_budget(self):
        self.config["modules"]["icescreen"].update(enabled=True, mode="native", memory_mb=999999, gpus=1)
        plan = make_plan(self.config, deep=False, extend=False)
        self.assertNotIn("icescreen", plan["enabled_modules_by_sample"]["DEMO_A"])

    def test_unknown_sample_override_rejected_before_validation_or_output_creation(self):
        self.config["sample_module_overrides"] = {"typo": {"amrfinder": {"enabled": False, "reason": "scope"}}}
        with patch("basis.pipeline.validate_sample", side_effect=AssertionError("unexpected validation")):
            with self.assertRaisesRegex(ValueError, "unknown sample IDs"):
                make_plan(self.config, deep=False, extend=False)
        self.assertFalse(Path(self.config["outdir"]).exists())

    def test_direct_deep_worker_cannot_consume_stale_excluded_evidence(self):
        old = make_plan(self.config, deep=False, extend=False)
        with contextlib.redirect_stdout(io.StringIO()):
            for module in ALL_MODULES:
                execute_module(old, old["samples"][0], module)
        self.excluded()
        new = make_plan(self.config, deep=True, extend=False)
        captured = []
        def synthetic_deep(sample, records, cfg, work, runner):
            captured.extend(records)
            return {"complete": True, "candidates": [], "metrics": {}}
        with patch("basis.deep.run_deep", side_effect=synthetic_deep):
            result = execute_deep(new, new["samples"][0])
        amr = next(r for r in captured if r["module"] == "amrfinder")
        self.assertEqual(amr["findings"], [])
        self.assertEqual(amr["execution_status"], "not_assessed")
        self.assertEqual(result["upstream_incomplete"], [])


if __name__ == "__main__":
    unittest.main()
