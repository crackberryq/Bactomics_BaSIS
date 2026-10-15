"""Exact invocation selection, PID reuse protection and owned-child cancellation."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from basis.run_control import (Process, _signal_verified, matches_config,
                               owned_descendants, process_table, read_process,
                               select_launcher, stop_invocation)


class ProcessSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = self.root / "config.retry-rc3.yaml"
        self.cfg.write_text("{}")
        self.process = Process(42420, 42, os.getuid(), "99", "S",
            ("/some/venv/bin/python", "/some/venv/bin/basis", "run", "-c", self.cfg.name),
            str(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def test_console_script_and_isolated_module_match_exact_config(self):
        self.assertTrue(matches_config(self.process, self.cfg))
        module = replace(self.process, argv=("/some/bin/python3.11", "-I", "-m", "basis", "run",
                                            "--config=" + str(self.cfg)))
        self.assertTrue(matches_config(module, self.cfg))
        short = replace(self.process, argv=("/some/bin/basis", "run", "-c" + str(self.cfg)))
        self.assertTrue(matches_config(short, self.cfg))

    def test_no_shell_text_other_config_or_hybas_match(self):
        other = self.root / "other.yaml"
        other.write_text("{}")
        self.assertFalse(matches_config(self.process, other))
        for argv in [("bash", "-c", "basis run -c " + str(self.cfg)),
                     ("snakemake", "--config", str(self.cfg)),
                     ("python", "-m", "hybas", "run", "-c", str(self.cfg)),
                     ("echo", "basis", "run", "-c", str(self.cfg)),
                     ("basis", "doctor", "-c", str(self.cfg))]:
            self.assertFalse(matches_config(replace(self.process, argv=argv), self.cfg))

    def test_multiple_matching_launchers_require_explicit_selection(self):
        second = replace(self.process, pid=42421)
        table = {p.pid: p for p in (self.process, second)}
        with self.assertRaisesRegex(ValueError, "Multiple matching"):
            select_launcher(table, self.cfg)
        self.assertEqual(select_launcher(table, self.cfg, second.pid), second)
        with self.assertRaisesRegex(ValueError, "No matching"):
            select_launcher(table, self.cfg, 1)

    def test_descendant_chain_includes_detached_child_but_not_unrelated_job(self):
        child = replace(self.process, pid=42421, ppid=self.process.pid, argv=("conda", "run"))
        grandchild = replace(child, pid=42422, ppid=child.pid, argv=("python", "model.py"))
        unrelated = replace(self.process, pid=42423, argv=("snakemake", "hybas"))
        stranger = replace(child, pid=42424, uid=os.getuid() + 1)
        table = {p.pid: p for p in (self.process, child, grandchild, unrelated, stranger)}
        self.assertEqual(set(owned_descendants(table, {self.process.pid: self.process})),
                         {self.process.pid, child.pid, grandchild.pid})

    def test_observed_orphan_retained_but_reused_pid_not_owned(self):
        child = replace(self.process, pid=42421, ppid=self.process.pid)
        orphan = replace(child, ppid=1)
        unrelated = replace(self.process, start_time="100")
        table = {orphan.pid: orphan, unrelated.pid: unrelated}
        owned = owned_descendants(table, {p.pid: p for p in (self.process, child)})
        self.assertEqual(set(owned), {child.pid})

    def test_changed_pid_identity_is_never_signalled(self):
        with patch("basis.run_control.read_process", return_value=replace(self.process, start_time="100")), \
                patch("basis.run_control.signal.pidfd_send_signal") as send:
            self.assertFalse(_signal_verified(self.process, signal.SIGTERM))
            send.assert_not_called()

    def test_pid_reuse_between_open_and_second_check_is_never_signalled(self):
        with patch("basis.run_control.read_process", side_effect=[self.process, replace(self.process, start_time="100")]), \
                patch("basis.run_control.os.pidfd_open", return_value=987), \
                patch("basis.run_control.os.close") as close, \
                patch("basis.run_control.signal.pidfd_send_signal") as send:
            self.assertFalse(_signal_verified(self.process, signal.SIGTERM))
            send.assert_not_called()
            close.assert_called_once_with(987)

    def test_foreign_uid_is_never_signalled(self):
        with patch("basis.run_control.read_process") as read, \
                patch("basis.run_control.signal.pidfd_send_signal") as send:
            self.assertFalse(_signal_verified(replace(self.process, uid=os.getuid() + 1), signal.SIGTERM))
            read.assert_not_called()
            send.assert_not_called()

    def test_stop_algorithm_retains_observed_orphan_and_never_targets_unrelated_job(self):
        child = replace(self.process, pid=42421, ppid=self.process.pid)
        orphan = replace(child, ppid=1)
        unrelated = replace(self.process, pid=42423, argv=("snakemake", "hybas"))
        initial = {p.pid: p for p in (self.process, child, unrelated)}
        after_interrupt = {p.pid: p for p in (orphan, unrelated)}
        finished = {unrelated.pid: unrelated}
        with patch("basis.run_control.os.readlink", return_value=str(os.getpid())), \
                patch("basis.run_control.process_table", side_effect=[initial, after_interrupt, after_interrupt, finished]), \
                patch("basis.run_control._signal_verified", return_value=True) as send:
            result = stop_invocation(self.process, interrupt_grace=0, terminate_grace=0)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["owned_pids"], [self.process.pid, child.pid])
        self.assertEqual([(call.args[0].pid, call.args[1]) for call in send.call_args_list],
                         [(self.process.pid, signal.SIGINT), (child.pid, signal.SIGTERM),
                          (child.pid, signal.SIGKILL)])


@unittest.skipUnless(sys.platform == "linux" and hasattr(os, "pidfd_open")
                     and hasattr(signal, "pidfd_send_signal"), "Linux pidfds required")
class OwnedProcessIntegrationTests(unittest.TestCase):
    def test_stop_owned_detached_child_leaves_unrelated_process_alive(self):
        if os.readlink("/proc/self") != str(os.getpid()):
            self.skipTest("Procfs PID namespace differs")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "config.yaml"
            cfg.write_text("{}")
            child_info = root / "child.json"
            script = root / "basis"
            script.write_text(
                "import json,os,signal,subprocess,sys,time\n"
                "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "child=subprocess.Popen([sys.executable, '-c', "
                "'import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'], start_new_session=True)\n"
                f"open({str(child_info)!r},'w').write(json.dumps({{'pid':child.pid}}))\n"
                "time.sleep(60)\n")
            unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                         start_new_session=True)
            launcher = subprocess.Popen([sys.executable, str(script), "run", "-c", str(cfg)],
                                        cwd=root, start_new_session=True)
            child_pid = None
            try:
                deadline = time.monotonic() + 5
                while not child_info.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                child_pid = json.loads(child_info.read_text())["pid"]
                table = process_table()
                selected = select_launcher(table, cfg)
                self.assertEqual(selected.pid, launcher.pid)
                result = stop_invocation(selected, interrupt_grace=0.1, terminate_grace=0.1)
                launcher.wait(timeout=3)
                self.assertEqual(result["status"], "stopped")
                self.assertIn(child_pid, result["owned_pids"])
                self.assertIsNone(read_process(child_pid))
                self.assertIsNone(unrelated.poll(), "unrelated HybAs-equivalent process was stopped")
                self.assertNotIn(unrelated.pid, result["owned_pids"])
            finally:
                for process in (launcher, unrelated):
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=3)
                if child_pid and read_process(child_pid) is not None:
                    os.kill(child_pid, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
