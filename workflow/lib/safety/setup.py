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


def validate(sample, config, directory):
    directory = Path(directory)
    validation = validate_sample(sample)
    sample = copy.deepcopy(sample)
    sample["input_validation"] = validation
    write_json(directory / "validation.json", validation)
    write_json(directory / "sample.json", sample)
    write_json(directory / "requested-config.json", config)


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


def finalize(directory, resources, environments):
    directory = Path(directory)
    cfg = read_json(directory / "requested-config.json")
    resource_ids = []
    for path in resources:
        receipt = verify_receipt(path)
        result = receipt["result"]
        for module, fields in result.get("module_updates", {}).items():
            cfg["modules"][module].update(fields)
        # Providers supply paths and provenance. They cannot silently override
        # a user's analysis settings or already configured reference panels.
        for key, value in result.get("deep_updates", {}).items():
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
    write_json(directory / "config.json", cfg)
    # Keep local copies for a portable review bundle; global resources remain
    # shared and are never copied into each isolate's result tree.
    provenance = directory / "resources"
    for path in resources:
        shutil.copy2(path, _destination(provenance / (Path(path).parent.name + ".json")))
    for path in environments:
        shutil.copy2(path, _destination(directory / "environments" / Path(path).name))
    write_json(directory / "setup.ready.json", {"schema": "basis-snake-setup-v1", "status": "completed",
        "resources": resource_ids, "environments": list(map(str, environments)),
        "inputs": read_json(directory / "validation.json")["input_hashes"],
        "finished_at": datetime.now(timezone.utc).isoformat()})


def _destination(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="task", required=True)
    check = sub.add_parser("validate")
    check.add_argument("--sample-json", required=True)
    check.add_argument("--config-json", required=True)
    check.add_argument("--directory", required=True)
    capture = sub.add_parser("environment")
    capture.add_argument("--recipe", required=True)
    capture.add_argument("--output", required=True)
    ready = sub.add_parser("finalize")
    ready.add_argument("--directory", required=True)
    ready.add_argument("--resources", nargs="*", default=[])
    ready.add_argument("--environments", nargs="*", default=[])
    args = parser.parse_args()
    if args.task == "validate":
        validate(json.loads(args.sample_json), json.loads(args.config_json), args.directory)
    elif args.task == "environment":
        environment(args.recipe, args.output)
    else:
        finalize(args.directory, args.resources, args.environments)


if __name__ == "__main__":
    main()
