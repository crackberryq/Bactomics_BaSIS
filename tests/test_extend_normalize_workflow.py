"""Exercise production CLI argument rendering with saved synthetic JSON only."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake is not installed")
class ExtendNormalizeWorkflowTests(unittest.TestCase):
    def run_normalization(self, enabled):
        with tempfile.TemporaryDirectory(prefix="extend normalize's fixture ") as temporary:
            root = Path(temporary)
            isolate = root / "isolate"
            metadata = isolate / "safety/metadata"
            metadata.mkdir(parents=True)
            (metadata / "sample.json").write_text(json.dumps({"sample_id": "synthetic"}))
            (metadata / "config.json").write_text(json.dumps({
                "modules": {"pathogenfinder2": {"enabled": enabled}}}))
            (metadata / "validation.json").write_text(json.dumps({
                "protein_coordinates": {}, "contig_lengths": {}}))
            native = isolate / "safety/native/pathogenfinder2/parsed.json"
            if enabled:
                native.parent.mkdir(parents=True)
                native.write_text(json.dumps({"findings": [], "interpretation": "inconclusive",
                    "metrics": {"prediction_mean": 0.5, "prediction_std": 0.0}, "artifacts": []}))
                before = (native.read_bytes(), native.stat().st_mtime_ns)
            output = isolate / "safety/evidence/pathogenfinder2.json"
            production = (ROOT / "workflow/rules/extend.smk").read_text()
            # Include the real shared helpers and normalization rule. Upstream
            # native rules are deliberately unavailable: only saved JSON is used.
            helpers = production.split("rule pathogenfinder2_embeddings:", 1)[0]
            normalize = "rule normalize_extend:" + production.split("rule normalize_extend:", 1)[1]
            snakefile = root / "Snakefile"
            snakefile.write_text(textwrap.dedent(f'''\
                from pathlib import Path
                ROOT = Path({str(ROOT)!r})
                PYTHON = {sys.executable!r}
                CODE_PATH = str(ROOT / "workflow/lib") + ":" + str(ROOT / "workflow/vendor")
                class Context:
                    def module_config_path(self, isolate, module):
                        return str(Path(isolate) / "safety/metadata/config.json")
                    def module_enabled(self, isolate, module):
                        return {enabled!r}
                CTX = Context()
                rule all:
                    input: {str(output)!r}
                    default_target: True
                ''') + helpers + normalize)

            def run():
                result = subprocess.run([sys.executable, "-m", "snakemake", "--snakefile",
                    str(snakefile), "--cores", "1", "--rerun-incomplete"], cwd=root,
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
                logs = "\n".join(p.read_text() for p in (isolate / "safety/logs").glob("*.log"))
                self.assertEqual(result.returncode, 0, result.stdout + logs)
                return result.stdout

            run()
            record = json.loads(output.read_text())
            self.assertEqual(record["execution_status"], "completed" if enabled else "not_assessed")
            self.assertEqual(record["sample_id"], "synthetic")
            self.assertFalse((isolate / "safety/completion/crisprcasfinder.json").exists())
            if enabled:
                self.assertEqual((native.read_bytes(), native.stat().st_mtime_ns), before)
            else:
                self.assertFalse(native.exists())
            self.assertIn("Nothing to be done", run())

    def test_saved_result_normalizes_without_receipt_and_is_reused(self):
        self.run_normalization(enabled=True)

    def test_disabled_module_normalizes_without_receipt_or_native_result(self):
        self.run_normalization(enabled=False)


if __name__ == "__main__":
    unittest.main()
