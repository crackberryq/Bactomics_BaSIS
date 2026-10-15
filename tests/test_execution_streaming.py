"""Live diagnostics, lossless data capture, and owned native-process cleanup."""
import contextlib
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from basis.execution import CommandRunner


def wait_for(path, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size:
            return
        time.sleep(.02)
    raise AssertionError("Timed out waiting for test child: " + str(path))


def process_running(pid):
    stat = Path('/proc') / str(pid) / 'stat'
    if stat.exists():
        # A killed orphan may briefly remain a zombie until container init reaps it.
        return stat.read_text().split(') ', 1)[1].split()[0] != 'Z'
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class ExecutionStreamingTests(unittest.TestCase):
    def test_stderr_and_log_arrive_before_process_finishes_stdout_stays_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            done = root / 'finish'
            event = threading.Event()
            class Monitor(io.StringIO):
                def write(self, value):
                    result = super().write(value)
                    if 'native-stage-ready' in self.getvalue():
                        event.set()
                    return result
            monitor = Monitor()
            runner = CommandRunner({'timeout_seconds': 10}, root / 'commands.log')
            result, errors = [], []
            script = "import sys,time;from pathlib import Path;print('data-output');print('native-stage-ready',file=sys.stderr);p=Path(sys.argv[1]);\nwhile not p.exists(): time.sleep(.02)"
            def run():
                try:
                    result.append(runner.run([sys.executable, '-c', script, str(done)]))
                except BaseException as exc:
                    errors.append(exc)
            with patch.dict(os.environ, {'BASIS_VERBOSE': '1'}), contextlib.redirect_stderr(monitor):
                worker = threading.Thread(target=run)
                worker.start()
                try:
                    self.assertTrue(event.wait(5), 'Native stderr was buffered until exit')
                    self.assertTrue(worker.is_alive())
                    self.assertIn('native-stage-ready', runner.logfile.read_text())
                    self.assertNotIn('data-output', monitor.getvalue())
                finally:
                    done.touch()
                    worker.join(10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(result[0].stdout, 'data-output\n')
            self.assertNotIn('still running', monitor.getvalue())

    def test_stdout_artifact_is_byte_exact_and_diagnostics_do_not_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b'>sample\r\nACD\n\xff'
            runner = CommandRunner({}, root / 'commands.log')
            with patch.dict(os.environ, {'BASIS_VERBOSE': '1'}), contextlib.redirect_stderr(io.StringIO()):
                result = runner.run([sys.executable, '-c',
                    "import os;os.write(1," + repr(payload) + ");os.write(2,b'diagnostic\\n')"],
                    stdout_path=root / 'out.faa')
            self.assertEqual((root / 'out.faa').read_bytes(), payload)
            self.assertEqual(result.stdout, '')
            self.assertIn('diagnostic', runner.logfile.read_text())
            self.assertNotIn('>sample\r\nACD', runner.logfile.read_text())

    def test_explicit_progress_stdout_is_teed_to_stderr_without_losing_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            terminal, cli_stdout = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {'BASIS_VERBOSE': '1'}), \
                    contextlib.redirect_stderr(terminal), contextlib.redirect_stdout(cli_stdout):
                result = CommandRunner({}, Path(tmp) / 'log').run(
                    [sys.executable, '-c', "print('embedding 1/2')"], stream_stdout=True)
            self.assertIn('embedding 1/2', terminal.getvalue())
            self.assertEqual(result.stdout, 'embedding 1/2\n')
            self.assertEqual(cli_stdout.getvalue(), '')

    def test_signal_handlers_restore_when_output_creation_fails_before_spawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blocked = root / 'blocked'
            blocked.write_text('not a directory')
            before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
            with self.assertRaises(FileExistsError):
                CommandRunner({}, root / 'log').run([sys.executable, '-c', 'pass'],
                                                   stdout_path=blocked / 'output')
            self.assertEqual(before, (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)))

    def test_backed_up_terminal_does_not_hold_log_lock_during_timeout_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            entered, release = threading.Event(), threading.Event()
            class BlockedTerminal(io.StringIO):
                def write(self, value):
                    if value == 'blocked-native-output\n':
                        entered.set()
                        release.wait(15)
                    return super().write(value)
            runner = CommandRunner({'timeout_seconds': 1}, Path(tmp) / 'commands.log')
            errors = []
            def run():
                try:
                    runner.run([sys.executable, '-c',
                        "import sys,time;print('blocked-native-output',file=sys.stderr);time.sleep(60)"])
                except BaseException as exc:
                    errors.append(exc)
            with patch.dict(os.environ, {'BASIS_VERBOSE': '1'}), contextlib.redirect_stderr(BlockedTerminal()):
                worker = threading.Thread(target=run)
                worker.start()
                try:
                    self.assertTrue(entered.wait(5))
                    worker.join(8)
                    self.assertFalse(worker.is_alive(), 'Timeout cleanup deadlocked on terminal writer')
                    self.assertIn('Command finished:', runner.logfile.read_text())
                finally:
                    release.set()
                    worker.join(5)
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], RuntimeError)
            self.assertIn('exceeded 1s', str(errors[0]))

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group cleanup')
    def test_timeout_kills_native_grandchild_and_records_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_pid = root / 'child.pid'
            script = "import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);Path(sys.argv[1]).write_text(str(p.pid));time.sleep(60)"
            runner = CommandRunner({'timeout_seconds': 1}, root / 'commands.log')
            with self.assertRaisesRegex(RuntimeError, 'process group stopped'):
                runner.run([sys.executable, '-c', script, str(child_pid)])
            self.assertTrue(child_pid.exists())
            self.assertFalse(process_running(int(child_pid.read_text())))
            self.assertIn('exceeded 1s', runner.commands[0]['error'])
            self.assertIn('Command finished:', runner.logfile.read_text())

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group cleanup')
    def test_sigterm_worker_cleans_its_detached_native_process_group(self):
        self._check_worker_cancellation(signal.SIGTERM, expected_code=143)

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group cleanup')
    def test_sigint_worker_cleans_its_detached_native_process_group(self):
        self._check_worker_cancellation(signal.SIGINT)

    def _check_worker_cancellation(self, requested_signal, expected_code=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            native_pid = root / 'native.pid'
            grandchild_pid = root / 'grandchild.pid'
            script = "import os,subprocess,sys,time;from pathlib import Path;Path(sys.argv[1]).write_text(str(os.getpid()));p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);Path(sys.argv[2]).write_text(str(p.pid));time.sleep(60)"
            controller = "from basis.execution import CommandRunner;import sys;CommandRunner({'timeout_seconds':60},sys.argv[1]).run([sys.executable,'-c',sys.argv[2],sys.argv[3],sys.argv[4]])"
            proc = subprocess.Popen([sys.executable, '-c', controller, str(root / 'log'), script,
                                     str(native_pid), str(grandchild_pid)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                wait_for(grandchild_pid)
                proc.send_signal(requested_signal)
                stdout, stderr = proc.communicate(timeout=10)
                self.assertNotEqual(proc.returncode, 0)
                if expected_code is not None:
                    self.assertEqual(proc.returncode, expected_code, stderr.decode())
                self.assertFalse(process_running(int(native_pid.read_text())))
                self.assertFalse(process_running(int(grandchild_pid.read_text())))
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
                for path in (native_pid, grandchild_pid):
                    if path.exists():
                        try:
                            os.kill(int(path.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass


if __name__ == '__main__':
    unittest.main()
