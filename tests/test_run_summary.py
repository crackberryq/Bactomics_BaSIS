"""Recorded scope summaries, without native tools or biological inference."""
import contextlib
import copy
import csv
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from basis.run_summary import build_plan_summary, build_run_summary, format_run_summary, write_run_summary


def decision(action="run", requested=True, reason="configured_module_requested"):
    return {"action": action, "requested": requested, "reason": reason,
            "scope_incomplete": requested and action == "withheld"}


def record(module, status="completed", sid="S1", **extra):
    return {"sample_id": sid, "module": module, "execution_status": status, **extra}


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.samples = [{"sample_id": "S1"}, {"sample_id": "S2"}]
        self.run = {"run_id": "fixture-run", "engine": "local", "validation_mode": "synthetic_fixture",
                    "completeness": "completed_requested_scope", "incomplete_modules": [],
                    "options": {"deep": False, "extend": False},
                    "module_decisions": {sid: {"amrfinder": decision()} for sid in ("S1", "S2")}}

    def test_failed_sample_does_not_erase_completed_sample_or_scope(self):
        self.run.update(completeness="incomplete", incomplete_modules=["S1:amrfinder"])
        before = copy.deepcopy(self.run)
        result = build_run_summary(self.run, self.samples,
                                   [record("amrfinder", "failed", error="fixture failure"), record("amrfinder", sid="S2")], [])
        self.assertEqual(self.run, before)
        self.assertEqual(result["completeness"], "incomplete")
        bad, good = result["samples"]
        self.assertEqual(bad["requested_modules"], {"total": 1, "completed": 0, "failed": 1, "blocked": 0, "not_started": 0, "not_assessed": 0})
        self.assertEqual(bad["incomplete_requested"][0]["reason"], "fixture failure")
        self.assertEqual(good["completeness"], "completed_requested_scope")

    def test_hold_exclusion_disabled_missing_and_cached_import_are_distinct(self):
        self.run["module_decisions"]["S1"] = {
            "amrfinder": decision(), "rgi": decision(),
            "icescreen": decision("withheld", True, "matching_genbank_required"),
            "pathogenfinder2": decision("withheld", False, "sample_scope_exclusion"),
            "vfdb": decision("disabled", False, "module_disabled"),
        }
        self.run["module_decisions"]["S1"]["pathogenfinder2"]["declared_reason"] = "Test scope"
        records = [record("amrfinder", cache_hit=True, effective_config={"mode": "import"}),
                   record("icescreen", "not_assessed", reason="matching_genbank_required"),
                   record("pathogenfinder2", cache_hit=True), record("vfdb", cache_hit=True), record("amrfinder", sid="S2")]
        own = build_run_summary(self.run, self.samples, records, [record("deepsafe", cache_hit=True)])["samples"][0]
        self.assertEqual(own["requested_modules"], {"total": 3, "completed": 1, "failed": 0, "blocked": 0, "not_started": 0, "not_assessed": 2})
        self.assertEqual((own["held"], own["excluded"], own["disabled"]), (1, 1, 1))
        self.assertEqual((own["cache_reused"], own["imported_modules"]), (1, 1))
        self.assertEqual(own["deepsafe"]["execution"], "not_requested")
        self.assertIsNone(own["deepsafe"]["selected_candidates"])

    def test_stale_completed_result_does_not_satisfy_requested_hold(self):
        self.run["module_decisions"]["S1"]["amrfinder"] = decision("withheld", True, "prerequisite_missing")
        own = build_run_summary(self.run, self.samples, [record("amrfinder", cache_hit=True)], [])["samples"][0]
        self.assertEqual(own["requested_modules"]["completed"], 0)
        self.assertEqual(own["requested_modules"]["not_assessed"], 1)
        self.assertEqual(own["cache_reused"], 0)

    def test_deep_selected_completion_and_deferred_scope_are_separate(self):
        self.run.update(completeness="incomplete", incomplete_modules=["S1:deepsafe"])
        self.run["options"]["deep"] = True
        candidates = [{"selected": True, "execution": "completed"}, {"selected": False, "execution": "deferred"}]
        deep = record("deepsafe", complete=False, cache_hit=True, candidates=candidates, upstream_incomplete=[])
        result = build_run_summary(self.run, self.samples, [record("amrfinder"), record("amrfinder", sid="S2")], [deep])
        own = result["samples"][0]
        self.assertEqual(own["completeness"], "incomplete")
        self.assertEqual(own["deepsafe"]["selected_execution"], "completed")
        self.assertEqual(own["deepsafe"]["completed_selected_candidates"], 1)
        self.assertEqual(own["deepsafe"]["deferred_candidates"], 1)
        self.assertEqual(own["cache_reused"], 1)
        text = format_run_summary(result)
        self.assertIn("selected 1/1 completed", text)
        self.assertIn("deferred: 1", text)
        self.assertIn("incomplete", text)

    def test_zero_selected_never_claims_every_candidate_was_assessed(self):
        self.run["options"]["deep"] = True
        result = build_run_summary(self.run, self.samples, [], [record("deepsafe", complete=False,
                                   candidates=[{"selected": False, "execution": "deferred"}], upstream_incomplete=[])])
        deep = result["samples"][0]["deepsafe"]
        self.assertEqual(deep["selected_candidates"], 0)
        self.assertEqual(deep["selected_execution"], "not_requested")
        self.assertEqual(deep["deferred_candidates"], 1)

    def test_failed_deep_and_upstream_remain_visible(self):
        self.run["options"]["deep"] = True
        result = build_run_summary(self.run, self.samples, [], [record("deepsafe", "failed", error="fixture error",
                                    upstream_incomplete=["amrfinder"])])
        deep = result["samples"][0]["deepsafe"]
        self.assertEqual(deep["execution"], "failed")
        self.assertIsNone(deep["selected_candidates"])
        self.assertEqual(deep["upstream_incomplete"], ["amrfinder"])
        self.assertIn("fixture error", format_run_summary(result))

    def test_conflicting_duplicate_records_are_not_completed_or_reused(self):
        own = build_run_summary(self.run, self.samples,
                                [record("amrfinder", cache_hit=True), record("amrfinder", "failed")], [])["samples"][0]
        self.assertEqual(own["requested_modules"]["total"], 1)
        self.assertEqual(own["requested_modules"]["failed"], 1)
        self.assertEqual(own["cache_reused"], 0)

    def test_authoritative_incomplete_identifier_survives_missing_detail(self):
        self.run.update(completeness="incomplete", incomplete_modules=["S1:historical-stage"], engine_error="scheduler failed")
        result = build_run_summary(self.run, self.samples, [record("amrfinder"), record("amrfinder", sid="S2")], [])
        self.assertEqual(result["samples"][0]["incomplete_requested"][0]["module"], "historical-stage")
        self.assertIn("Engine error: scheduler failed", format_run_summary(result))

    def test_plan_never_contains_execution_counts_or_claims_completion(self):
        plan = {"options": {"deep": True, "extend": True}, "config": {"validation_mode": "synthetic_fixture"},
                "samples": self.samples, "module_decisions": self.run["module_decisions"], "plan_path": "/fixture/plan.json"}
        result = build_plan_summary(plan, engine="snakemake", force=True)
        self.assertTrue(result["options"]["dry_run"])
        self.assertTrue(result["options"]["force"])
        self.assertNotIn("completeness", result)
        self.assertNotIn("requested_modules", result["samples"][0])
        self.assertEqual(result["samples"][0]["planned_modules"]["runnable"], 1)
        self.assertIn("Plan only", format_run_summary(result))

    def test_exports_agree_with_summary_and_guard_spreadsheet_formulas(self):
        sample = {"sample_id": "=FORMULA"}
        summary = build_run_summary({**self.run, "module_decisions": {}}, [sample], [], [])
        with tempfile.TemporaryDirectory() as directory:
            paths = write_run_summary(summary, directory)
            self.assertEqual(json.loads(Path(paths["summary_json"]).read_text()), summary)
            with Path(paths["summary_tsv"]).open(newline="") as f:
                rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(rows[0]["sample_id"], "'=FORMULA")
            self.assertEqual(rows[0]["deep_execution"], "not_requested")
            self.assertEqual(rows[0]["deep_selected"], "")
            self.assertEqual(list(Path(directory).glob('.*')), [])
            before = {p: p.read_bytes() for p in Path(directory).iterdir()}
            with self.assertRaises(ValueError):
                write_run_summary({**summary, "bad": float('nan')}, directory)
            self.assertEqual(before, {p: p.read_bytes() for p in before})


class SummaryIntegrationTests(unittest.TestCase):
    def test_synthetic_summary_matches_evidence_history_and_source(self):
        from basis.config import load_config
        from basis.demo import create_demo
        from basis.pipeline import make_plan, run_pipeline
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(create_demo(directory))
            plan = make_plan(config, False, True)
            with patch('basis.execution.CommandRunner.run', side_effect=AssertionError('Native command forbidden')):
                result, code = run_pipeline(plan)
            self.assertEqual(code, 0)
            summary = result["summary"]
            evidence = json.loads(Path(result['artifacts']['evidence_json']).read_text())
            self.assertEqual(evidence['run']['batch_summary'], summary)
            self.assertTrue(all(s['imported_modules'] == 4 for s in summary['samples']))
            self.assertTrue(all(s['cache_reused'] == 0 for s in summary['samples']))
            archive = Path(result['run']['evidence_snapshot']).parent
            self.assertEqual(json.loads((archive/'run-summary.json').read_text()), summary)
            self.assertEqual((archive/'run-summary.tsv').read_bytes(), Path(result['artifacts']['summary_tsv']).read_bytes())
            again, _ = run_pipeline(plan)
            self.assertTrue(all(s['cache_reused'] == 4 for s in again['summary']['samples']))

    def test_scheduler_failure_does_not_reuse_old_completed_records(self):
        from basis.config import load_config
        from basis.demo import create_demo
        from basis.pipeline import make_plan, run_pipeline
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(create_demo(directory))
            plan = make_plan(config, False, False)
            result, code = run_pipeline(plan)
            self.assertEqual(code, 0)
            with patch('basis.pipeline._run_scheduler', return_value=SimpleNamespace(returncode=1)):
                result, code = run_pipeline(plan, engine='snakemake')
            self.assertEqual(code, 2)
            self.assertIn('Snakemake exited 1', result['summary']['engine_error'])
            self.assertTrue(all(s['requested_modules']['not_started'] == 3 for s in result['summary']['samples']))
            self.assertTrue(all(s['cache_reused'] == 0 for s in result['summary']['samples']))


if __name__ == '__main__':
    unittest.main()
