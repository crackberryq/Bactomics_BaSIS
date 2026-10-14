import tempfile
import unittest
from pathlib import Path
import yaml
from basis.config import load_config
from basis.placement import DEFAULT_REGISTRY
from basis.placement_panels import load_registry, prepare_panel


class PlacementConfigTests(unittest.TestCase):
    def load(self, deep):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.yaml"
            p.write_text(yaml.safe_dump({"deep": deep}))
            return load_config(p)

    def test_legacy_default_and_resolved_paths(self):
        self.assertFalse(self.load({})["deep"]["family_placement"])
        cfg = self.load({"family_placement": True, "placement_registry": "panels.json", "placement_cache_dir": "cache"})
        self.assertTrue(Path(cfg["deep"]["placement_registry"]).is_absolute())
        self.assertTrue(Path(cfg["deep"]["placement_cache_dir"]).is_absolute())

    def test_invalid_settings_fail_before_execution(self):
        for settings in ({"family_placement": "true"}, {"placement_min_query_coverage": float("nan")},
                         {"placement_min_query_coverage": 70}, {"maximum_placement_candidates": True},
                         {"placement_min_columns": 0}, {"placement_model": "MFP"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.load(settings)

    def test_bundled_pilot_deduplicates_and_has_competing_anchors(self):
        registry = load_registry(DEFAULT_REGISTRY)
        candidate = {"gene": "cfr", "source_evidence": [{"tool": "amrfinder", "gene": "cfr", "evidence_level": "curated_model"}]}
        with tempfile.TemporaryDirectory() as tmp:
            panel = prepare_panel(candidate, registry, tmp, allow_download=False)
        self.assertTrue(panel["adequacy"]["eligible"])
        self.assertEqual(len(panel["members"]), 9)
        roles = {role: sum(m["role"] == role for m in panel["members"]) for role in ("amr", "alternative", "context")}
        self.assertEqual(roles, {"amr": 4, "alternative": 3, "context": 2})
        self.assertEqual(len({m["sequence_sha256"] for m in panel["members"]}), 9)


if __name__ == "__main__":
    unittest.main()
