"""Scoped recovery uses Snakemake itself and never runs biological jobs."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from basis.cli import main, parser
from basis.config import load_config
from basis.demo import create_demo
from basis.pipeline import make_plan
from basis.run_control import Process
from basis.util import atomic_json, hash_object
from basis.workflow_runtime import _write
from basis.workflow_unlock import _active_workers, unlock_workflow


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux process and flock contract")
class UnlockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis-unlock-")
        self.root = Path(self.temp.name)
        self.config_path = create_demo(self.root / "study")
        self.cfg = load_config(self.config_path)
        self.plan = make_plan(self.cfg, False, False)
        self.outdir = Path(self.plan["outdir"])
        self.state = self.outdir / "provenance/scheduler" / ("state-" + self.plan["plan_hash"])
        self.state.mkdir(parents=True)
        self.launch = self.state.parent / "saved-launch"
        _write(self.launch / "run-context.json", {
            "run_id": self.launch.name, "source_hash": self.plan["source_hash"],
            "config_hash": hash_object(self.plan["config"]), "engine": "snakemake",
            "scheduler_launch_dir": str(self.launch), "scheduler_receipt_dir": str(self.state)})
        self.locks = self.outdir / ".snakemake/locks"
        self.locks.mkdir(parents=True)
        (self.locks / "0.input.lock").write_text(self.plan["plan_path"] + "\n")
        (self.locks / "0.output.lock").write_text(str(self.launch / "outcome.json") + "\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_cli_exposes_explicit_scoped_command(self):
        args = parser().parse_args(["unlock", "-c", str(self.config_path)])
        self.assertEqual(args.command, "unlock")
        with patch("basis.workflow_unlock.unlock_workflow", return_value={"status": "unlocked"}) as unlock:
            self.assertEqual(main(["unlock", "-c", str(self.config_path)]), 0)
        unlock.assert_called_once_with(str(self.config_path))

    def test_live_launcher_flock_refuses_without_spawning_snakemake(self):
        with (self.outdir / ".basis.lock").open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch("basis.workflow_unlock.subprocess.run") as run:
                with self.assertRaisesRegex(RuntimeError, "Another BaSIS process"):
                    unlock_workflow(self.config_path)
            run.assert_not_called()
        self.assertTrue((self.locks / "0.input.lock").exists())

    def test_active_native_orphan_scope_refuses_and_does_not_signal(self):
        if os.readlink("/proc/self") != str(os.getpid()):
            self.skipTest("Host procfs differs from this test's PID namespace")
        # Parentage need not include a BaSIS launcher: the native cwd alone is
        # enough to prevent an unsafe unlock after launcher death.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=self.outdir)
        try:
            with patch("basis.workflow_unlock.subprocess.run") as run:
                with self.assertRaisesRegex(RuntimeError, str(child.pid)):
                    unlock_workflow(self.config_path)
            run.assert_not_called()
            self.assertIsNone(child.poll())
            self.assertTrue((self.locks / "0.output.lock").exists())
        finally:
            child.terminate()
            child.wait(timeout=10)

    def test_active_worker_matching_argv_and_shared_staging_are_detected(self):
        current = Process(os.getpid(), 0, os.getuid(), "1", "R", (sys.executable,), str(self.root))
        worker = Process(987650, 1, os.getuid(), "2", "S", ("python", "-m", "basis.bootstrap", "--plan", self.plan["plan_path"]), str(self.root))
        staging = self.root / "cache/staging/mobsuite-example"
        other = Process(987651, 1, os.getuid(), "3", "S", ("curl", "https://example.invalid/resource"), str(staging))
        plan = {**self.plan, "bootstrap_resources": [{"cache_directory": str(self.root / "cache")}]}
        with patch("basis.workflow_unlock._check_procfs"), \
                patch("basis.workflow_unlock.process_table", return_value={p.pid: p for p in (current, worker, other)}):
            found = _active_workers(self.config_path, self.outdir, plan)
        self.assertEqual(set(found), {worker.pid, other.pid})

    def test_other_study_worker_does_not_match_a_similar_prefix(self):
        current = Process(os.getpid(), 0, os.getuid(), "1", "R", (sys.executable,), str(self.root))
        worker = Process(987650, 1, os.getuid(), "2", "S", ("python", "-m", "basis.bootstrap", "--plan", str(self.outdir) + "-other/plan.json"), str(self.outdir) + "-other")
        with patch("basis.workflow_unlock._check_procfs"), \
                patch("basis.workflow_unlock.process_table", return_value={p.pid: p for p in (current, worker)}):
            self.assertFalse(_active_workers(self.config_path, self.outdir, self.plan))

    def test_process_namespace_mismatch_refuses(self):
        with patch("basis.workflow_unlock.os.readlink", return_value="not-this-pid"):
            with self.assertRaisesRegex(RuntimeError, "Safe unlocking requires"):
                unlock_workflow(self.config_path)
        self.assertTrue((self.locks / "0.input.lock").exists())

    def test_saved_context_cannot_redirect_unlock_to_another_study(self):
        other = self.root / "other-results"
        other.mkdir()
        context = json.loads((self.launch / "run-context.json").read_text())
        context["scheduler_receipt_dir"] = str(other)
        _write(self.launch / "run-context.json", context)
        with patch("basis.workflow_unlock.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "escapes"):
                unlock_workflow(self.config_path)
        run.assert_not_called()
        self.assertTrue((self.locks / "0.input.lock").exists())

    def test_corrupt_context_and_redirected_lock_tree_fail_closed(self):
        path = self.launch / "run-context.json"
        original = path.read_bytes()
        path.write_text(original.decode().replace("snakemake", "different"))
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            unlock_workflow(self.config_path)
        path.write_bytes(original)
        moved = self.root / "outside-locks"
        self.locks.rename(moved)
        self.locks.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked"):
            unlock_workflow(self.config_path)
        self.assertTrue((moved / "0.output.lock").exists())

    @unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake unavailable")
    def test_real_snakemake_unlock_preserves_evidence_other_studies_and_metadata(self):
        # Simulate a release upgrade: recorded source identity need not match
        # installed source. No new make_plan or native execution is allowed.
        old = {k: v for k, v in self.plan.items() if k not in {"plan_hash", "plan_path"}}
        old["source_hash"] = "source-from-previous-release"
        old["plan_hash"] = hash_object(old)
        path = self.outdir / "provenance/plans" / (old["plan_hash"] + ".json")
        atomic_json(path, old)
        old_state = self.state.parent / ("state-" + old["plan_hash"])
        old_state.mkdir()
        _write(self.launch / "run-context.json", {
            "run_id": self.launch.name, "source_hash": old["source_hash"],
            "config_hash": hash_object(old["config"]), "engine": "snakemake",
            "scheduler_launch_dir": str(self.launch), "scheduler_receipt_dir": str(old_state)})
        preserved = [self.outdir / "evidence.json", self.outdir / ".snakemake/metadata/fixture",
                     self.outdir / ".snakemake/incomplete/fixture", self.root / "other/.snakemake/locks/0.input.lock"]
        for p in preserved:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{"fixture":"unchanged"}')
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in preserved}
        saved = path.read_bytes()
        with patch("basis.workflow_unlock._active_workers", return_value={}), \
                patch("basis.pipeline.make_plan", side_effect=AssertionError("No replanning")), \
                patch("basis.pipeline.run_pipeline", side_effect=AssertionError("No native analysis")):
            result = unlock_workflow(self.config_path)
        self.assertEqual(result["status"], "unlocked")
        self.assertEqual(result["removed_lock_files"], 2)
        self.assertFalse(result["native_analyses_run"])
        self.assertEqual(result["saved_plan"], str(path))
        self.assertIn("Unlocked working directory", result["snakemake_output"])
        self.assertFalse(self.locks.exists())
        self.assertEqual(path.read_bytes(), saved)
        self.assertFalse((self.launch / "outcome.json").exists())
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in preserved}, before)
        with patch("basis.workflow_unlock._active_workers", return_value={}):
            self.assertEqual(unlock_workflow(self.config_path)["status"], "already_unlocked")


if __name__ == "__main__":
    unittest.main()
