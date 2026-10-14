"""Integrity and compatibility of the opt-in comparator expansion."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.placement import DEFAULT_REGISTRY
from basis.placement_panels import load_registry, prepare_panel


class ExpandedPlacementResourcesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.resources = self.root / "resources/placement"
        self.registry = self.resources / "registry-v2.json"

    def test_checksums_and_packaged_mirror_match(self):
        checks = json.loads((self.resources / "checksums-v2.json").read_text())
        for name, expected in checks["files"].items():
            content = (self.resources / name).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), expected)
            self.assertEqual(content, (self.root / "basis/resources/placement" / name).read_bytes())

    def test_original_panel_and_default_preserved(self):
        self.assertEqual(DEFAULT_REGISTRY.name, "registry-v1.json")
        old = load_registry(self.resources / "registry-v1.json")
        new = load_registry(self.registry)
        original = old["panels"][0]["references"]
        expanded = new["panels"][0]["references"]
        self.assertEqual(expanded[:len(original)], original)
        self.assertEqual({r["accession"] for r in expanded[len(original):]},
                         {"AKV84429.1", "CCL89685.1", "AEA94342.1"})
        self.assertEqual(next(r for r in expanded if r["accession"] == "O34617")["role"], "context")

    def test_offline_preparation_deduplicates_without_promoting_context(self):
        registry = load_registry(self.registry)
        with tempfile.TemporaryDirectory() as temp, patch(
                "basis.placement_panels.urlopen", side_effect=AssertionError("Downloads forbidden")):
            panel = prepare_panel({}, registry, temp, allow_download=False,
                                  panel_id="cfr_rlmn_source_audited_v2")
        self.assertTrue(panel["adequacy"]["eligible"])
        self.assertEqual(len(panel["members"]), 12)
        qualified = [m for m in panel["members"] if m.get("group") is not None]
        self.assertEqual(len(qualified), 10)
        self.assertEqual(sum(m["group"] == "Cfr_characterized" for m in qualified), 6)
        self.assertEqual(sum(m["group"] == "RlmN_like_rRNA_methylation" for m in qualified), 4)


if __name__ == "__main__":
    unittest.main()
