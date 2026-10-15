"""GUI launch identity, shared study outputs and scoped cancellation contracts."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from basis import jobs
from basis.config import default_config
from basis.run_control import Process


class ResumeJobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "study"
        self.source.mkdir()
        self.config = self.source / "config.yaml"
        self.settings = default_config()
        self.settings["deep"]["placement_cache_dir"] = "placement-cache"
        self.config.write_text(yaml.safe_dump(self.settings))

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self, name, **settings):
        directory = self.root / name
        directory.mkdir()
        request = jobs.normalized_request({"kind": "run", "config": str(self.config), **settings})
        return directory, jobs._prepare(directory, request, self.root)

    def test_resume_preserves_study_outputs_cache_and_config_mtime(self):
        first, (command, output) = self.prepare("first", cores=2)
        snapshot = Path(command[command.index("-c") + 1])
        original_mtime = snapshot.stat().st_mtime_ns
        second, (again, resumed_output) = self.prepare("second", cores=2)
        self.assertEqual(command, again)
        self.assertEqual(snapshot.stat().st_mtime_ns, original_mtime)
        self.assertEqual(output, resumed_output)
        self.assertEqual(output, str(self.source / "results"))
        actual = yaml.safe_load(snapshot.read_text())
        self.assertEqual(actual["deep"]["placement_cache_dir"], str(self.source / "placement-cache"))
        self.assertEqual(actual["samples"], str(self.source / "samples.tsv"))
        self.assertEqual((first / "config.yaml").read_bytes(), snapshot.read_bytes())
        self.assertEqual((second / "config.yaml").read_bytes(), snapshot.read_bytes())
        self.assertEqual(yaml.safe_load(self.config.read_text()), self.settings)
        _, (changed, changed_output) = self.prepare("third", cores=3)
        self.assertNotEqual(changed[changed.index("-c") + 1], str(snapshot))
        self.assertEqual(changed_output, output)
        self.assertEqual(actual, yaml.safe_load(snapshot.read_text()))

    def test_corrupt_snapshot_is_not_silently_overwritten(self):
        _, (command, _) = self.prepare("first")
        snapshot = Path(command[command.index("-c") + 1])
        snapshot.write_text("broken snapshot")
        with self.assertRaisesRegex(ValueError, "corrupt"):
            self.prepare("second")
        self.assertEqual(snapshot.read_text(), "broken snapshot")

    def test_public_analysis_jobs_only_accept_snakemake(self):
        for kind in ("run", "demo", "setup"):
            options = {"config": str(self.config)} if kind == "run" else {"envs": ["minced"]} if kind == "setup" else {}
            request = jobs.normalized_request({"kind": kind, **options})
            self.assertEqual(request["engine"], "snakemake")
            with self.assertRaisesRegex(ValueError, "snakemake"):
                jobs.normalized_request({**request, "engine": "local"})

    def test_init_command_prepares_only_a_study(self):
        request = jobs.normalized_request({"kind": "init", "hybas_dir": str(self.source)})
        directory = self.root / "init-job"
        directory.mkdir()
        command, output = jobs._prepare(directory, request, self.root)
        self.assertEqual(command, [sys.executable, "-I", "-m", "basis", "init",
            "--directory", str(directory / "study"), "--hybas-dir", str(self.source), "--preset", "full"])
        self.assertEqual(output, str(directory / "study"))
        self.assertFalse(Path(output).exists())
        request = jobs.normalized_request({**request, "directory": str(self.root / "named-study"),
            "resource_dir": str(self.root / "resources"), "preset": "core"})
        command, output = jobs._prepare(directory, request, self.root)
        self.assertEqual(output, str(self.root / "named-study"))
        self.assertEqual(command[-2:], ["--resource-dir", str(self.root / "resources")])
        self.assertNotIn("run", command)
        self.assertNotIn("--execute", command)
        self.assertFalse(Path(output).exists())

    def test_init_rejects_missing_or_misplaced_options(self):
        for request in ({"kind": "init"}, {"kind": "init", "hybas_dir": ""},
                {"kind": "init", "hybas_dir": str(self.config)},
                {"kind": "init", "hybas_dir": str(self.source), "preset": "anything"},
                {"kind": "init", "hybas_dir": str(self.source), "directory": ""},
                {"kind": "demo", "hybas_dir": str(self.source)}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                jobs.normalized_request(request)

    def queued_state(self, kind="run"):
        identifier = "a" * 24
        directory = jobs.job_path(self.root, identifier)
        directory.mkdir(parents=True)
        command, output = jobs._prepare(directory, {"kind": kind, "config": str(self.config)}, self.root)
        state = {"id": identifier, "kind": kind, "state": "queued", "created_at": jobs.now(),
            "command": command, "outdir": output, "directory": str(directory),
            "log": str(directory / "console.log"), "exit_code": None}
        jobs.atomic_json(directory / "status.json", state)
        return directory, state

    def old_evidence(self):
        output = self.source / "results"
        output.mkdir()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        evidence = {"run": {"run_id": "old-run", "started_at": yesterday, "finished_at": yesterday,
            "completeness": "completed_requested_scope"}}
        (output / "evidence.json").write_text(json.dumps(evidence))
        return output, evidence

    def test_failed_launch_does_not_relabel_old_evidence_complete(self):
        output, _ = self.old_evidence()
        directory, state = self.queued_state()
        for payload in ((output / "evidence.json").read_text(), "malformed prior evidence"):
            with self.subTest(payload=payload):
                (output / "evidence.json").write_text(payload)
                jobs.atomic_json(directory / "status.json", state)
                with patch("basis.jobs.subprocess.Popen") as popen, patch("basis.jobs._identity", return_value=["boot", "100"]):
                    popen.return_value.pid = 1234
                    popen.return_value.wait.return_value = 0
                    jobs.work(self.root, state["id"])
                actual = json.loads((directory / "status.json").read_text())
                self.assertEqual(actual["state"], "failed")
                self.assertEqual(actual["child_identity"], ["boot", "100"])
                self.assertEqual((output / "evidence.json").read_text(), payload)

    def test_fresh_final_evidence_attests_new_launch(self):
        output, _ = self.old_evidence()
        directory, state = self.queued_state()
        def finish():
            current = jobs.now()
            (output / "evidence.json").write_text(json.dumps({"run": {"run_id": "new-run", "local_job_id": state["id"],
                "started_at": current, "finished_at": current, "completeness": "completed_requested_scope"}}))
            return 0
        with patch("basis.jobs.subprocess.Popen") as popen, patch("basis.jobs._identity", return_value=["boot", "100"]):
            popen.return_value.pid = 1234
            popen.return_value.wait.side_effect = finish
            jobs.work(self.root, state["id"])
        actual = json.loads((directory / "status.json").read_text())
        self.assertEqual(actual["state"], "completed")
        self.assertEqual(actual["evidence_before"]["run_id"], "old-run")
        self.assertTrue(actual["evidence_current"])
        self.assertEqual(actual["result_run_id"], "new-run")

    def test_fresh_unrelated_evidence_is_not_this_job(self):
        output, _ = self.old_evidence()
        baseline = jobs._evidence_snapshot("run", output)
        launch = jobs.now()
        current = jobs.now()
        (output / "evidence.json").write_text(json.dumps({"run": {"run_id": "other-run",
            "local_job_id": "b" * 24, "started_at": current, "finished_at": current,
            "completeness": "completed_requested_scope"}}))
        state, message = jobs.outcome("run", 0, output, evidence_before=baseline,
            started_at=launch, job_id="a" * 24)
        self.assertEqual(state, "failed")
        self.assertIn("does not attest", message)

    def running_state(self):
        directory, state = self.queued_state()
        state.update(state="running", worker_pid=110, worker_identity=["boot", "1"],
            child_pid=120, child_identity=["boot", "2"])
        jobs.atomic_json(directory / "status.json", state)
        process = Process(120, 110, os.getuid(), "2", "S", tuple(state["command"]), str(self.root))
        return directory, state, process

    def test_stop_rejects_reused_pid_or_changed_boot_without_signals(self):
        _, state, _ = self.running_state()
        for current_identity in (["boot", "different-start"], ["another-boot", "2"], None):
            with self.subTest(identity=current_identity), patch("basis.jobs._identity", return_value=current_identity), \
                    patch("basis.run_control.stop_invocation") as stop:
                with self.assertRaisesRegex(ValueError, "identity"):
                    jobs.stop_job(self.root, state["id"])
                stop.assert_not_called()

    def test_stop_requires_exact_command_config_and_parent(self):
        _, state, process = self.running_state()
        cases = [Process(process.pid, 999, process.uid, process.start_time, "S", process.argv, process.cwd),
                 Process(process.pid, process.ppid, process.uid, process.start_time, "S", ("python", "other.py"), process.cwd)]
        for changed in cases:
            with self.subTest(process=changed), patch("basis.jobs._identity", side_effect=lambda pid: ["boot", "2" if pid == 120 else "1"]), \
                    patch("basis.run_control.read_process", return_value=changed), patch("basis.run_control.stop_invocation") as stop:
                with self.assertRaisesRegex(ValueError, "changed"):
                    jobs.stop_job(self.root, state["id"])
                stop.assert_not_called()

    def test_stop_valid_recorded_invocation_retains_audit(self):
        directory, state, process = self.running_state()
        result = {"status": "stopped", "owned_pids": [120], "remaining_pids": [],
            "signals": [{"pid": 120, "start_time": "2", "signal": "SIGINT"}]}
        with patch("basis.jobs._identity", side_effect=lambda pid: ["boot", "2" if pid == 120 else "1"]), \
                patch("basis.run_control.read_process", return_value=process), patch("basis.run_control.stop_invocation", return_value=result) as stop:
            actual = jobs.stop_job(self.root, state["id"])
        stop.assert_called_once_with(process)
        self.assertEqual(actual["state"], "cancelled")
        self.assertEqual(actual["stop_result"], result)
        audit = json.loads((directory / "cancellation.json").read_text())
        self.assertEqual(audit["child_identity"], ["boot", "2"])
        self.assertEqual(audit["result"], result)
        self.assertEqual(json.loads((directory / "status.json").read_text())["state"], "cancelled")

    def test_worker_finish_preserves_verified_cancellation(self):
        directory, state = self.queued_state()
        def process(_):
            return Process(120, os.getpid(), os.getuid(), "120", "S", tuple(state["command"]), str(self.root))
        def stop_while_waiting():
            actual = jobs.stop_job(self.root, state["id"])
            self.assertEqual(actual["state"], "cancelled")
            return 130
        with patch("basis.jobs._identity", side_effect=lambda pid: ["boot", str(pid)]), \
                patch("basis.run_control.read_process", side_effect=process), \
                patch("basis.run_control.stop_invocation", return_value={"status": "stopped", "signals": [{"pid": 120, "signal": "SIGINT"}]}), \
                patch("basis.jobs.subprocess.Popen") as popen:
            popen.return_value.pid = 120
            popen.return_value.wait.side_effect = stop_while_waiting
            jobs.work(self.root, state["id"])
        actual = json.loads((directory / "status.json").read_text())
        self.assertEqual(actual["state"], "cancelled")
        self.assertEqual(actual["exit_code"], 130)
        self.assertEqual(actual["stop_status"], "stopped")


if __name__ == "__main__":
    unittest.main()
