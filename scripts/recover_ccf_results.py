#!/usr/bin/env python3
"""Recover completed local command output after an interrupted result transfer.

The default command only prepares a plan. --apply copies and verifies retained
output on a Linux filesystem, without launching any native analysis command.
Original temporary files and the interrupted destination remain untouched.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor"), str(ROOT / "scripts")]

from archive_run_results import active_workflows
from safety import ccf_assembly as assembly, extend, result_store, runtime
from basis import inputs, normalize, util

RECOVERY_SCHEMA = "basis-ccf-transfer-recovery-v1"


def _progress(message):
    print(message, file=sys.stderr, flush=True)


def _absolute(value):
    return Path(os.path.abspath(Path(value).expanduser()))


def _read_object(path):
    value = runtime.read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _filesystem(path, mountinfo=None):
    """Find the actual containing mount, including paths reached via symlinks."""
    path = Path(path).resolve()
    mountinfo = (Path("/proc/self/mountinfo").read_text() if mountinfo is None else mountinfo)
    matches = []
    for line in mountinfo.splitlines():
        before, separator, after = line.partition(" - ")
        fields, tail = before.split(), after.split()
        if not separator or len(fields) < 5 or not tail:
            continue
        mount = Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4]))
        if path.is_relative_to(mount):
            matches.append((len(mount.parts), tail[0]))
    if not matches:
        raise ValueError(f"Cannot identify the filesystem for {path}")
    return max(matches)[1].lower()


def _require_local(path):
    kind = _filesystem(path)
    if kind in {"9p", "drvfs", "ntfs", "ntfs3", "fuseblk", "cifs", "smb3", "nfs", "nfs4"}:
        raise ValueError(f"Recovery requires a local Linux filesystem, not {kind}: {path}")


def _require_stopped():
    active = active_workflows()
    if active:
        raise ValueError("Workflow processes are still running (PIDs: " +
            ", ".join(str(row["pid"]) for row in active) + "). Stop them and wait before recovery.")


@contextmanager
def _original_lock(root):
    path = root / ".lock"
    mode = path.lstat()
    if not stat.S_ISREG(mode.st_mode) or mode.st_uid != os.getuid():
        raise ValueError(f"Original result-store lock is not an owned regular file: {path}")
    with path.open("r+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Original result store is still locked by another process") from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _identity(started, sample_path, config_path):
    original = started["identity"]
    sample = _read_object(sample_path)
    config = copy.deepcopy(_read_object(config_path))
    settings = config.get("modules", {}).get("crisprcasfinder")
    if not isinstance(settings, dict):
        raise ValueError("Saved configuration has no crisprcasfinder settings")
    if not settings.get("enabled", True) or config.get("module_decisions", {}).get(
            "crisprcasfinder", {}).get("action", "run") != "run":
        raise ValueError("Current saved configuration does not permit this module")
    if settings.get("cache", True) is not True:
        raise ValueError("Recovery requires cache reuse enabled to avoid rerunning the native command")
    threads = original.get("settings", {}).get("threads")
    if type(threads) is not int or threads < 1:
        raise ValueError("Original execution has no valid thread allocation")
    settings["threads"] = threads  # Snakemake's actual allocation, as in run_assembly.
    required_sources = {str(Path(module.__file__).resolve()) for module in
                        (assembly, assembly.ccf, inputs, normalize, util, extend, result_store, runtime)}
    source_paths = original.get("sources", {})
    if not isinstance(source_paths, dict) or required_sources != set(source_paths):
        raise ValueError("Recovery must use the original checkout and all original parser sources")
    executable = original.get("executable", {}).get("path")
    extras = original.get("extra_inputs", {})
    if not executable or not isinstance(extras, dict) or len(extras) != 2:
        raise ValueError("Original native executable identities are incomplete")
    current = result_store.execution_identity("crisprcasfinder", sample, config,
        sources=sorted(source_paths), extra_inputs=sorted(extras), executable=executable)
    if current != original:
        raise ValueError("Inputs, configuration, resources, environment or source identity changed; recovery refused")
    return sample, config, settings


def _load(attempt, sample, config, store, native, receipt):
    paths = {key: _absolute(value) for key, value in
             dict(attempt=attempt, sample=sample, config=config, store=store,
                  native=native, receipt=receipt).items()}
    attempt, store = paths["attempt"], paths["store"].resolve()
    paths["store"] = store
    if (attempt.is_symlink() or attempt.resolve() != attempt or not attempt.is_dir()
            or not re.fullmatch(r"attempt-[0-9a-f]{32}", attempt.name)
            or not re.fullmatch(r"[0-9a-f]{64}", attempt.parent.name)):
        raise ValueError("Expected an ordinary original result-store attempt directory")
    if paths["sample"].name != "sample.json" or paths["sample"].parent.name != "metadata":
        raise ValueError("Use the sample's existing safety/metadata/sample.json")
    safety = paths["sample"].parent.parent
    if (paths["native"] != safety / "native/crisprcasfinder"
            or paths["receipt"] != safety / "completion/crisprcasfinder.json"
            or not paths["config"].is_relative_to(safety / "metadata")):
        raise ValueError("Metadata, native destination and receipt must belong to the same sample")
    original_root = attempt.parent.parent
    protected = [attempt.resolve(), original_root.resolve(), (safety / "metadata").resolve(),
                 paths["native"].parent.resolve() / paths["native"].name,
                 paths["receipt"].parent.resolve() / paths["receipt"].name]
    if any(store == path or store.is_relative_to(path)
           or path.is_relative_to(store) for path in protected):
        raise ValueError("New result store overlaps an existing input or result location")
    _require_local(store)
    started = _read_object(attempt / "started.json")
    if (started.get("schema") != result_store.SCHEMA or started.get("status") != "started"
            or started.get("name") != "crisprcasfinder" or not isinstance(started.get("identity"), dict)
            or started.get("key") != attempt.parent.name
            or result_store.digest(started["identity"]) != started["key"]):
        raise ValueError("Original attempt identity is incomplete or inconsistent")
    sample_value, config_value, settings = _identity(started, paths["sample"], paths["config"])
    commands = runtime.read_json(attempt / "commands.json")
    if not isinstance(commands, list) or len(commands) != 1 or not isinstance(commands[0], dict):
        raise ValueError("Expected exactly one recorded native command")
    command = commands[0]
    expected = ["python", "basis_ccf_launch.py", settings.get("executable", "perl"),
        "CRISPRCasFinder.pl", "-in", "input.fna", "-out", "native", "-so", "./sel392v2.so",
        "-cas", "-keep", "-log", "-cpuM", str(settings["threads"]),
        "-gcode", str(assembly.ccf._int(settings.get("genetic_code", 11), "genetic code", 1))]
    if (command.get("status") != "completed" or type(command.get("returncode")) is not int
            or command["returncode"] != 0 or command.get("argv") != expected
            or not command.get("started_at") or not command.get("finished_at")):
        raise ValueError("Original native command did not complete successfully with the expected arguments")
    source = Path(command.get("cwd", ""))
    if (not source.is_absolute() or source.parent != Path("/tmp")
            or not re.fullmatch(r"basis-ccf-assembly-[A-Za-z0-9_-]+", source.name)):
        raise ValueError("Recorded source is not an expected temporary assembly directory")
    if source == store or store.is_relative_to(source) or source.is_relative_to(store):
        raise ValueError("Recovery source and new result store overlap")
    context = dict(paths=paths, started=started, command=command, source=source,
                   original_root=original_root, sample=sample_value, config=config_value, settings=settings)
    # A completed receipt is deliberately left where it is. Do not traverse a
    # large Windows payload just to decide that no recovery mutation is needed.
    if os.path.lexists(paths["receipt"]):
        row = result_store._record(paths["receipt"], expected_identity=started["identity"],
                                   destination=paths["native"])
        result_store._check_link(row)
        context["already_completed"] = True
        return context
    if source.is_symlink() or not source.is_dir() or source.resolve() != source:
        raise ValueError(f"Complete original temporary output is unavailable: {source}")
    if source.stat().st_uid != os.getuid():
        raise ValueError("Original temporary output is not owned by the current user")
    _require_local(source)
    isolation = _read_object(source / "runtime_isolation.json")
    if isolation.get("working_directory") != str(source):
        raise ValueError("Temporary output does not belong to the recorded native command")
    if (isolation.get("macsyfinder_executable") not in started["identity"]["extra_inputs"]
            or str(Path(isolation.get("python", "")).resolve()) not in started["identity"]["extra_inputs"]):
        raise ValueError("Temporary output native executables differ from the recorded execution identity")
    _check_destination(context)
    return context


def _check_destination(context):
    paths, started = context["paths"], context["started"]
    cache = paths["store"] / started["key"] / "cache.json"
    if cache.exists():
        row = result_store._record(cache, expected_identity=started["identity"], destination=paths["native"])
        recovery = _read_object(Path(row["payload_root"]).parent / "recovery.json")
        if (recovery.get("schema") != RECOVERY_SCHEMA or recovery.get("original_attempt") != str(paths["attempt"])
                or recovery.get("identity_key") != started["key"]):
            raise ValueError("Existing local cache belongs to a different recovery")
        if os.path.lexists(paths["native"]):
            result_store._check_link(row)
    elif os.path.lexists(paths["native"]):
        raise ValueError("Native destination already exists without this recovery's verified cache; no overwrite allowed")


def build_plan(attempt, sample, config, store, native, receipt):
    context = _load(attempt, sample, config, store, native, receipt)
    return {"schema": RECOVERY_SCHEMA, **{key: str(path) for key, path in context["paths"].items()},
            "source": str(context["source"]), "identity_key": context["started"]["key"],
            "status": "already_completed" if context.get("already_completed") else "ready_for_validation",
            "active_workflows": active_workflows(), "native_command_will_run": False,
            "original_files_will_be_removed": False}


def apply_plan(plan):
    if plan.get("schema") != RECOVERY_SCHEMA:
        raise ValueError("Unrecognized recovery plan")
    _require_stopped()
    fields = ("attempt", "sample", "config", "store", "native", "receipt")
    context = _load(**{key: plan[key] for key in fields})
    if context["started"]["key"] != plan.get("identity_key") or str(context["source"]) != plan.get("source"):
        raise ValueError("Original recovery inputs changed after planning")
    if context.get("already_completed"):
        _progress("Completed receipt already exists; leaving this sample untouched.")
        return {"status": "already_completed", "receipt": str(context["paths"]["receipt"])}
    with _original_lock(context["original_root"]):
        _require_stopped()
        # Repeat metadata/path checks under the original writer lock.
        context = _load(**{key: plan[key] for key in fields})
        if context["started"]["key"] != plan.get("identity_key") or str(context["source"]) != plan.get("source"):
            raise ValueError("Original recovery inputs changed after planning")
        if context.get("already_completed"):
            return {"status": "already_completed", "receipt": str(context["paths"]["receipt"])}
        paths, source, started = context["paths"], context["source"], context["started"]
        logged = assembly.ccf._ANSI.sub("", (paths["attempt"] / "commands.log").read_text(
            encoding="utf-8", errors="replace"))
        if assembly.ccf._ERROR.search(logged):
            raise ValueError("Original command log records a native error; recovery refused")
        sequences = inputs.read_fasta(context["sample"]["assembly"])
        level = assembly.ccf._int(context["settings"].get("min_evidence_level", 3), "minimum array evidence level", 1)
        if level > 4:
            raise ValueError("Minimum array evidence level must be 1..4")
        code = assembly.ccf._int(context["settings"].get("genetic_code", 11), "genetic code", 1)

        def verify_payload(payload, *, write_parsed):
            if result_store.sha256(payload / "CRISPRCasFinder.pl") != assembly.ccf.SOURCE_SHA256:
                raise ValueError("Retained native source does not match the pinned release")
            parsed = assembly.parse_assembly(payload, sequences, min_evidence_level=level,
                                            genetic_code=code, artifact_root=paths["native"])
            if write_parsed:
                runtime.write_json(payload / "parsed.json", parsed)
                model_link = payload / "models"
                if not model_link.is_symlink():
                    raise ValueError("Retained models entry must be the original directory symlink")
                model_link.unlink()

        _progress("Verifying retained temporary output on Linux; no native command will run.")
        verify_payload(source, write_parsed=False)
        _require_stopped()
        _check_destination(context)

        def execute(payload):
            _require_stopped()
            _check_destination(context)
            _progress("Copying verified temporary output into the Linux result store; preserving originals.")
            shutil.copytree(source, payload, dirs_exist_ok=True, symlinks=True)
            for name in ("commands.json", "commands.log"):
                shutil.copy2(paths["attempt"] / name, payload.parent / name)
            runtime.write_json(payload.parent / "recovery.json", {"schema": RECOVERY_SCHEMA,
                "original_attempt": str(paths["attempt"]), "original_temporary_output": str(source),
                "identity_key": started["key"], "original_started": started,
                "native_command": context["command"],
                "original_command_files": {name: result_store.sha256(paths["attempt"] / name)
                                           for name in ("commands.json", "commands.log")},
                "recovered_at": datetime.now(timezone.utc).isoformat(),
                "recovery_script_sha256": result_store.sha256(__file__)})

        def verify(payload):
            _progress("Verifying the local copy, then recording its complete file inventory.")
            verify_payload(payload, write_parsed=True)

        row, reused = result_store.run_cached("crisprcasfinder", started["identity"], paths["native"],
            paths["receipt"], paths["store"], execute, verify,
            failed_retention=context["settings"].get("failed_attempts_to_keep", 3))
        _progress("Recovery completed; original temporary output and interrupted Windows files were retained.")
        return {"status": "completed", "reused": reused, "receipt": str(paths["receipt"]),
                "payload_root": row["payload_root"], "native_command_ran": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("attempt", "sample", "config", "store", "native", "receipt"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--apply", action="store_true", help="Copy, validate and publish the recovery")
    args = parser.parse_args(argv)
    plan = build_plan(**{key: getattr(args, key) for key in ("attempt", "sample", "config", "store", "native", "receipt")})
    print(json.dumps(apply_plan(plan) if args.apply else plan, indent=2))


if __name__ == "__main__":
    main()
