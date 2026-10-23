"""geNomad transfer/extraction recovery using tiny artificial archives only."""
import errno
import io
import json
from pathlib import Path
import ssl
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/vendor")]
from basis import download_genomad as downloads


def archive_bytes(entries=None):
    entries = entries or [("genomad_db/version.txt", b"1.9\n"),
                          ("genomad_db/database.bin", b"synthetic resource bytes")]
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in entries:
            if isinstance(data, tarfile.TarInfo):
                archive.addfile(data)
            else:
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


class Response(io.BytesIO):
    def __init__(self, body, url, length=None):
        super().__init__(body)
        self.url = url
        self.headers = {"Content-Length": str(len(body) if length is None else length)}

    def geturl(self):
        return self.url


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.body = archive_bytes()
        self.calls = []

    def tearDown(self):
        self.temporary.cleanup()

    def open_url(self, url):
        self.calls.append(url)
        if url.endswith("releases.txt"):
            return Response(b"database\tpackage\n1.8\t1.10\n1.9\t1.11\n", url)
        return Response(self.body, url)

    def acquire(self, **kwargs):
        return downloads.acquire_archive("1.9", self.root / "cache",
                                         sleep=lambda seconds: None,
                                         open_url=kwargs.pop("open_url", self.open_url), **kwargs)

    def test_compatibility_selection_matches_native_last_row_policy(self):
        releases = "database\tpackage\n1.7\t1.10\n1.8\t1.11\n1.9\t1.11\n"
        self.assertEqual(downloads.resolve_database_version(releases, "1.11.2"), "1.9")
        with self.assertRaisesRegex(ValueError, "No compatible"):
            downloads.resolve_database_version(releases, "2.0.0")
        with self.assertRaisesRegex(ValueError, "No compatible"):
            downloads.resolve_database_version("header\n../../bad\t1.11\n", "1.11.0")

    def test_complete_archive_reused_without_network(self):
        archive, first = self.acquire()
        self.assertTrue(archive.is_file())
        self.assertFalse(first["archive_reused"])
        self.assertTrue((archive.parent / "download.json").is_file())
        def forbidden(url):
            raise AssertionError("Network attempted for a completed archive")
        same, second = self.acquire(open_url=forbidden)
        self.assertEqual(same, archive)
        self.assertTrue(second["archive_reused"])
        self.assertFalse(second["range_resume"])

    def test_short_transfer_retained_and_retried(self):
        calls = []
        def interrupted(url):
            calls.append(url)
            return Response(self.body[:20] if len(calls) == 1 else self.body,
                            url, length=len(self.body))
        archive, record = self.acquire(open_url=interrupted)
        self.assertEqual(len(calls), 2)
        self.assertEqual(record["attempts"], 2)
        self.assertEqual(archive.read_bytes(), self.body)
        retained = list((self.root / "cache/1.9").glob("*.part-*"))
        self.assertEqual(len(retained), 2)  # failed bytes and diagnostic JSON
        diagnostic = next(path for path in retained if path.suffix == ".json")
        self.assertEqual(json.loads(diagnostic.read_text())["status"], "failed")

    def test_truncated_gzip_with_matching_http_length_never_published(self):
        calls = []
        def corrupted(url):
            calls.append(url)
            return Response(self.body[:-8], url)
        with self.assertRaisesRegex(RuntimeError, "truncated or corrupt"):
            self.acquire(open_url=corrupted)
        self.assertEqual(len(calls), 3)
        self.assertFalse((self.root / "cache/1.9/complete").exists())
        self.assertEqual(len(list((self.root / "cache/1.9").glob("*.failed.json"))), 3)

    def test_crc_failure_is_detected(self):
        path = self.root / "bad.tar.gz"
        corrupted = bytearray(self.body)
        corrupted[-8] ^= 0x01
        path.write_bytes(corrupted)
        with self.assertRaises(downloads.DownloadIntegrityError):
            downloads.validate_archive(path)

    def test_mismatched_archive_version_never_poisons_completed_cache(self):
        self.body = archive_bytes([("genomad_db/version.txt", b"1.8\n")])
        with self.assertRaisesRegex(RuntimeError, "version differs"):
            self.acquire()
        self.assertEqual(len(self.calls), 3)
        self.assertFalse((self.root / "cache/1.9/complete").exists())
        self.body = archive_bytes()
        archive, record = self.acquire()
        self.assertFalse(record["archive_reused"])
        self.assertTrue(archive.is_file())

    def test_cached_archive_version_checked_even_if_checksum_matches(self):
        archive, _ = self.acquire()
        archive.write_bytes(archive_bytes([("genomad_db/version.txt", b"1.8\n")]))
        receipt = archive.parent / "download.json"
        record = json.loads(receipt.read_text())
        record.update(bytes=archive.stat().st_size, sha256=downloads._sha256(archive))
        receipt.write_text(json.dumps(record))
        same, refreshed = self.acquire()
        self.assertFalse(refreshed["archive_reused"])
        self.assertEqual(same.read_bytes(), self.body)
        self.assertEqual(len(list((self.root / "cache/1.9").glob("complete.invalid-*"))), 1)

    def test_prevalidated_archive_mutation_blocks_extraction(self):
        validation = {}
        archive, _ = self.acquire(validation=validation)
        archive.write_bytes(archive_bytes([("genomad_db/version.txt", b"1.8\n")]))
        with self.assertRaisesRegex(downloads.DownloadIntegrityError, "changed after verification"):
            downloads.extract_archive(archive, self.root / "output", validation=validation)
        self.assertFalse((self.root / "output/genomad_db").exists())

    def test_invalid_cached_bytes_retained_before_new_transfer(self):
        archive, _ = self.acquire()
        archive.write_bytes(b"corrupt cache")
        replacement, record = self.acquire()
        self.assertEqual(replacement.read_bytes(), self.body)
        self.assertFalse(record["archive_reused"])
        invalid = list((self.root / "cache/1.9").glob("complete.invalid-*"))
        self.assertEqual(len(invalid), 1)
        self.assertEqual((invalid[0] / archive.name).read_bytes(), b"corrupt cache")

    def test_non_transient_errors_are_not_retried(self):
        for error in (
            urllib.error.HTTPError(downloads.BASE_URL, 404, "missing", {}, None),
            urllib.error.URLError(ssl.SSLCertVerificationError("untrusted certificate")),
            OSError(errno.ENOSPC, "disk full"),
        ):
            with self.subTest(error=type(error).__name__):
                calls = []
                def fail(url):
                    calls.append(url)
                    raise error
                with self.assertRaises(RuntimeError):
                    self.acquire(open_url=fail)
                self.assertEqual(len(calls), 1)

    def test_shared_version_lock_prevents_a_second_transfer(self):
        directory = self.root / "cache/1.9"
        directory.mkdir(parents=True)
        with downloads._cache_lock(directory):
            with self.assertRaisesRegex(RuntimeError, "Another geNomad download"):
                self.acquire()
        self.assertFalse(self.calls)

    def test_failed_manifest_publication_never_exposes_complete_archive(self):
        original = downloads._atomic_json
        def fail_ready(path, value):
            if Path(path).parent.name.startswith(".ready-"):
                raise OSError(errno.ENOSPC, "manifest write interrupted")
            return original(path, value)
        with patch.object(downloads, "_atomic_json", side_effect=fail_ready):
            with self.assertRaisesRegex(RuntimeError, "manifest write interrupted"):
                self.acquire()
        self.assertFalse((self.root / "cache/1.9/complete").exists())
        retained = list((self.root / "cache/1.9").glob(".ready-*/genomad_db_v1.9.tar.gz"))
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].read_bytes(), self.body)

    def test_short_compatibility_response_retried_before_version_selection(self):
        calls = []
        full = b"database\tpackage\n1.9\t1.11\n"
        def metadata(url):
            calls.append(url)
            return Response(full[:5] if len(calls) == 1 else full, url, length=len(full))
        result = downloads._read_releases(metadata, lambda seconds: None)
        self.assertEqual(result, full.decode())
        self.assertEqual(len(calls), 2)

    def test_archive_path_traversal_is_rejected_without_retry(self):
        self.body = archive_bytes([
            ("genomad_db/version.txt", b"1.9\n"),
            ("genomad_db/../../outside", b"bad"),
        ])
        with self.assertRaisesRegex(RuntimeError, "Unsafe geNomad archive path"):
            self.acquire()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.root / "cache/1.9/complete").exists())

    def test_archive_link_escape_and_nested_link_entries_are_rejected(self):
        link = tarfile.TarInfo("genomad_db/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        path = self.root / "links.tar.gz"
        path.write_bytes(archive_bytes([("genomad_db/version.txt", b"1.9"), (link.name, link)]))
        with self.assertRaises(downloads.UnsafeArchiveError):
            downloads.validate_archive(path)
        link.linkname = "inside"
        path.write_bytes(archive_bytes([
            ("genomad_db/version.txt", b"1.9"), (link.name, link),
            ("genomad_db/link/file", b"nested write"),
        ]))
        with self.assertRaisesRegex(downloads.UnsafeArchiveError, "descends through"):
            downloads.validate_archive(path)

    def test_safe_internal_links_extract_and_existing_database_is_preserved(self):
        soft = tarfile.TarInfo("genomad_db/soft")
        soft.type, soft.linkname = tarfile.SYMTYPE, "version.txt"
        hard = tarfile.TarInfo("genomad_db/hard")
        hard.type, hard.linkname = tarfile.LNKTYPE, "genomad_db/version.txt"
        path = self.root / "links.tar.gz"
        path.write_bytes(archive_bytes([
            ("genomad_db/version.txt", b"1.9\n"), (soft.name, soft), (hard.name, hard),
        ]))
        database = downloads.extract_archive(path, self.root / "output")
        self.assertEqual((database / "soft").read_bytes(), b"1.9\n")
        self.assertEqual((database / "hard").read_bytes(), b"1.9\n")
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            downloads.extract_archive(path, self.root / "output")
        self.assertEqual((database / "version.txt").read_bytes(), b"1.9\n")

    def test_extraction_failure_keeps_verified_archive_for_next_attempt(self):
        archive, _ = self.acquire()
        with patch.object(downloads.shutil, "copyfileobj", side_effect=OSError("extraction failed")):
            with self.assertRaises(OSError):
                downloads.extract_archive(archive, self.root / "failed-attempt")
        def forbidden(url):
            raise AssertionError("Archive was downloaded again")
        same, record = self.acquire(open_url=forbidden)
        self.assertTrue(record["archive_reused"])
        database = downloads.extract_archive(same, self.root / "new-attempt")
        self.assertTrue((database / "database.bin").is_file())

    def test_preparation_resolves_compatibility_and_reuses_archive(self):
        with patch.object(downloads, "validate_archive", wraps=downloads.validate_archive) as validate:
            first = downloads.prepare_database(self.root / "attempt1", self.root / "cache", "1.11.0",
                                              open_url=self.open_url, sleep=lambda seconds: None)
        self.assertEqual(validate.call_count, 1)
        second = downloads.prepare_database(self.root / "attempt2", self.root / "cache", "1.11.0",
                                           open_url=self.open_url, sleep=lambda seconds: None)
        self.assertEqual(sum(url.endswith("releases.txt") for url in self.calls), 2)
        self.assertEqual(sum(url.endswith(".tar.gz") for url in self.calls), 1)
        self.assertFalse(first["archive_reused"])
        self.assertTrue(second["archive_reused"])
        self.assertEqual(second["database_version"], "1.9")
        self.assertTrue((self.root / "attempt2/genomad-download.json").is_file())

    def test_only_official_https_source_is_accepted(self):
        self.assertEqual(downloads._official_url(downloads.BASE_URL), downloads.BASE_URL)
        for url in ("http://portal.nersc.gov/genomad/__data__/x",
                    "https://example.com/genomad/__data__/x",
                    "https://portal.nersc.gov/elsewhere/x",
                    "https://user@portal.nersc.gov/genomad/__data__/x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                downloads._official_url(url)


if __name__ == "__main__":
    unittest.main()
