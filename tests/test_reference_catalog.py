import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import reference_catalog as catalog


class ReferenceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "units.tsv"
        self.source.write_bytes(b"quantity\tunit\nlength\tmm\n")
        self.row = {"id": "units", "kind": "reference", "status": "approved",
                    "source": "lab notebook", "source_version": "1", "reviewer": "Reviewer",
                    "reviewed_on": "2026-01-10", "citation": "Notebook 1, page 3",
                    "license": "Internal use", "path": self.source.name,
                    "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest()}
        self.spec = self.root / "spec.json"
        self.output = self.root / "published"
        self.rows = [self.row, {"id": "awaiting-review", "kind": "reference",
                                "status": "pending", "path": "does-not-exist.tsv"}]
        self.write_spec()

    def write_spec(self):
        self.spec.write_text(json.dumps({"schema": catalog.SPEC, "name": "Lab catalog",
                              "version": "1", "records": self.rows}))

    def test_build_move_verify_and_unreviewed_file_is_not_opened(self):
        result = catalog.build(self.spec, self.output)
        self.assertIsNone(result["placement_registry"])
        self.assertEqual((result["approved"], result["pending"]), (1, 1))
        manifest = json.loads((self.output / "catalog.json").read_bytes())
        self.assertEqual(manifest["spec_sha256"], hashlib.sha256(self.spec.read_bytes()).hexdigest())
        self.assertEqual(manifest["records"][0]["citation"], self.row["citation"])
        self.assertNotIn("path", manifest["records"][1])
        moved = self.output.rename(self.root / "moved")
        self.source.unlink()
        self.assertEqual(catalog.verify(moved)["catalog_sha256"], result["catalog_sha256"])
        with self.assertRaisesRegex(ValueError, "unused"):
            catalog.build(self.spec, moved)

    def test_require_reviewer_and_pinned_bytes_before_publish(self):
        self.row["reviewer"] = ""
        self.write_spec()
        with self.assertRaisesRegex(ValueError, "reviewer"):
            catalog.build(self.spec, self.output)
        self.row["reviewer"] = "Other"
        self.write_spec()
        self.source.write_bytes(b"edited")
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            catalog.build(self.spec, self.output)
        self.assertFalse(self.output.exists())

    def test_selected_spec_identity(self):
        digest = hashlib.sha256(self.spec.read_bytes()).hexdigest()
        self.spec.write_text(self.spec.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "specification SHA256"):
            catalog.build(self.spec, self.output, digest)
        self.assertFalse(self.output.exists())

    def test_package_detects_tampering_extra_files_and_path_escape(self):
        catalog.build(self.spec, self.output)
        asset = next((self.output / "files").iterdir())
        asset.write_bytes(b"altered")
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            catalog.verify(self.output)
        shutil.copy(self.source, asset)
        extra = self.output / "unrecorded.txt"
        extra.touch()
        with self.assertRaisesRegex(ValueError, "inventory"):
            catalog.verify(self.output)
        extra.unlink()
        asset.unlink()
        asset.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "invalid"):
            catalog.verify(self.output)
        manifest_path = self.output / "catalog.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["records"][0]["path"] = "../units.tsv"
        manifest_path.write_text(json.dumps(manifest))
        (self.output / "catalog.sha256").write_text(hashlib.sha256(manifest_path.read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError, "invalid"):
            catalog.verify(self.output)

    def test_registry_is_validated_and_copied_byte_for_byte(self):
        self.row["kind"] = "placement_registry"
        self.write_spec()
        with self.assertRaises(ValueError):
            catalog.build(self.spec, self.output)
        self.source.write_bytes(b'{"schema_version":1,"registry_id":"format-demo","panels":[]}')
        self.row["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.write_spec()
        built = catalog.build(self.spec, self.output)
        self.assertEqual(Path(built["placement_registry"]).read_bytes(), self.source.read_bytes())
        self.assertEqual(catalog.verify(self.output), built)

    def test_checked_template_cannot_be_published_without_review(self):
        spec = ROOT / "config/reference-catalog.example.json"
        result = subprocess.run([sys.executable, str(ROOT / "scripts/reference_catalog.py"),
                                "check", str(spec)], check=True, capture_output=True)
        self.assertEqual(json.loads(result.stdout)["approved"], 0)
        with self.assertRaisesRegex(ValueError, "At least one"):
            catalog.build(spec, self.output)
        self.assertFalse(self.output.exists())

    def test_reject_ambiguous_ids(self):
        self.rows[1]["id"] = "UNITS"
        self.write_spec()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            catalog.prepare(self.spec)

    def test_doi_lookup_drafts_only_and_cannot_approve_without_local_evidence(self):
        response = {"status": "ok", "message": {"DOI": "10.1234/Notes",
                    "title": ["Calibration <i>notes</i>"], "container-title": ["Example Journal"],
                    "author": [{"family": "Smith"}], "issued": {"date-parts": [[2020]]}}}
        with patch.object(catalog, "urlopen", return_value=BytesIO(json.dumps(response).encode())) as fetch:
            draft = catalog.draft_doi("https://doi.org/10.1234/notes")
        request = fetch.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.crossref.org/works/10.1234%2Fnotes")
        self.assertEqual(fetch.call_args.kwargs, {"timeout": 10})
        catalog.validate(draft, catalog.SPEC)
        row = draft["records"][0]
        self.assertEqual(row["source"], "https://doi.org/10.1234/notes")
        self.assertIn("Smith (2020) Calibration notes", row["citation"])
        self.assertEqual(row["status"], "pending")
        self.assertTrue(all(row[k] == "" for k in ("sha256", "path", "reviewer", "license")))
        self.spec.write_text(json.dumps(draft))
        with self.assertRaisesRegex(ValueError, "At least one"):
            catalog.build(self.spec, self.output)

    def test_failed_lookup_produces_no_usable_record(self):
        for code, reason in [(404, "No Crossref work"), (429, "rate limit"), (503, "HTTP 503")]:
            with self.subTest(code=code), patch.object(catalog, "urlopen", side_effect=HTTPError(
                    "https://api.crossref.org", code, "", None, None)):
                with self.assertRaisesRegex(ValueError, reason):
                    catalog.draft_doi("doi:10.1234/example")
        with patch.object(catalog, "urlopen", side_effect=TimeoutError):
            with self.assertRaisesRegex(ValueError, "connection"):
                catalog.draft_doi("10.1234/example")
        mismatch = b'{"status":"ok","message":{"DOI":"10.1234/other","title":["Other"]}}'
        with patch.object(catalog, "urlopen", return_value=BytesIO(mismatch)):
            with self.assertRaisesRegex(ValueError, "requested DOI"):
                catalog.draft_doi("10.1234/example")
        with patch.object(catalog, "urlopen") as fetch:
            with self.assertRaisesRegex(ValueError, "Enter a DOI"):
                catalog.draft_doi("https://example.org/paper")
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
