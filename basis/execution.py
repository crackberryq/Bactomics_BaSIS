from __future__ import annotations

import os
import codecs
import contextlib
import json
import shlex
import signal
import subprocess
import time
import threading
import sys
from pathlib import Path

from .util import now
from .progress import enabled, emit
from .native_environment import native_child_environment, native_command


class CommandRunner:
    """Checked, shell-free external execution with bounded runtime and logs."""

    def __init__(self, config: dict, logfile: str | Path):
        self.prefix = list(config.get("command_prefix", []))
        self.timeout = int(config.get("timeout_seconds", 14400))
        self.logfile = Path(logfile)
        self.logfile.parent.mkdir(parents=True, exist_ok=True)
        self.logfile.touch(exist_ok=True)
        self.commands = []
        self.threads = int(config.get("threads", 1))
        self.gpus = config.get("gpus")

    def run(self, argv, stdout_path=None, cwd=None, *, stream_stdout=False):
        """Run with live stderr and continuously flushed logs.

        Stdout is a data channel by default: FASTA/JSON/version callers receive
        exactly that stream, and redirected files retain the original bytes.
        Only callers whose stdout is human progress opt in to terminal display.
        Terminal diagnostics always go to stderr, leaving CLI JSON stdout clean.
        """
        label = "Native command: " + Path(str(argv[0])).name
        started = time.monotonic()
        emit(label + " — started | log: " + str(self.logfile) +
             " | timeout: " + str(self.timeout) + "s")
        try:
            result = self._run_impl(argv, stdout_path, cwd, stream_stdout=stream_stdout)
        except BaseException:
            emit(f"{label} — stopped ({time.monotonic()-started:.1f}s); see {self.logfile}")
            raise
        emit(f"{label} — finished ({time.monotonic()-started:.1f}s)")
        return result

    def _run_impl(self, argv, stdout_path=None, cwd=None, *, stream_stdout=False):
        command = native_command(self.prefix, [str(x) for x in argv])
        if not command or any("\x00" in x for x in command):
            raise ValueError("Invalid executable arguments")
        if self.timeout <= 0:
            raise ValueError("Command timeout_seconds must be positive")
        env, isolation = native_child_environment(os.environ, self.prefix)
        for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            env[key] = str(self.threads)
        # Python tools otherwise buffer stdout for minutes when run under pipes.
        env["PYTHONUNBUFFERED"] = "1"
        if self.gpus == 0:
            env["CUDA_VISIBLE_DEVICES"] = ""
        started = time.monotonic()
        deadline = started + self.timeout
        metadata = {"argv": command, "started_at": now(), "cwd": str(cwd) if cwd else None,
                    "environment_isolation": isolation, "timeout_seconds": self.timeout,
                    "live_stderr": enabled(), "live_stdout": bool(stream_stdout and enabled())}
        self.commands.append(metadata)
        with self.logfile.open("ab") as log, contextlib.ExitStack() as stack:
            log.write((f"\n[{metadata['started_at']}] {shlex.join(command)}\n" +
                       "Environment isolation: " + json.dumps(isolation, sort_keys=True) + "\n").encode("utf-8"))
            log.flush()
            capture = None
            proc = None
            threads = []
            chunks = []
            errors = []
            lock = threading.Lock()
            terminal = sys.stderr
            live = enabled()

            def pump(pipe, keep, display):
                decoder = codecs.getincrementaldecoder("utf-8")("replace")
                try:
                    while True:
                        block = pipe.read1(65536)
                        if not block:
                            break
                        with lock:
                            log.write(block)
                            log.flush()
                            if keep:
                                chunks.append(block)
                        # Never hold the log lock while writing to a terminal:
                        # a paused pipe consumer must not deadlock timeout cleanup.
                        if display and live:
                            terminal.write(decoder.decode(block))
                            terminal.flush()
                    if display and live:
                        terminal.write(decoder.decode(b"", final=True))
                        terminal.flush()
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    pipe.close()

            def stop_group():
                if proc is None:
                    return
                # A child can exit while its grandchildren still hold our pipes.
                # Kill its process group even if poll() says the leader exited.
                try:
                    if os.name == "posix":
                        os.killpg(proc.pid, signal.SIGKILL)
                    elif proc.poll() is None:
                        proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

            def terminate(signum, frame):
                raise SystemExit(128 + signum)

            try:
                if stdout_path is not None:
                    output = Path(stdout_path)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    capture = stack.enter_context(output.open("wb"))
                # Scoped SIGTERM cleanup is important when Snakemake cancels a
                # worker: native tools have their own session for timeout cleanup.
                if threading.current_thread() is threading.main_thread():
                    previous = signal.signal(signal.SIGTERM, terminate)
                    stack.callback(signal.signal, signal.SIGTERM, previous)
                proc = subprocess.Popen(command, stdout=capture if capture else subprocess.PIPE,
                                        stderr=subprocess.PIPE, cwd=cwd, env=env,
                                        start_new_session=(os.name == "posix"))
                metadata["pid"] = proc.pid
                emit("Native process PID: " + str(proc.pid))
                for pipe, keep, display in ((proc.stdout, True, stream_stdout),
                                             (proc.stderr, False, True)):
                    if pipe is not None:
                        thread = threading.Thread(target=pump, args=(pipe, keep, display), daemon=True)
                        threads.append(thread)
                        thread.start()
                proc.wait(timeout=max(.001, deadline - time.monotonic()))
                for thread in threads:
                    thread.join(timeout=max(0, deadline - time.monotonic()))
                if any(thread.is_alive() for thread in threads):
                    raise subprocess.TimeoutExpired(command, self.timeout)
                if errors:
                    raise RuntimeError("Unable to capture native command output: " + str(errors[0])) from errors[0]
                metadata["returncode"] = proc.returncode
                if proc.returncode:
                    raise RuntimeError(f"Command failed with exit code {proc.returncode}: {shlex.join(command)}; see {self.logfile}")
                stdout = b"".join(chunks).decode("utf-8", errors="replace")
                return subprocess.CompletedProcess(command, proc.returncode, stdout, "")
            except BaseException as exc:
                if threading.current_thread() is threading.main_thread():
                    # Scheduler cancellation may deliver TERM both via its
                    # process group and directly to a captured worker identity.
                    # A second signal must not interrupt native-child cleanup.
                    previous_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
                    stack.callback(signal.signal, signal.SIGINT, previous_int)
                    previous_cleanup_term = signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    stack.callback(signal.signal, signal.SIGTERM, previous_cleanup_term)
                stop_group()
                if proc is not None:
                    metadata["returncode"] = proc.returncode
                if isinstance(exc, subprocess.TimeoutExpired):
                    message = f"Command exceeded {self.timeout}s; process group stopped: {command[0]}; see {self.logfile}"
                    metadata["error"] = message
                    raise RuntimeError(message) from exc
                metadata["error"] = str(exc) or type(exc).__name__
                raise
            finally:
                for thread in threads:
                    thread.join(timeout=5)
                metadata["elapsed_seconds"] = round(time.monotonic() - started, 4)
                with lock:
                    log.write(("\nCommand finished: " + json.dumps({key: metadata[key] for key in
                        ("returncode", "elapsed_seconds", "error") if key in metadata}) + "\n").encode("utf-8"))
                    log.flush()


def probe_version(module: str, config: dict, runner: CommandRunner) -> dict:
    flags = config.get("version_args")
    if flags is None:
        if module == "rgi":
            flags = ["main", "--version"]
        elif module == "amrfinder":
            flags = ["-V"]
            if config.get("database"):
                flags += ["--database", config["database"]]
        else:
            flags = ["--version"]
    if not flags:
        return {"status": "not_assessed", "declared_version": config.get("version")}
    try:
        result = runner.run([config["executable"]] + list(flags))
        return {"status": "completed", "output": result.stdout.strip()[:16000],
                "declared_version": config.get("version")}
    except Exception as exc:
        return {"status": "failed", "error": str(exc), "declared_version": config.get("version")}
