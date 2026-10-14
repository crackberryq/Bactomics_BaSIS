"""First-run snapshot planning and current-run attestation, without downloads."""
import copy
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from basis.bootstrap import (MANIFEST, apply_resources, bootstrap_dependencies,
    plan_auto_resources, receipt_path, run_resource, verify_snapshot)
from basis.config import default_config
from basis.util import atomic_json, hash_object, read_json, sha256_file


class FakeRunner:
    threads = 2
    def __init__(self):
        self.commands = []


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.recipe = self.root / "recipe.yml"
        self.recipe.write_text("dependencies: [python=3.11]\n")
        self.recipe_patch = patch("basis.workflow_environments.environment_for_recipe", return_value=str(self.recipe))
        self.recipe_patch.start()

    def tearDown(self):
        self.recipe_patch.stop()
        self.temp.cleanup()

    def config(self, *enabled):
        cfg = default_config()
        cfg["resources"] = {"policy": "auto", "directory": str(self.root / "cache")}
        cfg["outdir"] = str(self.root / "results")
        for name, section in cfg["modules"].items():
            section["enabled"] = name in enabled
        return cfg

    def plan(self, cfg, deep=False, extend=False):
        config, specs = plan_auto_resources(cfg, deep, extend)
        return {"plan_hash": "plan-test", "source_hash": "source-test", "config": config,
                "options": {"deep": deep, "extend": extend}, "bootstrap_resources": specs,
                "outdir": cfg["outdir"], "enabled_modules": [m for m, c in cfg["modules"].items() if c["enabled"]]}

    def prepare(self, spec, destination, runner):
        path = destination / "database.bin"
        path.write_bytes(b"verified synthetic resource")
        return {"module_updates": {"vfdb": {"database": str(path), "database_version": "fixture-1"}},
                "deep_updates": {}, "provenance": {"provider": spec["provider"], "snapshot_path": str(destination)}}

    def test_manual_synthetic_import_and_disabled_resources_remain_untouched(self):
        cfg = self.config("vfdb")
        for change in (lambda c: c.pop("resources"),
                       lambda c: c.update(validation_mode="synthetic_fixture"),
                       lambda c: c["modules"]["vfdb"].update(mode="import"),
                       lambda c: c["modules"]["vfdb"].update(enabled=False)):
            supplied = copy.deepcopy(cfg)
            change(supplied)
            before = copy.deepcopy(supplied)
            resolved, specs = plan_auto_resources(supplied)
            self.assertEqual(specs, [])
            self.assertEqual(supplied, before)
            self.assertFalse((self.root / "cache").exists())

    def test_requested_resources_and_deep_dependencies_are_deterministic(self):
        cfg = self.config("vfdb", "pathogenfinder2")
        first = self.plan(cfg, deep=True, extend=False)
        second = self.plan(cfg, deep=True, extend=False)
        self.assertEqual(first["bootstrap_resources"], second["bootstrap_resources"])
        resources = {s["id"]: s for s in first["bootstrap_resources"]}
        self.assertNotIn("prott5", resources)
        self.assertEqual(set(resources["deep_catalogue"]["dependencies"]), {"amrfinder", "card", "vfdb", "antismash"})
        self.assertIn("deepsafe", resources["vfdb"]["targets"])
        self.assertIn("vfdb", resources["vfdb"]["targets"])
        self.assertFalse((self.root / "cache").exists())
        extend = self.plan(cfg, extend=True)
        self.assertIn("prott5", {s["id"] for s in extend["bootstrap_resources"]})

    def test_external_resource_never_replaced_or_scheduled(self):
        cfg = self.config("vfdb")
        cfg["modules"]["vfdb"]["database"] = "/external/vfdb.dmnd"
        _, specs = plan_auto_resources(cfg)
        self.assertEqual(specs, [])
        self.assertEqual(cfg["modules"]["vfdb"]["database"], "/external/vfdb.dmnd")

    def test_external_reference_pair_gets_pfam_and_bundles_without_replacement(self):
        cfg = self.config()
        cfg["deep"].update(reference_fasta="/external/refs.faa", reference_metadata="/external/refs.tsv")
        plan = self.plan(cfg, deep=True)
        specs = {s["id"]: s for s in plan["bootstrap_resources"]}
        self.assertEqual(set(specs), {"antismash", "prosite", "deep_catalogue"})
        self.assertEqual(specs["deep_catalogue"]["dependencies"], ["antismash"])
        self.assertTrue(specs["deep_catalogue"]["options"]["preserve_references"])
        self.assertEqual(plan["config"]["deep"]["reference_fasta"], "/external/refs.faa")

    def test_preset_and_packaged_policy_change_resource_identity(self):
        cfg = self.config("vfdb")
        before = self.plan(cfg)["bootstrap_resources"][0]["cache_key"]
        cfg["resources"]["preset"] = "full"
        self.assertNotEqual(before, self.plan(cfg)["bootstrap_resources"][0]["cache_key"])
        with patch("basis.bootstrap._packaged_inputs", return_value={"policy": "first"}):
            first = self.plan(cfg, deep=True)["bootstrap_resources"]
        with patch("basis.bootstrap._packaged_inputs", return_value={"policy": "second"}):
            second = self.plan(cfg, deep=True)["bootstrap_resources"]
        self.assertNotEqual(first[-1]["cache_key"], second[-1]["cache_key"])

    def test_publish_reuse_and_overlay_preserve_immutable_plan(self):
        plan = self.plan(self.config("vfdb"))
        original = copy.deepcopy(plan)
        run = self.root / "run-a"
        with patch("basis.bootstrap._runner", return_value=(FakeRunner(), {"backend": "test"})), \
             patch("basis.bootstrap._prepare", side_effect=self.prepare) as prepare:
            receipt = run_resource(plan, run, "vfdb")
            self.assertEqual(receipt["status"], "ready", receipt)
            self.assertFalse(receipt["reused"])
            again = run_resource(plan, self.root / "run-b", "vfdb")
            self.assertTrue(again["reused"])
            self.assertEqual(prepare.call_count, 1)
        configured = apply_resources(plan, run, "vfdb")
        resource = Path(configured["config"]["modules"]["vfdb"]["database"])
        self.assertTrue(resource.is_file())
        self.assertEqual(resource.parent, Path(plan["bootstrap_resources"][0]["snapshot"]))
        manifest = read_json(resource.parent / MANIFEST)
        self.assertEqual(manifest["result"]["provenance"]["snapshot_path"], str(resource.parent))
        self.assertEqual(configured["config"]["modules"]["vfdb"]["min_identity"], 80.)
        self.assertEqual(plan, original)

    def test_changed_snapshot_is_failure_and_never_automatically_redownloaded(self):
        plan = self.plan(self.config("vfdb"))
        with patch("basis.bootstrap._runner", return_value=(FakeRunner(), {})), \
             patch("basis.bootstrap._prepare", side_effect=self.prepare) as prepare:
            first = run_resource(plan, self.root / "one", "vfdb")
            (Path(first["snapshot"]) / "database.bin").write_bytes(b"corrupted")
            second = run_resource(plan, self.root / "two", "vfdb")
            self.assertEqual(second["status"], "failed")
            self.assertIn("checksum", second["error"])
            self.assertEqual(prepare.call_count, 1)
        with self.assertRaisesRegex(ValueError, "preparation failed"):
            apply_resources(plan, self.root / "two", "vfdb")

    def test_provider_failure_preserves_staging_and_blocks_consumer(self):
        plan = self.plan(self.config("vfdb"))
        def fail(spec, destination, runner):
            (destination / "partial.bin").write_bytes(b"partial")
            raise RuntimeError("download interrupted")
        with patch("basis.bootstrap._runner", return_value=(FakeRunner(), {})), \
             patch("basis.bootstrap._prepare", side_effect=fail):
            receipt = run_resource(plan, self.root / "run", "vfdb")
        self.assertEqual(receipt["status"], "failed")
        self.assertTrue((Path(receipt["staging_directory"]) / "partial.bin").is_file())
        self.assertFalse(Path(plan["bootstrap_resources"][0]["snapshot"]).exists())
        with self.assertRaisesRegex(ValueError, "download interrupted"):
            apply_resources(plan, self.root / "run", "vfdb")

    def test_shared_cache_lock_prevents_duplicate_parallel_publication(self):
        plan = self.plan(self.config("vfdb"))
        def delayed(*args):
            time.sleep(.03)
            return self.prepare(*args)
        with patch("basis.bootstrap._runner", side_effect=lambda *a: (FakeRunner(), {})), \
             patch("basis.bootstrap._prepare", side_effect=delayed) as prepare, \
             ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(run_resource, plan, self.root / name, "vfdb") for name in ("run-a", "run-b")]
            results = [job.result() for job in jobs]
        self.assertTrue(all(r["status"] == "ready" for r in results), results)
        self.assertEqual(sum(r["reused"] for r in results), 1)
        self.assertEqual(prepare.call_count, 1)

    def test_current_run_receipt_and_manifest_changes_are_rejected(self):
        plan = self.plan(self.config("vfdb"))
        run = self.root / "run"
        with patch("basis.bootstrap._runner", return_value=(FakeRunner(), {})), \
             patch("basis.bootstrap._prepare", side_effect=self.prepare):
            receipt = run_resource(plan, run, "vfdb")
        manifest = Path(receipt["manifest"])
        manifest.write_text(manifest.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            apply_resources(plan, run, "vfdb")

    def test_external_source_and_model_inventory_remain_frozen(self):
        plan = self.plan(self.config("vfdb"))
        external = self.root / "external"
        external.mkdir()
        source = external / "model.bin"
        source.write_bytes(b"source")
        def prepare(spec, destination, runner):
            value = self.prepare(spec, destination, runner)
            value["provenance"]["source_files"] = [{"path": str(source), "sha256": sha256_file(source)}]
            value["provenance"]["external_resources"] = {"model": {
                "path": str(external), "sha256": {source.name: sha256_file(source)}}}
            return value
        with patch("basis.bootstrap._runner", return_value=(FakeRunner(), {})), \
             patch("basis.bootstrap._prepare", side_effect=prepare) as provider:
            ready = run_resource(plan, self.root / "one", "vfdb")
            self.assertEqual(ready["status"], "ready", ready)
            (external / "extra.bin").write_bytes(b"unexpected")
            failed = run_resource(plan, self.root / "two", "vfdb")
            self.assertEqual(failed["status"], "failed")
            self.assertIn("inventory", failed["error"])
            (external / "extra.bin").unlink()
            source.write_bytes(b"different")
            failed = run_resource(plan, self.root / "three", "vfdb")
            self.assertEqual(failed["status"], "failed")
            self.assertIn("changed", failed["error"])
            self.assertEqual(provider.call_count, 1)

    def test_provider_declared_source_hash_mismatch_is_never_published(self):
        plan = self.plan(self.config("vfdb"))
        source = self.root / "source"
        source.write_bytes(b"changed after provider copied it")
        def prepare(spec, destination, runner):
            value = self.prepare(spec, destination, runner)
            value["provenance"]["details"] = {"source_sha256": {str(source): "0" * 64}}
            return value
        with patch("basis.bootstrap._runner", return_value=(FakeRunner(), {})), \
             patch("basis.bootstrap._prepare", side_effect=prepare):
            failed = run_resource(plan, self.root / "run", "vfdb")
        self.assertEqual(failed["status"], "failed")
        self.assertIn("changed before publication", failed["error"])
        self.assertFalse(Path(plan["bootstrap_resources"][0]["snapshot"]).exists())

    def test_internal_absolute_symlinks_survive_atomic_promotion_and_are_verified(self):
        plan = self.plan(self.config("vfdb"))
        def prepare(spec, destination, runner):
            value = self.prepare(spec, destination, runner)
            (destination / "latest").symlink_to(destination / "database.bin")
            return value
        with patch("basis.bootstrap._runner", return_value=(FakeRunner(), {})), \
             patch("basis.bootstrap._prepare", side_effect=prepare):
            ready = run_resource(plan, self.root / "one", "vfdb")
        self.assertEqual(ready["status"], "ready", ready)
        snapshot = Path(ready["snapshot"])
        self.assertEqual((snapshot / "latest").readlink(), Path("database.bin"))
        self.assertEqual((snapshot / "latest").read_bytes(), b"verified synthetic resource")
        verify_snapshot(plan["bootstrap_resources"][0])
        (snapshot / "latest").unlink()
        (snapshot / "latest").symlink_to("missing.bin")
        with self.assertRaises((ValueError, FileNotFoundError)):
            verify_snapshot(plan["bootstrap_resources"][0])

    def test_dependency_failure_never_calls_dependent_provider(self):
        cfg = self.config()
        cfg["deep"].update(reference_fasta="/external/refs.faa", reference_metadata="/external/refs.tsv")
        plan = self.plan(cfg, deep=True)
        with patch("basis.bootstrap._runner", return_value=(FakeRunner(), {})), \
             patch("basis.bootstrap._prepare", side_effect=RuntimeError("download interrupted")) as provider:
            first = run_resource(plan, self.root / "run", "antismash")
            second = run_resource(plan, self.root / "run", "deep_catalogue")
        self.assertEqual(first["status"], "failed")
        self.assertEqual(second["status"], "failed")
        self.assertIn("antismash", second["error"])
        self.assertEqual(provider.call_count, 1)

    def test_dependencies_respect_sample_scope_and_import_mode(self):
        plan = self.plan(self.config("vfdb"), deep=True)
        with patch.dict("os.environ", {"BASIS_SCHEDULER_RUN_ID": "run-test"}):
            dependencies = bootstrap_dependencies(plan, "sample", "vfdb")
            self.assertEqual(len(dependencies), 1)
            self.assertTrue(dependencies[0].endswith("/run-test/bootstrap/vfdb.json"))
            self.assertGreater(len(bootstrap_dependencies(plan, "sample", "deepsafe")), 1)
            plan["module_decisions"] = {"sample": {"vfdb": {"action": "withheld"}}}
            self.assertEqual(bootstrap_dependencies(plan, "sample", "vfdb"), [])

    @unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake is unavailable")
    def test_real_snakemake_resource_job_publishes_and_reuses_attested_snapshot(self):
        from basis.workflow_runtime import _write, read_document
        # Native command boundary is a deterministic local fixture; orchestration,
        # provider validation, atomic publication and separate-worker receipts are real.
        wrapper = self.root / "fixture native.py"
        wrapper.write_text('''import sys
from pathlib import Path
if sys.argv[1] == "amrfinder_update":
    root = Path(sys.argv[sys.argv.index("--database") + 1])
    version = root / "fixture-release"
    version.mkdir(parents=True)
    for name in ["AMRProt.fa", "AMRProt.fa.phr", "AMR.LIB"] + ["AMR.LIB.h3" + x for x in "fimp"]:
        (version / name).write_text(">fixture\\nMACDEFG\\n")
    (version / "version.txt").write_text("fixture-1\\n")
    (root / "latest").symlink_to(version, target_is_directory=True)
elif sys.argv[1] == "amrfinder":
    print("AMRFinder fixture-1")
else:
    raise SystemExit("Unexpected native command")
''')
        cfg = self.config("amrfinder")
        cfg["modules"]["amrfinder"]["command_prefix"] = [sys.executable, str(wrapper)]
        plan = self.plan(cfg)
        plan.pop("plan_hash")
        plan["plan_hash"] = hash_object(plan)
        plan_path = self.root / "plan.json"
        atomic_json(plan_path, plan)
        rules = Path(__file__).resolve().parents[1] / "basis/resources/rules/bootstrap.smk"
        controller = str(Path(__file__).resolve().parents[1])
        receipts = []
        for run_id in ("first", "second"):
            root = Path(plan["outdir"]) / "provenance/scheduler" / run_id
            _write(root / "run-context.json", {"run_id": run_id,
                   "source_hash": plan["source_hash"], "config_hash": hash_object(plan["config"])})
            snake = self.root / (run_id + ".smk")
            snake.write_text("\n".join([
                "import sys", "PLAN = config", "PLAN_PATH = " + repr(str(plan_path)),
                "RECEIPTS = " + repr(str(root)), "PYTHON = sys.executable",
                "CONTROLLER_SOURCE = " + repr(controller),
                "rule all:", "    input: RECEIPTS + '/bootstrap/amrfinder.json'",
                "include: " + repr(str(rules)), ""]))
            proc = subprocess.run([sys.executable, "-m", "snakemake", "--snakefile", str(snake),
                "--configfile", str(plan_path), "--cores", "2", "--directory", str(self.root)],
                text=True, capture_output=True, timeout=90,
                env={**os.environ, "PYTHONPATH": controller, "BASIS_SCHEDULER_RUN_ID": run_id})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            receipt = read_document(receipt_path(root, "amrfinder"))
            self.assertEqual(receipt["status"], "ready", receipt)
            receipts.append(receipt)
            resolved = apply_resources(plan, root, "amrfinder")
            self.assertTrue(Path(resolved["config"]["modules"]["amrfinder"]["database"]).is_dir())
        self.assertFalse(receipts[0]["reused"])
        self.assertTrue(receipts[1]["reused"])
        self.assertEqual(receipts[0]["manifest_sha256"], receipts[1]["manifest_sha256"])


if __name__ == "__main__":
    unittest.main()
