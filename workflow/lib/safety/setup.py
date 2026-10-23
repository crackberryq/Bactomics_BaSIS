"""Input checks and provenance capture as ordinary Snakemake jobs."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys

from basis.inputs import validate_sample
from .runtime import read_json, write_json
from .resources import verify_receipt


def write_stable(path, value):
    """Publish a changed document without touching an equivalent saved file."""
    path = Path(path)
    if path.is_file():
        try:
            if read_json(path) == value:
                return False
        except (ValueError, OSError):
            pass
    write_json(path, value)
    return True


def validate(sample, config, directory):
    directory = Path(directory)
    validation = validate_sample(sample)
    sample = copy.deepcopy(sample)
    sample["input_validation"] = validation
    write_stable(directory / "validation.json", validation)
    write_stable(directory / "sample.json", sample)
    # Retain the callable's compatibility for older callers. The workflow now
    # captures policy in a separate rule, so it cannot rerun input validation.
    if config is not None:
        write_stable(directory / "requested-config.json", config)


def environment(recipe, output):
    prefix = Path(os.environ.get("CONDA_PREFIX") or sys.prefix).resolve()
    packages = []
    for file in sorted((prefix / "conda-meta").glob("*.json")):
        record = read_json(file)
        packages.append({key: record.get(key) for key in
                         ("name", "version", "build", "channel", "url", "sha256", "md5")})
    recipe = Path(recipe)
    post = recipe.with_suffix(".post-deploy.sh")
    record = {"schema": "basis-snake-environment-v1", "status": "completed",
        "prefix": str(prefix), "recipe": str(recipe),
        "recipe_sha256": hashlib.sha256(recipe.read_bytes()).hexdigest(),
        "post_deploy_sha256": hashlib.sha256(post.read_bytes()).hexdigest(),
        "packages": packages, "conda_managed": bool(packages),
        "platform": platform.platform(), "machine": platform.machine()}
    output = Path(output)
    if output.is_file():
        previous = read_json(output)
        if {key: value for key, value in previous.items() if key != "created_at"} == record:
            return  # Stable contents and mtime avoid invalidating database jobs.
    record["created_at"] = datetime.now(timezone.utc).isoformat()
    write_json(output, record)


def resolved_config(config, resources, environments):
    cfg = copy.deepcopy(config)
    resource_ids = []
    for path in resources:
        receipt = verify_receipt(path)
        result = receipt["result"]
        for module, fields in result.get("module_updates", {}).items():
            if module in cfg.get("modules", {}):
                cfg["modules"][module].update(fields)
        # Providers supply paths and provenance. They cannot silently override
        # a user's analysis settings or already configured reference panels.
        for key, value in result.get("deep_updates", {}).items():
            if "deep" not in cfg:
                continue
            if isinstance(value, dict) and isinstance(cfg["deep"].get(key), dict):
                cfg["deep"][key] = {**value, **cfg["deep"][key]}
            elif cfg["deep"].get(key) is None:
                cfg["deep"][key] = value
        resource_ids.append({"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()})
    captures = [read_json(path) for path in environments]
    if any(row.get("status") != "completed" for row in captures):
        raise ValueError("Not all requested environments have completed verification")
    cfg["resource_receipts"] = resource_ids
    cfg["environment_receipts"] = list(map(str, environments))
    return cfg


def module_config(config, output, resources=(), environments=()):
    write_stable(output, resolved_config(config, resources, environments))


def finalize(directory, resources, environments):
    directory = Path(directory)
    cfg = resolved_config(read_json(directory / "requested-config.json"), resources, environments)
    write_stable(directory / "config.json", cfg)
    # Keep local copies for a portable review bundle; global resources remain
    # shared and are never copied into each isolate's result tree.
    provenance = directory / "resources"
    for path in resources:
        shutil.copy2(path, _destination(provenance / (Path(path).parent.name + ".json")))
    for path in environments:
        shutil.copy2(path, _destination(directory / "environments" / Path(path).name))
    ready = {"schema": "basis-snake-setup-v1", "status": "completed",
        "resources": cfg["resource_receipts"], "environments": list(map(str, environments)),
        "inputs": read_json(directory / "validation.json")["input_hashes"]}
    path = directory / "setup.ready.json"
    if path.is_file():
        previous = read_json(path)
        if {key: value for key, value in previous.items() if key != "finished_at"} == ready:
            return
    ready["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(path, ready)


def _destination(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="task", required=True)
    check = sub.add_parser("validate")
    check.add_argument("--sample-json", required=True)
    check.add_argument("--config-json")
    check.add_argument("--directory", required=True)
    capture = sub.add_parser("environment")
    capture.add_argument("--recipe", required=True)
    capture.add_argument("--output", required=True)
    requested = sub.add_parser("requested")
    requested.add_argument("--config-json", required=True)
    requested.add_argument("--output", required=True)
    scoped = sub.add_parser("module")
    scoped.add_argument("--config-json", required=True)
    scoped.add_argument("--output", required=True)
    scoped.add_argument("--resources", nargs="*", default=[])
    scoped.add_argument("--environments", nargs="*", default=[])
    ready = sub.add_parser("finalize")
    ready.add_argument("--directory", required=True)
    ready.add_argument("--resources", nargs="*", default=[])
    ready.add_argument("--environments", nargs="*", default=[])
    args = parser.parse_args()
    if args.task == "validate":
        validate(json.loads(args.sample_json), json.loads(args.config_json) if args.config_json else None,
                 args.directory)
    elif args.task == "environment":
        environment(args.recipe, args.output)
    elif args.task == "requested":
        write_stable(args.output, json.loads(args.config_json))
    elif args.task == "module":
        module_config(json.loads(args.config_json), args.output, args.resources, args.environments)
    else:
        finalize(args.directory, args.resources, args.environments)


if __name__ == "__main__":
    main()
