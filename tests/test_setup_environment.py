"""Environment receipts describe installed bytes, not capture time."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety.setup import environment


class EnvironmentReceiptTests(unittest.TestCase):
    def test_unchanged_recapture_is_stable_but_package_change_is_recorded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            metadata = root / "conda-meta"
            metadata.mkdir()
            package = metadata / "tool.json"
            package.write_text(json.dumps({"name": "synthetic-tool", "version": "1", "build": "0"}))
            recipe = root / "test.yml"
            recipe.write_text("dependencies: [synthetic-tool]\n")
            recipe.with_suffix(".post-deploy.sh").write_text("exit 0\n")
            output = root / "receipt.json"
            with patch.dict(os.environ, {"CONDA_PREFIX": str(root)}):
                environment(recipe, output)
                before = (output.read_bytes(), output.stat().st_mtime_ns)
                environment(recipe, output)
                self.assertEqual((output.read_bytes(), output.stat().st_mtime_ns), before)
                package.write_text(json.dumps({"name": "synthetic-tool", "version": "2", "build": "0"}))
                environment(recipe, output)
            self.assertEqual(json.loads(output.read_text())["packages"][0]["version"], "2")
            self.assertNotEqual(output.read_bytes(), before[0])


if __name__ == "__main__":
    unittest.main()
