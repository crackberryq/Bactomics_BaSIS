"""Resource snapshot integrity and independent upstream license preservation."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from basis import bootstrap_typing_defense as resources
from basis.util import sha256_file


class TypingDefenseResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def archive(self, name, files):
        output = self.root / (name + ".tar.gz")
        with tarfile.open(output, "w:gz") as handle:
            for path, text in files.items():
                data = text.encode()
                member = tarfile.TarInfo("upstream/" + path)
                member.size = len(data)
                handle.addfile(member, io.BytesIO(data))
        return output

    def test_archive_rejects_traversal_and_symlinks(self):
        bad = self.archive("escape", {"../escape.txt": "no"})
        with self.assertRaisesRegex(ValueError, "escaping"):
            resources._extract(bad, self.root / "extract")
        self.assertFalse((self.root / "escape.txt").exists())
        link = self.root / "link.tar.gz"
        with tarfile.open(link, "w:gz") as handle:
            member = tarfile.TarInfo("root/link"); member.type = tarfile.SYMTYPE; member.linkname = "/etc/passwd"
            handle.addfile(member)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            resources._extract(link, self.root / "link-extract")

    def test_mlst_snapshots_bundled_database_without_online_update(self):
        source = self.root / "native/db"
        (source / "pubmlst/test").mkdir(parents=True)
        (source / "pubmlst/test/test.txt").write_text("ST\tx\n1\t1\n")
        (source / "blast").mkdir()
        (source / "blast/mlst.fa").write_text(">test.x_1\nACGT\n")
        commands = []
        class Runner:
            def run(self, argv, cwd):
                commands.append(argv)
                return subprocess.CompletedProcess(argv, 0, "mlst 2.35.0\n" if argv[0] == "mlst" else str(source) + "\n")
        dest = self.root / "prepared"
        result = resources.prepare_resource({"backend": "mlst"}, dest, Runner())
        self.assertEqual((dest / "db/blast/mlst.fa").read_bytes(), (source / "blast/mlst.fa").read_bytes())
        self.assertEqual(result["module_updates"]["mlst"]["database"], str(dest / "db"))
        self.assertFalse(any("curl" in cmd for cmd in commands))
        self.assertTrue(result["provenance"]["sha256"])

    def test_ccf_source_and_models_preserve_licenses_and_checksums(self):
        source = self.archive("ccf", {"CRISPRCasFinder.pl": "print 'fixture';", "sel392v2.so": "fixture",
                                       "COPYING": "GPL-3.0-or-later"})
        models = self.archive("models", {"LICENSE": "CC BY-NC-SA 4.0", "metadata.yml": "vers: 3.1.0\n"})
        mapping = {"fixture:source": source, "fixture:models": models}
        pinned = {"crisprcasfinder": {"url": "fixture:source", "sha256": sha256_file(source), "repository": "fixture-source"},
                  "casfinder_models": {"url": "fixture:models", "sha256": sha256_file(models), "repository": "fixture-models"}}
        commands = []
        class Runner:
            def run(self, argv, cwd):
                commands.append(argv)
                if argv[0] == "curl": shutil.copy2(mapping[argv[-1]], argv[argv.index("--output") + 1])
                return subprocess.CompletedProcess(argv, 0, ">29 0 D 29 65\nGTTCACTGCCGTACAGGCAGCTTAGAAA\n" if argv[0] == "vmatch" else "")
        dest = self.root / "prepared"
        with patch.object(resources, "SOURCES", pinned), patch.object(resources.platform, "system", return_value="Linux"), \
             patch.object(resources.platform, "machine", return_value="x86_64"):
            result = resources.prepare_resource({"backend": "crisprcasfinder"}, dest, Runner())
        self.assertEqual((dest / "source/models/CASFinder/LICENSE").read_text(), "CC BY-NC-SA 4.0")
        self.assertEqual(result["module_updates"]["crisprcasfinder"]["executable"], "perl")
        self.assertEqual(result["module_updates"]["crisprcasfinder"]["version_args"], [])
        saved = json.loads((dest / "provenance.json").read_text())
        self.assertIn("models/CASFinder/LICENSE", saved["sha256"])
        self.assertTrue(any(cmd[:2] == ["perl", "-c"] for cmd in commands))
        self.assertEqual(saved["runtime_checks"]["vmatch_plugin"], "synthetic_repeat_detected")

    def test_bad_source_checksum_cannot_publish(self):
        archive = self.archive("wrong", {"file": "fixture"})
        class Runner:
            def run(self, argv, cwd):
                shutil.copy2(archive, argv[argv.index("--output") + 1])
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            resources._download({"url": "fixture", "sha256": "0" * 64, "repository": "fixture"},
                                self.root / "download.tar.gz", Runner())


if __name__ == "__main__": unittest.main()
