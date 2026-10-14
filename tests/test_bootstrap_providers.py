"""Provider contract tests: fixture data and fake native tools only."""
import gzip
import io
import json
from pathlib import Path
import sqlite3
import ssl
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
from unittest.mock import patch

from basis import bootstrap_providers as providers


class FakeRunner:
    def __init__(self, prefix=None, threads=2):
        self.prefix = ["conda", "run", "--prefix", str(prefix)] if prefix else []
        self.threads = threads
        self.commands = []
        self.callback = None

    def run(self, argv, cwd=None):
        self.commands.append(list(argv))
        if self.callback:
            self.callback(argv, Path(cwd))
        if argv[:2] == ["diamond", "version"]:
            return SimpleNamespace(stdout="diamond version 2.2.8\n")
        if argv[:2] == ["diamond", "makedb"]:
            Path(argv[argv.index("--db") + 1] + ".dmnd").write_bytes(b"fixture diamond index")
        return SimpleNamespace(stdout="native tool fixture 1.0\n")


def write_files(root, names, data=b"fixture"):
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def make_tar(path, entries):
    with tarfile.open(path, "w:bz2") as archive:
        for name, data in entries:
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))


class BootstrapProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.destination = self.root / "new snapshot"
        self.runner = FakeRunner()

    def tearDown(self):
        self.temp.cleanup()

    def spec(self, provider, **extra):
        return {"provider": provider, "id": provider, "threads": 8, **extra}

    def test_card_archive_parsed_and_returned_in_native_adapter_schema(self):
        def download(url, path, maximum=0):
            self.assertEqual(url, providers.CARD_URL)
            make_tar(path, [("card.json", json.dumps({"_version": "4.0.2", "123": {"model_sequences": {}}}).encode())])
            return {"source_url": url, "sha256": "a" * 64}
        with patch.object(providers, "_download", side_effect=download):
            result = providers.prepare_resource(self.spec("card"), self.destination, self.runner)
        database = Path(result["module_updates"]["rgi"]["database"])
        self.assertEqual(database.name, "card.json")
        self.assertEqual(result["module_updates"]["rgi"]["database_version"], "4.0.2")
        self.assertEqual(result["provenance"]["reference_sources"][0]["format"], "card_json")
        self.assertEqual(self.runner.commands, [])

    def test_card_rejects_unsafe_or_ambiguous_archive_and_invalid_schema(self):
        archive = self.root / "source.tar.bz2"
        scenarios = [[("../escape", b"x")], [("/absolute", b"x")],
                     [("card.json", b"{}"), ("nested/card.json", b"{}")],
                     [("card.json", b'{"_version":"4.0.2"}')]]
        for index, entries in enumerate(scenarios):
            make_tar(archive, entries)
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                providers.extract_card(archive, self.root / ("card" + str(index) + ".json"))
        self.assertFalse((self.root.parent / "escape").exists())

    def test_card_rejects_archive_links(self):
        archive = self.root / "source.tar.bz2"
        with tarfile.open(archive, "w:bz2") as handle:
            item = tarfile.TarInfo("card.json")
            item.type = tarfile.SYMTYPE
            item.linkname = "/etc/passwd"
            handle.addfile(item)
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            providers.extract_card(archive, self.root / "card.json")

    def test_vfdb_normalizes_header_only_and_preserves_exact_reference_ids(self):
        raw = b">VFG123(gb|WP_1) long\xa0description\r\nACDEFG\r\n>VFG456(gb|WP_2) description\nMKMK\n"
        fasta = self.root / "normalized.faa"
        result = providers.normalize_vfdb(raw, fasta)
        self.assertEqual(result["protein_count"], 2)
        self.assertEqual(result["headers_repaired"][0]["reference_id"], "VFG123(gb|WP_1)")
        self.assertTrue(result["sequence_bytes_preserved"])
        self.assertIn(b"ACDEFG\r\n", fasta.read_bytes())
        with self.assertRaises(UnicodeError):
            providers.normalize_vfdb(b">ref bad\xffheader\nACDE\n", self.root / "bad.faa")
        with self.assertRaises(UnicodeError):
            providers.normalize_vfdb(b">ref\nACD\xa0E\n", self.root / "bad2.faa")

    def test_vfdb_build_uses_allocated_threads_and_id_preservation(self):
        def download(url, path, maximum=0):
            self.assertEqual(url, providers.VFDB_URL)
            path.write_bytes(gzip.compress(b">VFG1(gb|WP_1) test\xa0header\nACDE\n"))
            return {"source_url": url, "sha256": "b" * 64}
        with patch.object(providers, "_download", side_effect=download):
            result = providers.prepare_resource(self.spec("vfdb"), self.destination, self.runner)
        command = self.runner.commands[-1]
        self.assertIn("--no-parse-seqids", command)
        self.assertEqual(command[command.index("--threads") + 1], "2")
        self.assertTrue(Path(result["module_updates"]["vfdb"]["database"]).is_file())
        self.assertEqual(result["provenance"]["details"]["normalization"]["protein_count"], 1)

    def test_old_diamond_rejected_before_build(self):
        class OldRunner(FakeRunner):
            def run(self, argv, cwd=None):
                return SimpleNamespace(stdout="diamond version 2.1.0")
        with self.assertRaisesRegex(ValueError, "2.1.7"):
            providers._diamond(OldRunner(), self.root / "source.faa", self.root / "index", 2, self.root)

    def test_amrfinder_resolves_versioned_directory_and_checks_index(self):
        def native(argv, root):
            if argv[0] != "amrfinder_update":
                return
            released = root / "database/2026-08-07.1"
            write_files(released, ["AMRProt.fa", "AMRProt.fa.phr", "AMR.LIB", "version.txt"] +
                        ["AMR.LIB.h3" + suffix for suffix in "fimp"])
            (released / "version.txt").write_text("2026-08-07.1\n")
            (released.parent / "latest").symlink_to(released, target_is_directory=True)
        self.runner.callback = native
        result = providers.prepare_resource(self.spec("amrfinder"), self.destination, self.runner)
        self.assertEqual(Path(result["module_updates"]["amrfinder"]["database"]).name, "2026-08-07.1")
        self.assertEqual(result["provenance"]["resolved_version"], "2026-08-07.1")
        self.assertEqual(self.runner.commands[1][:2], ["amrfinder", "-V"])
        self.assertFalse(Path(__import__("os").readlink(self.destination / "database/latest")).is_absolute())
        renamed = self.root / "published"
        self.destination.rename(renamed)
        self.assertTrue((renamed / "database/latest/AMRProt.fa").is_file())

    def test_native_success_without_required_artifacts_is_rejected(self):
        for provider in ("amrfinder", "genomad", "antismash", "mobsuite"):
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                providers.prepare_resource(self.spec(provider), self.root / provider, self.runner)

    def test_genomad_checks_complete_database_layout(self):
        def native(argv, root):
            db = root / "genomad_db"
            names = ["version.txt", "genomad_marker_metadata.tsv", "nodes.dmp", "names.dmp"]
            names += [prefix + suffix for prefix in ("genomad_db", "genomad_mini_db", "genomad_integrase_db")
                      for suffix in ("", ".dbtype", ".index")]
            write_files(db, names)
            (db / "version.txt").write_text("1.9\n")
        self.runner.callback = native
        result = providers.prepare_resource(self.spec("genomad"), self.destination, self.runner)
        self.assertEqual(result["module_updates"]["genomad"]["database_version"], "1.9")
        self.assertEqual(Path(result["module_updates"]["genomad"]["database"]).name, "genomad_db")

    def test_antismash_bundle_exposes_pfam_to_deepsafe(self):
        env = self.root / "env"
        model = "lib/python3.11/site-packages/antismash/detection/hmm_detection/data/bgc_seeds.hmm"
        write_files(env, ["conda-meta/history", model])
        self.runner = FakeRunner(env)
        def native(argv, root):
            if argv[0] == "antismash":
                return
            write_files(root, ["pfam/35.0/Pfam-A.hmm" + suffix for suffix in ("", ".h3f", ".h3i", ".h3m", ".h3p")])
            write_files(root, [name + "/fixture" for name in ("as-js", "mite", "resfam", "tigrfam", "clusterblast", "knownclusterblast", "clustercompare", "comparippson", "nrps_pks")])
        self.runner.callback = native
        result = providers.prepare_resource(self.spec("antismash"), self.destination, self.runner)
        self.assertTrue(Path(result["deep_updates"]["domain_database"]).is_file())
        self.assertEqual(result["provenance"]["details"]["pfam_version"], "35.0")
        self.assertEqual(result["provenance"]["details"]["source_sha256"][str(env / model)],
                         providers.sha256_file(env / model))

    def test_antismash_environment_asset_cannot_escape_native_environment(self):
        env = self.root / "env"
        model = env / "lib/python3.11/site-packages/antismash/data/model.hmm"
        write_files(env, ["conda-meta/history"])
        model.parent.mkdir(parents=True)
        outside = self.root / "outside.hmm"
        outside.write_text("outside model")
        model.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "outside"):
            providers._antismash_environment_assets(FakeRunner(env))

    def test_mobsuite_requires_indexes_and_populated_taxonomy(self):
        def native(argv, root):
            if argv[:2] != ["python", "-c"] or argv[2] != providers._MOB_AFFINITY_EXEC:
                return
            self.assertEqual(argv[3], "2")
            write_files(root, ["ncbi_plasmid_full_seqs.fas", "ncbi_plasmid_full_seqs.fas.msh", "repetitive.dna.fas",
                               "rep.dna.fas", "mob.proteins.faa", "mpf.proteins.faa", "orit.fas", "clusters.txt"])
            write_files(root, [prefix + "." + suffix for prefix in ("ncbi_plasmid_full_seqs.fas", "repetitive.dna.fas")
                               for suffix in ("nhr", "nin", "nsq")])
            with sqlite3.connect(root / "taxa.sqlite") as database:
                database.execute("CREATE TABLE species (taxid INTEGER, spname TEXT)")
                database.execute("INSERT INTO species VALUES (1, 'root')")
        self.runner.callback = native
        result = providers.prepare_resource(self.spec("mobsuite"), self.destination, self.runner)
        self.assertEqual(result["module_updates"]["mobsuite"]["database"], str(self.destination))
        self.assertTrue(result["provenance"]["resolved_version"].startswith("MOB-suite snapshot "))
        self.assertEqual(result["provenance"]["details"]["initialization_cpu_affinity_limit"], 2)

    def test_bacmet_reuses_reviewed_pin_validation_and_builds_index(self):
        def download(path, log):
            path.write_text(">BAC1|test|sp|P12345|ENTRY description\nACDE\n")
            return {"sha256": "pinned_fixture"}
        with patch("basis.optional_bacmet._download_source", side_effect=download), \
             patch("basis.optional_bacmet.validate_bacmet_fasta", return_value=[{"reference_id": "BAC1"}]) as validate:
            result = providers.prepare_resource(self.spec("bacmet"), self.destination, self.runner)
        validate.assert_called_once()
        self.assertTrue(Path(result["module_updates"]["bacmet"]["database"]).is_file())
        self.assertIn("experimentally confirmed", result["module_updates"]["bacmet"]["database_version"])
        self.assertEqual(result["provenance"]["comparator_review_status"], "unreviewed")

    def test_bundled_models_copied_hashed_and_native_source_binding_declared(self):
        env = self.root / "env"
        write_files(env, ["conda-meta/history", "bin/pHMMs/clusters.single.faa", "bin/pHMMs/clusters.faa.hmm"])
        runner = FakeRunner(env)
        result = providers.prepare_resource(self.spec("isescan"), self.destination, runner)
        self.assertEqual(result["module_updates"]["isescan"]["database"], str(env / "bin/pHMMs"))
        self.assertTrue((self.destination / "bundled/clusters.single.faa").is_file())
        self.assertEqual(result["provenance"]["details"]["runtime_binding"], "tool_environment_bundled_resources")
        self.assertEqual(len(result["provenance"]["details"]["source_sha256"]), 2)

    def test_bundled_models_cannot_escape_native_environment(self):
        env = self.root / "env"
        write_files(env, ["conda-meta/history", "bin/pHMMs/clusters.single.faa", "bin/pHMMs/clusters.faa.hmm"])
        outside = self.root / "outside"
        outside.write_text("outside")
        (env / "bin/pHMMs/escape").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "outside"):
            providers.prepare_resource(self.spec("isescan"), self.destination, FakeRunner(env))

    def test_expected_version_mismatch_and_arbitrary_urls_fail(self):
        with self.assertRaisesRegex(ValueError, "expected_version"):
            providers._check_version(self.spec("card", options={"expected_version": "1.0"}), "2.0")
        with self.assertRaisesRegex(ValueError, "fixed official"):
            providers.prepare_resource(self.spec("vfdb", options={"url": "https://example.com/test"}), self.destination, self.runner)
        self.assertFalse(self.destination.exists())

    def test_redirect_validation_rejects_insecure_or_unrelated_hosts(self):
        for url in ("http://card.mcmaster.ca/latest/data", "https://example.com/data", "https://user:pass@card.mcmaster.ca/data"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                providers._official_url(url)

    def test_download_rejects_content_length_mismatch(self):
        class Response(io.BytesIO):
            headers = {"Content-Length": "100"}
            def geturl(self):
                return providers.CARD_URL
        opener = SimpleNamespace(open=lambda *args, **kwargs: Response(b"partial"))
        with patch.object(providers.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(providers._InterruptedDownload, "expected 100 bytes, received 7"):
                providers._download_once(providers.CARD_URL, self.root / "partial", 1000)
        self.assertEqual((self.root / "partial").read_bytes(), b"partial")

    def test_download_retries_interrupted_ssl_from_clean_artifact(self):
        count = 0
        path = self.root / "source"
        def attempt(url, destination, maximum):
            nonlocal count
            count += 1
            self.assertFalse(destination.exists())
            destination.write_bytes(b"partial" if count == 1 else b"complete")
            if count == 1:
                raise ssl.SSLError("record layer failure")
            return {"sha256": providers.sha256_file(destination)}
        with patch.object(providers, "_download_once", side_effect=attempt), \
             patch.object(providers.time, "sleep") as sleep:
            result = providers._download(providers.CARD_URL, path)
        self.assertEqual(result["attempts"], 2)
        sleep.assert_called_once_with(2)
        self.assertEqual(path.read_bytes(), b"complete")

    def test_download_bounds_retries_and_preserves_final_failure_artifact(self):
        path = self.root / "source"
        def attempt(url, destination, maximum):
            self.assertFalse(destination.exists())
            destination.write_bytes(b"partial")
            raise providers._InterruptedDownload("truncated response")
        with patch.object(providers, "_download_once", side_effect=attempt) as download, \
             patch.object(providers.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "after 3 attempts"):
                providers._download(providers.CARD_URL, path)
        self.assertEqual(download.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])
        self.assertEqual(path.read_bytes(), b"partial")

    def test_download_permanent_http_failure_and_existing_file_are_not_retried(self):
        path = self.root / "source"
        error = urllib.error.HTTPError(providers.CARD_URL, 404, "Not Found", {}, None)
        with patch.object(providers, "_download_once", side_effect=error) as download, \
             patch.object(providers.time, "sleep") as sleep:
            with self.assertRaises(urllib.error.HTTPError):
                providers._download(providers.CARD_URL, path)
            path.write_bytes(b"saved artifact")
            with self.assertRaisesRegex(ValueError, "overwrite"):
                providers._download(providers.CARD_URL, path)
        download.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(path.read_bytes(), b"saved artifact")


if __name__ == "__main__":
    unittest.main()
