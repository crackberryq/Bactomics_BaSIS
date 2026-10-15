"""Inspect or stop one local BaSIS invocation without signalling unrelated jobs.

Linux/WSL procfs supplies process identities and parentage. Stopping is opt-in,
limited to the selected same-user launcher and its observed descendants, and
checks process start times before every signal. Old unowned orphans are never
inferred from a program name, GPU usage, or a similar output path.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import time

from .config import load_config


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    uid: int
    start_time: str
    state: str
    argv: tuple[str, ...]
    cwd: str

    @property
    def identity(self):
        return self.pid, self.uid, self.start_time


def read_process(pid, proc_root=Path("/proc")):
    """Read an identity twice so exec/exit/reuse races fail closed."""
    root = Path(proc_root) / str(pid)
    try:
        first = (root / "stat").read_text().rsplit(")", 1)[1].split()
        uid_line = next(line for line in (root / "status").read_text().splitlines()
                        if line.startswith("Uid:"))
        uid = int(uid_line.split()[1])
        argv = tuple(part.decode("utf-8", errors="surrogateescape")
                     for part in (root / "cmdline").read_bytes().split(b"\0") if part)
        cwd = os.readlink(root / "cwd")
        second = (root / "stat").read_text().rsplit(")", 1)[1].split()
        if (first[19] != second[19] or first[1] != second[1]
                or second[0] == "Z" or root.stat().st_uid != uid):
            return None
        return Process(int(pid), int(second[1]), uid, second[19], second[0], argv, cwd)
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def process_table(proc_root=Path("/proc"), uid=None):
    uid = os.getuid() if uid is None else uid
    table = {}
    for path in Path(proc_root).iterdir():
        if path.name.isdecimal():
            process = read_process(int(path.name), proc_root)
            if process is not None and process.uid == uid:
                table[process.pid] = process
    return table


def _run_arguments(argv):
    """Recognize console-script or python -m basis launchers, never shell text."""
    for index, token in enumerate(argv):
        if Path(token).name == "basis" and index + 1 < len(argv) and argv[index + 1] == "run":
            # The token must be the executable or the Python script/module.
            if index == 0 or (index == 1 and Path(argv[0]).name.startswith("python")):
                return argv[index + 2:]
        if (token == "-m" and index + 2 < len(argv) and argv[index + 1:index + 3] == ("basis", "run")
                and Path(argv[0]).name.startswith("python")
                and all(value.startswith("-") for value in argv[1:index])):
            return argv[index + 3:]
    return None


def matches_config(process, config_path):
    args = _run_arguments(process.argv)
    if args is None:
        return False
    values = []
    for index, token in enumerate(args):
        if token in {"-c", "--config"} and index + 1 < len(args):
            values.append(args[index + 1])
        elif token.startswith("--config="):
            values.append(token.split("=", 1)[1])
        elif token.startswith("-c") and len(token) > 2:
            values.append(token[2:])
    if len(values) != 1:
        return False
    try:
        candidate = Path(values[0])
        if not candidate.is_absolute():
            candidate = Path(process.cwd) / candidate
        return candidate.resolve(strict=True) == Path(config_path).resolve(strict=True)
    except (OSError, RuntimeError):
        return False


def select_launcher(table, config_path, pid=None):
    matches = [process for process in table.values() if matches_config(process, config_path)]
    if pid is not None:
        matches = [process for process in matches if process.pid == pid]
    if len(matches) > 1:
        raise ValueError("Multiple matching BaSIS launchers: " + ", ".join(str(p.pid) for p in matches)
                         + ". Inspect them and select one with --pid.")
    if not matches:
        raise ValueError("No matching active BaSIS launcher was found. No signals sent. "
                         "Already-orphaned processes cannot be safely assigned to this run; "
                         "share the diagnostic output before stopping them.")
    return matches[0]


def owned_descendants(table, known):
    """Add only processes connected to a currently verified known identity."""
    result = {pid: item for pid, item in known.items()
              if pid in table and table[pid].identity == item.identity}
    while True:
        extra = {pid: item for pid, item in table.items()
                 if pid not in result and item.ppid in result
                 and item.uid == result[item.ppid].uid}
        if not extra:
            return result
        result.update(extra)


def _signal_verified(process, sig):
    """Use pidfds where available to close the PID-reuse race after verification."""
    if process.uid != os.getuid() or process.pid == os.getpid():
        return False
    current = read_process(process.pid)
    if current is None or current.identity != process.identity:
        return False
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("Safe stopping requires Linux pidfd support; no fallback PID-only signal is used")
    try:
        fd = os.pidfd_open(process.pid)
    except ProcessLookupError:
        return False
    try:
        current = read_process(process.pid)
        if current is None or current.identity != process.identity:
            return False
        signal.pidfd_send_signal(fd, sig)
        return True
    except ProcessLookupError:
        return False
    finally:
        os.close(fd)


def stop_invocation(launcher, *, interrupt_grace=3.0, terminate_grace=3.0):
    if os.readlink("/proc/self") != str(os.getpid()):
        raise RuntimeError("Procfs PID namespace differs from this process; no signals sent")
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("Safe stopping requires Linux pidfd support; no signals sent")
    table = process_table()
    current = table.get(launcher.pid)
    if (current is None or current.identity != launcher.identity
            or current.argv != launcher.argv or current.cwd != launcher.cwd):
        raise ValueError("Launcher changed during inspection; no signals sent. Inspect again.")
    known = owned_descendants(table, {launcher.pid: launcher})
    signalled = []

    def send(process, sig):
        if _signal_verified(process, sig):
            signalled.append({"pid": process.pid, "start_time": process.start_time, "signal": sig.name})

    def wait_owned(seconds):
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            current = owned_descendants(process_table(), known)
            known.update(current)
            if not current or time.monotonic() >= deadline:
                return current
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    # Snapshot detached native children before the launcher can exit/reparent them.
    send(launcher, signal.SIGINT)
    survivors = wait_owned(interrupt_grace)
    for process in sorted(survivors.values(), key=lambda p: p.pid == launcher.pid):
        send(process, signal.SIGTERM)
    survivors = wait_owned(terminate_grace)
    for process in sorted(survivors.values(), key=lambda p: p.pid == launcher.pid):
        send(process, signal.SIGKILL)
    survivors = wait_owned(1.0)
    return {"status": "stopped" if not survivors else "still_running",
            "owned_pids": sorted(known), "signals": signalled,
            "remaining_pids": sorted(survivors),
            "untracked_orphans": "not_claimed_or_signalled"}


def inspect_logs(config_path, tail=20):
    config = load_config(config_path)
    outdir = Path(config["outdir"])
    paths = [outdir / "snakemake.log"]
    # Scoped to the configured study only; no home-directory search.
    paths += sorted(outdir.glob("results/*/pathogenfinder2/attempts/*/commands.log"),
                    key=lambda p: p.stat().st_mtime_ns)[-1:]
    paths += sorted(outdir.glob("provenance/scheduler/*/bootstrap/**/*.log"),
                    key=lambda p: p.stat().st_mtime_ns)[-3:]
    print("Config:", config_path)
    print("Results:", outdir)
    for path in paths:
        if not path.is_file():
            continue
        print("\nLOG:", path)
        # Avoid loading a long-running tool's entire output into memory.
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 32768))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
        if tail:
            print("\n".join(lines[-tail:]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", required=True)
    parser.add_argument("--pid", type=int, help="Choose one exact matching launcher when several exist")
    parser.add_argument("--stop", action="store_true", help="Stop only the verified launcher and its observed descendants")
    parser.add_argument("--tail", type=int, default=20, help="Log lines shown per file (0 to 100)")
    args = parser.parse_args(argv)
    if not 0 <= args.tail <= 100:
        parser.error("--tail must be between 0 and 100")
    config_path = Path(args.config).expanduser().resolve(strict=True)
    inspect_logs(config_path, args.tail)
    try:
        table = process_table()
        launcher = select_launcher(table, config_path, args.pid)
        owned = owned_descendants(table, {launcher.pid: launcher})
        print("\nACTIVE LAUNCHER:", launcher.pid)
        for process in sorted(owned.values(), key=lambda p: p.pid):
            # argv is deliberately visible for locally diagnosing the running stage.
            command = json.dumps(process.argv, ensure_ascii=True)
            if len(command) > 1200:
                command = command[:1200] + " ... [see command log for full arguments]"
            print("PID", process.pid, "| parent", process.ppid, "| state", process.state,
                  "|", command)
        if args.stop:
            result = stop_invocation(launcher)
            print("\nSTOP RESULT:", json.dumps(result, sort_keys=True))
            return 0 if result["status"] == "stopped" else 2
        print("\nInspection only. No signals sent; use --stop to stop this invocation.")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print("\nRun control:", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
