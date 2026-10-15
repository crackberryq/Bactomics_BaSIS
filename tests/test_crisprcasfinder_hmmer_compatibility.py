"""Static regression checks for the rc2 CRISPR environment solver conflict.

These checks do not replace a Linux conda solve or a native CRISPRCasFinder run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "envs" / "crisprcasfinder.yml"
BUNDLED = ROOT / "basis" / "resources" / "envs" / "crisprcasfinder.yml"


class CrisprCasFinderEnvironmentCompatibilityTests(unittest.TestCase):
    def test_source_and_bundled_environment_are_identical(self):
        self.assertEqual(SOURCE.read_bytes(), BUNDLED.read_bytes())

    def test_macsyfinder_2_0_uses_compatible_hmmer_pin(self):
        dependencies = {
            line.strip()[2:].split("#", 1)[0].strip()
            for line in BUNDLED.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("- ")
        }
        self.assertEqual(
            {item for item in dependencies if item.startswith("macsyfinder=")},
            {"macsyfinder=2.0"},
        )
        self.assertEqual(
            {item for item in dependencies if item.startswith("hmmer=")},
            {"hmmer=3.3.2"},
            "MacSyFinder 2.0 requires HMMER >=3.1b2,<=3.3.2; HMMER 3.4 conflicts.",
        )

    def test_manifest_matches_bundled_environment(self):
        manifest = json.loads(
            (ROOT / "basis" / "resources" / "release-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            manifest["sha256"]["resources/envs/crisprcasfinder.yml"],
            hashlib.sha256(BUNDLED.read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
