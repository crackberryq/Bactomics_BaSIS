"""Production rule rendering and saved synthetic-result reuse through Snakemake."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import re
import sys
import tempfile
import textwrap
import unittest

from test_integron import ROOT, make_fixture
from safety import runtime


@unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake is not installed")
class IntegronWorkflowTests(unittest.TestCase):
    def test_production_worker_normalizes_partial_coverage_and_reuses_cache(self):
        with tempfile.TemporaryDirectory(prefix="integron workflow's ") as temporary:
            root = Path(temporary)
            isolate = root / "isolate"
            isolate.mkdir()
            sample, config = make_fixture(isolate)
            metadata = isolate / "safety/metadata"
            runtime.write_json(metadata / "sample.json", sample)
            runtime.write_json(metadata / "config.json", config)
            runtime.write_json(metadata / "validation.json", {"input_hashes": {},
                "protein_coordinates": {}, "contig_lengths": {"1": 60, "4117": 60}})
            runtime.write_json(metadata / "setup.ready.json", {})
            production = (ROOT / "workflow/rules/native.smk").read_text()
            helpers = production.split("rule rgi_load_database:", 1)[0]
            worker = "rule integronfinder:" + production.split("rule integronfinder:", 1)[1].split("rule isescan:", 1)[0]
            if os.environ.get("BASIS_TEST_NO_BENCHMARK") == "1":
                # Managed-host /proc mismatch only; CI retains production benchmarks.
                worker = re.sub(r"(?m)^    benchmark:\n        .*\n", "", worker)
            normalize = "rule normalize_native:" + production.split("rule normalize_native:", 1)[1]
            output = isolate / "safety/evidence/integronfinder.json"
            receipt = isolate / "safety/completion/integronfinder.json"
            snakefile = root / "Snakefile"
            snakefile.write_text(textwrap.dedent(f'''\
                from pathlib import Path
                import sys
                ROOT = Path({str(ROOT)!r})
                sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
                PYTHON = {sys.executable!r}
                CODE_PATH = str(ROOT / "workflow/lib") + ":" + str(ROOT / "workflow/vendor")
                class Context:
                    samples = {{{str(isolate)!r}: {sample!r}}}
                    def module_config_path(self, isolate, module):
                        return str(Path(isolate) / "safety/metadata/config.json")
                    def module_config(self, isolate, module):
                        return {config['modules']['integronfinder']!r}
                    def module_enabled(self, isolate, module):
                        return True
                    def setup_target(self, isolate):
                        return str(Path(isolate) / "safety/metadata/setup.ready.json")
                    def resource_inputs(self, module):
                        return []
                    def env(self, module):
                        return str(ROOT / "envs/context.yml")
                CTX = Context()
                rule all:
                    input: {str(output)!r}, {str(receipt)!r}
                    default_target: True
                ''') + helpers + worker + normalize)
            def run():
                command = [sys.executable, "-m", "snakemake", "--snakefile", str(snakefile),
                           "--cores", "1", "--rerun-incomplete"]
                result = subprocess.run(command, cwd=root, text=True, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, timeout=60)
                logs = "\n".join(p.read_text() for p in (isolate / "safety/logs").glob("*.log"))
                self.assertEqual(result.returncode, 0, result.stdout + logs)
                return result.stdout
            run()
            row = runtime.read_json(output)
            self.assertTrue(row["scope_incomplete"])
            self.assertEqual(row["execution_status"], "completed")
            self.assertEqual(row["interpretation"], "inconclusive")
            saved = receipt.read_bytes()
            receipt.unlink()
            run()
            self.assertEqual(receipt.read_bytes(), saved)
            self.assertIn('"reused": true', (isolate / "safety/logs/integronfinder.log").read_text())
            self.assertIn("Nothing to be done", run())


if __name__ == "__main__":
    unittest.main()
