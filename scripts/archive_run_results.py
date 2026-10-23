#!/usr/bin/env python3
"""Preview, then explicitly archive selected result folders before a clean run.

Only whole ``<isolate>/safety`` directories are renamed to same-parent archives.
Databases, environments, annotations and workflow configuration are never moved.
Archives preserve files; absolute links in historical HTML/JSON are not rewritten,
so an archive is not automatically a relocated, runnable report bundle.
Checking every result entry can take time for large trees on mounted drives.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from basis.config import PATH_KEYS
from safety.context import SafetyContext
from safety.doctor import load_configuration
from safety.native import EXECUTABLES
from safety.resources import external_inputs
from safety.runtime import write_json


def _workflow_command(argv):
    names = {"snakemake", "basis_ccf_launch.py", "CRISPRCasFinder.pl", "macsyfinder",
             "vmatch", "mkvtree", "vsubseqselect", "fuzznuc", "needle", "hmmsearch",
             "prodigal", "pathogenfinder2", *EXECUTABLES.values()}
    modules = {"snakemake", "safety.extend", "safety.ccf_assembly", "safety.ccf_batches",
               "safety.native_execution", "safety.integron", "safety.deep", "safety.runtime",
               "safety.setup", "safety.resources", "safety.deep_resources", "safety.pf2_classify"}
    if any(Path(value).name in names for value in argv):
        return True
    if any(value == "-m" and argv[index + 1] in modules
           for index, value in enumerate(argv[:-1])):
        return True
    if argv and Path(argv[0]).name in {"bash", "sh", "dash"} and "-c" in argv:
        index = argv.index("-c") + 1
        if index < len(argv):
            try:
                return _workflow_command(shlex.split(argv[index]))
            except ValueError:
                # An unreadable command which mentions a workflow is not safe
                # evidence that all writers have stopped.
                return bool(re.search(r"\b(?:snakemake|safety\.(?:extend|deep|native_execution|integron))\b", argv[index]))
    return False


def active_workflows():
    """Read Linux /proc without launching or signalling another process."""
    proc = Path("/proc")
    if not proc.is_dir() or not hasattr(os, "getuid"):
        raise RuntimeError("Process checks require Linux/WSL /proc; no results were moved")
    found = []
    for folder in proc.iterdir():
        if not folder.name.isdecimal() or int(folder.name) == os.getpid():
            continue
        try:
            if folder.stat().st_uid != os.getuid():
                continue
            argv = [value.decode("utf-8", errors="replace")
                    for value in (folder / "cmdline").read_bytes().split(b"\0") if value]
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as error:
            raise RuntimeError(f"Cannot inspect current-user process {folder.name}; no results were moved") from error
        if _workflow_command(argv):
            found.append({"pid": int(folder.name), "program": Path(argv[0]).name})
    return found


def _require_stopped():
    active = active_workflows()
    if active:
        raise ValueError("Workflow processes are still running (PIDs: " +
                         ", ".join(str(row["pid"]) for row in active) +
                         "). Stop them cleanly and wait before archiving.")


def _declared_paths(value):
    if isinstance(value, dict):
        for name, item in value.items():
            if name in PATH_KEYS and isinstance(item, str) and item:
                yield Path(item).expanduser().resolve()
            elif isinstance(item, (dict, list)):
                yield from _declared_paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from _declared_paths(item)


def _walk_error(error):
    raise error


def _preflight(plan, *, scan_contents=True):
    sources = [Path(row["source"]) for row in plan["results"]]
    for source in sources:
        if source.name != "safety" or source.parent == source:
            raise ValueError(f"Unexpected result path: {source}")
        if any(other != source and source.is_relative_to(other) for other in sources):
            raise ValueError("Selected result folders overlap; no results were moved")
        for protected in plan["protected_paths"]:
            if Path(protected).is_relative_to(source):
                raise ValueError(f"A required input, resource or environment lies inside {source}: {protected}")
    for row in plan["results"]:
        source, destination = Path(row["source"]), Path(row["destination"])
        if destination.parent != source.parent or not destination.name.startswith("safety.before-"):
            raise ValueError("Archives must be same-parent safety.before-* directories")
        if os.path.lexists(destination):
            raise FileExistsError(f"Archive destination already exists: {destination}")
        if source.is_symlink():
            raise ValueError(f"Refusing a symlink result root: {source}")
        if not source.exists():
            row["status"] = "absent"
            continue
        if not source.is_dir() or source.resolve() != source:
            raise ValueError(f"Result path is not an unambiguous directory: {source}")
        if source.stat().st_dev != source.parent.stat().st_dev:
            raise ValueError(f"Result directory is a separate filesystem: {source}")
        if not scan_contents:
            row["status"] = "ready"
            continue
        for parent, directories, files in os.walk(source, followlinks=False, onerror=_walk_error):
            for name in directories + files:
                item = Path(parent) / name
                mode = item.lstat().st_mode
                if stat.S_ISLNK(mode):
                    target = item.resolve()
                    # Absolute internal links would still address the old live
                    # folder after it was renamed (or a future new run there).
                    if Path(os.readlink(item)).is_absolute() and any(target.is_relative_to(root) for root in sources):
                        raise ValueError(f"Archiving would break an internal absolute symlink: {item}")
                    expected = target
                    for other in plan["results"]:
                        other_source = Path(other["source"])
                        if target.is_relative_to(other_source):
                            expected = Path(other["destination"]) / target.relative_to(other_source)
                    future = (destination / item.relative_to(source)).parent / os.readlink(item)
                    if future.resolve() != expected:
                        raise ValueError(f"Archiving would change a symlink target: {item}")
                    if not target.exists():
                        raise ValueError(f"Result bundle contains a dangling symlink: {item}")
                elif not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    raise ValueError(f"Result bundle contains a special file: {item}")
        row["status"] = "ready"


def build_plan(root, configfiles=(), *, stamp=None, scan_contents=True):
    root = Path(root).resolve()
    config, files = load_configuration(root, configfiles)
    context = SafetyContext(config, root)
    if not context.samples:
        raise ValueError("No samples selected; no result folders to archive")
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", stamp):
        raise ValueError("Invalid archive stamp")
    protected = {root, Path(context.resource_root).resolve(), *(Path(path).resolve() for path in files)}
    if config.get("samples"):
        sample_sheet = Path(os.path.expandvars(config["samples"])).expanduser()
        protected.add((root / sample_sheet).resolve())
    protected.update(_declared_paths(context.config))
    for sample in context.samples.values():
        protected.update(Path(sample[key]).resolve() for key in
                         ("assembly", "proteins", "gff", "genbank", "hybas_dir") if sample.get(key))
    for name, spec in context.resource_specs().items():
        protected.update(Path(path).resolve() for path in external_inputs(spec))
        receipt = Path(context.resource_receipt(name))
        if receipt.is_file():
            record = json.loads(receipt.read_text())
            if record.get("payload_root"):
                protected.add(Path(record["payload_root"]).resolve())
            protected.update(_declared_paths(record.get("result", {})))
    for receipt in context.environment_receipts():
        if Path(receipt).is_file():
            record = json.loads(Path(receipt).read_text())
            if record.get("prefix"):
                protected.add(Path(record["prefix"]).resolve())
    plan = {"schema": "basis-result-archive-v1", "status": "preview", "workflow_root": str(root),
            "configfiles": files, "protected_paths": sorted(map(str, protected)),
            "manifest": str(root / "archive-reviews" / (stamp + ".json")),
            "results": [{"sample_id": sample["sample_id"], "source": str(Path(isolate) / "safety"),
                         "destination": str(Path(isolate) / ("safety.before-" + stamp))}
                        for isolate, sample in context.samples.items()]}
    _require_stopped()
    _preflight(plan, scan_contents=scan_contents)
    return plan


def apply_plan(plan):
    """Rename after a complete recheck; journal each completed rename."""
    _require_stopped()
    _preflight(plan, scan_contents=False)
    if not any(row["status"] == "ready" for row in plan["results"]):
        plan["status"] = "nothing_to_archive"
        return plan
    manifest = Path(plan["manifest"])
    if manifest.parent.is_symlink() or (manifest.parent.exists() and not manifest.parent.is_dir()):
        raise ValueError("Archive review directory must be an ordinary directory")
    manifest.parent.mkdir(exist_ok=True)
    lock_path = manifest.parent / ".lock"
    if lock_path.is_symlink() or (lock_path.exists() and not lock_path.is_file()):
        raise ValueError("Archive lock must be an ordinary file")
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another archive operation is active") from error
        _require_stopped()
        _preflight(plan)
        # Large historical result trees can take time to scan, especially on
        # WSL-mounted disks. Recheck writers immediately before the first move.
        _require_stopped()
        # Exclusive creation prevents overwriting a previous archive journal.
        with manifest.open("x") as handle:
            plan["status"] = "archiving"
            json.dump(plan, handle, indent=2)
            handle.write("\n")
        try:
            for row in plan["results"]:
                if row["status"] == "absent":
                    continue
                source, destination = Path(row["source"]), Path(row["destination"])
                if source.is_symlink() or not source.is_dir() or os.path.lexists(destination):
                    raise ValueError(f"Result paths changed after preflight: {source}")
                source.rename(destination)
                row["status"] = "archived"
                write_json(manifest, plan)
            plan["status"] = "archived"
            write_json(manifest, plan)
        except BaseException as error:
            plan["status"] = "interrupted"
            plan["error"] = str(error) or type(error).__name__
            try:
                write_json(manifest, plan)
            finally:
                moved = [row["destination"] for row in plan["results"] if row["status"] == "archived"]
                print("Archive interrupted; no files were deleted. Review " + str(manifest) +
                      ". Completed moves: " + json.dumps(moved), file=sys.stderr)
            raise
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-root", type=Path, default=ROOT)
    parser.add_argument("--configfile", type=Path, action="append", nargs="+", default=[])
    parser.add_argument("--apply", action="store_true", help="Rename the selected safety folders after all checks pass")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        # Applying scans every result tree once, under the lock. A standalone
        # preview scans in full too; it never authorizes a later unchecked move.
        plan = build_plan(args.workflow_root, [path for group in args.configfile for path in group],
                          scan_contents=not args.apply)
        if args.apply:
            apply_plan(plan)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
        parser.exit(2, "Archive stopped: " + str(error) + "\n")
    if args.json:
        print(json.dumps(plan, indent=2))
    else:
        print("Result archive: " + plan["status"])
        for row in plan["results"]:
            print(f"{row['sample_id']}: {row['status']}: {row['source']} -> {row['destination']}")
        if not args.apply:
            print("Preview only. Repeat with --apply to archive; no files were changed.")
        elif plan["status"] == "archived":
            print("Archive journal: " + plan["manifest"])
        print("Historical absolute report paths are retained; archives are not automatically relocated report bundles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
