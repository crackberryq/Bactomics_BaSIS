import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from basis import optional_bacmet as bm
from basis.adapters.amr import parse_diamond, run as run_homology


FASTA = (b">BAC0001|abeM|tr|Q5FAM9|ENTRY protein\r\nACDEFGX\r\n"
         b">BAC0707|chtR|ZP_06677963|DNA-binding response regulator\r\nMAVY\r\n"
         b">BAC0744|tr|F4ZBX1|F4ZBX1_XANCI Hypothetical protein\r\nMPTY\r\n")
HASH = hashlib.sha256(FASTA).hexdigest()


class BacMetSetupTests(unittest.TestCase):
    def validate(self, content=FASTA, count=3, digest=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refs.fasta"
            path.write_bytes(content)
            return bm.validate_bacmet_fasta(path, expected_count=count,
                expected_sha256=digest or hashlib.sha256(content).hexdigest())

    def test_original_headers_and_gene_missing_variants(self):
        refs = self.validate()
        self.assertEqual([r["gene"] for r in refs], ["abeM", "chtR", None])
        self.assertEqual(refs[1]["accession"], "ZP_06677963")
        self.assertEqual(refs[2]["accession"], "F4ZBX1")
        self.assertEqual(refs[0]["source_header"], "BAC0001|abeM|tr|Q5FAM9|ENTRY protein")
        self.assertEqual(refs[0]["length"], 7)

    def test_pin_and_count_are_required(self):
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.validate(digest="0" * 64)
        with self.assertRaisesRegex(ValueError, "Expected 4"):
            self.validate(count=4)

    def test_malformed_records_are_rejected(self):
        for data in [FASTA + FASTA[:53], FASTA.replace(b"ACDEFGX", b"AC12"),
                     FASTA.replace(b"ACDEFGX", b""), FASTA + FASTA.split(b">BAC0707")[0],
                     FASTA.replace(b"BAC0001", b"unexpected"), b"ACD\n" + FASTA,
                     FASTA + b">\nACD\n"]:
            with self.subTest(data=data[:40]), self.assertRaises(ValueError):
                self.validate(data)

    def test_reused_bacmet_label_preserves_distinct_source_records(self):
        refs = self.validate(FASTA.replace(b"BAC0707", b"BAC0001"))
        self.assertEqual(refs[0]["bacmet_id"], refs[1]["bacmet_id"])
        self.assertNotEqual(refs[0]["reference_id"], refs[1]["reference_id"])

    def test_explicit_official_urls_only(self):
        self.assertTrue(bm._allowed_url(bm.SOURCE_URLS[0]))
        self.assertTrue(bm._allowed_url(bm.SOURCE_URLS[1]))
        for url in ["http://evil.test" + bm.SOURCE_PATH,
                    "http://user@" + bm.SOURCE_HOST + bm.SOURCE_PATH,
                    bm.SOURCE_URLS[1] + "?mirror=1",
                    "file:///tmp/refs.faa"]:
            self.assertFalse(bm._allowed_url(url))

    def setup_mocks(self, tmp, create_index=True):
        root = Path(tmp)
        env = root / "env"
        (env / "conda-meta").mkdir(parents=True)
        (env / "bin").mkdir()
        (env / "bin/diamond").write_text("fixture")
        original = bm.validate_bacmet_fasta

        def validate(path, **kwargs):
            return original(path, expected_sha256=HASH, expected_count=3)

        def download(destination, log):
            Path(destination).write_bytes(FASTA)
            return {"source_url": bm.SOURCE_URLS[1], "transport": "http"}

        def run(runner, argv, **kwargs):
            runner.commands.append({"argv": argv, "returncode": 0})
            if argv[1] == "version":
                return subprocess.CompletedProcess(argv, 0, "diamond version 2.2.8\n", "")
            if create_index:
                Path(argv[argv.index("--db") + 1] + ".dmnd").write_bytes(b"test diamond index")
            return subprocess.CompletedProcess(argv, 0, "", "")

        return env, [patch.object(bm, "EXP_SHA256", HASH), patch.object(bm, "EXP_COUNT", 3),
                     patch.object(bm, "validate_bacmet_fasta", validate),
                     patch.object(bm, "_download_source", download),
                     patch.object(bm.CommandRunner, "run", run)]

    def test_build_preserves_source_and_verifies_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            env, mocks = self.setup_mocks(tmp)
            for mock in mocks:
                mock.start()
                self.addCleanup(mock.stop)
            root = Path(tmp) / "db"
            resources = bm.prepare_bacmet(root, env, log=lambda _: None)
            self.assertFalse(resources["reused"])
            self.assertEqual(Path(resources["reference_fasta"]).read_bytes(), FASTA)
            meta = json.loads(Path(resources["provenance"]).read_text())
            self.assertEqual(meta["status"], "ready")
            self.assertEqual(meta["protein_count"], 3)
            self.assertIn("does not establish", meta["interpretation"])
            reused = bm.prepare_bacmet(root, env, log=lambda _: None, allow_download=False)
            self.assertTrue(reused["reused"])
            self.assertEqual(resources["database"], reused["database"])
            Path(resources["database"]).write_bytes(b"corrupted")
            repaired = bm.prepare_bacmet(root, env, log=lambda _: None)
            self.assertFalse(repaired["reused"])
            self.assertNotEqual(resources["database"], repaired["database"])
            self.assertEqual(Path(resources["database"]).read_bytes(), b"corrupted")

    def test_no_install_flag_never_downloads_missing_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            env, mocks = self.setup_mocks(tmp)
            for mock in mocks:
                mock.start()
                self.addCleanup(mock.stop)
            with patch.object(bm, "_download_source") as fetch:
                with self.assertRaisesRegex(ValueError, "rerun with --install"):
                    bm.prepare_bacmet(Path(tmp) / "db", env, log=lambda _: None, allow_download=False)
                fetch.assert_not_called()

    def test_no_success_manifest_for_missing_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            env, mocks = self.setup_mocks(tmp, create_index=False)
            for mock in mocks:
                mock.start()
                self.addCleanup(mock.stop)
            root = Path(tmp) / "db"
            with self.assertRaisesRegex(ValueError, "nonempty"):
                bm.prepare_bacmet(root, env, log=lambda _: None)
            self.assertEqual(list(root.glob("*/provenance.json")), [])
            status = json.loads(next(root.glob("*/setup_status.json")).read_text())
            self.assertEqual(status["status"], "failed")

    def test_missing_environment_does_not_download(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bm, "_download_source") as fetch:
            with self.assertRaisesRegex(ValueError, "environment"):
                bm.prepare_bacmet(Path(tmp) / "db", Path(tmp) / "missing")
            fetch.assert_not_called()

    def diamond_row(self, reference, title):
        return ["QUERY", reference, "85", "80", "100", "100", "1", "80", "1", "80",
                "1e-30", "100", "80", "80", title]

    def test_native_source_header_annotation_keeps_distinct_references(self):
        headers = ["BAC0291|cnrR/cnrX|sp|P37975|CNRR_RALME Nickel resistance protein",
                   "BAC0291|oprJ|sp|Q51397|OPRJ_PSEAE Outer membrane protein",
                   "BAC0744|tr|F4ZBX1|F4ZBX1_XANCI Hypothetical protein"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.tsv"
            path.write_text("\n".join("\t".join(self.diamond_row(h.split()[0], h)) for h in headers) + "\n")
            findings = parse_diamond(path, "bacmet")
            self.assertEqual([f["gene"] for f in findings], ["cnrR/cnrX", "oprJ", headers[2].split()[0]])
            self.assertEqual(len({f["reference"] for f in findings}), 3)
            for finding in findings:
                self.assertEqual(finding["category"], "stress")
                self.assertEqual(finding["evidence_level"], "homology")
                self.assertFalse(finding["attributes"]["function_confirmed"])
                self.assertEqual(finding["attributes"]["reference_header_status"], "parsed_exact_reference_id")

    def test_native_source_title_mismatch_does_not_assign_a_gene(self):
        title = "BAC0291|oprJ|sp|Q51397|OPRJ_PSEAE Outer membrane protein"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.tsv"
            path.write_text("\t".join(self.diamond_row("BAC0291", title)) + "\n")
            finding = parse_diamond(path, "bacmet")[0]
            self.assertEqual(finding["gene"], "BAC0291")
            self.assertNotIn("source_reference_annotation", finding["attributes"])
            self.assertEqual(finding["attributes"]["reference_header_status"], "reference_title_mismatch_retained_as_recorded")

    def test_source_id_preservation_flag_is_bacmet_specific(self):
        class Runner:
            def __init__(self):
                self.calls = []

            def run(self, argv, **kwargs):
                self.calls.append(argv)
                Path(argv[argv.index("--out") + 1]).write_text("")
                return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "refs.dmnd"
            database.write_bytes(b"test index")
            for module in ("vfdb", "bacmet"):
                runner = Runner()
                run_homology(module, {"proteins": str(root / "proteome.faa")},
                             {"database": str(database)}, root / module, runner)
                self.assertEqual("--no-parse-seqids" in runner.calls[0], module == "bacmet")


if __name__ == "__main__":
    unittest.main()
