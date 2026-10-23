"""Filesystem placement contracts with synthetic directory names only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety.storage import native_result_store


class NativeResultStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis storage fixture ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.isolate = self.root / "samples/isolate"

    def store(self, value, isolate=None, module="crisprcasfinder"):
        return native_result_store(self.root, {"storage": {"native_results_directory": value}},
                                   self.isolate if isolate is None else isolate, module)

    def test_absent_override_preserves_existing_store_location(self):
        expected = self.isolate / "safety/results-cache/crisprcasfinder"
        self.assertEqual(native_result_store(self.root, {}, self.isolate, "crisprcasfinder"), expected)
        self.assertEqual(native_result_store(self.root, {"storage": {}}, self.isolate,
                                            "crisprcasfinder"), expected)

    def test_explicit_root_is_namespaced_without_creating_any_paths(self):
        selected = self.root / "retained results"
        key = hashlib.sha256(str(self.isolate.resolve()).encode()).hexdigest()
        self.assertEqual(self.store(str(selected)), selected / key / "crisprcasfinder")
        self.assertFalse(selected.exists())
        self.assertFalse(self.isolate.exists())

    def test_relative_and_home_paths_resolve_independently_of_current_directory(self):
        self.assertEqual(self.store("retained results"), self.store(str(self.root / "retained results")))
        self.assertEqual(self.store("~/basis retained results"),
                         self.store(str(Path.home() / "basis retained results")))
        self.assertEqual(self.store("retained", "samples/isolate"), self.store("retained"))

    def test_same_sample_names_do_not_collide_and_module_stores_are_separate(self):
        first = self.store("retained", self.root / "batch-a/isolate")
        second = self.store("retained", self.root / "batch-b/isolate")
        self.assertNotEqual(first, second)
        self.assertEqual(len(first.parent.name), 64)
        other = self.store("retained", self.root / "batch-a/isolate", module="example")
        self.assertEqual(first.parent, other.parent)
        self.assertNotEqual(first, other)

    def test_aliases_to_one_isolate_share_the_same_namespace(self):
        self.isolate.mkdir(parents=True)
        alias = self.root / "sample-alias"
        alias.symlink_to(self.isolate, target_is_directory=True)
        self.assertEqual(self.store("retained", alias), self.store("retained"))

    def test_storage_override_does_not_change_module_configuration_identity(self):
        from basis.config import ALL_MODULES
        from safety.context import SafetyContext

        self.isolate.mkdir(parents=True)
        sample = {"sample_id": "synthetic", "isolate_dir": str(self.isolate)}
        for key, suffix in (("assembly", "fna"), ("proteins", "faa"), ("gff", "gff")):
            path = self.isolate / ("synthetic." + suffix)
            path.write_text("synthetic configuration fixture\n")
            sample[key] = str(path)
        supplied = {"isolates": [sample], "deep": {"enabled": False},
            "modules": {name: {"enabled": False} for name in ALL_MODULES},
            "resources": {"directory": str(self.root / "resources")}}
        old = SafetyContext(supplied, ROOT)
        new = SafetyContext({**supplied, "storage": {"native_results_directory": "retained"}}, ROOT)
        self.assertEqual(old.config, new.config)
        self.assertEqual(old.samples, new.samples)
        self.assertEqual(old.module_document(str(self.isolate), "crisprcasfinder"),
                         new.module_document(str(self.isolate), "crisprcasfinder"))

    @unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake is not installed")
    def test_production_rule_publishes_links_to_explicit_store_and_then_is_idle(self):
        from tests.test_ccf_assembly_workflow import WholeAssemblySchedulerTests

        fixture = WholeAssemblySchedulerTests(
            "test_one_job_per_isolate_failure_resume_noop_and_missing_receipt")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.allow_all()
        selected = fixture.root / "retained results"
        arguments = ("--config", "storage=" + json.dumps({"native_results_directory": str(selected)}))
        fixture.run_workflow(*arguments)
        for name in ("isolateA", "isolateB"):
            receipt = json.loads(fixture.receipt(name).read_text())
            payload = Path(receipt["payload_root"])
            self.assertTrue(payload.is_relative_to(selected))
            self.assertEqual(fixture.path(name, "native/crisprcasfinder").resolve(), payload)
            self.assertFalse(fixture.path(name, "results-cache/crisprcasfinder").exists())
        self.assertEqual(len(fixture.calls()), 2)
        self.assertIn("Nothing to be done", fixture.run_workflow(*arguments))
        self.assertEqual(len(fixture.calls()), 2)

    @unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake is not installed")
    def test_storage_override_keeps_completed_store_and_does_not_rerun_native_work(self):
        from tests.test_ccf_assembly_workflow import WholeAssemblySchedulerTests

        fixture = WholeAssemblySchedulerTests(
            "test_one_job_per_isolate_failure_resume_noop_and_missing_receipt")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.allow_all()
        fixture.run_workflow()
        before = {name: fixture.receipt(name).read_bytes() for name in ("isolateA", "isolateB")}
        selected = fixture.root / "new retained results"
        output = fixture.run_workflow("--config", "storage=" + json.dumps({
            "native_results_directory": str(selected)}))
        self.assertIn("Nothing to be done", output)
        self.assertEqual(len(fixture.calls()), 2)
        self.assertFalse(selected.exists())
        self.assertEqual({name: fixture.receipt(name).read_bytes() for name in before}, before)

    def test_malformed_storage_is_rejected(self):
        for storage in (None, False, "retained", [], {"native_result_directory": "typo"}):
            with self.subTest(storage=storage), self.assertRaisesRegex(ValueError, "storage"):
                native_result_store(self.root, {"storage": storage}, self.isolate, "crisprcasfinder")
        for value in (None, False, 1, [], {}, "", "   ", "bad\x00path", Path("retained")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "path string"):
                self.store(value)
        for module in ("../outside", "/absolute", "", None, "a/b"):
            with self.subTest(module=module), self.assertRaisesRegex(ValueError, "module"):
                self.store("retained", module=module)


if __name__ == "__main__":
    unittest.main()
