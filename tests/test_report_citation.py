"""Citation metadata, portability and untrusted-version boundary checks."""
from pathlib import Path
import tempfile
import unittest

import yaml

from basis import __version__
from basis.report_citation import ORCID, TITLE, citation_text, write_citations


class CitationTests(unittest.TestCase):
    def test_cff_contains_confirmed_identity_and_no_invented_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = write_citations(tmp)
            cff = yaml.safe_load(Path(files["cite_cff"]).read_text())
            self.assertEqual(cff["cff-version"], "1.2.0")
            self.assertEqual(cff["type"], "software")
            self.assertEqual(cff["title"], TITLE)
            self.assertEqual(cff["authors"], [{"family-names": "Goldstein", "given-names": "Jimi", "orcid": ORCID}])
            self.assertEqual(cff["version"], __version__)
            for field in ("doi", "url", "repository-code", "date-released", "preferred-citation"):
                self.assertNotIn(field, cff)

    def test_recorded_version_used_consistently_and_output_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = write_citations(tmp, "0.1.0+report24")
            content = {key: Path(path).read_bytes() for key, path in first.items()}
            second = write_citations(tmp, "0.1.0+report24")
            self.assertEqual(content, {key: Path(path).read_bytes() for key, path in second.items()})
            for body in content.values():
                self.assertIn(b"0.1.0+report24", body)
                self.assertIn(b"Goldstein", body)

    def test_bibtex_and_ris_export_software_not_a_fabricated_article(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = write_citations(tmp)
            bib = Path(files["cite_bib"]).read_text()
            self.assertTrue(bib.startswith("@misc{goldstein_basis,"))
            self.assertIn("author = {Goldstein, Jimi}", bib)
            self.assertIn("{BaSIS}", bib)
            self.assertNotIn("year =", bib)
            self.assertNotIn("doi =", bib)
            self.assertEqual(bib.count("{"), bib.count("}"))
            ris = Path(files["cite_ris"]).read_text().splitlines()
            self.assertEqual(ris[0], "TY  - COMP")
            self.assertEqual(ris[-1], "ER  - ")
            self.assertIn("AU  - Goldstein, Jimi", ris)
            self.assertIn("ET  - " + __version__, ris)

    def test_plain_text_records_missing_date_and_tool_credit(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = write_citations(tmp)
            text = Path(files["cite_txt"]).read_text()
            self.assertTrue(text.startswith(citation_text()))
            self.assertIn("(n.d.)", text)
            self.assertIn("underlying tools and databases", text)

    def test_malformed_version_never_writes_partial_files(self):
        bad = ["", "1\nAU  - Injected", "1}", "<script>", "1/../../x", "1\\x", "a" * 65, 1]
        for value in bad:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                destination = Path(tmp) / "citations"
                with self.assertRaises(ValueError):
                    write_citations(destination, value)
                self.assertFalse(destination.exists())

    def test_project_cff_matches_generated_metadata(self):
        root = Path(__file__).resolve().parents[1]
        stored = yaml.safe_load((root / "CITATION.cff").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            generated = yaml.safe_load(Path(write_citations(tmp)["cite_cff"]).read_text())
        self.assertEqual(stored, generated)


if __name__ == "__main__":
    unittest.main()
