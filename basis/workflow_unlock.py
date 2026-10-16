"""Explicit, study-scoped Snakemake lock recovery after a stopped invocation.

This command never prepares a new plan, runs a workflow rule, removes native
outputs, or signals processes. Snakemake itself removes its stale lock files.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from .config import load_config
from .run_control import matches_config, owned_descendants, process_table
from .util import hash_object


def _within(path, root, *, must_exist=False):
    path = Path(path).resolve(strict=must_exist)
    if not path.is_relative_to(root):
        raise ValueError("Recovery path escapes the selected output directory: " + str(path))
    return path


def _document(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Recovery document must be a mapping: " + str(path))
    digest = value.pop("content_sha256", None)
    if digest != hash_object(value):
        raise ValueError("Recovery document checksum mismatch: " + str(path))
    return value


def _saved_invocation(outdir):
    scheduler = _within(outdir / "provenance/scheduler", outdir, must_exist=True)
    contexts = list(scheduler.glob("*/run-context.json"))
    if not contexts:
        raise ValueError("No recorded Snakemake invocation exists for this output directory")
    # A damaged latest context is an error, never a reason to silently choose
    # a different invocation or infer an execution plan from the current code.
    path = max(contexts, key=lambda p: p.stat().st_mtime_ns)
    path = _within(path, outdir, must_exist=True)
    launch = path.parent
    run = _document(path)
    if (launch.parent != scheduler or run.get("engine") != "snakemake"
            or run.get("run_id") != launch.name
            or Path(run.get("scheduler_launch_dir", "")).resolve() != launch):
        raise ValueError("Saved scheduler launch identity does not match its directory")
    root = _within(run["scheduler_receipt_dir"], outdir, must_exist=True)
    match = re.fullmatch(r"state-([a-f0-9]{64})", root.name)
    if root.parent != scheduler or not match:
        raise ValueError("Saved scheduler state is not a valid plan namespace")
    plan_hash = match.group(1)
    plan_path = _within(outdir / "provenance/plans" / (plan_hash + ".json"), outdir, must_exist=True)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("Saved execution plan is not a mapping")
    document = {key: value for key, value in plan.items() if key not in {"plan_hash", "plan_path"}}
    if (plan.get("schema_version") != 1 or plan.get("plan_hash") != plan_hash
            or hash_object(document) != plan_hash
            or Path(plan.get("outdir", "")).resolve() != outdir
            or Path(plan.get("config", {}).get("outdir", "")).resolve() != outdir
            or run.get("source_hash") != plan.get("source_hash")
            or run.get("config_hash") != hash_object(plan.get("config"))):
        raise ValueError("Saved execution plan does not match this study and invocation")
    # The original source hash is intentionally checked against the recorded
    # context, not the installed release: upgrading must not prevent recovery.
    return plan, plan_path, root, launch


def _path_in_scope(raw, cwd, roots):
    if not isinstance(raw, str) or not raw or "\0" in raw:
        return False
    # Handle both argv forms without executing/interpreting shell expressions.
    if raw.startswith("-"):
        if "=" not in raw:
            return False
        raw = raw.split("=", 1)[1]
    path = Path(raw)
    if not path.is_absolute():
        path = Path(cwd) / path
    try:
        path = path.resolve()
        return any(path.is_relative_to(root) for root in roots)
    except (OSError, RuntimeError):
        return False


def _check_procfs():
    if not sys.platform.startswith("linux") or os.readlink("/proc/self") != str(os.getpid()):
        raise RuntimeError("Safe unlocking requires readable Linux/WSL process identities")


def _active_workers(config_path, outdir, plan):
    _check_procfs()
    table = process_table()
    if os.getpid() not in table:
        raise RuntimeError("Unable to inspect the current process; no locks removed")
    ancestors = set()
    pid = os.getpid()
    while pid in table and pid not in ancestors:
        ancestors.add(pid)
        pid = table[pid].ppid
    roots = [outdir]
    # An abruptly orphaned downloader can have neither the launcher's argv nor
    # a cwd inside results. A live worker in shared staging is conservatively
    # treated as potentially related, even if another study launched it.
    for spec in plan.get("bootstrap_resources", []):
        if spec.get("cache_directory"):
            roots.append((Path(spec["cache_directory"]) / "staging").resolve())
    matched = {}
    for pid, process in table.items():
        if pid in ancestors or not process.argv:
            continue
        # An idle terminal in results is not a running workflow worker.
        executable = Path(process.argv[0]).name.lstrip("-")
        interactive_shell = executable in {"bash", "sh", "zsh", "fish"} and (
            len(process.argv) == 1 or "-i" in process.argv)
        if interactive_shell:
            continue
        if (matches_config(process, config_path)
                or _path_in_scope(process.cwd, process.cwd, roots)
                or any(_path_in_scope(arg, process.cwd, roots) for arg in process.argv[1:])):
            matched[pid] = process
            continue
        # Native children can inherit only stdout/stderr into the study's log.
        # This catches an orphan whose command and cwd no longer identify it.
        for number in (1, 2):
            try:
                target = os.readlink(f"/proc/{pid}/fd/{number}")
            except (OSError, ValueError):
                continue
            if target.startswith("/") and _path_in_scope(target, process.cwd, roots):
                matched[pid] = process
                break
    return owned_descendants(table, matched)


def unlock_workflow(config_path):
    """Return an unlock receipt, refusing ambiguous or active output ownership."""
    config_path = Path(config_path).expanduser().resolve(strict=True)
    config = load_config(config_path)
    outdir = Path(config["outdir"]).resolve(strict=True)
    if not outdir.is_dir():
        raise ValueError("Configured output is not a directory")
    # Do not follow redirected lock trees or let Snakemake act outside the
    # exact configured output scope. No output-directory search is performed.
    snake = outdir / ".snakemake"
    if snake.is_symlink() or (snake / "locks").is_symlink():
        raise ValueError("Refusing a symlinked Snakemake lock directory")
    if not snake.is_dir():
        raise ValueError("No Snakemake state exists in the configured output directory")
    for entry in snake.iterdir():
        if entry.is_symlink():
            raise ValueError("Refusing redirected Snakemake state: " + str(entry))
    lock_path = outdir / ".basis.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("BaSIS lock path is not a regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another BaSIS process is using this output directory; no locks removed") from exc
        plan, plan_path, state, launch = _saved_invocation(outdir)
        active = _active_workers(config_path, outdir, plan)
        if active:
            summary = ", ".join(f"{pid} ({Path(p.argv[0]).name})" for pid, p in sorted(active.items()))
            raise RuntimeError("Active study or shared-resource workers found: " + summary + "; no locks removed")
        lock_dir = snake / "locks"
        lock_count = sum(p.is_file() for p in lock_dir.iterdir()) if lock_dir.is_dir() else 0
        if not lock_count:
            return {"status": "already_unlocked", "outdir": str(outdir), "native_analyses_run": False,
                    "saved_plan": str(plan_path), "removed_lock_files": 0}
        from .native_environment import native_child_environment
        env, _ = native_child_environment(os.environ, ["conda", "run", "--prefix", sys.prefix])
        env["BASIS_SCHEDULER_RUN_ID"] = state.name
        env["BASIS_SCHEDULER_LAUNCH_DIR"] = str(launch)
        env["BASIS_FORCE"] = "0"
        package_root = Path(__file__).resolve().parent
        bootstrap = ("import sys; sys.path.insert(0, " + repr(str(package_root.parent))
                     + "); from snakemake.cli import main; main()")
        command = [sys.executable, "-c", bootstrap, "all", "--snakefile", str(package_root / "resources/Snakefile"),
                   "--configfile", str(plan_path), "--directory", str(outdir), "--cores", "1", "--unlock"]
        completed = subprocess.run(command, cwd=outdir, env=env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        if completed.returncode:
            raise RuntimeError("Snakemake unlock failed; no analyses were run:\n" + completed.stdout[-16000:])
        if lock_dir.exists() and any(lock_dir.iterdir()):
            raise RuntimeError("Snakemake returned success but lock files remain; do not restart yet")
        return {"status": "unlocked", "outdir": str(outdir), "saved_plan": str(plan_path),
                "saved_run": launch.name, "removed_lock_files": lock_count,
                "native_analyses_run": False, "command": command,
                "snakemake_output": completed.stdout.strip()}
    finally:
        os.close(fd)
