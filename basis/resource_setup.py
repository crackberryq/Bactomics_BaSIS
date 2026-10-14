"""Explicit, immutable resource preparation targets for the BaSIS workflow.

Normal analysis never calls these administrative download targets. Native
downloaders that select their own release require explicit acknowledgement and
an expected version-file value or content pins. Raw pinned files are not
represented as indexed, analysis-ready databases.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
import uuid

import yaml

from . import __version__
from .environment_setup import workspace_path
from .execution import CommandRunner
from .util import atomic_json, hash_object, sha256_file


BACKENDS = ("amrfinder", "genomad", "antismash", "mobsuite", "bacmet", "pinned_files")
RECIPES = {"amrfinder": "core-amr", "genomad": "context", "antismash": "antismash",
           "mobsuite": "mobsuite", "bacmet": "diamond"}
_NATIVE = {"amrfinder", "genomad", "antismash", "mobsuite"}
_KEYS = {"backend", "destination", "version", "environment_prefix", "required_paths", "expected_files",
         "version_file", "allow_upstream_current", "files", "threads", "timeout_seconds", "max_bytes"}


def _relative(value):
    if not isinstance(value, str):
        raise ValueError("Resource artifact paths must be strings")
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
        raise ValueError("Resource artifact must be a relative path without traversal: " + value)
    if path.as_posix() in {".", "provenance.json"}:
        raise ValueError("Reserved resource artifact path: " + value)
    return path.as_posix()


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError("Resource file requires a SHA256 content pin")
    return value.lower()


def _url(value):
    value = str(value)
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Resource URLs must use HTTPS without embedded credentials")
    return value


def load_resource_config(path):
    """Read and validate an explicit resource recipe without creating anything."""
    path = Path(path).expanduser().resolve(strict=True)
    supplied = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(supplied, dict) or supplied.get("schema_version") != 1:
        raise ValueError("Resource configuration requires schema_version: 1")
    if set(supplied) - {"schema_version", "resources"}:
        raise ValueError("Use a dedicated resource configuration with schema_version and resources only")
    entries = supplied.get("resources")
    if not isinstance(entries, dict) or not entries:
        raise ValueError("Resource configuration requires a nonempty resources mapping")
    resources = {}
    for name, source in entries.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
            raise ValueError("Invalid resource name: " + str(name))
        if not isinstance(source, dict) or set(source) - _KEYS:
            raise ValueError("Invalid or unsupported resource settings for " + name)
        row = dict(source)
        backend = row.get("backend")
        if backend not in BACKENDS:
            raise ValueError("Unsupported resource backend: " + str(backend))
        if not isinstance(row.get("destination"), str) or not row["destination"]:
            raise ValueError(name + ": destination is required")
        row["destination"] = str(workspace_path(path.parent / Path(row["destination"]).expanduser()))
        if row.get("environment_prefix"):
            if not isinstance(row["environment_prefix"], str):
                raise ValueError("environment_prefix must be a path string")
            row["environment_prefix"] = str(workspace_path(path.parent / Path(row["environment_prefix"]).expanduser()))
        if backend == "bacmet":
            from .optional_bacmet import RELEASE
            if row.get("version", RELEASE) != RELEASE:
                raise ValueError("The BacMet backend supports the pinned experimental v2 snapshot only")
            row["version"] = RELEASE
        version = row.get("version")
        if not isinstance(version, str) or not version.strip() or version.lower() in {"latest", "current", "unknown"}:
            raise ValueError(name + ": an explicit version/revision label is required")
        for key, default in (("threads", 1), ("timeout_seconds", 14400), ("max_bytes", 50_000_000_000)):
            row.setdefault(key, default)
            if isinstance(row[key], bool) or not isinstance(row[key], int) or row[key] < 1:
                raise ValueError(name + ": " + key + " must be a positive integer")
        required = row.get("required_paths", [])
        if not isinstance(required, list) or not all(isinstance(p, str) for p in required):
            raise ValueError("required_paths must be a list of relative artifact paths")
        row["required_paths"] = list(dict.fromkeys(_relative(p) for p in required))
        hashes = row.get("expected_files", {})
        if not isinstance(hashes, dict):
            raise ValueError("expected_files must map relative paths to SHA256 values")
        row["expected_files"] = {_relative(k): _digest(v) for k, v in hashes.items()}
        if row.get("version_file"):
            row["version_file"] = _relative(row["version_file"])
        if backend in _NATIVE:
            if row.get("allow_upstream_current") is not True:
                raise ValueError(name + ": native downloader chooses upstream content; explicitly set allow_upstream_current: true")
            if not row.get("version_file") and not row["expected_files"]:
                raise ValueError(name + ": native preparation requires version_file or expected_files content pins")
            if not row["required_paths"]:
                raise ValueError(name + ": declare required_paths for the database artifacts")
        if backend == "pinned_files":
            files = row.get("files")
            if not isinstance(files, list) or not files:
                raise ValueError(name + ": pinned_files requires a nonempty files list")
            normalized, filenames = [], set()
            for item in files:
                if not isinstance(item, dict) or set(item) - {"path", "url", "source", "sha256"}:
                    raise ValueError("Pinned file requires path, sha256, and exactly one url or source")
                if bool(item.get("url")) == bool(item.get("source")):
                    raise ValueError("Pinned file requires exactly one url or source")
                filename = _relative(item.get("path", ""))
                if filename in filenames:
                    raise ValueError("Duplicate pinned destination: " + filename)
                filenames.add(filename)
                file = {"path": filename, "sha256": _digest(item.get("sha256"))}
                if item.get("url"):
                    file["url"] = _url(item["url"])
                else:
                    if not isinstance(item["source"], str):
                        raise ValueError("Pinned local source must be a path string")
                    file["source"] = str((path.parent / Path(item["source"]).expanduser()).resolve())
                normalized.append(file)
            row["files"] = normalized
        elif row.get("files"):
            raise ValueError("files is supported only by pinned_files")
        row["name"] = name
        row["scope"] = "source_files_only" if backend == "pinned_files" else "database_snapshot"
        resources[name] = row
    destinations = [Path(r["destination"]) for r in resources.values()]
    for i, destination in enumerate(destinations):
        if any(destination == p or destination in p.parents or p in destination.parents for p in destinations[i + 1:]):
            raise ValueError("Resource snapshot destinations must not overlap")
    return {"schema_version": 1, "resources": resources, "config_path": str(path)}


def build_resource_plan(path, selected):
    config = load_resource_config(path)
    names = list(dict.fromkeys(selected or []))
    if not names:
        raise ValueError("Select one or more resources explicitly with --resource")
    unknown = set(names) - set(config["resources"])
    if unknown:
        raise ValueError("Unknown resource names: " + ", ".join(sorted(unknown)))
    entries = [config["resources"][name] for name in names]
    plan = {"schema": "basis-resource-plan-v1", "resources": entries,
            "source_config": config["config_path"], "source_config_sha256": sha256_file(config["config_path"]),
            "automatic_refresh": False, "analysis_readiness": "not_assessed",
            "generator": {"basis_version": __version__, "adapter_sha256": sha256_file(__file__)}}
    plan["plan_hash"] = hash_object(plan)
    return plan


def _contained(root, relative):
    path = root / _relative(relative)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Resource artifact escapes snapshot: " + relative)
    return path


def _verify_expected(row, root):
    for relative in row["required_paths"]:
        path = _contained(root, relative)
        if not path.exists() or (path.is_file() and not path.stat().st_size) or (path.is_dir() and not any(path.iterdir())):
            raise ValueError("Required resource artifact is missing or empty: " + relative)
    for relative, expected in row["expected_files"].items():
        path = _contained(root, relative)
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError("Expected resource checksum differs: " + relative)
    for item in row.get("files", []):
        path = _contained(root, item["path"])
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise ValueError("Pinned source checksum differs: " + item["path"])
    if row["backend"] == "bacmet":
        from .optional_bacmet import validate_bacmet_fasta
        sources = list(root.glob("experimental-v2-*/BacMet2_EXP_database.fasta"))
        if len(sources) != 1:
            raise ValueError("Expected exactly one pinned BacMet source snapshot")
        validate_bacmet_fasta(sources[0])
    if row.get("version_file"):
        path = _contained(root, row["version_file"])
        if not path.is_file() or path.read_text(encoding="utf-8").strip() != row["version"]:
            raise ValueError("Downloaded resource version differs from declared version: " + row["version"])


def _inventory(root):
    hashes = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == "provenance.json":
            continue
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Resource snapshot contains an external symlink: " + relative)
        if path.is_file():
            hashes[relative] = sha256_file(path)
    if not hashes:
        raise ValueError("Resource preparation produced no files")
    return hashes


def _links(root):
    return {path.relative_to(root).as_posix(): os.readlink(path)
            for path in sorted(root.rglob("*")) if path.is_symlink()}


def verify_snapshot(row):
    root = Path(row["destination"])
    manifest = root / "provenance.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("schema") != "basis-resource-snapshot-v1" or data.get("recipe_hash") != hash_object(row):
        raise ValueError("Existing resource snapshot has different preparation inputs; select a new destination")
    _verify_expected(row, root)
    if _inventory(root) != data.get("sha256"):
        raise ValueError("Existing resource snapshot bytes differ from recorded checksums")
    if _links(root) != data.get("symlinks", {}):
        raise ValueError("Existing resource snapshot symlinks differ from the recorded targets")
    return data


def _load_plan(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    content = {key: value for key, value in plan.items()
               if key not in {"plan_hash", "directory", "python", "summary_path"}}
    if plan.get("schema") != "basis-resource-plan-v1" or hash_object(content) != plan.get("plan_hash"):
        raise ValueError("Resource plan integrity check failed")
    return plan


class _HttpsRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _copy_pinned(item, root, maximum):
    path = _contained(root, item["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(_HttpsRedirect())
    source = (opener.open(urllib.request.Request(item["url"], headers={"User-Agent": "BaSIS-resource-setup/1"}), timeout=60)
              if "url" in item else Path(item["source"]).open("rb"))
    with source as stream, path.open("xb") as output:
        digest, count = hashlib.sha256(), 0
        while block := stream.read(1024 * 1024):
            count += len(block)
            if count > maximum:
                raise ValueError("Pinned file exceeds configured max_bytes")
            digest.update(block)
            output.write(block)
    if digest.hexdigest() != item["sha256"]:
        raise ValueError("Downloaded/copied source checksum differs: " + item["path"])


def _command_prefix(row):
    if row.get("environment_prefix"):
        prefix = Path(row["environment_prefix"])
        if not (prefix / "conda-meta/history").is_file():
            raise ValueError("Explicit resource environment is not a Conda prefix: " + str(prefix))
        return [os.environ.get("CONDA_EXE", "conda"), "run", "--no-capture-output", "--prefix", str(prefix)], prefix
    from .workflow_environments import command_prefix_for_active, managed_prefix
    return command_prefix_for_active(), managed_prefix()


def _prepare_native(row, root, log, threads=None):
    prefix, environment = _command_prefix(row)
    from .environment_workflow import verify
    from .workflow_environments import environment_for_recipe
    recipe_name = RECIPES[row["backend"]]
    recipe_path = Path(environment_for_recipe(recipe_name))
    verification = verify(environment, yaml.safe_load(recipe_path.read_text(encoding="utf-8")),
                          recipe_name, {"manager_executable": prefix[0]}, log)
    provenance = {"environment_prefix": str(environment), "environment_recipe": recipe_name,
                  "environment_recipe_sha256": sha256_file(recipe_path), "environment_verification": verification}
    threads = threads or row["threads"]
    provenance["allocated_threads"] = threads
    runner = CommandRunner({"command_prefix": prefix, "threads": threads,
                            "timeout_seconds": row["timeout_seconds"]}, log)
    backend = row["backend"]
    if backend == "bacmet":
        from .optional_bacmet import prepare_bacmet
        output = prepare_bacmet(root, environment, threads=threads, reuse=False, allow_download=True)
        return {"resolved_resources": output, **provenance}
    argv = {
        "amrfinder": ["amrfinder_update", "--database", str(root)],
        "genomad": ["genomad", "download-database", str(root)],
        "antismash": ["download-antismash-databases", "--database-dir", str(root)],
        "mobsuite": ["mob_init", "--database_directory", str(root)],
    }[backend]
    runner.run(argv)
    return {"commands": runner.commands, **provenance}


def prepare_resource(plan_path, name, receipt=None, allocated_threads=None):
    """Execute one immutable admin target; failed snapshots are preserved."""
    plan_path = Path(plan_path)
    plan = _load_plan(plan_path)
    row = next((r for r in plan["resources"] if r["name"] == name), None)
    if row is None:
        raise ValueError("Resource not selected in plan: " + name)
    if allocated_threads is not None and (allocated_threads < 1 or allocated_threads > row["threads"]):
        raise ValueError("Invalid resource worker thread allocation")
    attempt = plan_path.parent
    receipt = Path(receipt) if receipt else attempt / "resources" / (name + ".json")
    root = Path(row["destination"])
    record = {"schema": "basis-resource-receipt-v1", "name": name, "backend": row["backend"],
              "scope": row["scope"], "plan_hash": plan["plan_hash"], "status": "failed",
              "destination": str(root), "analysis_readiness": "not_assessed", "reused": False}
    try:
        root.parent.mkdir(parents=True, exist_ok=True)
        lockroot = root.parent / ".basis-resource-locks"
        lockroot.mkdir(exist_ok=True)
        with (lockroot / (hash_object(str(root)) + ".lock")).open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if root.exists():
                verify_snapshot(row)
                record["reused"] = True
            else:
                root.mkdir(exist_ok=False)
                if row["backend"] == "pinned_files":
                    for item in row["files"]:
                        print("Preparing pinned file: " + item["path"], flush=True)
                        _copy_pinned(item, root, row["max_bytes"])
                    details = {}
                else:
                    details = _prepare_native(row, root, attempt / "logs" / (name + ".log"), allocated_threads)
                _verify_expected(row, root)
                snapshot = {"schema": "basis-resource-snapshot-v1", "status": "ready", "name": name,
                            "backend": row["backend"], "version": row["version"], "scope": row["scope"],
                            "recipe_hash": hash_object(row), "recipe": row, "details": details,
                            "generator": plan["generator"],
                            "sha256": _inventory(root), "symlinks": _links(root),
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "analysis_readiness": "not_assessed", "automatic_refresh": False}
                snapshot["version_evidence"] = ("exact_version_file" if row.get("version_file") else
                                                "builtin_reviewed_content_pin" if row["backend"] == "bacmet" else
                                                "declared_label_with_selected_file_content_pins")
                atomic_json(root / "provenance.json", snapshot)
            record.update(status="ready", manifest=str(root / "provenance.json"),
                          manifest_sha256=sha256_file(root / "provenance.json"))
    except Exception as exc:
        record["error"] = str(exc)
        record["recovery"] = "Existing data were preserved. Inspect logs and choose a new empty snapshot destination."
    atomic_json(receipt, record)
    print(name + ": " + record["status"] + (" | " + record["error"] if record.get("error") else ""), flush=True)
    return record


def summarize_resources(plan_path, scheduler_error=None):
    plan_path = Path(plan_path)
    plan = _load_plan(plan_path)
    rows = []
    for entry in plan["resources"]:
        path = plan_path.parent / "resources" / (entry["name"] + ".json")
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if (row.get("schema") != "basis-resource-receipt-v1" or row.get("plan_hash") != plan["plan_hash"]
                    or row.get("name") != entry["name"] or row.get("backend") != entry["backend"]):
                raise ValueError("Resource receipt does not match the current plan")
            if row.get("status") == "ready":
                verify_snapshot(entry)
                if row.get("manifest") != str(Path(entry["destination"]) / "provenance.json"):
                    raise ValueError("Resource receipt references another snapshot manifest")
                if sha256_file(row["manifest"]) != row.get("manifest_sha256"):
                    raise ValueError("Resource manifest changed after worker completion")
        except Exception as exc:
            row = {"name": entry["name"], "backend": entry["backend"], "status": "failed", "error": str(exc)}
        rows.append(row)
    summary = {"schema": "basis-resource-setup-summary-v1", "plan_hash": plan["plan_hash"],
               "status": "completed" if all(r["status"] == "ready" for r in rows) and not scheduler_error else "incomplete",
               "resources": rows, "analysis_readiness": "not_assessed", "automatic_refresh": False,
               "summary_path": str(plan_path.parent / "summary.json"), "scheduler_error": scheduler_error,
               "limitations": ["Raw pinned files are not automatically indexed or configured for analysis.",
                               "Run the study readiness checks with explicit prepared paths before analysis."]}
    atomic_json(summary["summary_path"], summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", required=True, help="Dedicated resource recipe YAML")
    parser.add_argument("--resource", action="append", required=True, help="Explicit resource name; repeatable")
    parser.add_argument("--execute", action="store_true", help="Download/build selected immutable snapshots")
    parser.add_argument("--cores", type=int, default=1)
    parser.add_argument("--report-dir", help="New attempt directory; existing directories are refused")
    parser.add_argument("--conda-prefix", help="Parent for Snakemake-managed environment cache")
    args = parser.parse_args(argv)
    try:
        if args.cores < 1:
            raise ValueError("--cores must be positive")
        plan = build_resource_plan(args.config, args.resource)
        if not args.execute:
            print(json.dumps(plan, indent=2))
            print("Plan only: no directories, downloads, installations or indexes were created.")
            return 0
        from importlib.resources import as_file, files
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
        directory = workspace_path(args.report_dir or Path(plan["source_config"]).parent / ".basis-resources/attempts" / stamp)
        directory.mkdir(parents=True, exist_ok=False)
        plan.update(directory=str(directory), python=sys.executable, summary_path=str(directory / "summary.json"))
        plan_path = directory / "plan.json"
        atomic_json(plan_path, plan)
        config_path = directory / "workflow.json"
        atomic_json(config_path, {"workflow_mode": "resources", "resource_plan": str(plan_path)})
        scheduler_error = None
        try:
            with as_file(files("basis").joinpath("resources", "Snakefile")) as snakefile:
                command = [sys.executable, "-m", "snakemake", "--snakefile", str(snakefile), "--configfile", str(config_path),
                           "--directory", str(directory), "--cores", str(args.cores), "--keep-going", "--rerun-incomplete", "--printshellcmds"]
                if any(r["backend"] != "pinned_files" and not r.get("environment_prefix") for r in plan["resources"]):
                    command.append("--use-conda")
                    if args.conda_prefix:
                        command.extend(["--conda-prefix", str(workspace_path(args.conda_prefix))])
                command.append("all")
                print("Resource workflow: " + str(directory), flush=True)
                from .environment_workflow import _command
                response = _command(command, directory / "scheduler.log", source_imports=True,
                                    timeout=sum(r["timeout_seconds"] for r in plan["resources"]) + 7200)
                status = response.returncode
                if status:
                    scheduler_error = "Snakemake exited with code " + str(status)
        except Exception as exc:
            scheduler_error = str(exc)
        summary = summarize_resources(plan_path, scheduler_error)
        print("Resource summary: " + summary["summary_path"], flush=True)
        print("Resources: " + summary["status"] + "; analysis readiness requires study preflight.", flush=True)
        return 0 if summary["status"] == "completed" else 2
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print("Resource preparation stopped: " + str(exc), file=sys.stderr)
        return 2


def _worker_main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("worker", "summary"))
    parser.add_argument("--plan", required=True)
    parser.add_argument("--resource")
    parser.add_argument("--threads", type=int)
    args = parser.parse_args()
    if args.operation == "worker":
        prepare_resource(args.plan, args.resource, allocated_threads=args.threads)
    else:
        summarize_resources(args.plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main())
