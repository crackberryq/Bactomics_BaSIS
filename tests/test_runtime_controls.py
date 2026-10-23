"""Deadline, cancellation and quiet-command tests using synthetic subprocesses."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workflow/lib"))
from safety.runtime import CommandTimeoutError, DirectRunner


def running(pid):
    """A terminated orphan may briefly remain a zombie until the OS reaps it."""
    stat = Path(f"/proc/{pid}/stat")
    try:
        value = stat.read_text()
    except FileNotFoundError:
        return False
    return value[value.rfind(")") + 2:].split()[0] not in {"Z", "X"}


class RuntimeControlTests(unittest.TestCase):
    def test_deadlines_are_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            for key in ("timeout_seconds", "heartbeat_seconds"):
                for value in (0, -1, True, None, "unknown", float("nan"), float("inf")):
                    with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, key):
                        DirectRunner({key: value}, Path(folder) / "commands.log")

    def test_quiet_timeout_has_final_record_and_restores_signal_handlers(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = DirectRunner({"timeout_seconds": .15}, Path(folder) / "commands.log")
            handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
            started = time.monotonic()
            with self.assertRaisesRegex(CommandTimeoutError, "exceeded"):
                runner.run([sys.executable, "-c", "import time; time.sleep(30)"])
            self.assertLess(time.monotonic() - started, 3)
            record = runner.commands[0]
            self.assertEqual(record["status"], "timed_out")
            self.assertGreaterEqual(record["elapsed_seconds"], .15)
            self.assertIn("pid", record)
            self.assertIn("finished_at", record)
            self.assertIn('"status": "timed_out"', runner.logfile.read_text())
            self.assertEqual(handlers, {sig: signal.getsignal(sig) for sig in handlers})

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux process-group ownership check")
    def test_timeout_stops_inherited_pipe_holder_after_leader_exits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pid_file = root / "child.pid"
            script = (
                "import pathlib, subprocess, sys\n"
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
            )
            runner = DirectRunner({"timeout_seconds": .3}, root / "commands.log")
            with self.assertRaises(CommandTimeoutError):
                runner.run([sys.executable, "-c", script, str(pid_file)])
            child_pid = int(pid_file.read_text())
            deadline = time.monotonic() + 2
            while running(child_pid) and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(running(child_pid), "The child's inherited pipe must not outlive a timed-out job")
            self.assertEqual(runner.commands[0]["returncode"], 0, "The leader already exited successfully")
            self.assertEqual(runner.commands[0]["status"], "timed_out")

    def test_streams_closed_early_do_not_disable_the_deadline(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = DirectRunner({"timeout_seconds": .15}, Path(folder) / "commands.log")
            script = "import os,time; os.close(1); os.close(2); time.sleep(30)"
            with self.assertRaises(CommandTimeoutError):
                runner.run([sys.executable, "-c", script])

    @unittest.skipUnless(os.name == "posix", "POSIX signal escalation")
    def test_timeout_escalates_when_child_ignores_termination(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = DirectRunner({"timeout_seconds": .15}, Path(folder) / "commands.log")
            script = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"
            started = time.monotonic()
            with self.assertRaises(CommandTimeoutError):
                runner.run([sys.executable, "-c", script])
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(runner.commands[0]["returncode"], -signal.SIGKILL)

    def test_heartbeat_is_live_and_does_not_contaminate_stdout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            release = root / "release"
            runner = DirectRunner({"timeout_seconds": 5, "heartbeat_seconds": .05}, root / "commands.log")
            script = (
                "import pathlib,sys,time\n"
                "while not pathlib.Path(sys.argv[1]).exists(): time.sleep(.01)\n"
                "print('{\"ok\":true}')\n"
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                task = pool.submit(runner.run, [sys.executable, "-c", script, str(release)])
                try:
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        if runner.logfile.exists() and "still running; elapsed=" in runner.logfile.read_text():
                            break
                        time.sleep(.01)
                    else:
                        self.fail("A quiet command did not emit a heartbeat before finishing")
                    self.assertFalse(task.done())
                finally:
                    release.touch()
                result = task.result(timeout=3)
            self.assertEqual(json.loads(result.stdout), {"ok": True})
            self.assertEqual(runner.commands[0]["status"], "completed")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux scoped process cancellation check")
    def test_cli_cancellation_stops_its_child_and_preserves_unrelated_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            child_file, record = root / "child.pid", root / "record.json"
            script = (
                "import os,pathlib,sys,time\n"
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
                "time.sleep(30)\n"
            )
            env = dict(os.environ, PYTHONPATH=str(ROOT / "workflow/lib"))
            unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            worker = subprocess.Popen([
                sys.executable, "-m", "safety.runtime", "run", "--timeout-seconds", "10",
                "--log", str(root / "commands.log"), "--record", str(record), "--",
                sys.executable, "-c", script, str(child_file)], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 3
                while not child_file.exists() and time.monotonic() < deadline:
                    if worker.poll() is not None:
                        self.fail(f"Wrapper exited early: {worker.communicate()}")
                    time.sleep(.01)
                self.assertTrue(child_file.exists())
                worker.send_signal(signal.SIGTERM)
                worker.communicate(timeout=3)
                self.assertEqual(worker.returncode, 143)
                self.assertFalse(running(int(child_file.read_text())))
                self.assertIsNone(unrelated.poll(), "Cancellation must not affect an unrelated process")
                self.assertEqual(json.loads(record.read_text())["status"], "interrupted")
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.communicate(timeout=3)
                unrelated.kill()
                unrelated.wait(timeout=3)

    def test_cli_timeout_writes_failure_record_and_preserves_stdout_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env = dict(os.environ, PYTHONPATH=str(ROOT / "workflow/lib"))
            result = subprocess.run([
                sys.executable, "-m", "safety.runtime", "run", "--timeout-seconds", ".15",
                "--log", str(root / "commands.log"), "--record", str(root / "record.json"),
                "--stdout", str(root / "output.bin"), "--", sys.executable, "-c",
                "import sys,time;sys.stdout.buffer.write(b'\\x00\\xff');sys.stdout.flush();time.sleep(30)"],
                env=env, capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 124, result.stderr)
            self.assertEqual((root / "output.bin").read_bytes(), b"\x00\xff")
            self.assertEqual(json.loads((root / "record.json").read_text())["status"], "timed_out")


if __name__ == "__main__":
    unittest.main()
