"""Saved-report navigation keeps evidence and explicit path permissions separate."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.control_panel import build_control_panel
from basis.report_refresh import refresh_saved_report
from tests.test_saved_report_release import saved


class SourceArtifactTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source_dir = self.root / "source"
        self.source_dir.mkdir()
        self.source = self.source_dir / "evidence.json"
        (self.source_dir / "report.html").write_text("Original report: preserve exactly")
        self.external = self.root / "native-data"
        self.external.mkdir()
        self.native = self.external / "native"
        self.native.mkdir()
        self.file = self.native / "index.html"
        self.file.write_text("Saved native report")
        (self.native / "not-declared.txt").write_text("Do not discover this file")
        self.bundle = saved(selected=True)
        self.bundle["deep_records"][0]["candidates"].append({
            "candidate_id": "deferred", "protein_id": "deferred", "sample_id": "s1",
            "selected": False, "execution": "deferred", "assessment": {"interpretation": "unresolved"}})
        self.bundle["records"][0]["artifacts"] = [str(self.native), str(self.file)]
        self.store()

    def store(self):
        self.source.write_text(json.dumps(self.bundle))
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def refresh(self, name="refresh", roots=True):
        return refresh_saved_report(self.source, self.root / name,
                                    artifact_roots=[self.external] if roots else None,
                                    expected_sha256=self.digest)

    def panel(self, report, name="panel", roots=()):
        artifacts = build_control_panel(results=[report], outdir=self.root / name,
                                        artifact_roots=roots)
        return json.loads(Path(artifacts["inventory"]).read_text())["runs"][0]

    def test_exact_declared_files_directory_status_and_immutable_evidence(self):
        before = {self.source: self.source.read_bytes(), self.source_dir / "report.html":
                  (self.source_dir / "report.html").read_bytes(), self.file: self.file.read_bytes()}
        with patch("subprocess.run", side_effect=AssertionError("No native tools in report replay")):
            merged, artifacts = self.refresh()
            run = self.panel(Path(artifacts["html"]).parent, roots=[self.external, self.source_dir])
        index = json.loads(Path(artifacts["source_artifacts_json"]).read_text())
        self.assertEqual(index["counts"], {"file": 2, "directory": 1})
        directory = next(e for e in index["entries"] if e["status"] == "directory")
        self.assertIsNone(directory["href"])
        self.assertFalse(any("not-declared" in f["path"] for f in run["files"]))
        self.assertFalse(any("Source artifact availability" in w for w in run["warnings"]))
        self.assertEqual(run["source_snapshot"]["run_id"], self.bundle["run"]["run_id"])
        self.assertEqual(run["label"], "s1")
        self.assertEqual(run["status"], "incomplete")
        self.assertEqual(len(run["candidates"]), 1)
        self.assertEqual(run["source_artifacts"]["counts"], {"file": 2, "directory": 1})
        self.assertIn("source-artifacts.html", Path(artifacts["html"]).read_text())
        for key in ("samples", "records", "deep_records"):
            self.assertEqual(merged[key], self.bundle[key])
        old_run, new_run = deepcopy(self.bundle["run"]), deepcopy(merged["run"])
        new_run.pop("report_replays")
        self.assertEqual(old_run, new_run)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_panel_requires_its_own_explicit_external_permission(self):
        _, artifacts = self.refresh()
        run = self.panel(Path(artifacts["html"]).parent)
        self.assertEqual(run["source_artifacts"]["counts"], {"outside_allowed_roots": 3})
        self.assertFalse(any(f["path"].startswith("original/") for f in run["files"]))
        self.assertEqual(run["source_snapshot"]["sha256"], self.digest)

    def test_default_refresh_cannot_widen_roots_from_run_metadata(self):
        self.bundle["run"]["outdir"] = str(self.external)
        self.bundle["run"]["artifact_roots"] = [str(self.root)]
        self.store()
        _, artifacts = self.refresh(roots=False)
        index = json.loads(Path(artifacts["source_artifacts_json"]).read_text())
        self.assertEqual(index["counts"], {"file": 1, "outside_allowed_roots": 2})

    def test_missing_outside_symlink_and_error_have_distinct_statuses(self):
        blocked = self.root / "blocked"
        blocked.mkdir()
        target = blocked / "secret.txt"
        target.write_text("not authorized")
        (self.external / "escape").symlink_to(target)
        (self.external / "internal-link").symlink_to(self.file)
        self.bundle["records"][0]["artifacts"] += [str(self.external / "missing.txt"),
            str(target), str(self.external / "escape"), str(self.external / "internal-link"), "bad\x00path"]
        self.store()
        _, artifacts = self.refresh()
        entries = json.loads(Path(artifacts["source_artifacts_json"]).read_text())["entries"]
        by_declared = {e["declared_path"]: e for e in entries}
        for value, status in [(str(self.external / "missing.txt"), "missing"), (str(target), "outside_allowed_roots"),
                              (str(self.external / "escape"), "symlink"), (str(self.external / "internal-link"), "symlink"),
                              ("bad\x00path", "error")]:
            self.assertEqual(by_declared[value]["status"], status)
            self.assertIsNone(by_declared[value]["href"])

    def test_pin_mismatch_invalid_root_and_nested_output_write_nothing(self):
        output = self.root / "not-created"
        with patch("basis.report.write_report", side_effect=AssertionError("Cannot render bad source pin")):
            with self.assertRaisesRegex(ValueError, "SHA256 differs"):
                refresh_saved_report(self.source, output, expected_sha256="0" * 64)
        self.assertFalse(output.exists())
        with self.assertRaises(OSError):
            refresh_saved_report(self.source, output, artifact_roots=[self.root / "missing-root"])
        self.assertFalse(output.exists())
        nested = self.source_dir / "new-report"
        with self.assertRaisesRegex(ValueError, "outside"):
            refresh_saved_report(self.source, nested)
        self.assertFalse(nested.exists())

    def test_source_mutation_does_not_finalize_replay(self):
        from basis.report import write_report
        def mutate(*args):
            artifacts = write_report(*args)
            self.source.write_text(self.source.read_text() + "\n")
            return artifacts
        with patch("basis.report.write_report", side_effect=mutate):
            with self.assertRaisesRegex(RuntimeError, "Source evidence changed"):
                self.refresh()
        self.assertFalse((self.root / "refresh/replay-audit.json").exists())
        self.assertFalse(json.loads((self.root / "refresh/replay-status.json").read_text())["finalized"])

    def test_stale_artifact_index_does_not_add_external_links(self):
        _, artifacts = self.refresh()
        index_path = Path(artifacts["source_artifacts_json"])
        index = json.loads(index_path.read_text())
        index["entries"][1]["declared_path"] = str(self.native / "not-declared.txt")
        index_path.write_text(json.dumps(index))
        run = self.panel(index_path.parent, roots=[self.external, self.source_dir])
        self.assertEqual(run["source_artifacts"]["status"], "unavailable")
        self.assertFalse(any(f["path"].startswith("original/") for f in run["files"]))
        self.assertTrue(any("checksum differs" in w for w in run["warnings"]))

    def test_forged_index_declaration_rejected_even_when_index_hash_updated(self):
        _, artifacts = self.refresh()
        index_path = Path(artifacts["source_artifacts_json"])
        index = json.loads(index_path.read_text())
        index["entries"][1]["declared_path"] = str(self.native / "not-declared.txt")
        index_path.write_text(json.dumps(index))
        audit_path = Path(artifacts["replay_audit"])
        audit = json.loads(audit_path.read_text())
        audit["output_file_sha256"][index_path.name] = hashlib.sha256(index_path.read_bytes()).hexdigest()
        audit_path.write_text(json.dumps(audit))
        run = self.panel(index_path.parent, roots=[self.external])
        self.assertEqual(run["source_artifacts"]["status"], "unavailable")
        self.assertIn("declarations differ", run["source_artifacts"]["reason"])

    def test_direct_panel_exact_external_artifact_and_bad_root_before_output(self):
        run = self.panel(self.source_dir, roots=[self.external])
        self.assertTrue(any(f["href"].endswith("native/index.html") for f in run["files"]))
        self.assertFalse(any("not-declared" in f["path"] for f in run["files"]))
        with self.assertRaises(OSError):
            self.panel(self.source_dir, "not-created", roots=[self.root / "missing-root"])
        self.assertFalse((self.root / "not-created").exists())

    def test_panel_detects_source_change_before_publishing(self):
        from basis.control_panel import collect_run
        def mutate(*args, **kwargs):
            result = collect_run(*args, **kwargs)
            self.source.write_text(self.source.read_text() + "\n")
            return result
        with patch("basis.control_panel.collect_run", side_effect=mutate):
            with self.assertRaisesRegex(RuntimeError, "source changed"):
                self.panel(self.source_dir)
        self.assertFalse((self.root / "panel/index.html").exists())
        self.assertFalse((self.root / "panel/inventory.json").exists())

    def test_caller_project_root_is_authorized_only_when_explicit(self):
        artifacts = build_control_panel(results=[self.source_dir], outdir=self.root / "panel",
                                        project=self.root, artifact_roots=[self.root])
        run = json.loads(Path(artifacts["inventory"]).read_text())["runs"][0]
        self.assertTrue(any(f["href"].endswith("native/index.html") for f in run["files"]))


if __name__ == "__main__":
    unittest.main()
