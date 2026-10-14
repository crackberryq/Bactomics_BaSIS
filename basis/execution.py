from __future__ import annotations

import os
import json
import shlex
import signal
import subprocess
import time
from pathlib import Path

from .util import now
from .progress import stage, emit
from .native_environment import native_child_environment


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

    def run(self, argv, stdout_path=None, cwd=None):
        label = "Native command: " + Path(str(argv[0])).name
        emit("Command log: " + str(self.logfile))
        with stage(label):
            return self._run_impl(argv, stdout_path, cwd)

    def _run_impl(self, argv, stdout_path=None, cwd=None):
        command = self.prefix + [str(x) for x in argv]
        if not command or any("\x00" in x for x in command):
            raise ValueError("Invalid executable arguments")
        env, isolation = native_child_environment(os.environ, self.prefix)
        for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            env[key] = str(self.threads)
        if self.gpus == 0:
            env["CUDA_VISIBLE_DEVICES"] = ""
        started = time.monotonic()
        metadata = {"argv": command, "started_at": now(), "cwd": str(cwd) if cwd else None,
                    "environment_isolation": isolation}
        self.commands.append(metadata)
        with self.logfile.open("a", encoding="utf-8") as log:
            log.write(f"\n[{metadata['started_at']}] {shlex.join(command)}\n")
            log.write("Environment isolation: " + json.dumps(isolation, sort_keys=True) + "\n")
            log.flush()
            capture = None
            try:
                if stdout_path is not None:
                    output = Path(stdout_path)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    capture = output.open("w", encoding="utf-8")
                proc = subprocess.Popen(command, stdout=capture if capture else subprocess.PIPE,
                                        stderr=log, text=True, cwd=cwd, env=env,
                                        start_new_session=(os.name == "posix"))
                try:
                    stdout, _ = proc.communicate(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    if os.name == "posix":
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                    proc.communicate()
                    raise RuntimeError(f"Command exceeded {self.timeout}s: {command[0]}")
                if stdout:
                    log.write(stdout)
                metadata["returncode"] = proc.returncode
                if proc.returncode:
                    raise RuntimeError(f"Command failed with exit code {proc.returncode}: {shlex.join(command)}; see {self.logfile}")
                return subprocess.CompletedProcess(command, proc.returncode, stdout or "", "")
            except Exception as exc:
                metadata["error"] = str(exc)
                raise
            finally:
                metadata["elapsed_seconds"] = round(time.monotonic() - started, 4)
                if capture:
                    capture.close()


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
