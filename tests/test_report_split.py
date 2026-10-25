"""Separate saved-report views keep primary completion and source files intact."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from basis.report import _artifact_link, write_report
import safety.reporting as reporting
from safety.reporting import aggregate, render


class SplitReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "sample/safety"
        self.root.mkdir(parents=True)
        self.sample = {"sample_id": "synthetic", "input_mode": "external", "taxon": ""}
        self.config = {"version": "0.4.0a1", "modules": {"minced": {"enabled": True}},
                       "deep": {"enabled": True}, "requested_modules": ["minced", "deepsafe"]}
        self.primary = {"sample_id": "synthetic", "module": "minced",
                        "execution_status": "completed", "findings": [], "metrics": {}, "artifacts": []}
        self.candidate = {"candidate_id": "synthetic-1", "protein_id": "P1", "selected": True,
                          "execution": "completed", "selection_queue": "native_screen",
                          "analyses": {"domains": {"status": "completed", "hits": [{}]}},
                          "assessment": {"interpretation": "unresolved"}}
        self.deep = {"sample_id": "synthetic", "module": "deepsafe", "execution_status": "completed",
                     "findings": [], "metrics": {}, "artifacts": [], "complete": True,
                     "candidates": [self.candidate]}
        self.sample_path = self.write("metadata/sample.json", self.sample)
        self.config_path = self.write("metadata/config.json", self.config)
        self.primary_path = self.write("evidence/minced.json", self.primary)
        self.deep_path = self.write("evidence/deepsafe.json", self.deep)

    def write(self, path, value):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def render_view(self, view, paths=None):
        outdir = self.root / "deepsafe-report" if view == "deep" else self.root
        evidence = paths if paths is not None else [self.primary_path] + (
            [self.deep_path] if view == "deep" else [])
        with patch("subprocess.Popen", side_effect=AssertionError("report attempted a native process")):
            return render(self.sample_path, self.config_path, evidence, outdir,
                          view=view, evidence_root=self.root)

    def test_screening_finishes_without_reading_existing_broken_deep_evidence(self):
        self.deep_path.write_text("broken unfinished data", encoding="utf-8")
        original_config = self.config_path.read_bytes()
        artifacts = self.render_view("screening")
        result = json.loads((self.root / "evidence.json").read_text())
        self.assertEqual(result["run"]["report_view"], "screening")
        self.assertEqual(result["run"]["completeness"], "completed_requested_scope")
        self.assertFalse(result["run"]["options"]["deep"])
        self.assertEqual(result["deep_records"], [])
        self.assertEqual(result["run"]["report_inputs"]["config"]["sha256"],
                         hashlib.sha256(original_config).hexdigest())
        self.assertEqual(self.config_path.read_bytes(), original_config)
        self.assertEqual(self.deep_path.read_text(), "broken unfinished data")
        self.assertFalse(any(key.startswith("deep_workflow") for key in artifacts))
        self.assertFalse((self.root / "deepsafe-report").exists())

    def test_explicit_deep_input_cannot_be_silently_hidden_in_screening_report(self):
        with self.assertRaisesRegex(ValueError, "disabled report scope"):
            self.render_view("screening", [self.primary_path, self.deep_path])

    def test_counterpart_link_requires_matching_source_snapshots(self):
        from safety.report_links import matching_report_link
        self.render_view("screening")
        run = json.loads((self.root / "evidence.json").read_text())["run"]
        self.assertIsNone(matching_report_link(run, self.root))
        self.render_view("deep")
        self.assertEqual(matching_report_link(run, self.root)[0], "deepsafe-report/report.html")
        other = copy.deepcopy(run)
        other["report_inputs"]["sample"]["sha256"] = "f" * 64
        self.assertIsNone(matching_report_link(other, self.root))

    def test_counterpart_link_rejects_edited_report(self):
        from safety.report_links import matching_report_link
        self.render_view("screening")
        self.render_view("deep")
        run = json.loads((self.root / "evidence.json").read_text())["run"]
        (self.root / "deepsafe-report/report.html").write_text("changed")
        self.assertIsNone(matching_report_link(run, self.root))

    def test_counterpart_link_rejects_changed_followup_source(self):
        from safety.report_links import matching_report_link
        self.render_view("screening")
        self.render_view("deep")
        run = json.loads((self.root / "evidence.json").read_text())["run"]
        self.write("evidence/deepsafe.json", {**self.deep, "execution_status": "failed"})
        self.assertIsNone(matching_report_link(run, self.root))

    def test_counterpart_symlink_loop_does_not_break_screening(self):
        counterpart = self.root / "deepsafe-report"
        counterpart.mkdir()
        (counterpart / "metadata").symlink_to("metadata", target_is_directory=True)
        (counterpart / "report.html").write_text("existing")
        (counterpart / "evidence.json").write_text("{}")
        self.render_view("screening")
        self.assertTrue((self.root / "report.html").is_file())

    def test_provisional_deep_reports_do_not_change_the_completed_screening_bundle(self):
        self.render_view("screening")
        primary_snapshot = {path: path.read_bytes() for path in (
            self.root / "report.html", self.root / "evidence.json", self.root / "metadata/run-summary.json")}
        for status in ("failed", "blocked", "not_started", "not_assessed"):
            with self.subTest(status=status):
                self.write("evidence/deepsafe.json", {**self.deep, "execution_status": status,
                                                    "reason": "synthetic status fixture"})
                self.render_view("deep")
                result = json.loads((self.root / "deepsafe-report/evidence.json").read_text())
                self.assertEqual(result["run"]["report_view"], "deep")
                self.assertEqual(result["run"]["completeness"], "incomplete")
                self.assertEqual(result["deep_records"][0]["execution_status"], status)
                body = (self.root / "deepsafe-report/report.html").read_text()
                self.assertIn("Provisional", body)
                for path, contents in primary_snapshot.items():
                    self.assertEqual(path.read_bytes(), contents, str(path))

    def test_deep_view_keeps_native_failures_and_missing_evidence_fatal(self):
        self.write("evidence/minced.json", {**self.primary, "execution_status": "failed"})
        with self.assertRaisesRegex(ValueError, "minced is failed"):
            self.render_view("deep")
        self.write("evidence/minced.json", self.primary)
        with self.assertRaisesRegex(ValueError, "Missing requested evidence: deepsafe"):
            self.render_view("deep", [self.primary_path])

    def test_deep_view_rejects_disabled_scope_and_invalid_candidate_identity(self):
        self.write("metadata/config.json", {**self.config, "deep": {"enabled": False}})
        with self.assertRaisesRegex(ValueError, "disabled report scope"):
            self.render_view("deep")
        self.write("metadata/config.json", self.config)
        for candidates, message in ((["invalid"], "Invalid DeepSafe candidate list"),
                                    ([{**self.candidate, "sample_id": "other"}], "sample identity mismatch"),
                                    ([{**self.candidate, "execution": "failed"}], "unfinished selected candidates")):
            with self.subTest(candidates=candidates):
                self.write("evidence/deepsafe.json", {**self.deep, "candidates": candidates})
                with self.assertRaisesRegex(ValueError, message):
                    self.render_view("deep")

    def test_scoped_sibling_artifact_links_preserve_sources_and_restore_default_security(self):
        source = self.write("deepsafe/native/synthetic.json", {"synthetic": True})
        outside = self.root.parent / "outside.json"
        outside.write_text("external data", encoding="utf-8")
        symlink = self.root / "deepsafe/native/external.json"
        symlink.symlink_to(outside)
        self.candidate["query"] = {"path": "deepsafe/native/synthetic.json"}
        self.candidate["artifacts"] = [str(outside), str(symlink)]
        self.write("evidence/deepsafe.json", self.deep)
        source_files = [source, outside, self.sample_path, self.config_path, self.primary_path, self.deep_path]
        before = {path: path.read_bytes() for path in source_files}
        self.render_view("deep")
        body = (self.root / "deepsafe-report/report.html").read_text()
        self.assertIn('href="../deepsafe/native/synthetic.json"', body)
        self.assertNotIn('href="../../outside.json"', body)
        self.assertNotIn('href="../deepsafe/native/external.json"', body)
        self.assertIsNone(_artifact_link(str(source), self.root / "deepsafe-report"))
        files = json.loads((self.root / "deepsafe-report/metadata/files.json").read_text())
        indexed = {row["path"]: row["href"] for row in files["files"]}
        self.assertEqual(indexed["deepsafe/native/synthetic.json"], "../../deepsafe/native/synthetic.json")
        self.assertNotIn("deepsafe/native/external.json", indexed)
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)

    def test_evidence_root_is_not_an_arbitrary_link_permission(self):
        with self.assertRaisesRegex(ValueError, "External evidence root"):
            render(self.sample_path, self.config_path, [self.primary_path], self.root,
                   view="screening", evidence_root=self.root.parent)
        with self.assertRaisesRegex(ValueError, "External evidence root"):
            write_report({"report_view": "deep", "options": {"deep": True}}, [self.sample],
                         [self.primary], [self.deep], self.root / "other-report", evidence_root=self.root)
        self.assertFalse((self.root / "other-report").exists())

    def test_explicit_deep_root_cannot_overwrite_the_screening_bundle(self):
        self.render_view("screening")
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with self.assertRaisesRegex(ValueError, "External evidence root"):
            render(self.sample_path, self.config_path, [self.primary_path, self.deep_path],
                   self.root, view="deep", evidence_root=self.root)
        with self.assertRaisesRegex(ValueError, "External evidence root"):
            write_report({"report_view": "deep", "options": {"deep": True}}, [self.sample],
                         [self.primary], [self.deep], self.root, evidence_root=self.root)
        after = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_standalone_deep_rendering_without_external_scope_stays_supported(self):
        standalone = self.root.parent / "standalone-deep"
        with patch("subprocess.Popen", side_effect=AssertionError("report attempted a native process")):
            render(self.sample_path, self.config_path, [self.primary_path, self.deep_path],
                   standalone, view="deep")
        result = json.loads((standalone / "evidence.json").read_text())
        self.assertEqual(result["run"]["report_view"], "deep")
        self.assertIsNone(_artifact_link(str(self.primary_path), standalone))

    def test_nested_output_aliases_are_rejected_before_any_renderer_writes(self):
        self.render_view("screening")
        outdir = self.root / "deepsafe-report"
        sentinel = self.write("source-sentinel.json", {"preserve": "primary"})
        for relative in ("metadata", "tables", "metadata/citations", "report-data"):
            self.write(relative + "/sentinel.json", {"preserve": relative})
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        aliases = [(name, self.root / name) for name in
                   ("metadata", "tables", "metadata/citations", "report-data")]
        aliases.extend((name, sentinel) for name in
                       ("report.html", "metadata/deep-workflow.tsv", "tables/findings.tsv",
                        "report-data/saved-export.json"))
        for relative, target in aliases:
            with self.subTest(alias=relative):
                alias = outdir / relative
                alias.parent.mkdir(parents=True, exist_ok=True)
                alias.symlink_to(target, target_is_directory=target.is_dir())
                paths_before = set(outdir.rglob("*"))
                with patch("subprocess.Popen", side_effect=AssertionError("report attempted a native process")):
                    with self.assertRaisesRegex(ValueError, "Report output path must not be a symlink"):
                        render(self.sample_path, self.config_path, [self.primary_path, self.deep_path],
                               outdir, view="deep", evidence_root=self.root)
                    with self.assertRaisesRegex(ValueError, "Report output path must not be a symlink"):
                        write_report({"report_view": "deep", "options": {"deep": True}}, [self.sample],
                                     [self.primary], [self.deep], outdir, evidence_root=self.root)
                self.assertEqual(set(outdir.rglob("*")), paths_before)
                for path, contents in before.items():
                    self.assertEqual(path.read_bytes(), contents)
                alias.unlink()

    def test_read_only_input_aliases_do_not_block_report_output_validation(self):
        alias = self.root / "metadata/saved-sample-alias.json"
        alias.symlink_to(self.sample_path)
        original = self.sample_path.read_bytes()
        self.render_view("screening")
        self.assertTrue(alias.is_symlink())
        self.assertEqual(self.sample_path.read_bytes(), original)

    def test_cli_accepts_zero_evidence_only_for_an_empty_requested_scope(self):
        self.write("metadata/config.json", {"modules": {"minced": {"enabled": False}},
                                             "deep": {"enabled": False}})
        args = ["--sample", str(self.sample_path), "--config", str(self.config_path),
                "--view", "screening", "--evidence", "--outdir", str(self.root)]
        with patch("subprocess.Popen", side_effect=AssertionError("report attempted a native process")), \
                patch("builtins.print"):
            self.assertEqual(reporting.main(args), 0)
        result = json.loads((self.root / "evidence.json").read_text())
        self.assertEqual(result["run"]["evidence_sources"], [])
        self.assertEqual(result["run"]["completeness"], "completed_requested_scope")
        self.write("metadata/config.json", self.config)
        with self.assertRaisesRegex(ValueError, "Missing requested evidence: minced"):
            reporting.main(args)

    def test_deep_report_diagnostics_are_indexed_only_in_the_deep_view(self):
        diagnostics = [self.write("logs/deepsafe-report.log", "synthetic report diagnostic"),
                       self.write("benchmarks/deepsafe-report.tsv", "synthetic benchmark")]
        baseline = self.write("logs/minced.log", "synthetic screening diagnostic")
        before = {path: path.read_bytes() for path in diagnostics + [baseline]}
        self.render_view("screening")
        primary_index = json.loads((self.root / "metadata/files.json").read_text())
        primary_paths = {entry["path"] for entry in primary_index["files"]}
        self.assertIn("logs/minced.log", primary_paths)
        for path in diagnostics:
            self.assertNotIn(path.relative_to(self.root).as_posix(), primary_paths)
        self.render_view("deep")
        deep_index = json.loads((self.root / "deepsafe-report/metadata/files.json").read_text())
        deep_paths = {entry["path"] for entry in deep_index["files"]}
        for path in diagnostics:
            self.assertIn(path.relative_to(self.root).as_posix(), deep_paths)
        self.assertNotIn("logs/minced.log", deep_paths)
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)

    def test_aggregation_does_not_mutate_declared_configuration(self):
        config = copy.deepcopy(self.config)
        config["module_decisions"] = {"synthetic": {"deepsafe": {"requested": True, "action": "run"}}}
        before = copy.deepcopy(config)
        run, _, deep = aggregate(self.sample, config, [self.primary_path], view="screening")
        self.assertEqual(config, before)
        self.assertEqual(deep, [])
        self.assertEqual(run["report_view"], "screening")

    def test_provenance_hash_describes_parsed_snapshot_if_file_is_replaced_after_read(self):
        contents = self.primary_path.read_bytes()
        original_reader = reporting._read_snapshot
        def read_and_replace(path):
            result = original_reader(path)
            if path == self.primary_path:
                self.write("evidence/minced.json", {**self.primary, "metrics": {"revision": "later"}})
            return result
        with patch("safety.reporting._read_snapshot", side_effect=read_and_replace):
            run, records, _ = aggregate(self.sample, self.config, [self.primary_path], view="screening")
        self.assertEqual(records[0]["metrics"], {})
        self.assertEqual(run["evidence_sources"][0]["sha256"], hashlib.sha256(contents).hexdigest())
        self.assertNotEqual(self.primary_path.read_bytes(), contents)

    def test_artifact_permission_is_restored_when_rendering_raises(self):
        source = self.write("deepsafe/native/synthetic.json", {"synthetic": True})
        outdir = self.root / "deepsafe-report"
        with patch("basis.report_dashboard.render_dashboard", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                write_report({"report_view": "deep", "options": {"deep": True}}, [self.sample],
                             [self.primary], [self.deep], outdir, evidence_root=self.root)
        self.assertIsNone(_artifact_link(str(source), outdir))


if __name__ == "__main__":
    unittest.main()
