"""Persistent, single-host jobs shared by the CLI and optional local interface.

Only typed BaSIS operations are accepted. A Linux flock serializes workspace
jobs; it survives browser reruns and is held by the worker and its CLI child.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from . import __version__
from .config import load_config
from .environment_setup import recipe_names, workspace_path
from .util import atomic_json, sha256_file

ACTIVE = {"queued", "running"}


def now():
    return datetime.now(timezone.utc).isoformat()


def job_root(workspace):
    return workspace_path(workspace) / "jobs"


def job_path(workspace, job_id):
    if not re.fullmatch(r"[a-f0-9]{24}", job_id):
        raise ValueError("Invalid job identifier")
    return job_root(workspace) / job_id


@contextmanager
def lock(path, blocking=True):
    if sys.platform != "linux":
        raise ValueError("Persistent jobs currently require Linux or WSL2")
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield stream.fileno()
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _identity(pid):
    try:
        # Some containers expose procfs from a different PID namespace.
        # Never use another process's /proc entry as this worker's identity.
        if os.readlink("/proc/self") != str(os.getpid()):
            return None
        raw = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if raw[0] == "Z":
            return None
        return [Path("/proc/sys/kernel/random/boot_id").read_text().strip(), raw[19]]
    except (OSError, IndexError):
        return None


def _alive(pid):
    try:
        if not pid:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def read_job(workspace, job_id):
    directory = job_path(workspace, job_id)
    data = json.loads((directory / "status.json").read_text())
    if data["state"] in ACTIVE:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(data["created_at"])).total_seconds()
        if data["state"] == "queued" and age < 30:
            return data  # allow worker startup and its own identity write
        identity = _identity(data.get("worker_pid"))
        if not identity or identity != data.get("worker_identity"):
            # Do not relabel a child still holding the execution lock as finished.
            try:
                with lock(job_root(workspace) / "execution.lock", blocking=False):
                    latest = json.loads((directory / "status.json").read_text())
                    if latest["state"] not in ACTIVE:
                        return latest
                    data = {**data, "state": "interrupted", "message":
                            "Worker no longer running. Inspect saved logs before starting a new attempt."}
            except BlockingIOError:
                if _alive(data.get("worker_pid")) and not identity:
                    return data  # procfs unavailable; process + held lock agree
                data = {**data, "state": "orphaned_active", "message":
                        "Worker disconnected; a process still holds the workspace execution lock."}
    return data


def list_jobs(workspace):
    root = job_root(workspace)
    records = []
    for p in root.glob("*/status.json"):
        if re.fullmatch(r"[a-f0-9]{24}", p.parent.name):
            try:
                records.append(read_job(workspace, p.parent.name))
            except (OSError, ValueError, KeyError):
                continue
    return sorted(records, key=lambda r: r["created_at"], reverse=True)


def normalized_request(request):
    r = dict(request)
    allowed = {"kind", "config", "deep", "extend", "cores", "memory_mb", "gpus",
               "envs", "create", "retry_failed", "engine", "prefix_root", "file", "sheet", "root", "input_mode", "base_config", "preview", "results",
               "resources", "execute", "conda_prefix", "environment_backend"}
    if set(r) - allowed:
        raise ValueError("Unknown job options: " + ", ".join(sorted(set(r) - allowed)))
    if r.get("kind") not in {"run", "doctor", "demo", "setup", "resources", "import", "audit", "panel"}:
        raise ValueError("Unsupported job operation")
    for key in ("config", "file", "base_config"):
        if r.get(key):
            r[key] = str(Path(r[key]).expanduser().resolve(strict=True))
    if r.get("root"):
        r["root"] = str(Path(r["root"]).expanduser().resolve(strict=True))
    if r["kind"] in {"run", "doctor", "audit", "resources"} and not r.get("config"):
        raise ValueError("Select an existing configuration")
    if r["kind"] == "import" and not r.get("file"):
        raise ValueError("Select a sample sheet")
    for key in ("deep", "extend", "create", "preview", "retry_failed", "execute"):
        if key in r and not isinstance(r[key], bool):
            raise ValueError(key + " must be boolean")
    for key in ("cores", "memory_mb", "gpus"):
        if key in r and (type(r[key]) is not int or r[key] < (0 if key == "gpus" else 1)):
            raise ValueError(key + " has an invalid budget")
    if r.get("gpus", 0) > 1:
        raise ValueError("At most one scheduled GPU is supported")
    if r.get("input_mode", "auto") not in {"auto", "external", "hybas"}:
        raise ValueError("Invalid input mode")
    if "engine" in r and (r["kind"] not in {"run", "demo", "setup"} or r["engine"] not in {"local", "snakemake"}):
        raise ValueError("Engine must be local or snakemake for a run, demo or setup job")
    if r["kind"] in {"run", "setup"}:
        r.setdefault("engine", "snakemake")
    if r.get("retry_failed") and r["kind"] != "setup":
        raise ValueError("retry_failed is only valid for environment setup")
    if "prefix_root" in r:
        if r["kind"] != "setup" or not isinstance(r["prefix_root"], str) or not r["prefix_root"].strip():
            raise ValueError("prefix_root must be an environment parent directory for setup")
        r["prefix_root"] = str(workspace_path(r["prefix_root"]))
    if r["kind"] == "setup":
        if not r.get("envs") or any(v not in recipe_names() for v in r["envs"]):
            raise ValueError("Select valid environment recipes")
        r["envs"] = sorted(set(r["envs"]))
    if any(key in r for key in ("resources", "execute")) and r["kind"] != "resources":
        raise ValueError("Resource options are only valid for resource preparation")
    if "environment_backend" in r:
        if r["kind"] not in {"run", "doctor"} or r["environment_backend"] not in {"auto", "external", "managed"}:
            raise ValueError("Environment deployment must be auto, external or managed for a run or preflight")
    if "conda_prefix" in r:
        if r["kind"] not in {"run", "doctor", "resources"} or not isinstance(r["conda_prefix"], str) or not r["conda_prefix"].strip():
            raise ValueError("conda_prefix must be a writable environment cache directory for analysis or resources")
        r["conda_prefix"] = str(workspace_path(r["conda_prefix"]))
    if r["kind"] == "resources":
        names = r.get("resources")
        if not isinstance(names, list) or not names or any(
                not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) for name in names):
            raise ValueError("Select named resources from the resource configuration")
        r["resources"] = sorted(set(names))
    if r["kind"] == "panel":
        if not isinstance(r.get("results"), list) or not r["results"]:
            raise ValueError("Select result directories")
        r["results"] = [str(Path(p).expanduser().resolve(strict=True)) for p in r["results"]]
    return r


def _prepare(directory, request, workspace):
    import yaml
    r = request
    kind = r["kind"]
    command = [sys.executable, "-I", "-m", "basis"]
    outdir = None
    if kind == "audit":
        outdir = directory / "audit"
        command += ["audit-reproducibility", "-c", r["config"], "--outdir", str(outdir), "-v"]
    elif kind in {"run", "doctor"}:
        cfg = load_config(r["config"])
        cfg.pop("config_path", None)
        for section in list(cfg["modules"].values()) + [cfg["deep"]]:
            executable = section.get("executable", "")
            prefix = section.get("command_prefix", [])
            if executable and "/" in executable and not Path(executable).is_absolute():
                raise ValueError("Use an absolute executable path before starting a background job: " + executable)
            for i, token in enumerate(prefix):
                if ("/" in token and not token.startswith("/") and not token.startswith("-")) or \
                   (i > 0 and prefix[i-1] in {"-p", "--prefix"} and not Path(token).is_absolute()) or \
                   token.startswith("--prefix=") and not Path(token.split("=", 1)[1]).is_absolute():
                    raise ValueError("Use absolute command-prefix paths before starting a background job: " + token)
        outdir = directory / "results"
        cfg["outdir"] = str(outdir)
        # A private cache prevents two studies writing the same placement bundle.
        cfg["deep"]["placement_cache_dir"] = str(directory / "placement-cache")
        for key in ("cores", "memory_mb", "gpus", "environment_backend", "conda_prefix"):
            if key in r:
                cfg["execution"][key] = r[key]
        if r.get("environment_backend") == "external":
            # This explicit UI/CLI-job choice means no stage may create an
            # environment, including stages with a prior managed override.
            for section in list(cfg["modules"].values()) + [cfg["deep"]]:
                section["environment_backend"] = "external"
        config = directory / "config.yaml"
        config.write_text(yaml.safe_dump(cfg, sort_keys=False))
        command += [kind, "-c", str(config), "-v"]
        command += (["-d"] if r.get("deep") else []) + (["-e"] if r.get("extend") else [])
        if kind == "run":
            command += ["--output", "summary", "--engine", r.get("engine", "snakemake")]
        else:
            outdir = directory / "preflight"
            command += ["--report-dir", str(outdir)]
    elif kind == "demo":
        outdir = directory / "demo"
        command += ["demo", "--outdir", str(outdir), "-d", "-e", "-v", "--output", "summary"]
        if r.get("engine"):
            command += ["--engine", r["engine"]]
    elif kind == "setup":
        command += ["setup-environments", "--workspace", str(workspace),
                    "--engine", r.get("engine", "snakemake"), "--cores", str(r.get("cores", 1))]
        if r.get("prefix_root"):
            command += ["--prefix-root", r["prefix_root"]]
        for name in r["envs"]:
            command += ["--env", name]
        if r.get("create"):
            outdir = directory / "setup"
            command += ["--create", "--report-dir", str(outdir)]
        if r.get("retry_failed"):
            command += ["--retry-failed"]
    elif kind == "resources":
        from .resource_setup import load_resource_config
        loaded = load_resource_config(r["config"])
        snapshot = {"schema_version": 1, "resources": {
            name: {key: value for key, value in row.items() if key not in {"name", "scope"}}
            for name, row in loaded["resources"].items()}}
        config = directory / "config.yaml"
        config.write_text(yaml.safe_dump(snapshot, sort_keys=False))
        command += ["prepare-resources", "-c", str(config), "--cores", str(r.get("cores", 1))]
        for name in r["resources"]:
            command += ["--resource", name]
        if r.get("conda_prefix"):
            command += ["--conda-prefix", r["conda_prefix"]]
        if r.get("execute"):
            outdir = directory / "resources"
            command += ["--execute", "--report-dir", str(outdir)]
    elif kind == "panel":
        outdir = directory / "panel"
        command += ["control-panel", "--outdir", str(outdir)]
        for source in r["results"]:
            command += ["--results", source]
    elif kind == "import":
        command += ["import-samples", "--file", r["file"], "--input-mode", r.get("input_mode", "auto"), "-v"]
        for key in ("root", "sheet", "base_config"):
            if r.get(key):
                command += ["--" + key.replace("_", "-"), r[key]]
        if r.get("preview", True):
            command += ["--dry-run"]
        else:
            outdir = directory / "study"
            command += ["--directory", str(outdir)]
    return command, str(outdir) if outdir else None


def submit(workspace, request, *, new_attempt=False):
    workspace = workspace_path(workspace)
    root = job_root(workspace)
    r = normalized_request(request)
    hashes = {k: sha256_file(r[k]) for k in ("config", "file", "base_config") if r.get(k)}
    fingerprint = hashlib.sha256(json.dumps([__version__, r, hashes], sort_keys=True).encode()).hexdigest()
    with lock(root / "submit.lock"):
        previous = list_jobs(workspace)
        for old in previous:
            if old["fingerprint"] == fingerprint and (not new_attempt or old["state"] in ACTIVE):
                return {**old, "reconnected": True}
        if any(p["state"] in ACTIVE | {"orphaned_active"} for p in previous):
            raise ValueError("A workspace job is already active. Reconnect to it in Jobs.")
        # Also catches an active orphan not represented by a surviving status file.
        with lock(root / "execution.lock", blocking=False):
            pass
        identifier = uuid.uuid4().hex[:24]
        directory = root / identifier
        directory.mkdir(parents=True, exist_ok=False)
        command, outdir = _prepare(directory, r, workspace)
        for k, digest in hashes.items():
            if sha256_file(r[k]) != digest:
                raise ValueError("Source changed while preparing job: " + k)
        state = {"schema": "basis-local-job-v1", "id": identifier, "created_at": now(),
                 "state": "queued", "kind": r["kind"], "request": r, "source_hashes": hashes,
                 "basis_version": __version__, "fingerprint": fingerprint,
                 "command": command, "outdir": outdir, "directory": str(directory),
                 "log": str(directory / "console.log"), "exit_code": None}
        atomic_json(directory / "status.json", state)
        try:
            with (directory / "worker.log").open("ab") as output:
                child = subprocess.Popen([sys.executable, "-I", "-m", "basis.jobs", "_work",
                        "--workspace", str(workspace), "--job", identifier], cwd=workspace,
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        start_new_session=True)
            state.update(worker_pid=child.pid, worker_identity=_identity(child.pid))
            atomic_json(directory / "status.json", state)
        except OSError as exc:
            state.update(state="failed", message=str(exc), finished_at=now())
            atomic_json(directory / "status.json", state)
            raise
        return state


def outcome(kind, code, outdir):
    if kind == "resources" and outdir:
        summary = Path(outdir) / "summary.json"
        if not summary.is_file():
            return "failed", "No resource preparation summary was produced; inspect the log."
        result = json.loads(summary.read_text())
        if code == 0 and result.get("status") == "completed":
            return "completed", "Selected resource preparation finished. Check resource scope and run study preflight before analysis."
        return "needs_attention", "Resource preparation needs attention; inspect resources/summary.json and the log."
    if kind == "setup" and outdir:
        summary = Path(outdir) / "summary.json"
        if not summary.is_file():
            return "failed", "No setup summary was produced; inspect the log."
        result = json.loads(summary.read_text())
        if code == 0 and result.get("status") == "completed":
            return "completed", "Selected tool environments verified. Databases and models require separate readiness checks."
        return "needs_attention", "Environment setup needs attention; inspect setup/summary.json and the log."
    evidence_path = Path(outdir or ".") / ("results/evidence.json" if kind == "demo" else "evidence.json")
    if kind in {"run", "demo"} and evidence_path.is_file():
        evidence = json.loads(evidence_path.read_text())
        completeness = evidence.get("run", {}).get("completeness")
        if code == 0 and completeness == "completed_requested_scope":
            return "completed", completeness
        if code == 2 and completeness == "incomplete":
            return "incomplete", "Review module execution and deferred candidates in the report."
        return "failed", "Process outcome and saved evidence disagree; inspect the log."
    if kind in {"run", "demo"}:
        return "failed", "No final evidence file was produced; inspect the log."
    if code == 0:
        return "completed", "Requested operation finished."
    if kind == "doctor" or (kind == "import" and code == 2):
        return "needs_attention", "Preflight or input checks reported issues; inspect the log."
    return "failed", "Command failed; inspect the saved log."


def scheduler_progress(log_text):
    """Extract observed scheduler progress without inventing analysis completion."""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", log_text)
    rules = list(re.finditer(r"(?m)^\s*(?:localrule|rule|checkpoint)\s+([A-Za-z][A-Za-z0-9_]*):\s*$", text))
    steps = list(re.finditer(r"\b(\d+) of (\d+) steps \((\d+(?:\.\d+)?)%\) done\b", text))
    result = {}
    if rules:
        result["latest_rule"] = rules[-1].group(1)
    if steps:
        done, total, percent = steps[-1].groups()
        if 0 <= int(done) <= int(total) and int(total) > 0 and 0 <= float(percent) <= 100:
            result.update(completed_steps=int(done), scheduled_steps=int(total), scheduler_percent=float(percent))
    return result


def work(workspace, identifier):
    directory = job_path(workspace, identifier)
    path = directory / "status.json"
    with lock(job_root(workspace) / "execution.lock", blocking=False) as fd:
        # Parent writes the worker identity under the submit lock first.
        with lock(job_root(workspace) / "submit.lock"):
            state = json.loads(path.read_text())
            if state["state"] != "queued":
                raise ValueError("Job has already started")
            state.update(state="running", started_at=now(), worker_pid=os.getpid(), worker_identity=_identity(os.getpid()))
            atomic_json(path, state)
        try:
            with Path(state["log"]).open("ab", buffering=0) as output:
                child = subprocess.Popen(state["command"], stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT, cwd=workspace,
                    env={**os.environ, "PYTHONUNBUFFERED": "1"}, pass_fds=(fd,))
                state["child_pid"] = child.pid
                atomic_json(path, state)
                code = child.wait()
            result, message = outcome(state["kind"], code, state.get("outdir"))
            state.update(state=result, exit_code=code, message=message)
        except Exception as exc:
            state.update(state="failed", message=str(exc))
        state["finished_at"] = now()
        atomic_json(path, state)


def tail(path, max_bytes=32000):
    p = Path(path)
    if not p.is_file():
        return "Waiting for command output…"
    with p.open("rb") as stream:
        stream.seek(max(0, p.stat().st_size - max_bytes))
        return stream.read(max_bytes).decode("utf-8", "replace")


def main(argv=None):
    import yaml
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("list", "show", "start", "_work"))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--job")
    parser.add_argument("--request", help="JSON file containing typed job options")
    parser.add_argument("--new-attempt", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.operation == "_work":
            work(args.workspace, args.job)
            return 0
        if args.operation == "list":
            data = list_jobs(args.workspace)
        elif args.operation == "show":
            data = read_job(args.workspace, args.job)
        else:
            data = submit(args.workspace, json.loads(Path(args.request).read_text()), new_attempt=args.new_attempt)
        print(json.dumps(data, indent=2))
        return 0
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        print("Job error: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
