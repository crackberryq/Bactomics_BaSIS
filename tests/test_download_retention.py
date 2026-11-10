"""Downloaded-byte retention using mocked responses, never network services."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from basis import bootstrap_deep, bootstrap_providers, references


class Response(io.BytesIO):
    def __init__(self, content, declared=None):
        super().__init__(content)
        self.headers = {"Content-Length": str(len(content) if declared is None else declared)}

    def geturl(self):
        return "https://example.invalid/resource"


class DownloadRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_embedded(self, target, responses):
        with patch.object(sys, "argv", ["download", "https://example.invalid/resource", str(target), "1000"]), \
                patch("urllib.request.urlopen", side_effect=responses), patch("time.sleep"), \
                patch("sys.stdout", io.StringIO()):
            exec(compile(bootstrap_deep._DOWNLOAD, "resource-download", "exec"), {})

    def test_official_retry_preserves_failed_transfer_before_success(self):
        target = self.root / "download.bin"
        calls = []

        def download_once(url, path, maximum):
            calls.append(path)
            path.write_bytes(b"incomplete" if len(calls) == 1 else b"complete")
            if len(calls) == 1:
                raise OSError("synthetic interrupted response")
            return {"bytes": 8}

        with patch.object(bootstrap_providers, "_download_once", side_effect=download_once), \
                patch.object(bootstrap_providers.time, "sleep"):
            result = bootstrap_providers._download(bootstrap_providers.VFDB_URL, target)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(target.read_bytes(), b"complete")
        histories = list((self.root / ".previous-results").glob("*/data"))
        self.assertEqual([path.read_bytes() for path in histories], [b"incomplete"])

    def test_embedded_retry_keeps_failed_partial_and_previous_complete_target(self):
        target = self.root / "download.bin"
        target.write_bytes(b"previous completed response")
        self.run_embedded(target, [Response(b"short", declared=9), Response(b"complete")])
        self.assertEqual(target.read_bytes(), b"complete")
        self.assertEqual([p.read_bytes() for p in self.root.glob("download.bin.part-*")], [b"short"])
        self.assertEqual([p.read_bytes() for p in (self.root / ".previous-results").glob("*/data")],
                         [b"previous completed response"])

    def test_embedded_terminal_failure_keeps_all_partial_bytes(self):
        target = self.root / "download.bin"
        target.write_bytes(b"previous completed response")
        with self.assertRaisesRegex(ValueError, "truncated"):
            self.run_embedded(target, [Response(str(i).encode(), declared=9) for i in range(4)])
        self.assertEqual(target.read_bytes(), b"previous completed response")
        self.assertEqual(sorted(p.read_bytes() for p in self.root.glob("download.bin.part-*")),
                         [b"0", b"1", b"2", b"3"])

    def test_prosite_keeps_second_release_marker(self):
        def download(url, target, runner):
            target.write_bytes(b"Release 2026_01\n" if target.name in {"ps_reldt.txt", "release-after.txt"}
                               else b"synthetic resource")
            return target

        with patch.object(bootstrap_deep, "_download", side_effect=download), \
                patch("basis.prosite.load_snapshot", return_value=({"pattern": {}}, {"eligible_patterns": 1})), \
                patch("basis.prosite_profiles.load_profile_snapshot", return_value=(
                    {"profile": {}}, {"rule": {}}, {"rule_coverage": {"unavailable_rule_ids": []},
                                                     "limitation": "synthetic"})):
            bootstrap_deep._prosite({}, self.root, object())
        self.assertEqual((self.root / "release-after.txt").read_bytes(),
                         (self.root / "ps_reldt.txt").read_bytes())

    def requests(self):
        path = self.root / "requests.tsv"
        path.write_text("accession\treference_id\nAAA123.1\tsynthetic\n")
        return path

    def test_reference_responses_and_prior_catalogues_survive_rerun(self):
        fasta, metadata = self.root / "catalog.faa", self.root / "catalog.tsv"
        first = references.prepare_accession_catalog(self.requests(), fasta, metadata,
                                                     fetcher=lambda _: ">AAA123.1\nMKT\n")
        first_fasta, first_metadata = fasta.read_bytes(), metadata.read_bytes()
        second = references.prepare_accession_catalog(self.requests(), fasta, metadata,
                                                      fetcher=lambda _: ">AAA123.1\nMKTT\n")
        self.assertNotEqual(first["responses_directory"], second["responses_directory"])
        self.assertEqual((Path(first["responses_directory"]) / "response-0001.faa").read_text(),
                         ">AAA123.1\nMKT\n")
        retained = {path.read_bytes() for path in (self.root / ".previous-results").glob("*/data")}
        self.assertIn(first_fasta, retained)
        self.assertIn(first_metadata, retained)

    def test_invalid_reference_response_remains_without_replacing_catalogue(self):
        fasta, metadata = self.root / "catalog.faa", self.root / "catalog.tsv"
        fasta.write_text("previous fasta")
        metadata.write_text("previous metadata")
        with self.assertRaises(ValueError):
            references.prepare_accession_catalog(self.requests(), fasta, metadata,
                                                  fetcher=lambda _: "malformed response\n")
        responses = list((self.root / ".attempts/catalog.faa").glob("attempt-*/response-0001.faa"))
        self.assertEqual([p.read_text() for p in responses], ["malformed response\n"])
        self.assertEqual(fasta.read_text(), "previous fasta")
        self.assertEqual(metadata.read_text(), "previous metadata")


if __name__ == "__main__":
    unittest.main()
