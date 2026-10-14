"""Saved ICEscreen evidence may repair availability, never invent a rerun."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.adapters.context import parse_icescreen
from basis.deep_reporting import completion_summary
from basis.pipeline import path_fingerprint
from basis.reconcile import merge_zero_ice, reconcile, verify_retry
from basis.report import write_report
from basis.util import sha256_file
from tests.test_context import ICE_HEADER, ice_summary
from tests.test_report_dashboard import Document


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sample = {"sample_id": "isolate1", "taxon": "Lysinibacillus irui"}
        inputs = {
            "assembly": ("isolate1.fna", ">1\nATGATGATG\n"),
            "proteins": ("isolate1.faa", ">P1\nMMM\n"),
            "gff": ("isolate1.gff", "##gff-version 3\n1\tfixture\tCDS\t1\t9\t.\t+\t0\tID=P1\n"),
            "genbank": ("isolate1.gbk", "LOCUS       1 9 bp DNA\nFEATURES             Location/Qualifiers\n"
                        "     CDS             1..9\nORIGIN\n        1 atgatgatg\n//\n"),
        }
        for key, (name, text) in inputs.items():
            self.sample[key] = str(self.write("inputs/" + name, text))
        hashes = {key: sha256_file(self.sample[key]) for key in inputs}
        database = self.write("resources/database/model.hmm", "fixture model\n").parent
        metadata = self.write("resources/bacillota.yml", "phylum: bacillota\n")
        cfg = {"enabled": True, "mode": "native", "executable": "icescreen", "phylum": "bacillota",
               "version": "1.3.3", "threads": 4, "database": str(database), "metadata": str(metadata),
               "cache": True, "command_prefix": ["conda", "run", "--no-capture-output", "-p", "/env/ice"]}
        resources = {"database": path_fingerprint(database), "metadata": path_fingerprint(metadata)}
        self.work = self.root / "retry/attempts/run"
        staged = self.write("retry/attempts/run/genbank_input/basis.gbk", Path(self.sample["genbank"]).read_text())
        self.table = self.write("retry/attempts/run/native/basis_detected_ME.tsv", ICE_HEADER)
        self.summary = self.write("retry/attempts/run/native/basis_detected_ME.summary",
                                  ice_summary("1").replace("Total SPs:\t0\n", "Total SPs:\t7\n")
                                  + "Total Integrase:\t7\nUnassigned Integrase:\t7\n")
        self.log = self.write("retry/attempts/run/commands.log", "ICEscreen 1.3.3\ncompleted\n")
        native = self.table.parent
        commands = [
            {"argv": cfg["command_prefix"] + ["icescreen", "--version"], "returncode": 0},
            {"argv": cfg["command_prefix"] + ["icescreen", "--galaxy", "-g", str(staged.parent),
                "-o", str(native), "-j", "4", "--phylum", "bacillota"], "returncode": 0,
             "cwd": str(self.work)},
        ]
        self.retry = {"schema_version": 1, "sample_id": "isolate1", "module": "icescreen",
                      "tool": "icescreen", "record_id": "isolate1:icescreen", "version": "0.1.0",
                      "execution_status": "completed", "interpretation": "not_detected", "findings": [],
                      "input_hashes": hashes, "effective_config": dict(cfg, cache=False),
                      "provenance": {"tool_version": {"status": "completed", "output": "1.3.3"},
                                     "resources": resources, "commands": commands, "source_hash": "new-source"},
                      "metrics": parse_icescreen(self.table, self.summary, {"1"})["metrics"],
                      "artifacts": [str(self.log), str(self.work), str(self.table), str(self.summary), str(native)],
                      "artifact_checksums": {str(p): sha256_file(p) for p in (self.log, self.table, self.summary)},
                      "notes": [], "started_at": "2026-10-10T09:33:15+00:00",
                      "finished_at": "2026-10-10T09:33:40+00:00"}
        failed = copy.deepcopy(self.retry)
        failed.update(execution_status="failed", interpretation="inconclusive", metrics={},
                      error="Output directory does not exist", effective_config=cfg,
                      started_at="2026-10-10T08:38:19+00:00", finished_at="2026-10-10T08:38:21+00:00")
        failed["provenance"]["source_hash"] = "old-source"
        failed["provenance"]["commands"][-1]["returncode"] = 1
        candidate = {"candidate_id": "c1", "sample_id": "isolate1", "protein_id": "P1", "gene": "test",
                     "selected": True, "execution": "completed", "selection_queue": "native_screen",
                     "assessment": {"interpretation": "unresolved", "followup": "hold_for_comparator_review"},
                     "analyses": {"domains": {"status": "completed", "hits": []}}}
        deferred = dict(candidate, candidate_id="c2", protein_id="P2", selected=False,
                        execution="deferred", selection_queue="exploratory")
        deep = {"schema_version": 1, "sample_id": "isolate1", "module": "deepsafe", "tool": "deepsafe",
                "record_id": "isolate1:deepsafe", "execution_status": "completed", "complete": False,
                "input_hashes": copy.deepcopy(hashes), "findings": [], "interpretation": "inconclusive",
                "candidates": [candidate, deferred], "upstream_incomplete": ["icescreen"],
                "cache_key": "original-deep-cache", "cache_hit": True,
                "metrics": {"selected_candidates": 1, "completed_candidates": 1, "deferred_candidates": 1},
                "notes": ["Candidate selection is incomplete because requested upstream analyses failed."],
                "provenance": {"source_hash": "old-source"}, "artifacts": []}
        deep["completion_summary"] = completion_summary(deep)
        other = {"schema_version": 1, "sample_id": "isolate1", "module": "minced",
                 "execution_status": "completed", "interpretation": "not_detected", "findings": [],
                 "input_hashes": copy.deepcopy(hashes), "effective_config": {"enabled": True, "mode": "native"},
                 "metrics": {"array_count": 0}}
        self.evidence = {"schema_version": "basis-report-1", "samples": [self.sample],
                         "records": [other, failed], "deep_records": [deep],
                         "run": {"run_id": "source-run", "version": "0.1.0", "engine": "native",
                                 "source_hash": "old-source", "config_hash": "source-config",
                                 "options": {"deep": True, "extend": False},
                                 "enabled_modules": ["minced", "icescreen"], "completeness": "incomplete",
                                 "incomplete_modules": ["isolate1:icescreen", "isolate1:deepsafe"],
                                 "input_validations": {"isolate1": {"input_hashes": hashes}},
                                 "started_at": "2026-10-10T08:23:00+00:00",
                                 "finished_at": "2026-10-10T08:57:35+00:00"}}

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def seal(self, path):
        self.retry["artifact_checksums"][str(path)] = sha256_file(path)

    def merge(self):
        audit = verify_retry(self.evidence, self.retry)
        return merge_zero_ice(self.evidence, self.retry, audit, "2026-10-10T10:00:00+00:00")

    def test_verified_zero_elements_with_integrases_are_accepted(self):
        audit = verify_retry(self.evidence, self.retry)
        self.assertIsInstance(audit, dict)
        self.assertEqual(self.retry["metrics"]["summary"]["1"]["Total Integrase"], 7)

    def test_positive_retry_or_inconsistent_count_cannot_use_report_only_shortcut(self):
        for value in (dict(findings=[{"gene": "ICE_1"}]), dict(metrics={"predicted_element_count": 1}),
                      dict(execution_status="failed"), dict(interpretation="detected")):
            with self.subTest(value=value):
                retry = copy.deepcopy(self.retry)
                retry.update(value)
                with self.assertRaises(ValueError):
                    verify_retry(self.evidence, retry)

    def test_successful_original_is_not_silently_superseded(self):
        self.evidence["records"][1]["execution_status"] = "completed"
        with self.assertRaises(ValueError):
            verify_retry(self.evidence, self.retry)

    def test_changed_input_hash_or_current_file_rejected(self):
        self.retry["input_hashes"] = dict(self.retry["input_hashes"], genbank="0" * 64)
        with self.assertRaises(ValueError):
            verify_retry(self.evidence, self.retry)
        self.retry["input_hashes"] = copy.deepcopy(self.evidence["records"][1]["input_hashes"])
        Path(self.sample["proteins"]).write_text(">P1\nMMA\n")
        with self.assertRaises(ValueError):
            verify_retry(self.evidence, self.retry)

    def test_genbank_identity_required(self):
        for record in (self.retry, self.evidence["records"][1]):
            record["input_hashes"] = {k: v for k, v in record["input_hashes"].items() if k != "genbank"}
        with self.assertRaises(ValueError):
            verify_retry(self.evidence, self.retry)

    def test_missing_or_tampered_native_artifact_rejected(self):
        original = self.summary.read_bytes()
        self.summary.write_text("changed evidence\n")
        with self.assertRaises(ValueError):
            verify_retry(self.evidence, self.retry)
        self.summary.write_bytes(original)
        self.log.unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            verify_retry(self.evidence, self.retry)

    def test_unsealed_required_artifact_rejected(self):
        del self.retry["artifact_checksums"][str(self.summary)]
        with self.assertRaises(ValueError):
            verify_retry(self.evidence, self.retry)

    def test_native_summary_and_recorded_metrics_must_reconcile(self):
        self.retry["metrics"]["summary"]["1"]["Total Integrase"] = 8
        with self.assertRaises(ValueError):
            verify_retry(self.evidence, self.retry)

    def test_summary_must_assess_input_accessions(self):
        self.summary.write_text(ice_summary("different"))
        self.seal(self.summary)
        with self.assertRaises(ValueError):
            verify_retry(self.evidence, self.retry)

    def test_staged_genbank_must_match_verified_input(self):
        (self.work / "genbank_input/basis.gbk").write_text("different staged input\n")
        with self.assertRaises(ValueError):
            verify_retry(self.evidence, self.retry)

    def test_command_must_have_completed_successfully(self):
        self.retry["provenance"]["commands"][-1]["returncode"] = 1
        with self.assertRaises(ValueError):
            verify_retry(self.evidence, self.retry)

    def test_changed_parameters_or_resource_provenance_rejected(self):
        self.retry["effective_config"]["phylum"] = "other"
        with self.assertRaises(ValueError):
            verify_retry(self.evidence, self.retry)
        self.retry["effective_config"]["phylum"] = "bacillota"
        self.retry["provenance"]["resources"]["metadata"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            verify_retry(self.evidence, self.retry)

    def test_merge_preserves_sources_candidates_and_unrelated_records(self):
        before, retry_before = copy.deepcopy(self.evidence), copy.deepcopy(self.retry)
        merged = self.merge()
        self.assertEqual(self.evidence, before)
        self.assertEqual(self.retry, retry_before)
        self.assertEqual(merged["records"][0], before["records"][0])
        self.assertEqual(merged["records"][1], self.retry)
        deep = merged["deep_records"][0]
        self.assertEqual(deep["candidates"], before["deep_records"][0]["candidates"])
        self.assertIsNot(deep["candidates"], before["deep_records"][0]["candidates"])
        self.assertFalse(deep["complete"])
        self.assertEqual(deep["metrics"]["deferred_candidates"], 1)
        self.assertEqual(deep["upstream_incomplete"], [])
        self.assertEqual(deep["completion_summary"]["upstream_incomplete"], [])
        self.assertEqual(deep["completion_summary"]["deferred_candidates"], 1)
        self.assertNotIn("cache_key", deep)
        self.assertNotIn("cache_hit", deep)
        self.assertEqual(merged["run"]["completeness"], "incomplete")
        self.assertEqual(merged["run"]["incomplete_modules"], ["isolate1:deepsafe"])
        self.assertIn("report_reconciliation", merged["run"])
        audit_json = json.dumps(merged["run"]["report_reconciliation"])
        self.assertIn("source-run", audit_json)
        self.assertIn("original-deep-cache", audit_json)

    def test_unrelated_upstream_failure_stays_incomplete(self):
        self.evidence["records"][0].update(execution_status="failed", interpretation="inconclusive")
        self.evidence["deep_records"][0]["upstream_incomplete"].append("minced")
        self.evidence["run"]["incomplete_modules"].append("isolate1:minced")
        merged = self.merge()
        self.assertEqual(merged["deep_records"][0]["upstream_incomplete"], ["minced"])
        self.assertIn("isolate1:minced", merged["run"]["incomplete_modules"])

    def test_reconciled_report_has_visible_saved_evidence_notice(self):
        merged = self.merge()
        paths = write_report(merged["run"], merged["samples"], merged["records"],
                             merged["deep_records"], self.root / "rendered")
        html = Path(paths["html"]).read_text()
        overview = Document(html).root.find(ident="overview")[0]
        notices = [n for n in overview.find(cls="callout") if "Reconciled saved-evidence report" in n.text]
        self.assertEqual(len(notices), 1)
        self.assertFalse(notices[0].has_ancestor("details"))
        self.assertIn("7 unassigned integrase signatures", notices[0].text)
        self.assertIn("No native analyses or GPU inference ran", notices[0].text)

    def test_end_to_end_preserves_all_original_files_and_records_audit(self):
        source = self.write("source/evidence.json", json.dumps(self.evidence))
        retry_path = self.write("retry/result.json", json.dumps(self.retry))
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        output = self.root / "review"
        with patch("subprocess.Popen", side_effect=AssertionError("No native analyses permitted")):
            merged, artifacts = reconcile(source, retry_path, output)
        for name, value in before.items():
            self.assertEqual(Path(name).read_bytes(), value, name)
        self.assertTrue(Path(artifacts["html"]).is_file())
        saved = json.loads(Path(artifacts["evidence_json"]).read_text())
        self.assertEqual(saved["records"], merged["records"])
        self.assertEqual(saved["deep_records"][0]["candidates"], self.evidence["deep_records"][0]["candidates"])
        manifest = json.loads((output / "reconciliation.json").read_text())
        self.assertTrue(manifest["source_unchanged"])
        self.assertTrue(manifest["retry_unchanged"])
        self.assertFalse(manifest["native_analyses_run"])
        self.assertEqual(manifest["source_sha256"], sha256_file(source))
        self.assertEqual(manifest["retry_sha256"], sha256_file(retry_path))
        for name, expected in manifest["output_file_sha256"].items():
            self.assertEqual(sha256_file(output / name), expected)

    def test_existing_output_cannot_be_overwritten(self):
        source = self.write("source/evidence.json", json.dumps(self.evidence))
        retry_path = self.write("retry/result.json", json.dumps(self.retry))
        sentinel = self.write("review/preserved.txt", "earlier review\n")
        with self.assertRaises(FileExistsError):
            reconcile(source, retry_path, sentinel.parent)
        self.assertEqual(sentinel.read_text(), "earlier review\n")

    def test_concurrent_artifact_change_prevents_finalized_manifest(self):
        source = self.write("source/evidence.json", json.dumps(self.evidence))
        retry_path = self.write("retry/result.json", json.dumps(self.retry))
        output = self.root / "review"

        def change_artifact(*args, **kwargs):
            artifacts = write_report(*args, **kwargs)
            self.summary.write_text("changed during report rendering\n")
            return artifacts

        with patch("basis.report.write_report", side_effect=change_artifact):
            with self.assertRaisesRegex(RuntimeError, "changed during rendering"):
                reconcile(source, retry_path, output)
        self.assertFalse((output / "reconciliation.json").exists())


if __name__ == "__main__":
    unittest.main()
