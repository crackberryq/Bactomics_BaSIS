"""Prediction-schema and Snakemake resume regressions; no biological inference."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from unittest.mock import patch

import jsonschema

try:
    import h5py
    import numpy as np
except ImportError:
    h5py = np = None

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety import extend, pf2_classify

FIXTURES = ROOT / "tests/fixtures/pf2"


def fixture_schemas():
    """Load the exact small upstream schema fixture, retaining its provenance."""
    manifest = json.loads((FIXTURES / "provenance.json").read_text())
    for name, item in manifest["files"].items():
        actual = hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
        if actual != item["sha256"]:
            raise AssertionError(f"Pinned upstream fixture changed: {name}")
    namespace = {}
    exec(compile((FIXTURES / "config_schema.py").read_text(),
                 str(FIXTURES / "config_schema.py"), "exec"), namespace)
    return namespace


def file_snapshot(*paths):
    return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in paths}


@unittest.skipIf(h5py is None, "h5py/numpy required for pinned HDF5 contract fixtures")
class PredictionOnlyConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pf2 prediction fixture ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundled = self.root / "installed weights"
        self.prepared = self.root / "prepared weights"
        for folder in (self.bundled, self.prepared):
            folder.mkdir()
            for n in range(1, 5):
                (folder / f"weights_model{n}.pt").write_bytes(f"synthetic ensemble member {n}".encode())
        self.schemas = fixture_schemas()
        self.base = json.loads((FIXTURES / "config_base.json").read_text())
        self.configuration_calls = []
        calls, schemas, bundled, base = self.configuration_calls, self.schemas, self.bundled, self.base

        class ConfigurationPF2(dict):
            # Only the upstream configuration boundary is substituted; the
            # upstream schemas and default JSON are exact pinned fixtures.
            def __init__(self, mode, user_config=None):
                calls.append((mode, user_config))
                value = json.loads(json.dumps(base if user_config is None else user_config))
                if user_config is None:
                    value["Model Parameters"]["Network Weights"] = [
                        bundled / f"weights_model{n}.pt" for n in range(1, 5)]
                    schema = schemas["PREDICTION_SCHEMA"]
                else:
                    schema = schemas["USER_CONFIG_SCHEMA"]
                jsonschema.validate(value, schema)
                value["Misc Parameters"]["Actions"] = mode
                super().__init__(value)

        fake = types.ModuleType("pathogenfinder2.utils.configuration")
        fake.ConfigurationPF2 = ConfigurationPF2
        self.configuration_patch = patch.dict(sys.modules, {"pathogenfinder2.utils.configuration": fake})
        self.configuration_patch.start()
        self.addCleanup(self.configuration_patch.stop)
        self.config = {"weights_model": str(self.prepared), "prot_t5_identity": "fixture@pinned",
                       "attention": True, "expected_models": 4, "gpus": 0}
        self.proteins = self.root / "proteins.faa"
        self.proteins.write_text(">P1\nMACDE\n")
        self.sample = {"sample_id": "synthetic", "proteins": str(self.proteins)}
        self.embeddings = self.root / "ProteinEmbeddings.h5"
        with h5py.File(self.embeddings, "w") as data:
            data.create_dataset("Names", data=["P1"], dtype=h5py.string_dtype())
            data.create_dataset("Embeddings", data=np.zeros((1, 1024), dtype=np.float32))
            data.attrs["Amount Proteins"] = 1
            data.attrs["Amount Embeddings"] = 1
            data.attrs["Pooled"] = "mean"
        self.manifest = self.root / "embeddings_manifest.json"
        extend.write_json(self.manifest, extend.embedding_manifest(self.embeddings, self.proteins, self.config))
        self.output = self.root / "predictionPF2"

    def test_default_prediction_schema_and_actual_weights_are_recorded(self):
        with self.assertRaisesRegex(jsonschema.ValidationError, "Train Parameters"):
            jsonschema.validate(self.base, self.schemas["USER_CONFIG_SCHEMA"])
        jsonschema.validate(self.base, self.schemas["PREDICTION_SCHEMA"])
        value, audit = pf2_classify.prepare_classifier_configuration(self.config)
        self.assertEqual(self.configuration_calls, [("Prediction", None)])
        self.assertNotIn("Train Parameters", value)
        expected = json.loads(json.dumps(self.base))
        expected["Misc Parameters"]["Actions"] = "Prediction"
        expected["Model Parameters"]["Network Weights"] = [
            str(self.bundled / f"weights_model{n}.pt") for n in range(1, 5)]
        self.assertEqual(value, expected)
        self.assertEqual(audit["configuration_mode"], "upstream_prediction_defaults")
        self.assertTrue(audit["matches_pinned_bundled_ensemble"])
        for n in range(1, 5):
            source, actual = self.prepared / f"weights_model{n}.pt", self.bundled / f"weights_model{n}.pt"
            digest = hashlib.sha256(actual.read_bytes()).hexdigest()
            self.assertEqual(audit["sha256"][str(actual)], digest)
            self.assertEqual(audit["prepared_sha256"][str(source)], digest)

    def test_mismatched_weights_fail_before_native_prediction(self):
        (self.prepared / "weights_model4.pt").write_bytes(b"different model")
        before = file_snapshot(self.embeddings, self.manifest)
        with patch.object(pf2_classify, "version", return_value="0.7.1"), \
             patch.object(pf2_classify, "select_device", return_value={"selected": "cpu"}), \
             patch.object(pf2_classify.DirectRunner, "run") as native:
            with self.assertRaisesRegex(ValueError, "differ from the pinned"):
                pf2_classify.classify(self.sample, self.config, self.embeddings, self.manifest,
                                     self.output, self.root / "commands.log")
        native.assert_not_called()
        self.assertEqual(file_snapshot(self.embeddings, self.manifest), before)
        self.assertFalse(self.output.exists())

    def test_prediction_cli_uses_default_schema_and_preserves_saved_embeddings(self):
        before = file_snapshot(self.embeddings, self.manifest)
        invocations = []

        def native_run(runner, argv, **kwargs):
            invocations.append(argv)
            self.assertNotIn("--config", argv)
            self.assertNotIn("--weightsModel", argv)
            self.assertEqual(argv[:2], ["pathogenfinder2", "predict"])
            self.assertEqual(argv[argv.index("-f") + 1], "embeddings")
            self.assertEqual(argv[argv.index("-i") + 1], str(self.embeddings))
            self.assertEqual(argv[argv.index("--attProteins") + 1], "report")
            self.assertEqual(argv[argv.index("--embedProteome") + 1], "map")
            # Exercise the documented default loader/schema branch at the
            # native boundary, without importing torch or evaluating a model.
            sys.modules["pathogenfinder2.utils.configuration"].ConfigurationPF2(mode="Prediction")
            folder = Path(argv[argv.index("-o") + 1]) / "predictionPF2/results_PF2"
            folder.mkdir(parents=True)
            fields = ["File Name", "Prediction Mean", "Prediction STD", "Binary Prediction Mean",
                      "Phenotype", "Prediction_0", "Prediction_1", "Prediction_2", "Prediction_3"]
            values = ["PF2", "0.25", "0.11180339887498948", "0", "Human Non Pathogenic", "0.1", "0.2", "0.3", "0.4"]
            (folder / "predictions.tsv").write_text("\t".join(fields) + "\n" + "\t".join(values) + "\n")
            np.savez(folder / "attentions.npz", attentions=np.ones((4, 1)))

        with patch.object(pf2_classify, "version", return_value="0.7.1"), \
             patch.object(pf2_classify, "select_device", return_value={"selected": "cpu"}), \
             patch.object(pf2_classify.DirectRunner, "run", native_run):
            pf2_classify.classify(self.sample, {**self.config, "map": True}, self.embeddings,
                                 self.manifest, self.output, self.root / "commands.log")
        self.assertEqual(len(invocations), 1)
        self.assertEqual(file_snapshot(self.embeddings, self.manifest), before)
        saved = json.loads((self.output / "classifier-config.json").read_text())
        self.assertEqual(saved["Model Parameters"]["Network Weights"],
                         [str(self.bundled / f"weights_model{n}.pt") for n in range(1, 5)])
        audit = json.loads((self.output / "classifier-weights.json").read_text())
        self.assertEqual(set(audit["sha256"]), set(saved["Model Parameters"]["Network Weights"]))

    def test_native_failure_does_not_change_embeddings_or_manifest(self):
        before = file_snapshot(self.embeddings, self.manifest)
        with patch.object(pf2_classify, "version", return_value="0.7.1"), \
             patch.object(pf2_classify, "select_device", return_value={"selected": "cpu"}), \
             patch.object(pf2_classify.DirectRunner, "run", side_effect=RuntimeError("synthetic classifier failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic classifier failure"):
                pf2_classify.classify(self.sample, self.config, self.embeddings, self.manifest,
                                     self.output, self.root / "commands.log")
        self.assertEqual(file_snapshot(self.embeddings, self.manifest), before)
        self.assertFalse(self.output.exists())

    def test_wrong_native_version_or_changed_embeddings_fail_before_execution(self):
        with patch.object(pf2_classify, "version", return_value="99.0"), \
             patch.object(pf2_classify.DirectRunner, "run") as native:
            with self.assertRaises(ValueError):
                pf2_classify.classify(self.sample, self.config, self.embeddings, self.manifest,
                                     self.output, self.root / "commands.log")
        native.assert_not_called()
        with h5py.File(self.embeddings, "a") as data:
            data["Embeddings"][0, 0] = 1
        with patch.object(pf2_classify, "version", return_value="0.7.1"), \
             patch.object(pf2_classify.DirectRunner, "run") as native:
            with self.assertRaises(ValueError):
                pf2_classify.classify(self.sample, self.config, self.embeddings, self.manifest,
                                     self.output, self.root / "commands.log")
        native.assert_not_called()


class ClassifierRuleMigrationTests(unittest.TestCase):
    def test_classifier_fix_and_later_edit_preserve_completed_embedding_rule(self):
        """Use the production rules with a synthetic Python module boundary."""
        self.assertEqual(importlib.metadata.version("snakemake"), "9.6.2")
        with tempfile.TemporaryDirectory(prefix="pf2-rule-migration-") as temporary:
            root = Path(temporary)
            workflow_root = root / "workflow-copy"
            isolate = root / "isolate"
            metadata = isolate / "safety/metadata"
            metadata.mkdir(parents=True)
            for name in ("setup.ready.json", "sample.json", "config.json"):
                (metadata / name).write_text("{}\n")
            proteins = isolate / "proteins.faa"
            proteins.write_text(">synthetic\nMACDE\n")
            recipe = workflow_root / "envs/synthetic.yml"
            recipe.parent.mkdir(parents=True)
            recipe.write_text("dependencies: [python=3.11]\n")
            dependencies = ("workflow/lib/safety/extend.py", "workflow/lib/safety/runtime.py",
                            "workflow/vendor/basis/adapters/extend.py", "workflow/vendor/basis/inputs.py",
                            "workflow/vendor/basis/normalize.py", "workflow/vendor/basis/util.py",
                            "workflow/lib/safety/pf2_classify.py")
            for relative in dependencies:
                target = workflow_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, target)

            package = root / "synthetic-python/safety"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("")
            synthetic = textwrap.dedent(r'''
                import argparse, hashlib, json, os, sys
                from pathlib import Path
                def read_json(path):
                    return json.loads(Path(path).read_text())
                def run(stage):
                    parser = argparse.ArgumentParser()
                    for key in ("sample", "config", "output", "manifest", "threads", "embeddings", "log"):
                        parser.add_argument("--" + key)
                    args = parser.parse_args()
                    with open(os.environ["PF2_FIXTURE_EVENTS"], "a") as events:
                        events.write(stage + "\n")
                    target = Path(args.output)
                    if stage == "embed":
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(b"synthetic orchestration artifact; not biological embeddings")
                        Path(args.manifest).write_text(json.dumps({"sha256": hashlib.sha256(target.read_bytes()).hexdigest()}))
                    elif stage == "legacy-classify":
                        raise SystemExit("Synthetic original prediction-configuration failure")
                    else:
                        assert hashlib.sha256(Path(args.embeddings).read_bytes()).hexdigest() == read_json(args.manifest)["sha256"]
                        if target.exists():
                            raise SystemExit("Snakemake did not clean the output before rerunning its owner")
                        target.mkdir(parents=True)
                        (target / "synthetic-result.json").write_text('{"synthetic": true}\n')
                if __name__ == "__main__":
                    stage = sys.argv.pop(1)
                    run("embed" if stage == "embed" else "legacy-classify")
            ''')
            (package / "extend.py").write_text(synthetic)
            (package / "pf2_classify.py").write_text('from safety.extend import run\nrun("classify")\n')
            binaries = root / "bin"
            binaries.mkdir()
            (binaries / "python").symlink_to(sys.executable)
            events = root / "events.txt"
            environment = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"],
                               PF2_FIXTURE_EVENTS=str(events))
            # Retain production input, params, output, and shell definitions.
            # The only pre-fix differences were its module and source list.
            production = (ROOT / "workflow/rules/extend.smk").read_text()
            prefix, rest = production.split("rule pathogenfinder2_embeddings:", 1)
            embedding, rest = rest.split("rule pathogenfinder2_classification:", 1)
            classification = rest.split("rule pathogenfinder2_collect:", 1)[0]
            classifier_input = 'code=PF2_CODE + [str(ROOT / "workflow/lib/safety/pf2_classify.py")]'
            self.assertIn(classifier_input, classification)
            self.assertIn("python -m safety.pf2_classify", classification)
            legacy_classification = classification.replace(classifier_input, "code=PF2_CODE").replace(
                "python -m safety.pf2_classify", "python -m safety.extend classify")
            preamble = textwrap.dedent(f'''
                from pathlib import Path
                import sys
                ROOT = Path({str(workflow_root)!r})
                CODE_PATH = {str(package.parent)!r}
                sys.path.insert(0, CODE_PATH)
                ISOLATE = {str(isolate)!r}
                class Context:
                    samples = {{ISOLATE: {{"proteins": {str(proteins)!r}}}}}
                    def setup_target(self, isolate):
                        return [str(Path(isolate) / "safety/metadata/setup.ready.json")]
                    def resource_inputs(self, module):
                        return []
                    def module_config(self, isolate, module):
                        return {{"threads": 1, "memory_mb": 64, "gpus": 0}}
                    def env(self, module):
                        return {str(recipe)!r}
                CTX = Context()
                rule all:
                    input: ISOLATE + "/safety/native/pathogenfinder2/predictionPF2"
                    default_target: True
            ''')
            snakefile = root / "Snakefile"

            def write_rules(classifier):
                value = preamble + "\n" + prefix + "rule pathogenfinder2_embeddings:" + embedding + "rule pathogenfinder2_classification:" + classifier
                if os.environ.get("BASIS_TEST_NO_BENCHMARK") == "1":
                    # The same explicit container-only accommodation as the
                    # existing workflow tests; production/CI keep benchmarks.
                    value = re.sub(r"(?m)^    benchmark:\n        .*\n", "", value)
                snakefile.write_text(value)

            invocation = 0
            def snake(success=True):
                nonlocal invocation
                invocation += 1
                result = subprocess.run([sys.executable, "-m", "snakemake", "--snakefile", str(snakefile),
                                         "--directory", str(root), "--cores", "1", "--rerun-incomplete"],
                                        env=environment, text=True, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, timeout=120)
                if (result.returncode == 0) != success:
                    details = "\n".join(f"{p}:\n{p.read_text(errors='replace')}" for p in (isolate / "safety/logs").glob("*.log"))
                    self.fail(f"Migration invocation {invocation}:\n{result.stdout[-7000:]}\n{details}")
                return result

            write_rules(legacy_classification)
            snake(success=False)
            self.assertEqual(events.read_text().splitlines(), ["embed", "legacy-classify"])
            outputs = [isolate / "safety/native/pathogenfinder2" / name
                       for name in ("ProteinEmbeddings.h5", "embeddings_manifest.json")]
            before = file_snapshot(*outputs)
            write_rules(classification)
            snake()
            self.assertEqual(events.read_text().splitlines(), ["embed", "legacy-classify", "classify"])
            self.assertEqual(file_snapshot(*outputs), before)
            idle = snake()
            self.assertIn("Nothing to be done", idle.stdout)
            helper = workflow_root / "workflow/lib/safety/pf2_classify.py"
            with helper.open("a") as handle:
                handle.write("\n# Synthetic source-dependency change.\n")
            snake()
            self.assertEqual(events.read_text().splitlines(), ["embed", "legacy-classify", "classify", "classify"])
            self.assertEqual(file_snapshot(*outputs), before)


if __name__ == "__main__":
    unittest.main()
