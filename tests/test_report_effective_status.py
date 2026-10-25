"""Synthetic report status must distinguish retained work from accepted completion."""
from __future__ import annotations

import copy
import csv
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from basis.deep_reporting import completion_summary
from basis.report_dashboard import _candidate
from basis.report_deep_workflow import render_workflow_overview
from basis.run_summary import build_run_summary, format_run_summary, write_run_summary
from safety.deep import evidence_record


def candidate():
    return {
        "sample_id": "sample1", "candidate_id": "candidate-1", "protein_id": "P1",
        "selected": True, "execution": "completed", "selection_queue": "native_screen",
        "analyses": {name: {"status": "completed", "hits": [], "rules": []}
                     for name in ("domains", "motifs", "prosite_patterns", "prosite_profiles", "phylogeny")},
        "assessment": {"interpretation": "unresolved"},
        "workflow_coverage": {
            "schema": "basis-deep-workflow-v1", "sample_id": "sample1",
            "candidate_id": "candidate-1", "protein_id": "P1", "selected": True,
            "routing_status": "recorded", "profile": {}, "placement": {},
            "stages": [{"name": "domains", "status": "completed"}],
        },
    }


def record(status="completed", item=None):
    return {"sample_id": "sample1", "module": "deepsafe", "execution_status": status,
            "candidates": [candidate() if item is None else item], "complete": True}


class Badges(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.badges = []
        self.current = None
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        if tag == "span" and "badge" in dict(attrs).get("class", "").split():
            self.current = []

    def handle_endtag(self, tag):
        if tag == "span" and self.current is not None:
            self.badges.append("".join(self.current).strip().lower())
            self.current = None

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)


class EffectiveStatusTests(unittest.TestCase):
    def test_parent_failure_and_missing_status_keep_raw_completed_counts_provisional(self):
        for status in ("failed", "blocked", "not_started", "not_assessed", "not_requested", "unknown", None):
            with self.subTest(status=status):
                value = record(status)
                before = copy.deepcopy(value)
                summary = completion_summary(value)
                self.assertEqual(summary["selected_execution"], "provisional")
                self.assertEqual(summary["recorded_selected_execution"], "completed")
                self.assertEqual(summary["completed_selected_candidates"], 1)
                self.assertEqual(summary["accepted_completed_selected_candidates"], 0)
                self.assertEqual(summary["provisional_completed_selected_candidates"], 1)
                self.assertEqual(value, before)
        value = record()
        del value["execution_status"]
        self.assertEqual(completion_summary(value)["selected_execution"], "provisional")

    def test_completed_parent_retains_completion_and_never_promotes_failed_child(self):
        value = record()
        value["candidates"].append({"selected": False, "execution": "deferred"})
        summary = completion_summary(value)
        self.assertEqual(summary["selected_execution"], "completed")
        self.assertEqual(summary["accepted_completed_selected_candidates"], 1)
        self.assertEqual(summary["provisional_completed_selected_candidates"], 0)
        self.assertEqual(summary["deferred_candidates"], 1)
        value["candidates"][0]["execution"] = "failed"
        self.assertEqual(completion_summary(value)["selected_execution"], "incomplete")
        self.assertEqual(completion_summary(value)["accepted_completed_selected_candidates"], 0)

    def test_execution_alias_and_empty_failed_run_retain_recorded_scope(self):
        value = record()
        del value["execution_status"]
        value["execution"] = {"status": "completed"}
        self.assertEqual(completion_summary(value)["selected_execution"], "completed")
        for status in ("failed", "blocked", "not_started"):
            with self.subTest(status=status):
                summary = completion_summary({"execution_status": status, "candidates": []})
                self.assertEqual(summary["selected_execution"], status)
                self.assertEqual(summary["recorded_selected_execution"], "not_requested")

    def test_candidate_heading_coverage_and_tree_chips_qualify_completed_children(self):
        for status in ("failed", "blocked", "not_started", "not_assessed", None):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                item = candidate()
                value = record(status, item)
                before = copy.deepcopy((item, value))
                with patch("subprocess.Popen", side_effect=AssertionError("native command attempted")):
                    body = _candidate(item, "sample1", Path(directory), value)
                    overview = render_workflow_overview([("sample1", item)], [value])
                heading = body.split("</summary>", 1)[0]
                for part in (heading, body, overview):
                    self.assertIn("provisional", Badges(part).badges)
                    self.assertNotIn("completed", Badges(part).badges)
                self.assertIn("Retained evidence is provisional", body)
                self.assertIn("Recorded execution: completed", body)
                self.assertEqual((item, value), before)
                exports = list((Path(directory) / "report-data").glob("candidate-*.json"))
                self.assertEqual(len(exports), 1)
                self.assertEqual(json.loads(exports[0].read_text()), item)

    def test_completed_candidate_keeps_badges_but_failed_candidate_qualifies_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            item = candidate()
            body = _candidate(item, "sample1", Path(directory), record(item=item))
            self.assertIn("completed", Badges(body).badges)
            self.assertNotIn("provisional", Badges(body).badges)
            item["execution"] = "failed"
            body = _candidate(item, "sample1", Path(directory), record(item=item))
            self.assertIn("failed", Badges(body.split("</summary>", 1)[0]).badges)
            self.assertIn("provisional", Badges(body).badges)
            self.assertNotIn("completed", Badges(body).badges)

    def test_missing_parent_and_untrusted_status_cannot_promote_or_inject_badges(self):
        with tempfile.TemporaryDirectory() as directory:
            item = candidate()
            for parent in (None, {}, record('<img src=x onerror="bad()">', item)):
                with self.subTest(parent=parent):
                    body = _candidate(item, "sample1", Path(directory), parent)
                    self.assertNotIn("completed", Badges(body).badges)
                    self.assertIn("provisional", Badges(body).badges)
                    self.assertNotIn("<img", body)

    def test_json_tsv_and_text_exports_preserve_raw_counts_and_effective_status(self):
        run = {"run_id": "synthetic", "options": {"deep": True}, "enabled_modules": []}
        samples = [{"sample_id": "sample1"}]
        value = record("failed")
        for summary_only in (False, True):
            with self.subTest(summary_only=summary_only), tempfile.TemporaryDirectory() as directory:
                source = copy.deepcopy(value)
                if summary_only:
                    del source["candidates"]
                    source["completion_summary"] = {"selected_candidates": 1,
                        "completed_selected_candidates": 1, "selected_execution": "completed"}
                before = copy.deepcopy(source)
                summary = build_run_summary(run, samples, [], [source])
                paths = write_run_summary(summary, directory)
                saved = json.loads(Path(paths["summary_json"]).read_text())
                deep = saved["samples"][0]["deepsafe"]
                self.assertEqual(deep["execution"], "failed")
                self.assertEqual(deep["selected_execution"], "provisional")
                self.assertEqual(deep["recorded_selected_execution"], "completed")
                self.assertEqual(deep["completed_selected_candidates"], 1)
                self.assertEqual(deep["accepted_completed_selected_candidates"], 0)
                with Path(paths["summary_tsv"]).open() as stream:
                    row, = csv.DictReader(stream, delimiter="\t")
                self.assertEqual(row["deep_selected_execution"], "provisional")
                self.assertEqual(row["deep_recorded_selected_execution"], "completed")
                self.assertEqual(row["deep_completed_selected"], "1")
                self.assertEqual(row["deep_accepted_completed_selected"], "0")
                self.assertEqual(row["deep_provisional_completed_selected"], "1")
                self.assertIn("recorded completed; effective status: provisional", format_run_summary(summary))
                self.assertEqual(source, before)

    def test_final_evidence_requalifies_child_only_summary_after_parent_completion(self):
        item = candidate()
        payload = {"candidates": [item], "complete": True,
                   "completion_summary": completion_summary({"candidates": [item]})}
        self.assertEqual(payload["completion_summary"]["selected_execution"], "provisional")
        document = {"context": {"sample": {"sample_id": "sample1"}, "config": {},
                                "records": [{"module": "synthetic_hold", "execution_status": "not_assessed"}]},
                    "context_sha256": "a" * 64, "state": {"payload": payload}}
        before = copy.deepcopy(document)
        result = evidence_record(document)
        self.assertEqual(result["execution_status"], "completed")
        self.assertEqual(result["completion_summary"]["selected_execution"], "completed")
        self.assertEqual(result["completion_summary"]["accepted_completed_selected_candidates"], 1)
        self.assertEqual(result["completion_summary"]["upstream_incomplete"], ["synthetic_hold"])
        self.assertFalse(result["complete"])
        self.assertEqual(document, before)


if __name__ == "__main__":
    unittest.main()
