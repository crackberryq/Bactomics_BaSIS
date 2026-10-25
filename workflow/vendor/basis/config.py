from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

from .util import resolve_path

CORE = ["amrfinder", "rgi", "vfdb", "antismash", "genomad", "mobsuite", "integronfinder"]
OPTIONAL = ["bacmet", "isescan", "icescreen", "crisprcasfinder", "minced", "mlst"]
ALL_MODULES = CORE + OPTIONAL + ["pathogenfinder2"]
EXECUTABLES = {"amrfinder": "amrfinder", "rgi": "rgi", "vfdb": "diamond", "bacmet": "diamond",
               "antismash": "antismash", "genomad": "genomad", "mobsuite": "mob_recon",
               "integronfinder": "integron_finder", "isescan": "isescan.py", "icescreen": "icescreen",
               "crisprcasfinder": "perl", "minced": "minced", "mlst": "mlst",
               "pathogenfinder2": "pathogenfinder2"}
PATH_KEYS = {"database", "database_manifest", "result", "profiles_dir", "reference_fasta", "reference_metadata",
             "prot_t5_path", "weights_model", "embeddings", "embeddings_manifest", "db_proteins", "metadata", "domain_database", "tree_review_registry", "motif_rules", "prosite_snapshot", "placement_registry", "placement_cache_dir"}


def default_config() -> dict:
    modules = {}
    for module in ALL_MODULES:
        modules[module] = {"enabled": module in CORE, "mode": "native", "executable": EXECUTABLES[module],
                           "command_prefix": [], "threads": 4, "memory_mb": 4000,
                           "timeout_seconds": 14400, "cache": True}
    for module in ("vfdb", "bacmet"):
        modules[module].update(database=None, min_identity=80.0, min_coverage=70.0,
                               min_query_coverage=0.0, evalue=1e-5, max_target_seqs=25,
                               sensitivity="sensitive")
    modules["rgi"].update(database=None, include_loose=True)
    modules["amrfinder"].update(database=None, annotation_format="prokka")
    modules["genomad"].update(database=None, memory_mb=12000)
    modules["mobsuite"].update(database=None)
    modules["antismash"].update(database=None, memory_mb=12000)
    modules["crisprcasfinder"].update(database=None, version_args=[], batch_size=32)
    modules["pathogenfinder2"].update(enabled=True, attention=True, attention_top_n=20, map=False,
                                      memory_mb=16000, gpus=0,
                                      pf2_source_commit="3837caf6710da5da3038f5d1246dd82ab26e003a")
    return {"schema_version": 1, "project": "BaSIS study", "samples": "samples.tsv", "outdir": "results",
            "validation_mode": "research", "execution": {"cores": 8, "memory_mb": 32000, "gpus": 0,
                                                         "environment_backend": "auto", "conda_prefix": None},
            "modules": modules, "sample_module_overrides": {},
            "deep": {"motifs": True, "motif_rules": None, "prosite_snapshot": None, "prosite_profiles": False, "profiles_dir": None, "reference_fasta": None, "reference_metadata": None,
                     "reference_search_tool": "blastp", "maximum_candidates": 5,
                     "aware": {"enabled": True, "preset": "auto-medium", "tools": {}, "queues": {}},
                     "maximum_attention_candidates": 20, "attention_queue_for_mixed_evidence": False, "maximum_exploratory_candidates": 20,
                     "include_exploratory": True, "include_stress": False, "max_references": 40,
                     "min_identity": 30, "min_coverage": 70, "phylogeny": True,
                     "auto_phylogeny": False, "maximum_tree_candidates": 6, "tree_review_registry": None,
                     "family_placement": False, "placement_registry": None, "placement_cache_dir": None,
                     "placement_allow_download": True, "maximum_placement_candidates": 20,
                     "placement_model": "LG+G4", "placement_min_query_coverage": 0.7,
                     "placement_min_columns": 50,
                     "threads": 4, "memory_mb": 8000, "timeout_seconds": 14400,
                     "command_prefix": [], "context_flank": 20000, "circular_contigs": []}}


def merge_dict(base: dict, update: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def new_study_config(preset="full", resource_dir=None) -> dict:
    """Usable clean-install defaults; legacy loaded configurations stay explicit.

    Downloads are tasks of the requested run, never an effect of initialization.
    The first verified snapshot is reused and is not implicitly refreshed.
    """
    if preset not in {"core", "full"}:
        raise ValueError("Study preset must be core or full")
    config = default_config()
    cache = Path(resource_dir).expanduser() if resource_dir else Path(
        os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "bactromics-basis"
    config["resources"] = {"policy": "auto", "directory": str(cache.resolve()), "preset": preset}
    config["execution"].update(cores=min(4, os.cpu_count() or 1),
                               conda_prefix=str(cache.resolve() / "environments"), device="auto")
    if preset == "full":
        for module in config["modules"].values():
            module["enabled"] = True
        config["deep"].update(prosite_profiles=True, auto_phylogeny=True,
                              family_placement=True)
    config["deep"]["placement_cache_dir"] = "placement-cache"
    return config


def load_config(path: str | Path) -> dict:
    path = Path(path).resolve()
    with path.open(encoding="utf-8") as handle:
        supplied = yaml.safe_load(handle)
    if not isinstance(supplied, dict):
        raise ValueError("Configuration must be a YAML mapping")
    if supplied.get("schema_version", 1) != 1:
        raise ValueError("Unsupported configuration schema_version")
    unknown = set(supplied.get("modules", {})) - set(ALL_MODULES)
    if unknown:
        raise ValueError(f"Unknown module names: {sorted(unknown)}")
    config = merge_dict(default_config(), supplied)
    resources = config.get("resources", {})
    if not isinstance(resources, dict) or resources.get("policy", "manual") not in {"manual", "auto"}:
        raise ValueError("resources.policy must be auto or manual")
    if resources.get("directory"):
        resources["directory"] = resolve_path(str(resources["directory"]), path.parent)
    if config["execution"].get("device", "configured") not in {"configured", "auto", "cpu", "cuda"}:
        raise ValueError("execution.device must be configured, auto, cpu or cuda")
    if config["execution"].get("environment_backend") not in {"auto", "external", "managed"}:
        raise ValueError("execution.environment_backend must be auto, external or managed")
    conda_prefix = config["execution"].get("conda_prefix")
    if conda_prefix is not None:
        if not isinstance(conda_prefix, str) or not conda_prefix.strip():
            raise ValueError("execution.conda_prefix must be a nonempty path or null")
        config["execution"]["conda_prefix"] = resolve_path(conda_prefix, path.parent)
    for key in ("cores", "memory_mb"):
        value = config["execution"].get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"execution.{key} must be a positive integer")
    if not isinstance(config["execution"].get("gpus"), int) or config["execution"]["gpus"] < 0:
        raise ValueError("execution.gpus must be a nonnegative integer")
    if config["execution"]["gpus"] > 1:
        raise ValueError("BaSIS 0.1 supports one scheduled GPU; use execution.gpus: 0 or 1")
    for key in ("samples", "outdir"):
        config[key] = resolve_path(str(config[key]), path.parent)
    for section in list(config["modules"].values()) + [config["deep"]]:
        if section.get("environment_backend", "auto") not in {"auto", "external", "managed"}:
            raise ValueError("environment_backend must be auto, external or managed")
        if not isinstance(section.get("command_prefix", []), list) or not all(isinstance(x, str) for x in section.get("command_prefix", [])):
            raise ValueError("command_prefix must be an argv list, not a shell command string")
        if "enabled" in section and not isinstance(section["enabled"], bool):
            raise ValueError("enabled must be a YAML boolean (true/false), not a string")
        if section.get("gpus", 0) not in (0, 1):
            raise ValueError("Module gpus must be 0 or 1")
        for key in PATH_KEYS:
            if isinstance(section.get(key), str) and section[key]:
                section[key] = resolve_path(section[key], path.parent)
        for key in ("threads", "memory_mb", "timeout_seconds"):
            if int(section.get(key, 1)) < 1:
                raise ValueError(f"{key} must be positive")
        if section.get("mode", "native") not in ("native", "import"):
            raise ValueError("Module mode must be native or import")
    if config["validation_mode"] not in ("research", "synthetic_fixture"):
        raise ValueError("validation_mode must be research or synthetic_fixture")
    deep = config["deep"]
    from .aware import resolve_policy
    resolve_policy(deep)
    for key in ("family_placement", "placement_allow_download"):
        if not isinstance(deep[key], bool):
            raise ValueError(f"deep.{key} must be a YAML boolean")
    for key, minimum in (("maximum_placement_candidates", 0), ("placement_min_columns", 1)):
        if isinstance(deep[key], bool) or not isinstance(deep[key], int) or deep[key] < minimum:
            raise ValueError(f"deep.{key} must be an integer >= {minimum}")
    coverage = deep["placement_min_query_coverage"]
    if isinstance(coverage, bool) or not isinstance(coverage, (float, int)) or not 0 < coverage <= 1:
        raise ValueError("deep.placement_min_query_coverage must be in (0, 1]")
    if deep["placement_model"] != "LG+G4":
        raise ValueError("The initial placement adapter supports the fixed LG+G4 model only")
    config["config_path"] = str(path)
    return config
