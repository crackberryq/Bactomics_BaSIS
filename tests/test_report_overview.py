"""Report reading order and saved-result explanations over synthetic records."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/vendor"), str(ROOT / "workflow/lib")]
from basis.report import write_report
from basis.report_overview import tool_results, render_tool_results
from test_report_screening import synthetic_finding, Text


def record(module="amrfinder", **changes):
    value = {"sample_id": "synthetic-sample", "module": module, "execution_status": "completed",
             "interpretation": "not_detected", "findings": [], "metrics": {}, "artifacts": []}
    value.update(changes)
    return value


class ToolOverviewTests(unittest.TestCase):
    def row(self, value, findings=(), run=None):
        return tool_results(run or {}, [], [value], list(findings))[0]

    def test_empty_rows_are_not_always_negative(self):
        for interpretation, outcome in (("not_detected", "No findings reported"),
                                         ("inconclusive", "Inconclusive result"),
                                         ("unknown", "No finding rows saved")):
            with self.subTest(interpretation=interpretation):
                value = record(interpretation=interpretation)
                original = copy.deepcopy(value)
                self.assertEqual(self.row(value)["outcome"], outcome)
                self.assertEqual(value, original)

    def test_incomplete_assessment_wins_over_completed_execution(self):
        value = record("isescan", scope_incomplete=True, reason="some input was not assessed")
        result = self.row(value)
        self.assertEqual(result["execution"], "completed")
        self.assertEqual(result["outcome"], "Assessment incomplete")
        self.assertEqual(result["reason"], value["reason"])
        run = {"module_decisions": {"synthetic-sample": {"isescan": {"requested": True, "scope_incomplete": True}}}}
        self.assertEqual(self.row(record("isescan"), run=run)["outcome"], "Assessment incomplete")
        run["module_decisions"]["synthetic-sample"]["isescan"]["scope_incomplete"] = False
        self.assertTrue(self.row(value, run=run)["limited"], "Planning must not erase observed incomplete scope")

    def test_failed_blocked_and_unassessed_are_not_negative(self):
        for status, outcome in (("failed", "Tool failed"), ("blocked", "Tool blocked"),
                                ("not_started", "Tool not started"), ("not_assessed", "Not assessed")):
            value = record(execution_status=status, effective_config={"enabled": True})
            result = self.row(value)
            self.assertEqual(result["outcome"], outcome)
            self.assertIn("not a negative result", result["explanation"])

    def test_missing_requested_record_remains_visible(self):
        run = {"module_decisions": {"synthetic-sample": {"amrfinder": {"requested": True, "action": "run"}}}}
        result = tool_results(run, [], [], [])
        self.assertEqual(result[0]["outcome"], "Requested result missing")
        self.assertEqual(result[0]["total"], 0)

    def test_disabled_tool_is_not_treated_as_a_requested_negative(self):
        value = record(execution_status="not_assessed", module_decision={"requested": False, "action": "disabled"})
        self.assertEqual(tool_results({}, [], [value], []), [])

    def test_counts_retain_groups_samples_and_duplicate_source_rows(self):
        rows = [synthetic_finding(), synthetic_finding(),
                synthetic_finding(evidence_level="exploratory", attributes={}),
                synthetic_finding(execution_status="failed"),
                synthetic_finding(sample_id="other")]
        original = copy.deepcopy(rows)
        result = self.row(record(interpretation="detected"), rows)
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["groups"], {"review": 2, "exploratory": 1, "provisional": 1})
        self.assertEqual(rows, original)

    def test_multiple_parent_records_are_explicit(self):
        rows = tool_results({}, [], [record(), record(execution_status="failed")], [])
        self.assertEqual(rows[0]["outcome"], "Multiple saved tool records")

    def test_summary_escapes_source_labels_and_keeps_original_reason(self):
        value = record(reason='<img src=x onerror="bad()">', scope_incomplete=True)
        row = self.row(value)
        page = render_tool_results([row], {"amrfinder": "<script>bad()</script>"})
        self.assertNotIn('<img src=x', page)
        self.assertNotIn('<script>bad()', page)
        self.assertEqual(row["reason"], value["reason"])

    def render(self, records, run=None):
        temporary = tempfile.TemporaryDirectory(prefix="basis-reading-flow-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        run = run or {"run_id": "synthetic-flow", "version": "test", "report_view": "screening",
                      "options": {"deep": False}, "completeness": "completed_requested_scope"}
        originals = copy.deepcopy(records)
        with patch("subprocess.Popen", side_effect=AssertionError("Reporting ran an analysis")):
            write_report(run, [{"sample_id": "synthetic-sample", "input_mode": "external"}], records, [], root)
        self.assertEqual(records, originals)
        return (root / "report.html").read_text(), json.loads((root / "evidence.json").read_text())

    def test_findings_precede_input_scope_and_genome_views(self):
        page, _ = self.render([record(interpretation="detected", findings=[synthetic_finding()])])
        self.assertLess(page.index('id="screens"'), page.index('id="inputs"'))
        self.assertLess(page.index('id="screening-findings"'), page.index('id="genome"'))
        self.assertIn('id="tool-results"', page)
        self.assertIn('data-findings-tool="amrfinder"', page)
        self.assertIn('data-findings-sample="synthetic-sample"', page)
        self.assertNotIn('id="extend"', page)
        self.assertNotIn('href="#extend"', page)
        self.assertIn('class="panel sample-context"', page)

    def test_fourteen_finished_tools_do_not_hide_incomplete_assessment(self):
        records = [record("synthetic_tool_" + str(i)) for i in range(13)]
        records.append(record("isescan", interpretation="inconclusive", scope_incomplete=True,
                              reason="synthetic unassessed input"))
        page, evidence = self.render(records)
        text = Text(page).text
        self.assertIn("14 / 14", text)
        self.assertIn("Tools finished · review assessment limits", text)
        self.assertIn("Assessment incomplete", text)
        self.assertIn("synthetic unassessed input", text)
        self.assertEqual(evidence["records"][-1]["interpretation"], "inconclusive")
        self.assertEqual(evidence["records"][-1]["execution_status"], "completed")

    def test_disabled_empty_model_section_stays_in_scope_records_only(self):
        model = record("pathogenfinder2", execution_status="not_assessed", interpretation="unavailable",
                       module_decision={"requested": False, "action": "disabled"})
        page, evidence = self.render([record(), model])
        self.assertNotIn('id="extend"', page)
        self.assertIn("pathogenfinder2", str(evidence))
        self.assertIn('id="optional-modules"', page)


if __name__ == "__main__":
    unittest.main()
