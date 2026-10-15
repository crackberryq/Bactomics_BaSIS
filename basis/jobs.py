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
import tempfile
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
               "resources", "execute", "conda_prefix", "environment_backend",
               "hybas_dir", "directory", "preset", "resource_dir"}
    if set(r) - allowed:
        raise ValueError("Unknown job options: " + ", ".join(sorted(set(r) - allowed)))
    if r.get("kind") not in {"run", "doctor", "demo", "setup", "resources", "import", "audit", "panel", "init"}:
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
    if "engine" in r and (r["kind"] not in {"run", "demo", "setup"} or r["engine"] != "snakemake"):
        raise ValueError("Run, demo and setup jobs use the snakemake engine")
    if r["kind"] in {"run", "demo", "setup"}:
        r.setdefault("engine", "snakemake")
    if any(key in r for key in ("hybas_dir", "directory", "preset", "resource_dir")) and r["kind"] != "init":
        raise ValueError("Study initialization options are only valid for init")
    if r["kind"] == "init":
        if not isinstance(r.get("hybas_dir"), str) or not r["hybas_dir"].strip():
            raise ValueError("Select a HybAs annotation directory")
        root = Path(r["hybas_dir"]).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("HybAs input must be a directory")
        r["hybas_dir"] = str(root)
        r.setdefault("preset", "full")
        if r["preset"] not in {"core", "full"}:
            raise ValueError("Study preset must be core or full")
        for key in ("directory", "resource_dir"):
            if key in r:
                if not isinstance(r[key], str) or not r[key].strip():
                    raise ValueError(key + " must be a nonempty directory path")
                r[key] = str(Path(r[key]).expanduser().resolve())
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


def _resolved_config_snapshot(workspace, config):
    """Publish immutable resolved settings, keeping identical Snake inputs stable.

    A completed temporary file is linked into place without overwriting an
    existing target. Existing content must match, including after a race.
    """
    import yaml
    payload = yaml.safe_dump(config, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    root = job_root(workspace) / "resolved-configs"
    if root.is_symlink():
        raise ValueError("Resolved configuration store must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    target = root / (digest + ".yaml")
    if not target.exists() and not target.is_symlink():
        descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=root)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            Path(temporary).unlink(missing_ok=True)
    if target.is_symlink() or not target.is_file() or target.read_bytes() != payload:
        raise ValueError("Resolved configuration snapshot is corrupt; existing file was not replaced: " + str(target))
    return target, payload


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
        # The study owns scientific outputs and caches. New launch records must
        # not change Snake targets or hide previously completed work.
        outdir = Path(cfg["outdir"])
        for key in ("cores", "memory_mb", "gpus", "environment_backend", "conda_prefix"):
            if key in r:
                cfg["execution"][key] = r[key]
        if r.get("environment_backend") == "external":
            # This explicit UI/CLI-job choice means no stage may create an
            # environment, including stages with a prior managed override.
            for section in list(cfg["modules"].values()) + [cfg["deep"]]:
                section["environment_backend"] = "external"
        config, payload = _resolved_config_snapshot(workspace, cfg)
        (directory / "config.yaml").write_bytes(payload)  # per-attempt audit copy
        command += [kind, "-c", str(config), "-v"]
        command += (["-d"] if r.get("deep") else []) + (["-e"] if r.get("extend") else [])
        if kind == "run":
            command += ["--output", "summary", "--engine", r.get("engine", "snakemake")]
        else:
            outdir = directory / "preflight"
            command += ["--report-dir", str(outdir)]
    elif kind == "demo":
        outdir = directory / "demo"
        command += ["demo", "--outdir", str(outdir), "-d", "-e", "-v", "--output", "summary", "--engine", "snakemake"]
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
    elif kind == "init":
        outdir = Path(r["directory"]) if r.get("directory") else directory / "study"
        command += ["init", "--directory", str(outdir), "--hybas-dir", r["hybas_dir"],
                    "--preset", r.get("preset", "full")]
        if r.get("resource_dir"):
            command += ["--resource-dir", r["resource_dir"]]
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


def _evidence_path(kind, outdir):
    return Path(outdir or ".") / ("results/evidence.json" if kind == "demo" else "evidence.json")


def _evidence_snapshot(kind, outdir):
    """Record prior evidence without trusting or requiring its JSON contents."""
    path = _evidence_path(kind, outdir)
    if not path.exists():
        return {"exists": False}
    payload = path.read_bytes()
    result = {"exists": True, "sha256": hashlib.sha256(payload).hexdigest()}
    try:
        data = json.loads(payload)
        result["run_id"] = data["run"]["run_id"]
    except (ValueError, KeyError, TypeError):
        result["run_id"] = None
    return result


def outcome(kind, code, outdir, *, evidence_before=None, started_at=None, job_id=None):
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
    evidence_path = _evidence_path(kind, outdir)
    if kind in {"run", "demo"} and evidence_path.is_file():
        try:
            payload = evidence_path.read_bytes()
            evidence = json.loads(payload)
            run = evidence["run"]
            completeness = run.get("completeness")
            if evidence_before is not None:
                launch_started = datetime.fromisoformat(started_at)
                run_started = datetime.fromisoformat(run["started_at"])
                run_finished = datetime.fromisoformat(run["finished_at"])
                if (not isinstance(run.get("run_id"), str) or not run["run_id"]
                        or run["run_id"] == evidence_before.get("run_id")
                        or hashlib.sha256(payload).hexdigest() == evidence_before.get("sha256")
                        or (job_id is not None and run.get("local_job_id") != job_id)
                        or run_started < launch_started or run_finished < run_started):
                    return "failed", "Saved evidence does not attest this launch; inspect the current command log."
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return "failed", "No valid current-run evidence was produced; inspect the command log."
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


def stop_job(workspace, job_id):
    """Stop only this job's recorded, still-identical BaSIS run launcher.

    No program-name or output-path process search is used. Native descendants
    are handled by run_control's pidfd verification, never numeric PID kills.
    """
    from .run_control import matches_config, read_process, stop_invocation
    directory = job_path(workspace, job_id)
    status_path = directory / "status.json"
    audit_path = directory / "cancellation.json"
    with lock(directory / "stop.lock", blocking=False):
        state = json.loads(status_path.read_text())
        if state.get("kind") != "run" or state.get("state") != "running":
            raise ValueError("Only a recorded running analysis job can be stopped here; no signals sent")
        pid = state.get("child_pid")
        identity = state.get("child_identity")
        command = state.get("command", [])
        if (type(pid) is not int or not isinstance(identity, list) or len(identity) != 2 or _identity(pid) != identity
                or not state.get("worker_identity")
                or _identity(state.get("worker_pid")) != state["worker_identity"]):
            raise ValueError("Recorded worker or child identity no longer matches; no signals sent")
        launcher = read_process(pid)
        if (launcher is None or launcher.uid != os.getuid()
                or launcher.start_time != str(identity[1])
                or launcher.ppid != state.get("worker_pid")
                or not isinstance(command, list) or not all(isinstance(value, str) for value in command)
                or tuple(command) != launcher.argv
                or launcher.cwd != str(workspace_path(workspace))
                or command.count("-c") != 1 or command[-1:] == ["-c"]):
            raise ValueError("Recorded child process changed; no signals sent")
        config = command[command.index("-c") + 1]
        if not matches_config(launcher, config):
            raise ValueError("Recorded analysis configuration does not match; no signals sent")
        audit = {"schema": "basis-job-cancellation-v1", "job_id": job_id,
                 "requested_at": now(), "child_pid": pid, "child_identity": identity,
                 "command": command, "state": "requested", "signals": []}
        atomic_json(audit_path, audit)
        try:
            result = stop_invocation(launcher)
        except (OSError, ValueError, RuntimeError) as exc:
            audit.update(state="rejected", error=str(exc), finished_at=now())
            atomic_json(audit_path, audit)
            raise
        audit.update(state=result["status"], result=result, signals=result.get("signals", []), finished_at=now())
        atomic_json(audit_path, audit)
        with lock(directory / "status.lock"):
            latest = json.loads(status_path.read_text())
            latest["cancellation"] = str(audit_path)
            latest["stop_status"] = result["status"]
            if result["status"] == "stopped" and result.get("signals"):
                latest.update(state="cancelled", finished_at=now(), message="Stopped the verified analysis invocation. Resume uses the same study outputs.")
            atomic_json(status_path, latest)
        return {**latest, "stop_result": result}


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
            if state["kind"] in {"run", "demo"}:
                state["evidence_before"] = _evidence_snapshot(state["kind"], state.get("outdir"))
                state["evidence_current"] = False
            with Path(state["log"]).open("ab", buffering=0) as output:
                child = subprocess.Popen(state["command"], stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT, cwd=workspace,
                    env={**os.environ, "PYTHONUNBUFFERED": "1", "BASIS_LOCAL_JOB_ID": identifier}, pass_fds=(fd,))
                state.update(child_pid=child.pid, child_identity=_identity(child.pid))
                with lock(directory / "status.lock"):
                    atomic_json(path, state)
                code = child.wait()
            result, message = outcome(state["kind"], code, state.get("outdir"),
                                      evidence_before=state.get("evidence_before"),
                                      started_at=state["started_at"], job_id=identifier)
            state.update(state=result, exit_code=code, message=message)
            if state["kind"] in {"run", "demo"} and result in {"completed", "incomplete"}:
                evidence = json.loads(_evidence_path(state["kind"], state.get("outdir")).read_text())
                if evidence["run"].get("local_job_id") != identifier:
                    raise ValueError("Study evidence changed while recording this job outcome")
                state.update(evidence_current=True, result_run_id=evidence["run"]["run_id"])
        except Exception as exc:
            state.update(state="failed", message=str(exc))
        state["finished_at"] = now()
        with lock(directory / "status.lock"):
            latest = json.loads(path.read_text())
            latest.update(state)
            cancellation = directory / "cancellation.json"
            if cancellation.is_file():
                audit = json.loads(cancellation.read_text())
                latest["cancellation"] = str(cancellation)
                if (audit.get("child_identity") == state.get("child_identity")
                        and audit.get("state") == "stopped" and audit.get("signals")):
                    latest.update(state="cancelled", stop_status="stopped", message="Stopped the verified analysis invocation. Resume uses the same study outputs.")
            atomic_json(path, latest)


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
    parser.add_argument("operation", choices=("list", "show", "start", "stop", "_work"))
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
        elif args.operation == "stop":
            data = stop_job(args.workspace, args.job)
        else:
            data = submit(args.workspace, json.loads(Path(args.request).read_text()), new_attempt=args.new_attempt)
        print(json.dumps(data, indent=2))
        return 0
    except (OSError, ValueError, TypeError, RuntimeError, yaml.YAMLError) as exc:
        print("Job error: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
