"""Check the bytes actually shipped in a clone, without downloading resources."""
import hashlib
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "workflow/vendor/basis/resources/comparators/reviewed-v1"


class PackagedResourcesTests(unittest.TestCase):
    def test_bundled_comparator_bytes_match_recorded_checksums(self):
        manifest = json.loads((PANEL / "checksums.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest, "Bundled comparator inventory must not be empty")
        self.assertTrue({"references.faa", "references.tsv", "reviews.json", "coverage.json"} <= set(manifest))
        for relative, expected in manifest.items():
            with self.subTest(resource=relative):
                path = (PANEL / relative).resolve(strict=True)
                self.assertTrue(path.is_relative_to(PANEL.resolve()))
                self.assertTrue(path.is_file())
                self.assertIsNotNone(re.fullmatch(r"[0-9a-f]{64}", expected))
                # read_bytes is intentional: text newline normalization would
                # hide the exact publication defect this regression guards.
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(actual, expected,
                    f"Bundled asset changed during packaging or checkout: {relative}")


if __name__ == "__main__":
    unittest.main()
