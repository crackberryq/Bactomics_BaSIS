"""Portable benchmark reporting keeps uncertainty and denominators visible."""
import copy
import csv
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.report_placement_benchmark import write_benchmark_report


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == "a":
            self.links.append(dict(attrs).get("href"))


def bundle():
    queries = [{"query_id": f"Q{i}", "fold_id": f"fold{i}", "cluster_id": f"cluster{i}",
                "true_group": "Cfr" if i < 3 else "RlmN", "accessions": [f"UP_TEST{i}"]}
               for i in range(1, 4)]
    rows = []
    for method in ["placement", "blastp"]:
        for i, query in enumerate(queries, 1):
            rows.append(dict(query, method=method, status="completed" if i < 3 else "held",
                             predicted_group=query["true_group"] if i == 1 else ("RlmN" if i == 2 else None),
                             abstained=i == 3, reason="panel_inadequate" if i == 3 else "decision_rule",
                             elapsed_seconds=float(i), metrics={"function_probability": None}))
    return {"schema": "basis-placement-seed-benchmark-run-v1", "status": "completed",
            "started_at": "2026-10-11T00:00:00Z", "finished_at": "2026-10-11T00:01:00Z",
            "protocol": {"queries": queries, "policy": {"cluster_identity": 70.0, "placement_min_mass": 0.8},
                         "folds": [{"fold_id": "fold1", "cluster_id": "cluster1", "status": "ready",
                                    "query_ids": ["Q1"], "held_out_ids": ["Q1", "CONTEXT_Q1"],
                                    "reference_ids": ["R1", "R2", "R3", "R4"],
                                    "training_group_counts": {"Cfr": 2, "RlmN": 2}, "reasons": []}]},
            "results": rows, "evaluation": {"function_probability": None}}


class PlacementBenchmarkReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def render(self, data):
        paths = write_benchmark_report(data, self.root / "report")
        return paths, Path(paths["html"]).read_text()

    def test_complete_offline_bundle_with_exact_evidence_and_readable_protocol(self):
        data = bundle()
        original = copy.deepcopy(data)
        paths, page = self.render(data)
        self.assertEqual(data, original)
        self.assertEqual(json.loads(Path(paths["benchmark_json"]).read_text()), data)
        self.assertEqual(json.loads(Path(paths["protocol_json"]).read_text()), data["protocol"])
        self.assertEqual(json.loads(Path(paths["evaluation_json"]).read_text()), data["evaluation"])
        for text in ["Internal seed-panel benchmark — not independent validation or calibrated probabilities",
                     "66.7%", "2 / 3", "50.0%", "1 / 2", "33.3%", "1 / 3", "CONTEXT_Q1", "UP_TEST1",
                     "Similarity clustering: identity (%)", "0.8"]:
            self.assertIn(text, page)
        parser = Elements()
        parser.feed(page)
        for href in parser.links:
            if not href.startswith("#"):
                self.assertTrue((Path(paths["html"]).parent / href).is_file())
        self.assertEqual(parser.tags.count("script"), 1)
        self.assertNotIn('<script src=', page)
        self.assertNotIn('http://', page)
        self.assertNotIn('https://', page)
        with Path(paths["results_tsv"]).open() as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        self.assertEqual(len(rows), 6)
        self.assertEqual({r["outcome"] for r in rows}, {"correct", "incorrect", "held"})

    def test_all_held_has_zero_coverage_and_undefined_accepted_accuracy(self):
        data = bundle()
        for row in data["results"]:
            row.update(status="held", predicted_group=None, abstained=True)
        _, page = self.render(data)
        self.assertEqual(page.count("No eligible denominator"), 2)
        self.assertIn("0 / 3", page)
        self.assertNotIn("100.0%", page)
        self.assertEqual(page.count('data-outcome="held"'), 6)

    def test_missing_rows_remain_in_planned_denominator(self):
        data = bundle()
        data["status"] = "incomplete"
        data["results"] = [data["results"][0]]
        _, page = self.render(data)
        self.assertIn("Run: incomplete", page)
        self.assertIn("1 / 3", page)
        self.assertIn("0 / 3", page)
        self.assertIn("Missing", page)

    def test_empty_protocol_and_results_render_without_fake_percentages(self):
        data = bundle()
        data["protocol"] = {"queries": [], "folds": [], "policy": {}}
        data["results"] = []
        _, page = self.render(data)
        self.assertIn("No query results were recorded", page)
        self.assertEqual(page.count("No eligible denominator"), 6)

    def test_prepare_only_reports_no_predictions_or_evaluation(self):
        data = bundle()
        data.update(status="prepared", results=[], evaluation=None)
        original = copy.deepcopy(data)
        paths, page = self.render(data)
        self.assertEqual(data, original)
        self.assertEqual(json.loads(Path(paths["benchmark_json"]).read_text()), original)
        self.assertIn("Preparation complete · predictions not run", page)
        self.assertIn("No method evaluation, accuracy or coverage is available", page)
        self.assertIn("Evaluation has not been run", page)
        self.assertNotIn("0.0%", page)
        self.assertNotIn("100.0%", page)
        self.assertNotIn('href="evaluation.json"', page)
        self.assertIn("CONTEXT_Q1", page)
        self.assertIn("Unique queries in the frozen protocol", page)

    def test_execution_failure_is_not_biological_abstention(self):
        for abstained in [None, False]:
            with self.subTest(abstained=abstained):
                data = bundle()
                data["status"] = "incomplete"
                data["results"][0].update(status="failed", abstained=abstained,
                                           predicted_group=None, reason="native_tool_failed")
                paths, page = self.render(data)
                self.assertIn('data-outcome="failed"', page)
                self.assertNotIn('data-outcome="abstained"', page)
                self.assertEqual(json.loads(Path(paths["benchmark_json"]).read_text()), data)
                with Path(paths["results_tsv"]).open() as handle:
                    row = next(csv.DictReader(handle, delimiter="\t"))
                self.assertEqual(row["outcome"], "failed")

    def test_html_escaping_and_tsv_formula_protection(self):
        data = bundle()
        data["results"][0]["reason"] = '=HYPERLINK("https://example.invalid")<img src=x onerror=alert(1)>'
        data["results"][0]["metrics"]["text"] = '</pre><script>alert(2)</script>'
        paths, page = self.render(data)
        parser = Elements()
        parser.feed(page)
        self.assertEqual(parser.tags.count("script"), 1)
        self.assertNotIn("img", parser.tags)
        self.assertIn("&lt;script&gt;alert(2)&lt;/script&gt;", page)
        with Path(paths["results_tsv"]).open() as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        self.assertTrue(rows[0]["reason"].startswith("'=HYPERLINK"))
        self.assertEqual(json.loads(Path(paths["benchmark_json"]).read_text()), data)

    def test_validation_precedes_file_creation(self):
        cases = []
        data = bundle()
        data["results"].append(dict(data["results"][0]))
        cases.append(data)
        data = bundle()
        data["results"][0]["true_group"] = "changed_source_label"
        cases.append(data)
        data = bundle()
        data["results"][0]["abstained"] = True
        cases.append(data)
        data = bundle()
        data["results"][0]["elapsed_seconds"] = float("nan")
        cases.append(data)
        data = bundle()
        data["status"] = "prepared"
        cases.append(data)
        data = bundle()
        data["results"][0].update(status="failed", predicted_group=None, abstained=True)
        cases.append(data)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                write_benchmark_report(data, self.root / "invalid")
        self.assertFalse((self.root / "invalid").exists())

    def test_optional_tree_export_delegates_to_verified_renderer(self):
        data = bundle()
        data["placement_candidates"] = [{"candidate": {"id": "Q1"}, "deep_record": {"execution_status": "completed"}}]
        with patch("basis.report_placement_tree.render_placement_tree", return_value="<p>Verified viewer test</p>") as render:
            _, page = self.render(data)
        render.assert_called_once_with(data["placement_candidates"][0]["candidate"],
                                       data["placement_candidates"][0]["deep_record"], self.root / "report")
        self.assertIn("Verified viewer test", page)
        self.assertIn("Inspect saved placement trees", page)

    def test_verified_tree_link_is_attached_to_placement_query_row(self):
        data = bundle()
        data["placement_candidates"] = [{"query_id": "Q1", "candidate": {"query": {"id": "native_rewritten_id"}},
                                          "deep_record": {"execution_status": "completed"}}]
        original = copy.deepcopy(data)
        with patch("basis.report_placement_tree.render_placement_tree", return_value="<p>Verified tree</p>") as renderer, \
                patch("basis.report_placement_tree.exported_tree_href", return_value="report-data/verified-tree.html"):
            paths, page = self.render(data)
        rendered_candidate = renderer.call_args.args[0]
        self.assertEqual(rendered_candidate["protein_id"], "UP_TEST1")
        self.assertEqual(rendered_candidate["query"]["id"], "native_rewritten_id")
        self.assertEqual(data, original)
        self.assertEqual(json.loads(Path(paths["benchmark_json"]).read_text()), original)
        self.assertEqual(page.count('href="report-data/verified-tree.html"'), 1)
        row = page.split('data-method="placement" data-outcome="correct"', 1)[1].split('</tr>', 1)[0]
        self.assertIn("Open placement tree", row)


if __name__ == "__main__":
    unittest.main()
