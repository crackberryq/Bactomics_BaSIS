"""Configuration invalidation contracts using only synthetic worker processes."""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from basis.config import ALL_MODULES
from safety.context import SafetyContext
from safety.resources import SCHEMA
from safety.setup import module_config


class ScopedConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis-scoped-config-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.isolate = self.root / "isolate"
        annotation = self.isolate / "annotation"
        annotation.mkdir(parents=True)
        (annotation / "isolate.fna").write_text(">contig1\nATGAAATTTTAA\n")
        (annotation / "isolate.faa").write_text(">protein1\nMKF\n")
        (annotation / "isolate.gff").write_text(
            "##gff-version 3\ncontig1\tfixture\tCDS\t1\t12\t.\t+\t0\t"
            "ID=protein1;locus_tag=protein1;transl_table=11\n")
        self.config = {"isolates": [str(self.isolate)], "extend": True,
            "deep": {"enabled": True, "threads": 3, "memory_mb": 5555,
                     "aware": {"enabled": True, "preset": "auto-medium"}},
            "modules": {name: {"enabled": name in {"minced", "crisprcasfinder", "pathogenfinder2"}}
                        for name in ALL_MODULES},
            "resources": {"directory": str(self.root / "resources")}}

    def test_downstream_policy_is_absent_from_upstream_configuration(self):
        before = SafetyContext(self.config, ROOT)
        changed = copy.deepcopy(self.config)
        changed["deep"]["aware"]["preset"] = "auto-strict"
        after = SafetyContext(changed, ROOT)
        for module in ("minced", "crisprcasfinder", "pathogenfinder2"):
            self.assertEqual(before.module_document(str(self.isolate), module),
                             after.module_document(str(self.isolate), module))
        self.assertNotEqual(before.module_document(str(self.isolate), "deepsafe"),
                            after.module_document(str(self.isolate), "deepsafe"))
        self.assertEqual(after.deep_config(str(self.isolate))["threads"], 3)
        self.assertEqual(after.deep_config(str(self.isolate))["memory_mb"], 5555)

    def test_stable_document_preserves_mtime_and_only_merges_its_resource_fields(self):
        payload = self.root / "payload"
        payload.write_text("synthetic data")
        receipt = self.root / "receipt.json"
        receipt.write_text(json.dumps({"schema": SCHEMA, "status": "completed", "artifacts": {
            str(payload): {"bytes": payload.stat().st_size,
                           "sha256": hashlib.sha256(payload.read_bytes()).hexdigest()}},
            "result": {"module_updates": {"minced": {"database_version": "test"},
                                           "other": {"database": "unused"}},
                       "deep_updates": {"reference_fasta": "unused"}}}))
        context = SafetyContext(self.config, ROOT)
        document = context.module_document(str(self.isolate), "minced")
        output = self.root / "config.json"
        module_config(document, output, [receipt])
        before = (output.read_bytes(), output.stat().st_mtime_ns)
        module_config(document, output, [receipt])
        self.assertEqual((output.read_bytes(), output.stat().st_mtime_ns), before)
        saved = json.loads(output.read_text())
        self.assertEqual(set(saved["modules"]), {"minced"})
        self.assertNotIn("deep", saved)
        self.assertEqual(saved["modules"]["minced"]["database_version"], "test")
        self.assertEqual(saved["resource_receipts"][0]["path"], str(receipt))

    @unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake acceptance dependency unavailable")
    def test_real_scheduler_scopes_policy_changes_but_tracks_inputs_and_resources(self):
        """Use production setup rules and harmless JSON-writing workers.

        The environment/resource planners are replaced only to avoid installs;
        all config generation, input validation and scheduler dependencies are
        production behavior. No biological executable is invoked.
        """
        cfg = self.root / "config.yaml"
        cfg.write_text(yaml.safe_dump(self.config))
        artifact = self.root / "fixture-resource.txt"
        receipt = self.root / "fixture-resource.json"
        environment = self.root / "fixture-environment.json"
        environment.write_text(json.dumps({"status": "completed", "packages": [{"version": "1"}]}))

        def resource(version):
            artifact.write_text("synthetic resource version " + str(version))
            receipt.write_text(json.dumps({"schema": SCHEMA, "status": "completed", "artifacts": {
                str(artifact): {"bytes": artifact.stat().st_size,
                                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}},
                "result": {"module_updates": {"minced": {"database_version": str(version)}}}}))

        resource(1)
        events = self.root / "events.jsonl"
        worker = self.root / "worker.py"
        worker.write_text(textwrap.dedent("""
            import json, sys
            from pathlib import Path
            module, config, output, events = sys.argv[1:]
            data = json.loads(Path(config).read_text())
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_text(json.dumps(data, sort_keys=True))
            with Path(events).open('a') as log:
                log.write(module + '\\n')
        """))
        snakefile = self.root / "Snakefile"
        snakefile.write_text(textwrap.dedent(f"""
            from pathlib import Path
            import json, os, re, sys
            ROOT = Path({str(ROOT)!r})
            PYTHON = {sys.executable!r}
            CODE_PATH = os.pathsep.join(str(ROOT / path) for path in ('workflow/lib', 'workflow/vendor'))
            sys.path[:0] = CODE_PATH.split(os.pathsep)
            from safety.context import SafetyContext
            class FixtureContext(SafetyContext):
                def _resource_specs(self): return {{}}
                def needed_recipes(self): return []
                def environment_receipts(self): return [{str(environment)!r}]
                def module_environment_receipts(self, module):
                    return [{str(environment)!r}] if module == 'pathogenfinder2' else []
                def resource_inputs(self, module):
                    return [{str(receipt)!r}] if module == 'minced' else []
            CTX = FixtureContext(config, ROOT)
            ISOLATE = {str(self.isolate)!r}
            MODULES = ('minced', 'crisprcasfinder', 'pathogenfinder2', 'deepsafe')
            wildcard_constraints:
                isolate=re.escape(ISOLATE), module='|'.join(MODULES)
            rule all:
                input: [ISOLATE + '/fake/' + module + '.json' for module in MODULES]
                default_target: True
            include: str(ROOT / 'workflow/rules/setup.smk')
            rule fake_worker:
                input:
                    config=lambda w: CTX.module_config_path(w.isolate, w.module),
                    sample='{{isolate}}/safety/metadata/sample.json',
                    validation='{{isolate}}/safety/metadata/validation.json',
                    setup=lambda w: ancient(CTX.setup_target(w.isolate))
                output: '{{isolate}}/fake/{{module}}.json'
                params: worker={str(worker)!r}, events={str(events)!r}
                shell: '{{PYTHON:q}} {{params.worker:q}} {{wildcards.module:q}} {{input.config:q}} {{output:q}} {{params.events:q}}'
        """))

        def run():
            result = subprocess.run([sys.executable, "-m", "snakemake", "--snakefile", str(snakefile),
                "--directory", str(self.root), "--configfile", str(cfg), "--cores", "2",
                "--rerun-incomplete"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout[-9000:])
            return Counter(events.read_text().splitlines())

        expected = Counter({module: 1 for module in
                            ("minced", "crisprcasfinder", "pathogenfinder2", "deepsafe")})
        self.assertEqual(run(), expected)
        before = (self.isolate / "safety/metadata/sample.json").stat().st_mtime_ns
        self.config["deep"]["aware"]["preset"] = "auto-strict"
        cfg.write_text(yaml.safe_dump(self.config))
        expected["deepsafe"] += 1
        self.assertEqual(run(), expected)
        self.assertEqual((self.isolate / "safety/metadata/sample.json").stat().st_mtime_ns, before)
        self.config["modules"]["minced"]["threads"] = 2
        cfg.write_text(yaml.safe_dump(self.config))
        expected["minced"] += 1
        self.assertEqual(run(), expected)
        resource(2)
        expected["minced"] += 1
        self.assertEqual(run(), expected)
        environment.write_text(json.dumps({"status": "completed", "packages": [{"version": "2"}]}))
        expected["pathogenfinder2"] += 1
        self.assertEqual(run(), expected)
        self.assertEqual(run(), expected, "Unchanged environment should not repeatedly invalidate workers")
        (self.isolate / "annotation/isolate.fna").write_text(">contig1\nATGAAGTTTTAA\n")
        expected.update({module: 1 for module in expected})
        self.assertEqual(run(), expected)
        self.assertEqual(run(), expected, "Unchanged workflow should not rerun workers")


if __name__ == "__main__":
    unittest.main()
