"""End-to-end contracts using labelled synthetic imports, never biological tools."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from basis.cli import main
from basis.config import ALL_MODULES, load_config
from basis.demo import create_demo
from basis.pipeline import execute_module, make_plan, record_path


class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis_integration_")
        self.root = Path(self.temp.name)
        self.config_path = create_demo(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *flags):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(["-c", str(self.config_path), *flags])

    def evidence(self):
        return json.loads((self.root / "results" / "evidence.json").read_text())

    def imported(self, module="amrfinder", sample="DEMO_A"):
        path = self.root / "inputs" / f"{sample}.{module}.json"
        return path, json.loads(path.read_text())

    def write_imported(self, path, value):
        path.write_text(json.dumps(value, indent=2) + "\n")

    def plan(self, deep=False, extend=False):
        return make_plan(load_config(self.config_path), deep, extend)

    def test_four_flag_combinations_have_correct_scope_and_complete_artifacts(self):
        for deep, extend in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(deep=deep, extend=extend):
                flags = (["-d"] if deep else []) + (["-e"] if extend else [])
                self.assertEqual(self.cli(*flags), 0)
                evidence = self.evidence()
                self.assertEqual(evidence["run"]["options"], {"deep": deep, "extend": extend})
                self.assertEqual(evidence["run"]["validation_mode"], "synthetic_fixture")
                self.assertEqual(evidence["run"]["completeness"], "completed_requested_scope")
                self.assertEqual(len(evidence["records"]), len(ALL_MODULES) * 2)
                pf2 = [r for r in evidence["records"] if r["module"] == "pathogenfinder2"]
                self.assertEqual({r["execution_status"] for r in pf2}, {"completed" if extend else "not_assessed"})
                self.assertEqual({r["execution_status"] for r in evidence["deep_records"]},
                                 {"completed" if deep else "not_assessed"})
                for filename in ("report.html", "findings.tsv", "recommendations.tsv", "evidence.json", "methods.txt", "locus_registry.json"):
                    self.assertTrue((self.root / "results" / filename).is_file(), filename)

    def test_import_wrong_sample_and_hash_are_failed_not_negative(self):
        for field, invalid in (("sample_id", "ANOTHER_ISOLATE"), ("input_hashes", {"assembly": "not-the-input-hash"})):
            with self.subTest(field=field):
                create_demo(self.root)
                path, imported = self.imported()
                imported[field] = invalid
                imported["findings"] = []
                imported["interpretation"] = "not_detected"
                self.write_imported(path, imported)
                self.assertEqual(self.cli(), 2)
                evidence = self.evidence()
                failed = next(r for r in evidence["records"] if r["sample_id"] == "DEMO_A" and r["module"] == "amrfinder")
                self.assertEqual(failed["execution_status"], "failed")
                self.assertEqual(failed["interpretation"], "inconclusive")
                self.assertEqual(failed["findings"], [])
                self.assertIn("DEMO_A:amrfinder", evidence["run"]["incomplete_modules"])
                self.assertTrue(any(r["sample_id"] == "DEMO_A" and r["category"] == "execution" for r in evidence["recommendations"]))

    def test_import_cannot_convert_failed_upstream_into_completed_no_hits(self):
        path, imported = self.imported()
        imported.update(execution_status="failed", interpretation="not_detected", findings=[])
        self.write_imported(path, imported)
        plan = self.plan()
        result = execute_module(plan, plan["samples"][0], "amrfinder")
        self.assertEqual(result["execution_status"], "failed")
        self.assertIn("Only successfully completed", result["error"])
        self.assertEqual(result["interpretation"], "inconclusive")

    def test_disabled_flags_clear_stale_optional_evidence(self):
        self.assertEqual(self.cli("-d", "-e"), 0)
        first = self.evidence()
        self.assertTrue(any(f.get("category") == "ml_attention" for r in first["records"] for f in r["findings"]))
        self.assertTrue(any(r.get("candidates") for r in first["deep_records"]))
        self.assertEqual(self.cli(), 0)
        second = self.evidence()
        pf2 = [r for r in second["records"] if r["module"] == "pathogenfinder2"]
        self.assertTrue(all(r["execution_status"] == "not_assessed" and not r["findings"] for r in pf2))
        self.assertTrue(all(r["execution_status"] == "not_assessed" and not r.get("candidates") for r in second["deep_records"]))
        self.assertFalse(any(f.get("category") == "ml_attention" for r in second["records"] for f in r["findings"]))
        self.assertFalse(any(r["category"] == "ml_attention" for r in second["recommendations"]))

    def test_protein_coordinates_link_independent_tool_calls_to_same_locus(self):
        self.assertEqual(self.cli(), 0)
        records = self.evidence()["records"]
        calls = [r["findings"][0] for r in records if r["sample_id"] == "DEMO_A" and r["module"] in {"amrfinder", "rgi"}]
        self.assertEqual(len(calls), 2)
        self.assertEqual({f["locus_id"] for f in calls}, {calls[0]["locus_id"]})
        self.assertEqual(len({f["finding_id"] for f in calls}), 2)
        self.assertTrue(all((f["contig"], f["start"], f["end"], f["strand"]) == ("DEMO_A_chr", 1, 93, "+") for f in calls))
        registry = json.loads((self.root / "results" / "locus_registry.json").read_text())
        locus = next(l for l in registry if l["locus_id"] == calls[0]["locus_id"])
        self.assertEqual(set(locus["tools"]), {"amrfinder", "rgi"})

    def test_matching_import_is_cached_then_changed_json_invalidates_cache(self):
        plan = self.plan()
        sample = plan["samples"][0]
        first = execute_module(plan, sample, "amrfinder")
        self.assertFalse(first["cache_hit"])
        second = execute_module(plan, sample, "amrfinder")
        self.assertTrue(second["cache_hit"])
        self.assertEqual(first["cache_key"], second["cache_key"])
        path, imported = self.imported()
        imported["metrics"]["review_marker"] = "changed-import-evidence"
        self.write_imported(path, imported)
        third = execute_module(plan, sample, "amrfinder")
        self.assertEqual(third["execution_status"], "completed")
        self.assertFalse(third["cache_hit"])
        self.assertNotEqual(second["cache_key"], third["cache_key"])
        self.assertEqual(third["metrics"]["review_marker"], "changed-import-evidence")

    def test_assembly_change_invalidates_cache_and_rejects_stale_import(self):
        first_plan = self.plan()
        sample = first_plan["samples"][0]
        first = execute_module(first_plan, sample, "amrfinder")
        self.assertEqual(first["execution_status"], "completed")
        assembly = Path(sample["assembly"])
        text = assembly.read_text()
        # Change only the synthetic spacer, preserving identifiers and coordinates.
        assembly.write_text(text.replace("N" * 27, "A" + "N" * 26, 1))
        changed_plan = self.plan()
        changed_sample = changed_plan["samples"][0]
        stale = execute_module(changed_plan, changed_sample, "amrfinder")
        self.assertEqual(stale["execution_status"], "failed")
        self.assertIn("matching", stale["error"])
        self.assertNotEqual(first["cache_key"], stale["cache_key"])
        path, imported = self.imported()
        imported["input_hashes"] = changed_plan["validations"]["DEMO_A"]["input_hashes"]
        self.write_imported(path, imported)
        refreshed = execute_module(changed_plan, changed_sample, "amrfinder")
        self.assertEqual(refreshed["execution_status"], "completed")
        self.assertFalse(refreshed["cache_hit"])

    def test_missing_import_file_is_failed_and_other_modules_still_report(self):
        path, _ = self.imported()
        path.unlink()
        self.assertEqual(self.cli(), 2)
        evidence = self.evidence()
        amr = next(r for r in evidence["records"] if r["sample_id"] == "DEMO_A" and r["module"] == "amrfinder")
        rgi = next(r for r in evidence["records"] if r["sample_id"] == "DEMO_A" and r["module"] == "rgi")
        self.assertEqual(amr["execution_status"], "failed")
        self.assertEqual(rgi["execution_status"], "completed")
        self.assertTrue(Path(self.root / "results" / "report.html").is_file())


if __name__ == "__main__":
    unittest.main()
