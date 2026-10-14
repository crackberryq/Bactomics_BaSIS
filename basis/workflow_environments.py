"""Native Snakemake Conda deployment and legacy external-environment routing.

The workflow owns deployment. These helpers select recipes, verify the activated
prefix, and route native tools away from the controller's Python environment.
"""
from __future__ import annotations

import copy
from importlib.resources import files
import json
import os
from pathlib import Path
import shutil

import yaml

from .config import ALL_MODULES
from .environment_setup import recipe_names, workspace_path
from .sample_policy import module_decision
from .util import atomic_json, hash_object, now, sha256_file


MODULE_RECIPES = {
    "amrfinder": "core-amr", "rgi": "rgi", "vfdb": "diamond", "bacmet": "diamond",
    "antismash": "antismash", "genomad": "context", "integronfinder": "context",
    "mobsuite": "mobsuite", "isescan": "isescan", "icescreen": "icescreen",
    "minced": "minced", "pathogenfinder2": "pathogenfinder2", "deepsafe": "deepsafe",
}


def environment_for_recipe(name):
    if name not in recipe_names():
        raise ValueError("No supported native environment recipe: " + str(name))
    path = Path(str(files("basis").joinpath("resources", "envs", name + ".yml")))
    if not path.is_file():
        raise ValueError("Snakemake requires an installed filesystem recipe: " + str(path))
    return str(path.resolve())


def conda_storage(plan):
    config = plan.get("config", {})
    supplied = config.get("execution", {}).get("conda_prefix")
    root = supplied or str(Path(plan["outdir"]).parent / ".basis-envs")
    return str(workspace_path(root))


def backend_for_job(plan, sample_id, module):
    config = plan["config"]
    cfg = config["deep"] if module == "deepsafe" else config["modules"][module]
    if config.get("validation_mode") == "synthetic_fixture":
        return "none"
    if module == "deepsafe":
        if not plan.get("options", {}).get("deep"):
            return "none"
    elif module_decision(plan, sample_id, module)["action"] != "run":
        return "none"
    if cfg.get("mode", "native") != "native":
        return "none"
    policy = cfg.get("environment_backend", config.get("execution", {}).get("environment_backend", "auto"))
    if policy not in {"auto", "external", "managed"}:
        raise ValueError("Invalid environment backend: " + str(policy))
    explicit = bool(cfg.get("command_prefix"))
    if explicit and policy == "managed":
        raise ValueError("Managed deployment conflicts with an explicit command_prefix; preserve it or explicitly remove it")
    if explicit or policy == "external":
        return "external"
    if module not in MODULE_RECIPES:
        raise ValueError("Managed environment unavailable for unsupported module: " + module)
    return "managed"


def environment_for_job(plan, sample_id, module):
    return environment_for_recipe(MODULE_RECIPES[module]) if backend_for_job(plan, sample_id, module) == "managed" else None


def has_managed_environments(plan):
    modules = ALL_MODULES + (["deepsafe"] if plan.get("options", {}).get("deep") else [])
    return any(environment_for_job(plan, sample["sample_id"], module) is not None
               for sample in plan["samples"] for module in modules)


def managed_prefix(expected_root=None):
    value = os.environ.get("CONDA_PREFIX")
    if not value:
        raise ValueError("Snakemake did not activate the requested Conda environment")
    prefix = Path(value).expanduser().resolve()
    if expected_root is not None and not prefix.is_relative_to(Path(expected_root).expanduser().resolve()):
        raise ValueError("Activated Conda environment is outside the configured managed environment root")
    if not (prefix / "conda-meta/history").is_file():
        raise ValueError("Activated environment lacks a completed Conda transaction history: " + str(prefix))
    return prefix


def command_prefix_for_active(expected_root=None):
    prefix = managed_prefix(expected_root)
    executable = os.environ.get("CONDA_EXE") or shutil.which("conda")
    if not executable or not Path(executable).is_file():
        raise ValueError("Conda executable unavailable for the activated native environment")
    return [str(Path(executable).resolve()), "run", "--no-capture-output", "--prefix", str(prefix)]


def active_environment(recipe_name, expected_root=None):
    """Return explicit routing for an environment activated by a conda: rule."""
    recipe = environment_for_recipe(recipe_name)
    prefix = managed_prefix(expected_root)
    return {"backend": "managed", "recipe_name": recipe_name, "recipe": recipe,
            "recipe_sha256": sha256_file(recipe), "prefix": str(prefix),
            "command_prefix": command_prefix_for_active(expected_root)}


def execution_config(plan, sample_id, module, cfg):
    """Return a copy with verified managed routing; external configs are preserved."""
    result = copy.deepcopy(cfg)
    backend = backend_for_job(plan, sample_id, module)
    if backend != "managed":
        return result
    from .environment_workflow import verify
    active = active_environment(MODULE_RECIPES[module], conda_storage(plan))
    prefix = Path(active["prefix"])
    # Configured executables may be names or paths within this exact environment;
    # do not silently route a requested external executable through a managed env.
    paths = [result.get("executable"), result.get("python_executable")]
    paths.extend((result.get("executables") or {}).values())
    for executable in paths:
        if executable and "/" in str(executable) and not Path(executable).resolve().is_relative_to(prefix):
            raise ValueError("Configured executable is outside managed environment: " + str(executable))
    run_id = os.environ.get("BASIS_SCHEDULER_RUN_ID", plan.get("plan_hash", "environment-check"))
    if Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("Invalid environment verification run identifier")
    root = Path(plan["outdir"]) / "provenance" / "environments" / run_id / sample_id
    root.mkdir(parents=True, exist_ok=True)
    log = root / (module + ".log")
    verification = verify(prefix, yaml.safe_load(Path(active["recipe"]).read_text()),
                          active["recipe_name"], {"manager_executable": active["command_prefix"][0]}, log)
    manifest = {"schema": "basis-managed-environment-v1", **active, "sample_id": sample_id,
                "module": module, "plan_hash": plan.get("plan_hash"), "verified_at": now(),
                "verification": verification,
                "scope": "recipe constraints and executable startup; databases/models/GPU not assessed"}
    path = root / (module + ".json")
    atomic_json(path, manifest)
    result["command_prefix"] = active["command_prefix"]
    # Bind logical IQ-TREE to the actual supported installed alias.
    if module == "deepsafe":
        iqtree = next(p["executable"] for p in verification["probes"]
                      if Path(p["executable"]).name in {"iqtree", "iqtree2"})
        result.setdefault("executables", {}).setdefault("iqtree", iqtree)
    result["environment_provenance"] = {"backend": "managed", "prefix": str(prefix),
        "recipe_name": active["recipe_name"], "recipe_sha256": active["recipe_sha256"],
        "metadata_sha256": hash_object(verification["metadata_sha256"])}
    return result
