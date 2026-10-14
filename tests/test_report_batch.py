"""Multi-run reporting is explicit, source-pinned and preserves recorded scope."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.cli import main
from basis.report_batch import inspect_sources, refresh_saved_reports
from basis.util import sha256_file


def evidence(sid):
    return {"schema_version": "basis-report-1",
            "run": {"run_id": sid + "-saved", "version": "0.1.0", "completeness": "incomplete"},
            "samples": [{"sample_id": sid}], "records": [],
            "deep_records": [{"sample_id": sid, "module": "deepsafe", "execution_status": "completed",
                              "candidates": [{"sample_id": sid, "candidate_id": "c1", "protein_id": "p1",
                                  "selected": False, "execution": "deferred", "analyses": {},
                                  "assessment": {"interpretation": "unresolved"}}]}]}


class BatchReportTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.sources = []
        for sid in ("a", "b"):
            p = self.root / sid / "evidence.json"; p.parent.mkdir()
            p.write_text(json.dumps(evidence(sid)))
            self.sources.append({"sample_id": sid, "evidence": str(p.relative_to(self.root)), "evidence_sha256": sha256_file(p)})
        self.manifest = self.root / "sources.json"
        self.save()

    def save(self):
        self.manifest.write_text(json.dumps(self.sources))

    def test_prevalidates_all_pins_before_any_output(self):
        self.sources[1]["evidence_sha256"] = "0" * 64; self.save()
        out = self.root / "new"
        with self.assertRaisesRegex(ValueError, "checksum"):
            refresh_saved_reports(self.manifest, out)
        self.assertFalse(out.exists())

    def test_source_identity_and_duplicates_are_not_silently_substituted(self):
        self.sources[1]["sample_id"] = "wrong"; self.save()
        with self.assertRaisesRegex(ValueError, "sample identity"):
            inspect_sources(self.manifest, self.root / "new")
        self.sources = [self.sources[0], self.sources[0]]; self.save()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            inspect_sources(self.manifest, self.root / "new")

    def test_rejects_nested_output_and_dangling_output_symlink(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            inspect_sources(self.manifest, self.root / "a/new")
        dest = self.root / "dangling"; dest.symlink_to(self.root / "absent")
        with self.assertRaises(FileExistsError):
            inspect_sources(self.manifest, dest)

    def test_installed_style_cli_renders_two_sources_without_subprocess(self):
        out = self.root / "replayed"
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with patch("subprocess.run", side_effect=AssertionError("No analyses")), contextlib.redirect_stdout(io.StringIO()):
            code = main(["report", "--sources", str(self.manifest), "--outdir", str(out)])
        self.assertEqual(code, 0)
        audit = json.loads((out / "refresh-audit.json").read_text())
        self.assertEqual(audit["status"], "completed_reporting_refresh")
        self.assertTrue(audit["original_files_unchanged"])
        self.assertFalse(audit["native_analyses_run"])
        self.assertEqual((out / "sources.json").read_bytes(), self.manifest.read_bytes())
        panel = json.loads((out / "control-panel/inventory.json").read_text())
        self.assertEqual(len(panel["runs"]), 2)
        for original in before:
            self.assertEqual(Path(original).read_bytes(), before[original])
        for idx, sid in enumerate(("a", "b"), 1):
            rendered = json.loads((out / f"reports/run{idx:03d}/evidence.json").read_text())
            source = evidence(sid)
            for key in ("samples", "records", "deep_records"):
                self.assertEqual(rendered[key], source[key])
            self.assertEqual(rendered["run"]["completeness"], "incomplete")
        self.assertTrue((out / "control-panel/index.html").is_file())

    def test_cli_rejects_ambiguous_pin_option(self):
        out = self.root / "bad"
        with contextlib.redirect_stderr(io.StringIO()):
            code = main(["report", "--sources", str(self.manifest), "--expected-sha256", "0"*64, "--outdir", str(out)])
        self.assertEqual(code, 2); self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
