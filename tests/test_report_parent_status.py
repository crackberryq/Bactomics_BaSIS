"""Synthetic saved records must not promote follow-up from incomplete parents."""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/vendor")]

from basis.report_evidence_views import render_evidence_matrix
from basis.report import write_report


def candidate(sample="sample1"):
    return {
        "sample_id": sample, "candidate_id": "candidate-1", "protein_id": "P1",
        "selected": True, "selection_queue": "native_screen", "execution": "completed",
        "source_evidence": [{"tool": "amrfinder", "gene": "synthetic_marker",
                             "attributes": {"native_method": "RECORDED"}}],
        "analyses": {
            "domains": {"status": "completed", "hits": [{"id": "synthetic_domain"}]},
            "motifs": {"status": "completed", "rules": [{"status": "present"}]},
            "prosite_patterns": {"status": "completed", "hits": [{}]},
            "prosite_profiles": {"status": "completed", "hits": [{}]},
            "references": {"status": "completed", "selected_count": 2},
            "phylogeny": {"status": "completed"},
        },
        "taxonomy_review": {"status": "reviewed"},
        "assessment": {"interpretation": "unresolved"},
    }


def parent(item, status="completed"):
    return {"sample_id": item["sample_id"], "module": "deepsafe",
            "execution_status": status, "candidates": [item]}


def source(sample="sample1", status="completed"):
    return {"sample_id": sample, "module": "amrfinder", "execution_status": status}


def body_rows(body):
    content = body.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    return [row for row in content.split("</tr>") if row]


class ParentStatusMatrixTests(unittest.TestCase):
    def test_incomplete_parent_retains_followup_as_provisional(self):
        for status in ("failed", "blocked", "not_started", "not_assessed",
                       "not_requested", "unknown", None):
            with self.subTest(status=status):
                item = candidate()
                record = parent(item, status)
                before = copy.deepcopy((item, record))
                body = render_evidence_matrix([("sample1", item)], [source()], {id(item): record})
                row, = body_rows(body)
                self.assertEqual(row.count(">Provisional</span>"), 7)
                self.assertIn("RECORDED: synthetic_marker", row)
                for label in (">1 hits</span>", ">1 present</span>", ">1 matches</span>",
                              ">2 refs</span>", ">reviewed</span>", ">Done · unresolved</span>"):
                    self.assertNotIn(label, row)
                self.assertIn("Parent execution:", row)
                self.assertEqual((item, record), before)

    def test_missing_parent_is_not_assumed_completed(self):
        item = candidate()
        for mapping in (None, {}, {id(item): {}}, {id(item): None}):
            with self.subTest(mapping=mapping):
                row, = body_rows(render_evidence_matrix([("sample1", item)], [source()], mapping))
                self.assertEqual(row.count(">Provisional</span>"), 7)
                self.assertIn("Parent execution not recorded", row)
                self.assertIn("RECORDED: synthetic_marker", row)

    def test_completed_parent_preserves_recorded_results(self):
        item = candidate()
        row, = body_rows(render_evidence_matrix(
            [("sample1", item)], [source()], {id(item): parent(item)}))
        self.assertNotIn(">Provisional</span>", row)
        for label in (">1 hits</span>", ">1 present</span>", ">1 matches</span>",
                      ">2 refs</span>", ">reviewed</span>", ">Done · unresolved</span>"):
            self.assertIn(label, row)

    def test_completed_parent_does_not_promote_failed_candidate_or_source(self):
        item = candidate()
        item["execution"] = "failed"
        row, = body_rows(render_evidence_matrix(
            [("sample1", item)], [source(status="failed")], {id(item): parent(item)}))
        self.assertNotIn("RECORDED: synthetic_marker", row)
        self.assertNotIn(">1 hits</span>", row)
        self.assertNotIn(">Done · unresolved</span>", row)
        self.assertEqual(row.count(">Failed</span>"), 7)
        self.assertIn(">Not evaluated</span>", row)

    def test_same_candidate_ids_in_different_samples_keep_own_parent_status(self):
        first, second = candidate("sample1"), candidate("sample2")
        parents = {id(first): parent(first, "blocked"), id(second): parent(second)}
        rows = body_rows(render_evidence_matrix(
            [("sample1", first), ("sample2", second)], [source(), source("sample2")], parents))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].count(">Provisional</span>"), 7)
        self.assertNotIn(">Provisional</span>", rows[1])
        self.assertIn(">1 hits</span>", rows[1])

    def test_cross_sample_parent_is_not_accepted(self):
        item = candidate()
        record = parent(item)
        record["sample_id"] = "other_sample"
        row, = body_rows(render_evidence_matrix(
            [("sample1", item)], [source()], {id(item): record}))
        self.assertEqual(row.count(">Provisional</span>"), 7)
        self.assertIn("Parent sample does not match this candidate", row)

    def test_parent_status_uses_shared_execution_aliases(self):
        item = candidate()
        record = parent(item)
        del record["execution_status"]
        record["execution"] = {"status": "completed"}
        row, = body_rows(render_evidence_matrix(
            [("sample1", item)], [source()], {id(item): record}))
        self.assertIn(">1 hits</span>", row)
        self.assertNotIn(">Provisional</span>", row)

    def test_unknown_status_is_escaped_and_inputs_remain_unchanged(self):
        item = candidate()
        record = parent(item, '<img src=x onerror="bad()">')
        inputs = [source()]
        before = copy.deepcopy((item, record, inputs))
        body = render_evidence_matrix([("sample1", item)], inputs, {id(item): record})
        self.assertNotIn('<img src=x', body)
        self.assertIn('&lt;img src=x', body)
        self.assertEqual((item, record, inputs), before)

    def test_completed_layer_missing_count_and_explicit_zero_remain_distinct(self):
        item = candidate()
        item["analyses"]["domains"] = {"status": "completed"}
        item["analyses"]["prosite_patterns"] = {"status": "completed", "hits": []}
        row, = body_rows(render_evidence_matrix(
            [("sample1", item)], [source()], {id(item): parent(item)}))
        self.assertIn('title="Match count not recorded">Completed</span>', row)
        self.assertIn(">No match</span>", row)

    def test_dashboard_passes_each_parent_status_to_the_matrix(self):
        for status in ("failed", "blocked", "not_started", "completed"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                item = candidate()
                record = parent(item, status)
                run = {"run_id": "synthetic-parent-status", "version": "test",
                       "options": {"deep": True}, "enabled_modules": ["amrfinder"]}
                samples = [{"sample_id": "sample1", "input_mode": "external", "taxon": ""}]
                before = copy.deepcopy((run, samples, item, record))
                with patch("subprocess.Popen", side_effect=AssertionError("native execution attempted")):
                    artifacts = write_report(run, samples, [source()], [record], Path(directory))
                body = Path(artifacts["html"]).read_text(encoding="utf-8")
                matrix = body.split('id="evidence-matrix"', 1)[1].split("</section>", 1)[0]
                row, = body_rows(matrix)
                if status == "completed":
                    self.assertIn(">1 hits</span>", row)
                    self.assertNotIn(">Provisional</span>", row)
                else:
                    self.assertEqual(row.count(">Provisional</span>"), 7)
                    self.assertIn("Retained evidence is provisional", body)
                self.assertIn("RECORDED: synthetic_marker", row)
                self.assertEqual((run, samples, item, record), before)


if __name__ == "__main__":
    unittest.main()
