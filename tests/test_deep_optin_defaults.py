"""Default-off changes orchestration only; explicit opt-in remains available."""
from pathlib import Path
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from basis.config import ALL_MODULES, default_config
from safety.context import SafetyContext


class DeepOptInDefaultsTests(unittest.TestCase):
    def config(self, **changes):
        return {"isolates": [], "modules": {m: {"enabled": False} for m in ALL_MODULES},
                **changes}

    def test_packaged_and_library_defaults_disable_followup(self):
        self.assertIs(default_config()["deep"]["enabled"], False)
        for file in ("config.yaml", "config/deepsafe-followup.example.yaml"):
            self.assertIs(yaml.safe_load((ROOT / file).read_text())["deep"]["enabled"], False)

    def test_omitted_or_partial_deep_config_does_not_install_followup(self):
        for supplied in (self.config(), self.config(deep={"maximum_candidates": 2})):
            context = SafetyContext(supplied, ROOT)
            self.assertIs(context.deep_enabled, False)
            self.assertEqual(context.resource_specs(), {})
            self.assertEqual(context.needed_recipes(), [])
            context.samples = {"/tmp/synthetic": {}}
            self.assertEqual(context.report_targets(), ["/tmp/synthetic/safety/report.html"])

    def test_explicit_optin_retains_existing_setup_behavior(self):
        context = SafetyContext(self.config(deep={"enabled": True}), ROOT)
        self.assertTrue(context.deep_enabled)
        self.assertIn("deepsafe", context.needed_recipes())
        self.assertIn("deep_catalogue", context.resource_specs())


if __name__ == "__main__":
    unittest.main()
