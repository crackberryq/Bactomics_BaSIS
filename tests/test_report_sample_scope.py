"""Recorded scope is displayed without reclassifying samples or execution."""
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

from basis.report import write_report
from basis.report_sample_scope import module_scope_rows, render_sample_scope
from basis.config import default_config
from basis.sample_policy import plan_sample_modules
from tests.test_report_dashboard import Document


def decision(action, requested, reason):
    return {"action": action, "requested": requested,
            "scope_incomplete": action == "withheld", "reason": reason, "warnings": []}


class SampleScopeReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.sample = {"sample_id": "external1", "input_mode": "external",
                       "taxon": "", "taxon_source": "", "taxon_phylum": ""}
        self.run = {"version": "test", "enabled_modules": ["amrfinder", "icescreen", "pathogenfinder2"],
                    "module_decisions": {"external1": {
                        "amrfinder": decision("run", True, "enabled_for_sample"),
                        "icescreen": decision("withheld", True, "explicit_phylum_required"),
                        "pathogenfinder2": decision("disabled", False, "sample_exclusion"),
                    }}}
        self.records = [
            {"sample_id": "external1", "module": "amrfinder", "execution_status": "completed", "findings": []},
            {"sample_id": "external1", "module": "icescreen", "execution_status": "not_assessed", "findings": []},
            {"sample_id": "external1", "module": "pathogenfinder2", "execution_status": "not_assessed", "findings": []},
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def render(self):
        artifacts = write_report(self.run, [self.sample], self.records, [], self.out)
        text = Path(artifacts["html"]).read_text()
        return Document(text).root, artifacts, text

    def test_scope_distinguishes_exclusion_hold_and_actual_execution(self):
        self.run["module_decisions"]["external1"]["pathogenfinder2"]["declared_reason"] = "Not included in this study question"
        self.run["module_decisions"]["external1"]["icescreen"]["warnings"] = ["Supply an explicit taxonomy declaration"]
        doc, artifacts, html = self.render()
        scope = doc.find(ident="inputs")[0]
        self.assertIn("Inputs and module scope", scope.text)
        self.assertIn("External annotated inputs", scope.text)
        self.assertIn("Unresolved / not supplied", scope.text)
        self.assertIn("HybAs metadata and Kraken results are not required", scope.text)
        self.assertIn("Planned run", scope.text)
        self.assertIn("Held — requested", scope.text)
        self.assertIn("Excluded", scope.text)
        self.assertIn("explicit_phylum_required", scope.text)
        self.assertIn("Not included in this study question", scope.text)
        self.assertIn("Supply an explicit taxonomy declaration", scope.text)
        self.assertIn("Actual execution", scope.text)
        self.assertIn("Requested: Yes · Scope incomplete: Yes", scope.text)
        self.assertTrue(scope.find("details")[0].attrs.get("open") is None)
        self.assertIn("open", scope.find("details")[0].attrs)
        self.assertIn('href="#inputs"', html)
        self.assertIn('href="module-scope.tsv"', html)
        with Path(artifacts["module_scope_tsv"]).open() as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        self.assertEqual(len(rows), 3)
        self.assertEqual(next(row for row in rows if row["module"] == "icescreen")["requested"], "True")

    def test_overview_excludes_sample_disabled_even_when_globally_enabled(self):
        doc, _, _ = self.render()
        stats = doc.find(ident="overview")[0].find(cls="stat")
        requested = next(node for node in stats if node.find(cls="stat-label")[0].text == "Requested modules")
        self.assertEqual(requested.find(cls="stat-value")[0].text, "1 / 2")
        self.assertIn("Execution review required", doc.text)

    def test_actual_policy_exclusion_is_not_labelled_as_requested_hold(self):
        config = default_config()
        for module, module_config in config["modules"].items():
            module_config["enabled"] = module in {"amrfinder", "icescreen", "pathogenfinder2"}
        reason = "Excluded from the current study question"
        config["sample_module_overrides"] = {
            "external1": {"pathogenfinder2": {"enabled": False, "reason": reason}}}
        decisions = plan_sample_modules(config, self.sample, extend=True)
        self.assertEqual(decisions["pathogenfinder2"]["action"], "withheld")
        self.assertIs(decisions["pathogenfinder2"]["requested"], False)
        self.run["module_decisions"] = {"external1": decisions}
        doc, _, _ = self.render()
        scope = doc.find(ident="inputs")[0]
        model_row = next(row for row in scope.find("tr") if "PathogenFinder2" in row.text)
        ice_row = next(row for row in scope.find("tr") if "ICEscreen" in row.text)
        self.assertIn("Excluded", model_row.text)
        self.assertNotIn("Held", model_row.text)
        self.assertIn("Declared reason: " + reason, model_row.text)
        self.assertIn("Requested: No · Scope incomplete: No", model_row.text)
        self.assertIn("Held — requested", ice_row.text)
        self.assertIn("1 held · 1 excluded · " + str(len(decisions) - 3) + " disabled", scope.text)
        stats = doc.find(ident="overview")[0].find(cls="stat")
        requested = next(node for node in stats if node.find(cls="stat-label")[0].text == "Requested modules")
        self.assertEqual(requested.find(cls="stat-value")[0].text, "1 / 2")

    def test_planned_run_without_execution_stays_in_completion_denominator(self):
        self.records = self.records[:1]
        self.run["module_decisions"]["external1"]["icescreen"] = decision("run", True, "requested")
        doc, _, _ = self.render()
        self.assertIn("Execution review required", doc.text)
        stats = doc.find(ident="overview")[0].find(cls="stat")
        requested = next(node for node in stats if node.find(cls="stat-label")[0].text == "Requested modules")
        self.assertEqual(requested.find(cls="stat-value")[0].text, "1 / 2")
        scope = doc.find(ident="inputs")[0]
        self.assertIn("not recorded", scope.text)

    def test_historical_evidence_does_not_invent_plans_or_input_mode(self):
        self.run.pop("module_decisions")
        self.sample.pop("input_mode")
        doc, paths, _ = self.render()
        scope = doc.find(ident="inputs")[0]
        self.assertIn("Input mode not recorded", scope.text)
        self.assertIn("No input source has been inferred", scope.text)
        self.assertIn("Requested: Not recorded", scope.text)
        self.assertNotIn("Planned run", scope.text)
        self.assertNotIn("Held — requested", scope.text)
        with Path(paths["module_scope_tsv"]).open() as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        self.assertTrue(all(row["decision_source"] == "not_recorded" for row in rows))
        self.assertTrue(all(row["requested"] == "" for row in rows))

    def test_record_decision_used_when_run_map_is_absent(self):
        self.run.pop("module_decisions")
        self.records[1]["module_decision"] = decision("withheld", True, "required_input_missing")
        rows = module_scope_rows(self.run, [self.sample], self.records)
        held = next(row for row in rows if row["module"] == "icescreen")
        self.assertEqual(held["decision_source"], "record.module_decision")
        self.assertEqual(held["reason"], "required_input_missing")

    def test_arbitrary_metadata_is_escaped_and_tsv_formulas_neutralized(self):
        attack = '<script>alert("x")</script>'
        self.sample.update(taxon=attack, taxon_source=attack, taxon_phylum=attack)
        self.run["module_decisions"]["external1"]["icescreen"].update(reason="=HYPERLINK(\"bad\")", warnings=[attack])
        _, artifacts, html = self.render()
        self.assertNotIn(attack, html)
        self.assertIn("&lt;script&gt;", html)
        with Path(artifacts["module_scope_tsv"]).open() as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        self.assertEqual(next(row for row in rows if row["module"] == "icescreen")["reason"], "'=HYPERLINK(\"bad\")")

    def test_rendering_preserves_source_records_and_decisions(self):
        before = copy.deepcopy((self.run, self.sample, self.records))
        _, artifacts, _ = self.render()
        self.assertEqual((self.run, self.sample, self.records), before)
        saved = json.loads(Path(artifacts["evidence_json"]).read_text())
        self.assertEqual(saved["run"], self.run)
        self.assertEqual(saved["records"], self.records)

    def test_external_mode_does_not_report_expected_hybas_context(self):
        self.sample["hybas_context"] = {"status": "not_found"}
        content = render_sample_scope(self.run, [self.sample], self.records)
        self.assertNotIn("Recorded HybAs metadata status", content)
        self.assertIn("not required for external annotated inputs", content)

    def test_multiple_execution_records_remain_visible(self):
        second = dict(self.records[0], execution_status="failed")
        rows = module_scope_rows(self.run, [self.sample], self.records + [second])
        row = next(row for row in rows if row["module"] == "amrfinder")
        self.assertEqual(row["execution_status"], "completed; failed")
        self.assertIn("Multiple execution records", row["warnings"][0])

    def test_globally_disabled_modules_are_not_counted_as_exclusions(self):
        config = default_config()
        for item in config["modules"].values():
            item["enabled"] = False
        decisions = plan_sample_modules(config, self.sample, extend=False)
        self.run["module_decisions"] = {"external1": decisions}
        content = render_sample_scope(self.run, [self.sample], [])
        self.assertIn("0 held · 0 excluded · " + str(len(decisions)) + " disabled", content)
        self.assertNotIn('>Excluded</span>', content)

    def test_unrequested_historical_reason_does_not_invent_exclusion(self):
        self.run["module_decisions"]["external1"]["pathogenfinder2"]["action"] = "not_recorded"
        content = render_sample_scope(self.run, [self.sample], self.records)
        self.assertIn('>Not requested</span>', content)
        self.assertIn("0 excluded", content)
        self.assertNotIn('>Excluded</span>', content)
        # The display change leaves the authoritative requested accounting alone.
        doc, _, _ = self.render()
        stats = doc.find(ident="overview")[0].find(cls="stat")
        requested = next(node for node in stats if node.find(cls="stat-label")[0].text == "Requested modules")
        self.assertEqual(requested.find(cls="stat-value")[0].text, "1 / 2")

    def test_custom_disabled_reason_retains_disabled_action(self):
        for reason in ("not_available_in_release", "sample_scope_exclusion"):
            self.run["module_decisions"]["external1"]["pathogenfinder2"]["reason"] = reason
            content = render_sample_scope(self.run, [self.sample], self.records)
            self.assertIn('>Disabled</span>', content)
            self.assertIn("0 excluded · 1 disabled", content)


if __name__ == "__main__":
    unittest.main()
