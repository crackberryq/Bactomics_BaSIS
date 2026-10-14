"""Preflight presentation preserves evidence, failures and declared scope."""
import copy
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.report_preflight import write_preflight_report
from tests.test_report_dashboard import Document


def module(action="run", requested=True, reason="enabled_for_sample", status="not_checked", **kwargs):
    return {"decision": {"action": action, "requested": requested, "reason": reason,
                         "scope_incomplete": action == "withheld" and requested,
                         "warnings": [], **kwargs},
            "status": status, "errors": [], "warnings": [], "declared_exclusion": None}


def sample(sid="sample-a", status="passed"):
    return {"sample_id": sid, "status": status, "input_status": "passed",
            "input_context": {"input_mode": "external", "taxon": "", "taxon_status": "not_recorded",
                              "taxon_source": "", "hybas_metadata_status": "not_consulted",
                              "hybas_metadata_policy": "not_consulted_external_mode",
                              "taxonomic_classification_performed": False},
            "metrics": {"contigs": 3, "assembly_bp": 10000, "protein_count": 20},
            "input_hashes": {"assembly": "a" * 64}, "errors": [], "warnings": [],
            "input_warnings": [], "module_checks": {"screen": module()}}


def result(samples=None):
    return {"schema": "basis-preflight-v1", "status": "passed", "resource_checks_performed": False,
            "global_checks": {"errors": [], "warnings": []}, "sample_checks": samples or [],
            "errors": [], "warnings": [], "checks": [], "module_decisions": {}}


class PreflightReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / "report"

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, source, **kwargs):
        paths = write_preflight_report(source, self.out, **kwargs)
        text = Path(paths["html"]).read_text()
        return paths, text, Document(text).root

    def read_tsv(self, path):
        with Path(path).open(newline="") as handle:
            return list(csv.DictReader(handle, delimiter="\t"))

    def test_empty_report_and_offline_structure(self):
        paths, text, doc = self.render(result())
        self.assertEqual(set(paths), {"html", "evidence_json", "samples_tsv", "module_scope_tsv"})
        self.assertEqual(Path(paths["html"]).name, "index.html")
        self.assertIn("No per-sample check records were supplied", doc.text)
        self.assertIn("No sample checks recorded", doc.find("header")[0].text)
        self.assertIn("Resources not checked", doc.text)
        self.assertFalse(doc.find("script")[0].attrs.get("src"))
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)
        self.assertEqual(self.read_tsv(paths["samples_tsv"]), [])
        self.assertEqual(self.read_tsv(paths["module_scope_tsv"]), [])
        self.assertEqual(len(doc.find("html")), 1)

    def test_global_errors_keep_overall_failed_with_good_sample(self):
        data = result([sample()])
        # The presentation must not turn a global error into a passed report.
        data["global_checks"]["errors"] = ["Unknown exclusion sample: typo"]
        data["global_checks"]["warnings"] = ["Shared profile advisory"]
        _, _, doc = self.render(data)
        self.assertIn("Failed", doc.find("header")[0].text)
        self.assertIn("Unknown exclusion sample: typo", doc.find(ident="global")[0].text)
        self.assertIn("open", doc.find(ident="global")[0].find("details")[0].attrs)
        self.assertIn("Shared profile advisory", doc.text)
        self.assertIn("Passed", doc.find(cls="sample-card")[0].text)

    def test_mixed_rows_show_individual_errors_without_disabling_unresolved_row(self):
        good, bad = sample("valid"), sample("missing", "failed")
        bad.update(input_status="unresolved", input_context={}, metrics={}, input_hashes={},
                   errors=["Protein file missing"], module_checks={})
        good["input_warnings"] = ["Alternative initiation residue"]
        good["module_checks"]["screen"]["warnings"] = ["Prefix found; native acceptance still required"]
        data = result([good, bad])
        data["resource_checks_performed"] = True
        paths, _, doc = self.render(data)
        self.assertIn("Failed", doc.find("header")[0].text)
        self.assertIn("Prefix found; native acceptance still required", doc.text)
        self.assertIn("Input consistency warnings · 1", doc.text)
        self.assertIn("Alternative initiation residue", doc.text)
        failed = doc.find(cls="sample-card")[1]
        self.assertIn("Protein file missing", failed.text)
        self.assertIn("Module scope not evaluated", failed.text)
        matrix_row = doc.find(cls="matrix-sample")[1]
        self.assertIn("Not evaluated", matrix_row.text)
        self.assertNotIn("Disabled", matrix_row.text)
        self.assertEqual(len(self.read_tsv(paths["module_scope_tsv"])), 1)
        self.assertEqual([r["sample_id"] for r in self.read_tsv(paths["samples_tsv"])], ["valid", "missing"])
        self.assertTrue(doc.find(ident="sample-search"))
        self.assertEqual(doc.find(ident="sample-status")[0].tag, "select")
        self.assertIn("not native execution acceptance", doc.text)

    def test_disabled_excluded_held_unknown_and_inactive_declaration(self):
        row = sample()
        row["module_checks"] = {
            "disabled": module("disabled", False, "module_disabled"),
            "excluded": module("withheld", False, "sample_scope_exclusion", declared_reason="Outside this study"),
            "held": module("withheld", True, "taxonomy_required", status="passed_with_warnings"),
            "historical": module("not_recorded", False, "unspecified_legacy_reason"),
            "custom_disabled": module("disabled", False, "custom_disabled_reason"),
        }
        row["module_checks"]["excluded"]["declared_exclusion"] = {"enabled": False, "reason": "Outside this study"}
        row["module_checks"]["disabled"]["declared_exclusion"] = {"enabled": False, "reason": "Inactive because globally disabled"}
        paths, _, doc = self.render(result([row]))
        rows = {r["module"]: r for r in self.read_tsv(paths["module_scope_tsv"])}
        self.assertEqual({k: v["scope_label"] for k, v in rows.items()}, {
            "disabled": "Disabled", "excluded": "Excluded", "held": "Held — requested", "historical": "Not requested", "custom_disabled": "Disabled"})
        self.assertEqual(rows["held"]["requested"], "True")
        self.assertIn("Exclusion declaration: not applied by this decision", doc.text)
        self.assertIn("Exclusion declaration: active", doc.text)
        self.assertIn("Inactive because globally disabled", doc.text)

    def test_errors_and_arbitrary_metadata_are_escaped_and_tsv_is_spreadsheet_safe(self):
        attack = '<script>alert("sample")</script>'
        row = sample("=2+2")
        row["input_context"].update(taxon=attack, input_mode='external" onmouseover="evil')
        row["errors"] = [attack]
        row["module_checks"] = {"@HYPERLINK(1)": module(reason=" \t=evil()")}
        row["module_checks"]["@HYPERLINK(1)"]["declared_exclusion"] = {"reason": attack}
        data = result([row])
        paths, text, doc = self.render(data)
        self.assertNotIn(attack, text)
        self.assertIn("&lt;script&gt;", text)
        self.assertEqual(len(doc.find("script")), 1)
        self.assertEqual(self.read_tsv(paths["samples_tsv"])[0]["sample_id"], "'=2+2")
        exported = self.read_tsv(paths["module_scope_tsv"])[0]
        self.assertEqual(exported["module"], "'@HYPERLINK(1)")
        self.assertEqual(exported["reason"], "' \t=evil()")
        self.assertEqual(json.loads(Path(paths["evidence_json"]).read_text()), data)

    def test_source_and_config_preserved_and_hash_labeled_as_report_snapshot(self):
        source = result([sample()])
        before = copy.deepcopy(source)
        config = self.root / "config.yaml"
        config.write_text("samples: example.tsv\n")
        original = config.read_bytes()
        paths, _, doc = self.render(source, config_path=config)
        self.assertEqual(source, before)
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual(json.loads(Path(paths["evidence_json"]).read_text()), before)
        self.assertIn(hashlib.sha256(original).hexdigest(), doc.text)
        self.assertIn("not a claim that these bytes were validated", doc.text)

    def test_existing_destination_and_symlink_refused_without_mutation(self):
        self.out.mkdir()
        marker = self.out / "keep"
        marker.write_text("original")
        with self.assertRaises(FileExistsError):
            write_preflight_report(result([sample()]), self.out)
        self.assertEqual(marker.read_text(), "original")
        self.assertEqual(list(self.out.iterdir()), [marker])
        dangling = self.root / "link"
        dangling.symlink_to(self.root / "absent")
        with self.assertRaises(FileExistsError):
            write_preflight_report(result(), dangling)
        self.assertTrue(dangling.is_symlink())

    def test_commit_failure_removes_new_output_and_staging(self):
        import basis.report_preflight as report
        replace = report.os.replace
        calls = []

        def fail_second(source, destination):
            calls.append(destination)
            if len(calls) == 2:
                raise OSError("simulated commit failure")
            return replace(source, destination)

        with patch.object(report.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "simulated commit failure"):
                write_preflight_report(result([sample()]), self.out)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_no_input_source_paths_are_read_and_invalid_data_never_creates_output(self):
        row = sample()
        row["input_context"]["assembly"] = "/definitely/nonexistent/source.fna"
        self.render(result([row]))  # Only supplied metadata is rendered.
        other = self.root / "bad"
        with self.assertRaises(ValueError):
            write_preflight_report({"sample_checks": {}}, other)
        self.assertFalse(other.exists())


if __name__ == "__main__":
    unittest.main()
