"""Plan isolated tool environments using the recipes bundled with BaSIS.

Recipes are installation starting points, not solved environment locks. Existing
prefixes must pass installed-package and executable checks before being reused.
Package data is read-only; destinations belong to an explicit workspace.
"""
from __future__ import annotations

import argparse
import os
from importlib.resources import as_file, files
from pathlib import Path
import shlex
import shutil
import subprocess
import sysconfig


DEFAULT_ENVIRONMENTS = ("core-amr", "diamond", "context", "deepsafe")


def workspace_path(value):
    """Resolve a writable destination, refusing package and installation trees."""
    path = Path(value).expanduser().resolve()
    protected = {Path(__file__).resolve().parent}
    for key in ("purelib", "platlib"):
        location = sysconfig.get_path(key)
        if location:
            protected.add(Path(location).resolve())
    if any(path == root or root in path.parents for root in protected):
        raise ValueError("Choose a workspace outside the installed Python package: " + str(path))
    return path


def recipe_names():
    root = files("basis").joinpath("resources", "envs")
    return tuple(sorted(item.name[:-4] for item in root.iterdir()
                        if item.is_file() and item.name.endswith(".yml")))


def recipe_resource(name):
    if name not in recipe_names():
        raise ValueError("Unknown environment recipe: " + str(name))
    return files("basis").joinpath("resources", "envs", name + ".yml")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", "--workspace", dest="project", default=".",
                        help="Writable workspace; default: current working directory")
    parser.add_argument("--env", action="append", choices=recipe_names(), dest="environments",
                        help="Environment recipe to select; repeatable")
    parser.add_argument("--prefix-root", help="Environment parent; default: WORKSPACE/.basis-envs")
    parser.add_argument("--manager", choices=("conda", "mamba", "micromamba"), default="conda")
    parser.add_argument("--engine", choices=("snakemake", "local"), default="snakemake",
                        help="Snakemake schedules environment setup; local runs the same workers serially")
    parser.add_argument("--cores", type=int, default=1, help="Maximum concurrent setup workers (default: 1)")
    parser.add_argument("--report-dir", help="New directory for this setup attempt and summary")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Repair a failed/interrupted prefix created by this workspace with the same recipe")
    parser.add_argument("--create", action="store_true",
                        help="Create explicitly selected environments; default prints commands only")
    args = parser.parse_args(argv)
    if args.create and not args.environments:
        parser.error("--create requires one or more explicit --env selections")
    if args.cores < 1:
        parser.error("--cores must be positive")
    if args.retry_failed and not args.create:
        parser.error("--retry-failed requires --create")
    try:
        project = workspace_path(args.project)
        prefix_root = workspace_path(project / (args.prefix_root or ".basis-envs"))
        selected = tuple(dict.fromkeys(args.environments or DEFAULT_ENVIRONMENTS))
        if args.create:
            if not shutil.which(args.manager):
                raise ValueError("Package manager is not on PATH: " + args.manager)
            from .environment_workflow import run_setup
            summary, code = run_setup(project, prefix_root, selected, manager=args.manager,
                                      engine=args.engine, cores=args.cores,
                                      retry_failed=args.retry_failed, report_dir=args.report_dir)
            print("Setup summary: " + summary["summary_path"], flush=True)
            print("Tool environments: " + summary["status"], flush=True)
            print("Databases and models: not assessed. Run study preflight before analysis.", flush=True)
            return code
        for name in selected:
            prefix = workspace_path(prefix_root / ("basis-" + name))
            # as_file also works for non-filesystem package providers. The recipe
            # remains available for the entire child process invocation.
            with as_file(recipe_resource(name)) as recipe:
                operation = ["create"] if args.manager == "micromamba" else ["env", "create"]
                command = [args.manager, *operation, "--prefix", str(prefix), "--file", str(recipe), "--yes"]
                print("\n# " + name, flush=True)
                print("CONDA_CHANNEL_PRIORITY=strict " + shlex.join(command), flush=True)
        if not args.create:
            print("\nPlan only: no environments, packages or databases were created or downloaded.")
        print("Recipes are starting points; use captured explicit locks to restore tested package versions.")
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print("Environment setup stopped: " + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
