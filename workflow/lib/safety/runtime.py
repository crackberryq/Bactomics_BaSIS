"""Small synchronous command boundary inside one Snakemake job.

This module does not activate environments, schedule, retry, cache, detach, or
launch another BaSIS workflow. The enclosing rule owns all of those decisions.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
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


class DirectRunner:
    """Run a tool in the environment already selected by the Snakefile."""
    def __init__(self, config=None, logfile=None):
        self.config = dict(config or {})
        if self.config.get("command_prefix"):
            raise ValueError("Standalone rules own activation; command_prefix is unsupported")
        self.threads = int(self.config.get("threads", 1))
        self.gpus = int(self.config.get("gpus", 0))
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
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            environment[name] = str(self.threads)
        entry = {"argv": argv, "cwd": str(cwd) if cwd else None,
                 "started_at": datetime.now(timezone.utc).isoformat()}
        self.commands.append(entry)
        with self.logfile.open("a", encoding="utf-8") as log:
            log.write("\n$ " + shlex.join(argv) + "\n")
            log.flush()
            # Stream stderr directly so a live log remains useful. Capture stdout
            # in a temporary file: return-value parsers need it, large tool output
            # need not occupy RAM while the process is running.
            if stdout_path:
                target = Path(stdout_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("w", encoding="utf-8") as output:
                    result = subprocess.run(argv, cwd=cwd, env=environment,
                                            stdout=output, stderr=log, text=True)
                stdout = ""
            else:
                with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output:
                    result = subprocess.run(argv, cwd=cwd, env=environment,
                                            stdout=output, stderr=log, text=True)
                    output.seek(0)
                    stdout = output.read()
                    log.write(stdout)
            entry.update(returncode=result.returncode,
                         finished_at=datetime.now(timezone.utc).isoformat())
        if result.returncode:
            raise RuntimeError(f"{Path(argv[0]).name} exited {result.returncode}; log: {self.logfile}")
        return subprocess.CompletedProcess(argv, result.returncode, stdout=stdout, stderr="")
