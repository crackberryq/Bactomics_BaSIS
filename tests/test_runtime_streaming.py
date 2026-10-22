"""Real subprocess logging tests; no native analysis or network access."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workflow/lib"))
from safety.runtime import DirectRunner
from safety import runtime


class RuntimeStreamingTests(unittest.TestCase):
    def test_both_streams_are_live_and_stdout_remains_parseable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            release = root / "release"
            logfile = root / "commands.log"
            script = (
                "import pathlib, sys, time\n"
                "print('{\"status\": \"ready\"}')\n"
                "print('diagnostic on stderr', file=sys.stderr)\n"
                "deadline=time.monotonic()+10\n"
                "while not pathlib.Path(sys.argv[1]).exists():\n"
                "    if time.monotonic()>deadline: raise SystemExit(9)\n"
                "    time.sleep(.02)\n"
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                task = pool.submit(DirectRunner(logfile=logfile).run,
                                   [sys.executable, "-c", script, str(release)])
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        text = logfile.read_text() if logfile.exists() else ""
                        # The printed JSON differs from the shell-quoted command.
                        if '\n{"status": "ready"}\n' in text and '\ndiagnostic on stderr\n' in text:
                            break
                        time.sleep(.02)
                    else:
                        self.fail("Output did not reach the log before child exit")
                    self.assertFalse(task.done())
                finally:
                    release.touch()
                result = task.result(timeout=5)
            self.assertEqual(result.stdout, '{"status": "ready"}\n')

    def test_redirected_binary_stdout_and_failure_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runner = DirectRunner(logfile=root / "commands.log")
            target = root / "output.bin"
            script = "import sys; sys.stdout.buffer.write(b'\\x00\\xff'); print('failed',file=sys.stderr); sys.exit(7)"
            with self.assertRaisesRegex(RuntimeError, "exited 7"):
                runner.run([sys.executable, "-c", script], stdout_path=target)
            self.assertEqual(target.read_bytes(), b"\x00\xff")
            self.assertIn("\nfailed\n", runner.logfile.read_text())
            self.assertEqual(runner.commands[0]["returncode"], 7)

    def test_log_write_failure_stops_the_child(self):
        with tempfile.TemporaryDirectory() as folder:
            logfile = Path(folder) / "commands.log"
            original_open, original_popen = Path.open, runtime.subprocess.Popen
            children = []

            class BrokenLog:
                def __init__(self):
                    self.file = original_open(logfile, "ab")
                    self.writes = 0
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    self.file.close()
                def write(self, block):
                    self.writes += 1
                    if self.writes > 1:
                        raise OSError("synthetic full log filesystem")
                    return self.file.write(block)
                def flush(self):
                    self.file.flush()
                def close(self):
                    self.file.close()

            def open_file(path, *args, **kwargs):
                return BrokenLog() if path == logfile else original_open(path, *args, **kwargs)

            def start_child(*args, **kwargs):
                child = original_popen(*args, **kwargs)
                children.append(child)
                return child

            with patch.object(Path, "open", open_file), patch.object(runtime.subprocess, "Popen", start_child):
                with self.assertRaisesRegex(OSError, "full log"):
                    DirectRunner(logfile=logfile).run([
                        sys.executable, "-c", "import time; print('progress'); time.sleep(2)"])
            self.assertEqual(len(children), 1)
            self.assertLess(children[0].returncode, 0, "The child must be killed before leaving the failed runner")


if __name__ == "__main__":
    unittest.main()
