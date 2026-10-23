"""Small synchronous command boundary inside one Snakemake job.

This module does not activate environments, schedule, retry, cache, detach, or
launch another BaSIS workflow. The enclosing rule owns all of those decisions.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import selectors
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix="." + path.name, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class CommandTimeoutError(RuntimeError):
    """A native command exceeded its configured elapsed-time deadline."""


class _CommandSignal(SystemExit):
    def __init__(self, signum):
        self.signum = signum
        super().__init__(128 + signum)


def _positive_seconds(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite positive number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return number


def _stop_child(process):
    """Stop only the process/session created by this runner, including pipe holders.

    POSIX children get a dedicated session. Snakemake retains ownership of the
    enclosing worker; its TERM/INT is forwarded through the worker's handler.
    The process-group kill must also run when the leader already exited, because
    a grandchild can still be holding stdout/stderr open.
    """
    def send(signum):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signum)
            elif process.poll() is None:
                process.terminate() if signum == signal.SIGTERM else process.kill()
        except ProcessLookupError:
            pass

    send(signal.SIGTERM)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    finally:
        send(signal.SIGKILL)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        # Do not convert a bounded failure into an unbounded cleanup wait.
        pass


class DirectRunner:
    """Run a tool in the environment already selected by the Snakefile."""
    def __init__(self, config=None, logfile=None):
        self.config = dict(config or {})
        if self.config.get("command_prefix"):
            raise ValueError("Standalone rules own activation; command_prefix is unsupported")
        self.threads = int(self.config.get("threads", 1))
        if self.threads < 1:
            raise ValueError("threads must be positive")
        self.gpus = int(self.config.get("gpus", 0))
        self.timeout = _positive_seconds(self.config.get("timeout_seconds", 14400), "timeout_seconds")
        self.heartbeat = _positive_seconds(self.config.get("heartbeat_seconds", 30), "heartbeat_seconds")
        self.logfile = Path(logfile or "commands.log").resolve()
        self.logfile.parent.mkdir(parents=True, exist_ok=True)
        self.commands = []

    def run(self, argv, stdout_path=None, cwd=None, stream_stdout=False):
        del stream_stdout  # Tool output belongs to the rule log, never a second UI.
        argv = [str(value) for value in argv]
        if not argv or Path(argv[0]).name in {"conda", "mamba", "micromamba", "snakemake", "basis"}:
            raise ValueError("A rule helper cannot invoke another environment/workflow launcher")
        environment = dict(os.environ)
        # Keep Snakemake's activated PATH and Conda hooks intact. Controller-only
        # Python imports must not leak into native Python tools.
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            environment[name] = str(self.threads)
        started = time.monotonic()
        deadline = started + self.timeout
        next_heartbeat = started + self.heartbeat
        entry = {"argv": argv, "cwd": str(cwd) if cwd else None,
                 "started_at": datetime.now(timezone.utc).isoformat(),
                 "timeout_seconds": self.timeout, "status": "starting"}
        self.commands.append(entry)
        process, log, target = None, None, None
        handlers = {}
        failed = False

        def write_status(message):
            log.write(("\n# " + message + "\n").encode("utf-8"))
            log.flush()

        def remaining_wait():
            nonlocal next_heartbeat
            now = time.monotonic()
            if now >= deadline:
                raise CommandTimeoutError(
                    f"Command exceeded {self.timeout:g}s: {Path(argv[0]).name}; log: {self.logfile}")
            if now >= next_heartbeat:
                write_status(f"PID {process.pid} still running; elapsed={now-started:.1f}s; "
                             f"timeout={self.timeout:g}s")
                next_heartbeat = now + self.heartbeat
            return max(.001, min(deadline - now, next_heartbeat - now))

        def interrupted(signum, frame):
            raise _CommandSignal(signum)

        try:
            log = self.logfile.open("ab")
            log.write(("\n$ " + shlex.join(argv) + "\n").encode("utf-8"))
            log.flush()
            # Drain both streams continuously; stdout remains a separate data
            # channel, while progress records and stderr belong only to the log.
            with tempfile.TemporaryFile() as captured:
                if stdout_path:
                    path = Path(stdout_path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    target = path.open("wb")
                if threading.current_thread() is threading.main_thread():
                    for signum in (signal.SIGTERM, signal.SIGINT):
                        handlers[signum] = signal.signal(signum, interrupted)
                process = subprocess.Popen(argv, cwd=cwd, env=environment,
                                           stdout=target or subprocess.PIPE,
                                           stderr=subprocess.PIPE,
                                           start_new_session=(os.name == "posix"))
                entry.update(pid=process.pid, status="running")
                write_status(f"Started PID {process.pid}; timeout={self.timeout:g}s")
                with selectors.DefaultSelector() as streams:
                    if process.stdout is not None:
                        streams.register(process.stdout, selectors.EVENT_READ, "stdout")
                    streams.register(process.stderr, selectors.EVENT_READ, "stderr")
                    while streams.get_map():
                        for key, _ in streams.select(timeout=remaining_wait()):
                            block = os.read(key.fd, 65536)
                            if not block:
                                streams.unregister(key.fileobj)
                                continue
                            if key.data == "stdout":
                                captured.write(block)
                            log.write(block)
                            log.flush()
                # A command can close both streams before exiting; enforce the
                # same deadline and heartbeat while awaiting its exit status.
                while process.poll() is None:
                    try:
                        process.wait(timeout=remaining_wait())
                    except subprocess.TimeoutExpired:
                        continue
                entry["returncode"] = process.returncode
                if process.returncode:
                    raise RuntimeError(f"{Path(argv[0]).name} exited {process.returncode}; log: {self.logfile}")
                captured.seek(0)
                stdout = captured.read().decode("utf-8", errors="replace") if not stdout_path else ""
                entry["status"] = "completed"
                return subprocess.CompletedProcess(argv, process.returncode, stdout=stdout, stderr="")
        except BaseException as exc:
            failed = True
            entry.update(status=("timed_out" if isinstance(exc, CommandTimeoutError) else
                                 "interrupted" if isinstance(exc, (KeyboardInterrupt, _CommandSignal)) else "failed"),
                         error=(f"Received signal {exc.signum}" if isinstance(exc, _CommandSignal)
                                else str(exc) or type(exc).__name__))
            # A repeated scheduler signal must not interrupt child cleanup.
            for signum in handlers:
                signal.signal(signum, signal.SIG_IGN)
            if process is not None:
                _stop_child(process)
                entry["returncode"] = process.returncode
            raise
        finally:
            entry.update(finished_at=datetime.now(timezone.utc).isoformat(),
                         elapsed_seconds=round(time.monotonic() - started, 4))
            try:
                if log is not None:
                    write_status("Command finished: " + json.dumps(entry, sort_keys=True, allow_nan=False))
            except OSError as exc:
                entry["log_error"] = str(exc)
                if not failed:
                    raise
            finally:
                close_error = None
                # A failed flush/close must neither mask the primary failure
                # nor leave our temporary signal handlers installed.
                handles = ([process.stdout, process.stderr] if process is not None else []) + [target, log]
                try:
                    for handle in handles:
                        if handle is not None:
                            try:
                                handle.close()
                            except OSError as exc:
                                entry["close_error"] = str(exc)
                                close_error = close_error or exc
                finally:
                    for signum, previous in handlers.items():
                        signal.signal(signum, previous)
                if close_error is not None and not failed and sys.exc_info()[0] is None:
                    raise close_error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["run"])
    parser.add_argument("--timeout-seconds", type=float, default=14400)
    parser.add_argument("--heartbeat-seconds", type=float, default=30)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--log", required=True)
    parser.add_argument("--record", help="Write the command's final JSON record, including failures")
    parser.add_argument("--stdout", help="Write native stdout bytes to this file")
    parser.add_argument("--cwd")
    # Split explicitly: argparse.REMAINDER after the stage positional would
    # otherwise swallow wrapper options placed after ``run``.
    arguments = list(sys.argv[1:] if argv is None else argv)
    separator = arguments.index("--") if "--" in arguments else len(arguments)
    args = parser.parse_args(arguments[:separator])
    command = arguments[separator + 1:]
    if not command:
        parser.error("a native command is required after --")
    runner = None
    try:
        runner = DirectRunner({"threads": args.threads, "timeout_seconds": args.timeout_seconds,
                               "heartbeat_seconds": args.heartbeat_seconds}, args.log)
        runner.run(command, stdout_path=args.stdout, cwd=args.cwd)
        return 0
    except CommandTimeoutError as exc:
        print(str(exc), file=sys.stderr)
        return 124
    except _CommandSignal as exc:
        return exc.code
    except KeyboardInterrupt:
        return 130
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if args.record and runner is not None and runner.commands:
            write_json(args.record, runner.commands[-1])


if __name__ == "__main__":
    raise SystemExit(main())
