"""Verified tool-environment setup, shared by Snakemake, CLI and local jobs.

No study configuration is changed and no external database/model is prepared.
Recipe checks support the simple numeric constraints in our bundled recipes;
they are not a general Conda solver or a claim of native biological acceptance.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.resources import as_file, files
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import threading
import signal
import uuid

import yaml

from . import __version__
from .environment_setup import recipe_resource, workspace_path
from .util import atomic_json, hash_object, now, sha256_file


# Alternatives are explicit aliases; executables must resolve inside the prefix.
# Help probes permit a documented usage-style nonzero exit only with matching text.
PROBES = {
    "core-amr": [(("amrfinder",), ["-h"], r"amrfinder|AMRFinder", (0,))],
    "diamond": [(("diamond",), ["version"], r"diamond version", (0,))],
    "context": [(("genomad",), ["--version"], r"genomad", (0,)),
                (("integron_finder",), ["--version"], r"integron_finder", (0,))],
    "deepsafe": [(("blastp",), ["-version"], r"blastp", (0,)),
                 (("mafft",), ["--version"], r"v?\d+\.\d+", (0,)),
                 (("iqtree2", "iqtree"), ["--version"], r"IQ.TREE", (0,)),
                 (("epa-ng",), ["--version"], r"EPA.ng", (0,)),
                 (("hmmscan",), ["-h"], r"hmmscan", (0,)),
                 (("seqkit",), ["version"], r"seqkit", (0,)),
                 (("perl",), ["-e", "print qq(BaSIS Perl probe OK\\n)"], r"BaSIS Perl probe OK", (0,)),
                 (("pfscanV3",), ["-h"], r"pfscan|pftools|profile.*scan", (0, 1))],
    "rgi": [(("rgi",), ["main", "--version"], r"\d+\.\d+\.\d+", (0,))],
    "antismash": [(("antismash",), ["--version"], r"antismash", (0,))],
    "mobsuite": [(("mob_recon",), ["--version"], r"mob_recon", (0,))],
    "isescan": [(("isescan.py",), ["--version"], r"isescan", (0,)),
                (("hmmsearch",), ["-h"], r"hmmsearch", (0,)),
                (("phmmer",), ["-h"], r"phmmer", (0,))],
    "icescreen": [(("icescreen",), ["--version"], r"\d+\.\d+\.\d+", (0,)),
                  (("snakemake",), ["--version"], r"\d+\.\d+", (0,))],
    "minced": [(("minced",), ["--version"], r"minced", (0,)),
               (("java",), ["-version"], r"version", (0,))],
    "pathogenfinder2": [(("pathogenfinder2",), ["--version"], r"\d+\.\d+", (0,))],
}


def _numeric_version(value):
    if not re.fullmatch(r"\d+(?:\.\d+)*", str(value)):
        raise ValueError("Unsupported version for numeric recipe check: " + str(value))
    return tuple(map(int, str(value).split(".")))


def version_matches(version, constraints):
    """Evaluate only our explicit numeric constraints; fail closed for others."""
    if not constraints:
        return True
    actual = _numeric_version(version)
    for clause in constraints.split(","):
        match = re.fullmatch(r"(>=|<=|==|!=|=|>|<)(\d+(?:\.\d+)*)(\.\*)?", clause.strip())
        if not match:
            raise ValueError("Unsupported recipe version constraint: " + clause)
        op, text, wildcard = match.groups()
        expected = _numeric_version(text)
        if wildcard and op not in {"=", "=="}:
            raise ValueError("Unsupported recipe wildcard: " + clause)
        if op == "=" or wildcard:
            ok = actual[:len(expected)] == expected
        else:
            width = max(len(actual), len(expected))
            a = actual + (0,) * (width - len(actual))
            b = expected + (0,) * (width - len(expected))
            ok = {"==": a == b, "!=": a != b, ">=": a >= b, "<=": a <= b,
                  ">": a > b, "<": a < b}[op]
        if not ok:
            return False
    return True


def inventory(prefix, recipe):
    """Read installed records; an existing directory alone never passes."""
    meta = prefix / "conda-meta"
    if not (meta / "history").is_file():
        raise ValueError("Existing prefix has no Conda transaction history; preserved: " + str(prefix))
    records, digests = {}, {}
    for path in sorted(meta.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("name") or not data.get("version"):
            raise ValueError("Invalid Conda package record: " + str(path))
        if data["name"] in records:
            raise ValueError("Duplicate Conda package record: " + data["name"])
        records[data["name"]] = data
        digests[path.name] = sha256_file(path)
    checked, pip_specs = [], []
    for dep in recipe.get("dependencies", []):
        if isinstance(dep, dict) and set(dep) == {"pip"}:
            pip_specs.extend(dep["pip"])
            continue
        if not isinstance(dep, str):
            raise ValueError("Unsupported recipe dependency: " + repr(dep))
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(.*)", dep)
        if not match:
            raise ValueError("Unsupported recipe dependency: " + dep)
        name, constraints = match.groups()
        record = records.get(name)
        if record is None or not version_matches(record["version"], constraints):
            raise ValueError("Installed package does not satisfy " + dep + "; existing prefix preserved")
        checked.append({"specification": dep, "version": record["version"], "build": record.get("build")})
    return {"packages": sorted(records.values(), key=lambda p: p["name"]),
            "metadata_sha256": digests, "checked_dependencies": checked, "pip_specs": pip_specs}


def _command(argv, logfile, *, timeout=180, source_imports=False):
    print("$ " + shlex.join(map(str, argv)), flush=True)
    env = dict(os.environ, CONDA_CHANNEL_PRIORITY="strict", CONDA_FETCH_THREADS="1",
               CONDA_REMOTE_MAX_RETRIES="5", CONDA_REMOTE_READ_TIMEOUT_SECS="120",
               PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1", BASIS_VERBOSE="1")
    # Workers must import this same source when developing from a checkout as
    # well as when installed in site-packages; Snakemake changes its workdir.
    package_parent = str(Path(__file__).resolve().parent.parent)
    if source_imports:
        env["PYTHONPATH"] = package_parent
    else:
        env.pop("PYTHONPATH", None)
        if "--prefix" in argv:
            from .native_environment import native_child_environment
            target = str(argv[list(argv).index("--prefix") + 1])
            # All commands here are explicitly targeted managed environments.
            # Apply the same launcher cleanup for Conda, Mamba and Micromamba.
            env, _ = native_child_environment(env, ["conda", "run", "--prefix", target])
    # Pipe output to both the persistent attempt log and GUI/CLI job log.
    from .progress import stage, verbosity
    with logfile.open("a", encoding="utf-8") as log:
        log.write("\n[" + now() + "] " + shlex.join(map(str, argv)) + "\n")
        log.flush()
        with verbosity(True), stage("Environment command: " + Path(str(argv[0])).name):
            child = subprocess.Popen(list(map(str, argv)), stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, env=env, start_new_session=True)
            captured = []
            def copy_output():
                import codecs
                decoder = codecs.getincrementaldecoder("utf-8")("replace")
                while True:
                    data = os.read(child.stdout.fileno(), 8192)
                    if not data:
                        break
                    chunk = decoder.decode(data)
                    captured.append(chunk)
                    log.write(chunk)
                    log.flush()
                    print(chunk, end="", flush=True)
                suffix = decoder.decode(b"", final=True)
                if suffix:
                    captured.append(suffix)
                    log.write(suffix)
                    log.flush()
                    print(suffix, end="", flush=True)
            reader = threading.Thread(target=copy_output, daemon=True)
            reader.start()
            try:
                code = child.wait(timeout=timeout)
            except BaseException:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                raise
            finally:
                reader.join()
                child.stdout.close()
    return subprocess.CompletedProcess(list(map(str, argv)), code, "".join(captured))


def _binary(prefix, alternatives):
    for name in alternatives:
        path = prefix / "bin" / name
        if path.is_file() and os.access(path, os.X_OK):
            if not path.resolve().is_relative_to(prefix.resolve()):
                raise ValueError("Executable resolves outside selected environment: " + str(path))
            return path
    raise ValueError("Environment executable missing: " + "/".join(alternatives))


def _run_prefix(plan, prefix, arguments, logfile):
    return _command([plan["manager_executable"], "run", "--prefix", str(prefix), *arguments], logfile)


def verify(prefix, recipe, name, plan, logfile):
    captured = inventory(prefix, recipe)
    probes = []
    for alternatives, arguments, pattern, codes in PROBES[name]:
        binary = _binary(prefix, alternatives)
        response = _run_prefix(plan, prefix, [str(binary), *arguments], logfile)
        output = response.stdout or ""
        if response.returncode not in codes or not re.search(pattern, output, re.I):
            raise ValueError("Executable probe failed: " + str(binary) + "; see " + str(logfile))
        probes.append({"executable": str(binary), "sha256": sha256_file(binary),
                       "arguments": arguments, "exit_code": response.returncode,
                       "output": output[-12000:]})
    pip_records = []
    if captured["pip_specs"]:
        python = _binary(prefix, ("python",))
        for spec in captured["pip_specs"]:
            match = re.fullmatch(r"([\w.-]+)(?:\[[\w,.-]+\])? @ git\+(https://[^\s]+)@([0-9a-f]{40})", spec)
            if not match:
                raise ValueError("Unsupported pinned pip requirement: " + spec)
            distribution, repository, commit = match.groups()
            code = ("import json,importlib.metadata as m; d=m.distribution(" + repr(distribution) + "); "
                    "print(json.dumps({'version':d.version,'direct_url':json.loads(d.read_text('direct_url.json') or '{}')}))")
            response = _run_prefix(plan, prefix, [str(python), "-I", "-c", code], logfile)
            if response.returncode:
                raise ValueError("Pinned pip distribution unavailable: " + distribution)
            data = json.loads(response.stdout.strip().splitlines()[-1])
            direct = data.get("direct_url", {})
            if direct.get("url", "").removesuffix(".git") != repository.removesuffix(".git") or \
                    direct.get("vcs_info", {}).get("commit_id") != commit:
                raise ValueError("Installed pip source does not match pinned commit: " + distribution)
            pip_records.append({"specification": spec, **data})
        if name == "pathogenfinder2":
            response = _run_prefix(plan, prefix, [str(python), "-I", "-c",
                "import torch,h5py,numpy; from transformers import T5EncoderModel,T5Tokenizer; "
                "print('BaSIS model runtime imports OK')"], logfile)
            if response.returncode or "BaSIS model runtime imports OK" not in response.stdout:
                raise ValueError("PathogenFinder2 model-runtime imports failed")
    captured.update(probes=probes, pip_records=pip_records,
                    validation_scope="installed_recipe_constraints_and_executable_startup; no biological acceptance or GPU inference")
    return captured


@contextmanager
def _prefix_lock(prefix):
    import fcntl
    root = prefix.parent / ".basis-setup-locks"
    root.mkdir(parents=True, exist_ok=True)
    key = hash_object(str(prefix.resolve()))
    with (root / (key + ".lock")).open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another setup process owns this prefix: " + str(prefix)) from exc
        try:
            yield root / (key + ".json")
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def load_plan(path):
    plan = json.loads(Path(path).read_text())
    supplied = plan.pop("plan_hash", None)
    if supplied != hash_object(plan):
        raise ValueError("Environment setup plan changed")
    plan["plan_hash"] = supplied
    return plan


def worker(plan_path, name):
    plan = load_plan(plan_path)
    entry = next((e for e in plan["environments"] if e["name"] == name), None)
    if entry is None:
        raise ValueError("Environment absent from setup plan")
    output = Path(entry["record"])
    if output.exists():
        raise ValueError("Setup record already exists; start a new explicit attempt")
    logfile = Path(entry["log"])
    state = {"schema": "basis-environment-setup-record-v1", "name": name, "prefix": entry["prefix"],
             "plan_hash": plan["plan_hash"], "recipe_sha256": entry["recipe_sha256"],
             "started_at": now(), "status": "preparing", "operation": "verify", "log": str(logfile)}
    try:
        recipe_path = Path(entry["recipe"])
        if sha256_file(recipe_path) != entry["recipe_sha256"]:
            raise ValueError("Recipe differs from captured setup plan")
        recipe = yaml.safe_load(recipe_path.read_text())
        prefix = workspace_path(entry["prefix"])
        with _prefix_lock(prefix) as claim_path:
            claim = json.loads(claim_path.read_text()) if claim_path.is_file() else {}
            owned = (claim.get("prefix") == str(prefix) and claim.get("workspace") == plan["workspace"]
                     and claim.get("recipe_sha256") == entry["recipe_sha256"])
            fresh = not prefix.exists()
            repair = False
            if not fresh:
                try:
                    state["verification"] = verify(prefix, recipe, name, plan, logfile)
                    state.update(status="ready", operation="reused")
                except Exception:
                    if not (plan["retry_failed"] and owned and claim.get("status") in {"preparing", "failed"}):
                        raise
                    repair = True
            if fresh or repair:
                state["operation"] = "retry" if repair else "created"
                claim = {"prefix": str(prefix), "workspace": plan["workspace"],
                         "recipe_sha256": entry["recipe_sha256"], "status": "preparing",
                         "attempt": plan["directory"], "updated_at": now()}
                atomic_json(claim_path, claim)
                if repair:
                    operation = ["env", "update"]
                else:
                    operation = ["create"] if plan["manager"] == "micromamba" else ["env", "create"]
                command = [plan["manager_executable"], *operation, "--prefix", str(prefix),
                           "--file", str(recipe_path)]
                if not repair or plan["manager"] == "micromamba":
                    command.append("--yes")
                try:
                    response = _command(command, logfile, timeout=86400)
                    if response.returncode:
                        raise ValueError("Environment manager exited " + str(response.returncode) + "; retry requires --retry-failed")
                    state["verification"] = verify(prefix, recipe, name, plan, logfile)
                    state["status"] = "ready"
                    claim.update(status="ready", updated_at=now())
                except Exception:
                    claim.update(status="failed", updated_at=now())
                    raise
                finally:
                    atomic_json(claim_path, claim)
    except Exception as exc:
        state.update(status="failed", error=str(exc))
    state["finished_at"] = now()
    atomic_json(output, state)
    print(name + ": " + state["status"] + (" | " + state["error"] if state.get("error") else ""), flush=True)
    # Failed readiness is recorded as data so the final summary always runs.
    return state


def summarize(plan_path, scheduler_error=None):
    plan = load_plan(plan_path)
    results = []
    for entry in plan["environments"]:
        path = Path(entry["record"])
        try:
            row = json.loads(path.read_text())
            if row.get("plan_hash") != plan["plan_hash"] or row.get("name") != entry["name"] or \
                    row.get("recipe_sha256") != entry["recipe_sha256"] or row.get("prefix") != entry["prefix"]:
                raise ValueError("Setup record identity mismatch")
            if row.get("status") == "ready" and not row.get("verification", {}).get("probes"):
                raise ValueError("Ready record lacks executable verification")
            result = {key: row.get(key) for key in ("name", "prefix", "status", "operation", "error")}
        except (OSError, ValueError, TypeError) as exc:
            result = {"name": entry["name"], "prefix": entry["prefix"], "status": "failed", "error": str(exc)}
        results.append({**result, "record": str(path)})
    summary = {"schema": "basis-environment-setup-summary-v1", "scope": "tool_environments_only",
               "status": "completed" if all(r["status"] == "ready" for r in results) and not scheduler_error else "incomplete",
               "environments": results, "databases": "not_assessed", "models": "not_assessed",
               "analysis_readiness": "not_assessed", "plan_hash": plan["plan_hash"], "finished_at": now(),
               "summary_path": plan["summary_path"], "scheduler_error": scheduler_error,
               "limitations": ["Recipe versions are not solved environment locks.",
                               "Startup probes do not establish native biological acceptance or GPU/model readiness."]}
    atomic_json(plan["summary_path"], summary)
    return summary


def run_setup(workspace, prefix_root, names, *, manager="conda", engine="snakemake", cores=1,
              retry_failed=False, report_dir=None):
    workspace, prefix_root = workspace_path(workspace), workspace_path(prefix_root)
    if engine not in {"local", "snakemake"} or manager not in {"conda", "mamba", "micromamba"}:
        raise ValueError("Unsupported setup engine or manager")
    if type(cores) is not int or cores < 1:
        raise ValueError("Setup cores must be positive")
    if not names or len(set(names)) != len(names):
        raise ValueError("Select distinct environment names")
    recipes = {name: recipe_resource(name).read_bytes() for name in names}
    executable = shutil.which(manager)
    if not executable:
        raise ValueError("Package manager is not on PATH: " + manager)
    if engine == "snakemake":
        import importlib.util
        if importlib.util.find_spec("snakemake") is None:
            raise ValueError("Snakemake is unavailable in this Python installation; install BaSIS workflow dependencies or use --engine local")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    directory = workspace_path(report_dir) if report_dir else workspace / ".basis-setup/attempts" / stamp
    if directory.exists() or directory.is_symlink():
        raise ValueError("Refusing to overwrite an existing setup report directory: " + str(directory))
    directory.mkdir(parents=True)
    (directory / "recipes").mkdir()
    (directory / "environments").mkdir()
    plan = {"schema": "basis-environment-setup-plan-v1", "basis_version": __version__, "created_at": now(),
            "workspace": str(workspace), "directory": str(directory), "prefix_root": str(prefix_root),
            "manager": manager, "manager_executable": str(Path(executable).absolute()), "engine": engine,
            "cores": cores, "retry_failed": bool(retry_failed), "python": sys.executable,
            "summary_path": str(directory / "summary.json"), "environments": []}
    for name, content in recipes.items():
        recipe = directory / "recipes" / (name + ".yml")
        recipe.write_bytes(content)
        plan["environments"].append({"name": name, "prefix": str(workspace_path(prefix_root / ("basis-" + name))),
                                     "recipe": str(recipe), "recipe_sha256": sha256_file(recipe),
                                     "record": str(directory / "environments" / (name + ".json")),
                                     "log": str(directory / "environments" / (name + ".log"))})
    plan["plan_hash"] = hash_object(plan)
    plan_path = directory / "plan.json"
    atomic_json(plan_path, plan)
    print("Setup attempt: " + str(directory), flush=True)
    scheduler_error = None
    try:
        if engine == "local":
            for name in names:
                worker(plan_path, name)
        else:
            with as_file(files("basis").joinpath("resources", "SetupSnakefile")) as snake:
                command = [sys.executable, "-m", "snakemake", "--snakefile", str(snake), "--configfile", str(plan_path),
                           "--directory", str(directory), "--cores", str(cores), "--rerun-incomplete", "--nolock"]
                response = _command(command, directory / "snakemake.log", timeout=172800, source_imports=True)
                if response.returncode:
                    scheduler_error = "Snakemake exited " + str(response.returncode)
    except Exception as exc:
        scheduler_error = str(exc)
    # Always produce a failure-aware summary, including scheduler/bootstrap errors.
    # The Snakemake summary rule also produces this output inside its dependency graph.
    summary = summarize(plan_path, scheduler_error)
    return summary, 0 if summary["status"] == "completed" else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("worker", "summary"))
    parser.add_argument("--plan", required=True)
    parser.add_argument("--environment")
    args = parser.parse_args(argv)
    if args.action == "worker":
        if not args.environment:
            parser.error("worker requires --environment")
        worker(args.plan, args.environment)
    else:
        summarize(args.plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
