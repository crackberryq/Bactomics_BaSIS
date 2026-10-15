"""Stable Snakemake recovery with synthetic native commands, never biology."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from basis.config import load_config
from basis.demo import create_demo
from basis.pipeline import make_plan, run_pipeline
from basis.scheduler_persistence import prepare_scheduler_state
from basis.util import atomic_json, sha256_file


class StableIdentityTests(unittest.TestCase):
    def test_config_copy_does_not_change_execution_identity_but_actual_changes_do(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = load_config(create_demo(folder))
            first = make_plan(cfg, False, False)
            cfg["config_path"] = str(Path(folder) / "another-copy.yaml")
            same = make_plan(cfg, False, False)
            self.assertEqual(first["plan_hash"], same["plan_hash"])
            cfg["modules"]["amrfinder"]["threads"] += 1
            changed = make_plan(cfg, False, False)
            self.assertNotEqual(first["plan_hash"], changed["plan_hash"])
            with patch("basis.pipeline.code_fingerprint", return_value="synthetic-new-source"):
                self.assertNotEqual(changed["plan_hash"], make_plan(cfg, False, False)["plan_hash"])
            assembly = Path(changed["samples"][0]["assembly"])
            assembly.write_text(assembly.read_text() + "\n")
            self.assertNotEqual(changed["plan_hash"], make_plan(cfg, False, False)["plan_hash"])

    def test_external_profile_and_reviewed_artifact_closure(self):
        from basis.scheduler_persistence import resource_fingerprints
        import yaml
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profiles = root / "profiles"
            profiles.mkdir()
            database = root / "custom.hmm"
            for suffix in ("", ".h3f", ".h3i", ".h3m", ".h3p"):
                Path(str(database) + suffix).write_text("fixture-v1")
            (profiles / "custom.yaml").write_text(yaml.safe_dump({"resources": {"domains": {"database": "../custom.hmm"}}}))
            tree = root / "external.tree"
            tree.write_text("synthetic-v1")
            registry = root / "review.json"
            registry.write_text(json.dumps({"records": [{"artifacts": {str(tree): "stored-audit-hash"}}]}))
            cfg = {"profiles_dir": str(profiles), "tree_review_registry": str(registry)}
            first = resource_fingerprints(cfg, require_present=True)
            database.write_text("fixture-v2")
            second = resource_fingerprints(cfg, require_present=True)
            self.assertNotEqual(first, second)
            Path(str(database) + ".h3i").write_text("index-v2")
            third = resource_fingerprints(cfg, require_present=True)
            self.assertNotEqual(second, third)
            tree.write_text("synthetic-v2")
            self.assertNotEqual(third, resource_fingerprints(cfg, require_present=True))
            Path(str(database) + ".h3p").unlink()
            with self.assertRaisesRegex(ValueError, "does not exist"):
                resource_fingerprints(cfg, require_present=True)
            (profiles / "custom.yaml").write_text(yaml.safe_dump({"resources": {"future_resource": "untracked"}}))
            self.assertTrue(resource_fingerprints(cfg)["untracked_resource_scopes"])


@unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake unavailable")
class StableNativeSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis-stable-dag-")
        self.root = Path(self.temp.name)
        self.cfg = load_config(create_demo(self.root))
        self.cfg["execution"].update(cores=2)
        for cfg in self.cfg["modules"].values():
            cfg.update(threads=1, memory_mb=32)
        self.cfg["deep"].update(threads=1, memory_mb=64)
        self.database = self.root / "synthetic-db"
        self.database.mkdir()
        (self.database / "version").write_text("fixture-v1")
        tool = self.root / "native_fixture.py"
        tool.write_text("#!" + sys.executable + "\n" + '''import sys
from pathlib import Path
root = Path(__file__).parent
if '-n' not in sys.argv:
    print('Synthetic native fixture version 1')
    raise SystemExit(0)
sample = Path(sys.argv[sys.argv.index('-n') + 1]).stem
with (root / 'native.calls').open('a') as log:
    log.write(sample + '\\n')
if (root / ('fail-' + sample)).exists():
    raise SystemExit('Intentional synthetic job failure')
header = ['Protein id','Contig id','Start','Stop','Strand','Scope','Method',
          '% Coverage of reference','% Identity to reference','Element symbol',
          'Element name','Type','Subtype']
Path(sys.argv[sys.argv.index('-o') + 1]).write_text('\\t'.join(header) + '\\n')
''')
        tool.chmod(0o755)
        self.cfg["modules"]["amrfinder"].update(mode="native", executable=str(tool),
            database=str(self.database), database_manifest=None, result=None, command_prefix=[],
            environment_backend="external")

    def tearDown(self):
        self.temp.cleanup()

    def run_plan(self, plan, force=False):
        result, code = run_pipeline(plan, engine="snakemake", force=force)
        evidence = json.loads(Path(result["artifacts"]["evidence_json"]).read_text())
        return result, code, evidence

    def calls(self):
        p = self.root / "native.calls"
        return p.read_text().splitlines() if p.exists() else []

    def test_failed_job_restart_preserves_successful_native_command_and_fresh_reporting(self):
        fail = self.root / "fail-DEMO_B"
        fail.touch()
        plan = make_plan(self.cfg, False, False)
        first, code, evidence = self.run_plan(plan)
        self.assertEqual(code, 2)
        self.assertEqual(sorted(self.calls()), ["DEMO_A", "DEMO_B"])
        state = Path(first["run"]["scheduler_receipt_dir"])
        successful = state / "DEMO_A/amrfinder.ready.json"
        original = successful.read_bytes()
        fail.unlink()
        second, code, evidence = self.run_plan(plan)
        self.assertEqual(code, 0, second["run"].get("engine_error"))
        self.assertEqual(self.calls().count("DEMO_A"), 1)
        self.assertEqual(self.calls().count("DEMO_B"), 2)
        self.assertEqual(original, successful.read_bytes())
        self.assertEqual(first["run"]["scheduler_receipt_dir"], second["run"]["scheduler_receipt_dir"])
        self.assertNotEqual(first["run"]["run_id"], second["run"]["run_id"])
        preserved = next(r for r in evidence["records"] if r["sample_id"] == "DEMO_A" and r["module"] == "amrfinder")
        self.assertEqual(preserved["scheduler_execution"]["status"], "preserved_by_snakemake")
        third, code, _ = self.run_plan(plan)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls()), 3)
        self.assertNotEqual(second["run"]["run_id"], third["run"]["run_id"])
        log = Path(third["run"]["scheduler_log"]).read_text()
        self.assertNotIn("localrule amrfinder:", log)
        self.assertIn("localrule render_report:", log)

    def test_tool_identity_gate_detects_modified_external_executable(self):
        from basis.execution import CommandRunner
        from basis.scheduler_persistence import capture_tool_identity, file_inventory
        from basis.util import hash_object
        from basis.workflow_runtime import _write, _ready
        plan = make_plan(self.cfg, False, False)
        root = Path(plan["outdir"]) / "provenance/scheduler" / ("state-" + plan["plan_hash"])
        launch = root.parent / "identity-fixture"
        identity = capture_tool_identity("amrfinder", self.cfg["modules"]["amrfinder"],
            CommandRunner(self.cfg["modules"]["amrfinder"], self.root / "identity.log"))
        self.assertTrue(identity["executables"])
        result, code, _ = self.run_plan(plan)
        self.assertEqual(code, 0)
        receipt = root / "DEMO_A/checks/amrfinder.json"
        gate = json.loads(receipt.read_text())
        environment = self.root / "synthetic-conda-environment"
        environment.mkdir()
        package_file = environment / "implementation.py"
        package_file.write_text("synthetic version one")
        gate["tool_identity"] = {**identity, "conda": True, "prefix": str(environment),
                                  "environment_sha256": hash_object(file_inventory(environment)), "reuse_supported": True}
        _write(receipt, gate)
        _ready(receipt, plan, root, "DEMO_A")
        package_file.write_text("synthetic version two")
        environment_audit = prepare_scheduler_state(plan, root, root.parent / "environment-fixture")
        environment_details = json.loads(Path(environment_audit["path"]).read_text())
        self.assertTrue(any("Tool environment content changed" in r["reason"] for r in environment_details["revoked"]))
        gate["tool_identity"] = identity
        _write(receipt, gate)
        _ready(receipt, plan, root, "DEMO_A")
        executable = Path(self.cfg["modules"]["amrfinder"]["executable"])
        executable.write_text(executable.read_text() + "\n# changed synthetic implementation\n")
        audit = prepare_scheduler_state(plan, root, launch)
        details = json.loads(Path(audit["path"]).read_text())
        self.assertTrue(any("Executable content changed" in r["reason"] for r in details["revoked"]))
        self.assertFalse((root / "DEMO_A/checks/amrfinder.ready.json").exists())

    def test_same_version_binary_change_cannot_reuse_inner_native_cache(self):
        from basis.execution import CommandRunner
        from basis.scheduler_persistence import capture_tool_identity
        from basis.workflow_runtime import _execution_plan, _write, _ready
        from basis.pipeline import execute_module
        from basis.util import hash_object
        plan = make_plan(self.cfg, False, False)
        first, code, _ = self.run_plan(plan)
        self.assertEqual(code, 0)
        root = Path(first["run"]["scheduler_receipt_dir"])
        receipt = root / "DEMO_A/checks/amrfinder.json"
        gate = json.loads(receipt.read_text())
        sample = next(s for s in plan["samples"] if s["sample_id"] == "DEMO_A")
        cfg = self.cfg["modules"]["amrfinder"]
        runner = CommandRunner(cfg, self.root / "capture-identity.log")
        identity = capture_tool_identity("amrfinder", cfg, runner)
        # Exercise the supported complete-environment identity contract with a
        # tiny synthetic environment instead of hashing the test interpreter.
        identity["reuse_supported"] = True
        gate["tool_identity"] = identity
        _write(receipt, gate)
        _ready(receipt, plan, root, "DEMO_A")
        with patch.dict(os.environ, {"BASIS_SCHEDULER_RUN_ID": root.name}):
            selected = _execution_plan(plan, sample, "amrfinder")
            before = execute_module(selected, sample, "amrfinder")
            self.assertEqual(before["execution_status"], "completed")
            stable = execute_module(selected, sample, "amrfinder")
            self.assertTrue(stable["cache_hit"])
            executable = Path(cfg["executable"])
            executable.write_text(executable.read_text() + "\n# same version, changed implementation\n")
            changed = capture_tool_identity("amrfinder", cfg, runner)
            changed["reuse_supported"] = True
            gate["tool_identity"] = changed
            _write(receipt, gate)
            _ready(receipt, plan, root, "DEMO_A")
            selected_after = _execution_plan(plan, sample, "amrfinder")
            after = execute_module(selected_after, sample, "amrfinder")
        self.assertEqual(after["execution_status"], "completed")
        self.assertNotEqual(before["cache_key"], after["cache_key"])
        self.assertFalse(after.get("cache_hit", False))
        self.assertEqual(self.calls().count("DEMO_A"), 3)
        self.assertEqual(self.calls().count("DEMO_B"), 1)

    def test_setup_failure_prevents_every_native_sample_command(self):
        missing = self.root / "inputs/DEMO_B.rgi.json"
        missing.unlink()
        result, code, _ = self.run_plan(make_plan(self.cfg, False, False))
        self.assertEqual(code, 2)
        self.assertEqual(self.calls(), [])
        root = Path(result["run"]["scheduler_receipt_dir"])
        self.assertFalse((root / "setup.ready.json").exists())
        self.assertFalse((root / "DEMO_A/amrfinder.json").exists())

    def test_resource_change_and_corrupt_output_invalidate_only_relevant_targets(self):
        plan = make_plan(self.cfg, False, False)
        first, code, _ = self.run_plan(plan)
        self.assertEqual(code, 0)
        state = Path(first["run"]["scheduler_receipt_dir"])
        rgi = state / "DEMO_A/rgi.ready.json"
        rgi_before = rgi.read_bytes()
        (self.database / "version").write_text("fixture-v2")
        second, code, _ = self.run_plan(plan)
        self.assertEqual(code, 0, second["run"].get("engine_error"))
        self.assertEqual(len(self.calls()), 4)
        self.assertEqual(rgi_before, rgi.read_bytes())
        native = next((Path(plan["outdir"]) / "results/DEMO_A/amrfinder/attempts").rglob("amrfinder.tsv"))
        # Select the currently attested output rather than an archived attempt.
        record = json.loads((Path(plan["outdir"]) / "results/DEMO_A/amrfinder/result.json").read_text())
        native = next(Path(p) for p in record["artifacts"] if str(p).endswith("amrfinder.tsv"))
        native.write_text("corrupted")
        third, code, _ = self.run_plan(plan)
        self.assertEqual(code, 0, third["run"].get("engine_error"))
        self.assertEqual(self.calls().count("DEMO_A"), 3)
        self.assertEqual(self.calls().count("DEMO_B"), 2)
        self.assertEqual(rgi_before, rgi.read_bytes())

    def test_force_runs_native_commands_even_with_valid_stable_targets(self):
        plan = make_plan(self.cfg, False, False)
        self.assertEqual(self.run_plan(plan)[1], 0)
        self.assertEqual(self.run_plan(plan, force=True)[1], 0)
        self.assertEqual(len(self.calls()), 4)


if __name__ == "__main__":
    unittest.main()
