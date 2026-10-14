from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

from . import __version__
from .config import ALL_MODULES, CORE, default_config, load_config
from .inputs import load_samples, validate_sample
from .util import atomic_json, read_json, sha256_file


def _run_args(parser):
    parser.add_argument("-c", "--config", required=True, help="YAML configuration")
    parser.add_argument("-d", "--deep", action="store_true", help="Enable adaptive DeepSafe follow-up")
    parser.add_argument("-e", "--extend", action="store_true", help="Enable PathogenFinder2 complementary assessment")
    parser.add_argument("--cores", type=int, help="Total CPU core budget")
    parser.add_argument("--engine", choices=("local", "snakemake"), default="snakemake", help="snakemake (default) schedules dependencies and independent jobs; local is serial")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs and show the selected plan without running biological tools")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show stages, candidates, native commands and 30-second heartbeats")
    parser.add_argument("--force", action="store_true", help="Recompute selected analyses instead of using matching caches")
    parser.add_argument("--output", choices=("json", "summary"), default="json", help="stdout format; JSON remains the default; concise summary also appears on stderr")


def parser():
    p = argparse.ArgumentParser(prog="basis", description="BaSIS — Bacterial Adaptive Safety Investigation System")
    p.add_argument("--version", action="version", version=f"BaSIS {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-install", help="Verify installed source and bundled resource checksums")
    sub.add_parser("setup-environments", help="Plan isolated tool environments; use this command's --help for options")
    sub.add_parser("prepare-resources", help="Preview or explicitly prepare versioned databases; use this command's --help")
    sub.add_parser("gui", help="Open the optional local Streamlit interface; use gui --help")
    sub.add_parser("jobs", help="Manage persistent local jobs; use jobs --help")
    report = sub.add_parser("report", help="Render saved evidence to a new report without running analyses")
    report_source = report.add_mutually_exclusive_group(required=True)
    report_source.add_argument("--evidence", help="One saved evidence.json")
    report_source.add_argument("--sources", help="JSON list of explicitly selected evidence snapshots; writes reports and one control panel")
    report.add_argument("--outdir", required=True)
    report.add_argument("--expected-sha256", help="Require this checksum for --evidence before writing")
    report.add_argument("--artifact-root", action="append", default=[], help="Allow exact recorded artifact links under this directory; repeatable, no external directory scan")
    report.add_argument("-v", "--verbose", action="store_true", help="Show reporting stages and heartbeats")
    hub = sub.add_parser("control-panel", help="Build a linked control panel from saved runs")
    hub.add_argument("--results", action="append", default=[], help="Saved run output directory; repeatable")
    hub.add_argument("--pilot-dir", help="Existing pilot directory with pilot.json")
    hub.add_argument("--outdir", required=True, help="New control-panel output directory")
    hub.add_argument("--artifact-root", action="append", default=[], help="Allow exact recorded artifact links under this directory; repeatable")
    audit = sub.add_parser("audit-reproducibility", help="Freeze selected source records and audit local environment/resource identities without running analyses")
    audit.add_argument("-c", "--config", action="append", default=[], help="Configuration to freeze; repeatable")
    audit.add_argument("--evidence", action="append", default=[], help="Saved evidence to freeze; repeatable")
    audit.add_argument("--environment", action="append", default=[], help="Existing Conda environment prefix to inspect without executing it; repeatable")
    audit.add_argument("--conda-explicit", nargs=2, action="append", default=[], metavar=("PREFIX", "EXPORT"), help="Compare a prefix with a captured explicit export; repeatable")
    audit.add_argument("--hash-resources", action="store_true", help="Hash configured database/model resource bytes; may take substantial time")
    audit.add_argument("--outdir", required=True, help="New audit directory")
    audit.add_argument("-v", "--verbose", action="store_true", help="Show audit stages and heartbeats")
    _run_args(sub.add_parser("run", help="Run the unified workflow"))
    init = sub.add_parser("init", help="Create configuration and sample-sheet templates")
    init.add_argument("--directory", required=True)
    init.add_argument("--hybas-dir", help="Discover matching Prokka fna/faa/gff sets under an existing HybAs project")
    batch = sub.add_parser("import-samples", help="Import an Excel, CSV or TSV batch sheet into a new study")
    batch.add_argument("--file", required=True, help=".xlsx, .csv or .tsv source sheet")
    batch.add_argument("--sheet", help="Worksheet name; required for a workbook with multiple sheets")
    batch.add_argument("--root", help="Root folder for exact sample-name based annotation discovery")
    batch.add_argument("--directory", help="New study directory; required unless --dry-run; existing paths are preserved")
    batch.add_argument("--dry-run", action="store_true", help="Preview all readable rows and input-file issues without creating a study")
    batch.add_argument("-v", "--verbose", action="store_true", help="Show batch progress and 30-second heartbeats on stderr")
    batch.add_argument("--input-mode", choices=("auto", "hybas", "external"), default="auto",
                       help="external skips adjacent HybAs/Kraken metadata; a row's input_mode takes precedence")
    batch.add_argument("--base-config", help="Reuse a configuration's tool/resource settings; output and cache paths are new")
    sync = sub.add_parser("sync-hybas", help="Import HybAs taxonomy context into an existing sample sheet")
    sync.add_argument("-c", "--config", required=True)
    sync.add_argument("--sample", help="Limit the update to one sample ID")
    sync.add_argument("--species", help="Record a user-confirmed species for --sample, separately from the HybAs target")
    sync.add_argument("--dry-run", action="store_true", help="Preview imported taxonomy without changing files")
    for name in ("validate", "doctor"):
        doctor = sub.add_parser(name, help="Validate inputs" if name == "validate" else "Check inputs, executables and declared resources")
        doctor.add_argument("-c", "--config", required=True)
        doctor.add_argument("-d", "--deep", action="store_true")
        doctor.add_argument("-e", "--extend", action="store_true")
        doctor.add_argument("-v", "--verbose", action="store_true", help="Show input-check stages and 30-second heartbeats on stderr")
        doctor.add_argument("--report-dir", help="Write an offline preflight HTML/JSON/TSV review into a new directory")
    demo = sub.add_parser("demo", help="Run labelled synthetic software fixtures, without biological engines")
    demo.add_argument("--outdir", required=True)
    demo.add_argument("-d", "--deep", action="store_true")
    demo.add_argument("-e", "--extend", action="store_true")
    demo.add_argument("--engine", choices=("local", "snakemake"), default="local")
    demo.add_argument("-v", "--verbose", action="store_true", help="Show stages and 30-second heartbeats on stderr")
    demo.add_argument("--output", choices=("json", "summary"), default="json", help="stdout format; JSON remains the default; concise summary also appears on stderr")
    prep = sub.add_parser("prepare-references", help="Fetch public protein accessions and build a traceable local catalogue")
    source = prep.add_mutually_exclusive_group(required=True)
    source.add_argument("--requests", help="Accession request TSV; see docs/DEEPSAFE.md")
    source.add_argument("--source-fasta", help="Convert an existing protein reference database into an unreviewed search catalogue")
    prep.add_argument("--source-name", help="Database name/release for --source-fasta provenance")
    prep.add_argument("--fasta", required=True)
    prep.add_argument("--metadata", required=True)
    prep.add_argument("--maximum-accessions", type=int, default=100)
    compare = sub.add_parser("compare", help="Compare two integrated evidence.json reports")
    compare.add_argument("old")
    compare.add_argument("new")
    compare.add_argument("--out", required=True)
    worker = sub.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("--plan", required=True)
    worker.add_argument("--sample", required=True)
    worker.add_argument("--module", required=True)
    worker.add_argument("--force", action="store_true")
    worker.add_argument("--receipt", help=argparse.SUPPRESS)
    worker.add_argument("--upstream-receipts", help=argparse.SUPPRESS)
    return p


def initialize(directory, hybas_dir=None):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("config.yaml", "samples.tsv"):
        if (directory / name).exists():
            raise ValueError(f"Refusing to overwrite existing {directory / name}")
    config = default_config()
    # Templates deliberately point to resources that the user must install and identify.
    for module in ("amrfinder", "rgi", "vfdb", "antismash", "genomad", "mobsuite"):
        config["modules"][module]["database"] = f"/path/to/databases/{module}"
    profiles = Path(__file__).parent / "resources" / "profiles"
    if profiles.exists():
        shutil.copytree(profiles, directory / "profiles", dirs_exist_ok=True)
        config["deep"]["profiles_dir"] = "profiles"
    rows = []
    if hybas_dir:
        root = Path(hybas_dir).resolve()
        if not root.is_dir():
            raise ValueError(f"HybAs directory not found: {root}")
        seen = set()
        for gff in sorted(root.rglob("*.gff")):
            faa, fna = gff.with_suffix(".faa"), gff.with_suffix(".fna")
            if faa.is_file() and fna.is_file():
                sid = gff.stem
                if sid in seen:
                    raise ValueError(f"Multiple annotation sets for {sid}; select a narrower --hybas-dir or construct samples.tsv explicitly")
                seen.add(sid)
                row = {"sample_id": sid, "assembly": str(fna), "proteins": str(faa), "gff": str(gff)}
                gbk = gff.with_suffix(".gbk")
                row["genbank"] = str(gbk) if gbk.is_file() else ""
                from .hybas import enrich_sample
                enriched = enrich_sample(row)
                row.update({key: enriched.get(key, "") for key in
                            ("taxon", "taxon_rank", "taxon_source", "hybas_dir")})
                rows.append(row)
        if not rows:
            raise ValueError("No matching .fna/.faa/.gff annotation sets discovered; create the manifest explicitly")
    else:
        rows = [{"sample_id": "AFB02", "assembly": "/path/to/AFB02.fna", "proteins": "/path/to/AFB02.faa", "gff": "/path/to/AFB02.gff"}]
    with (directory / "samples.tsv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["sample_id", "assembly", "proteins", "gff", "genbank", "taxon", "amrfinder_organism", "phenotypes", "taxon_rank", "taxon_source", "hybas_dir"], delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    (directory / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    return {"config": str(directory / "config.yaml"), "samples": str(directory / "samples.tsv"),
            "note": "Review sample identity, resource paths and enabled modules before native execution."}


def import_study(file, directory=None, root=None, sheet=None, input_mode="auto", base_config=None, dry_run=False):
    """Prepare inputs in a fresh study; never run tools or reuse a result directory."""
    from .sample_import import import_samples, preview_samples
    if not dry_run and not directory:
        raise ValueError("--directory is required to import a study; use --dry-run for a read-only preview")
    if base_config:
        base_config = str(Path(base_config).expanduser().resolve())
    base_hash = sha256_file(base_config) if base_config else None
    config = copy.deepcopy(load_config(base_config) if base_config else default_config())
    if base_config and sha256_file(base_config) != base_hash:
        raise ValueError("Base configuration changed during import preparation; retry with a stable file")
    if base_config and (config["deep"].get("circular_contigs") or
                        str(config["modules"]["integronfinder"].get("topology", "linear")).casefold() != "linear"):
        raise ValueError("Base configuration contains sample-specific circular topology declarations. "
                         "Prepare a batch base configuration without deep.circular_contigs or circular "
                         "integronfinder.topology; review topology separately for the new assemblies.")
    config.pop("config_path", None)
    config.update(samples="samples.tsv", outdir="results")
    config["deep"]["placement_cache_dir"] = "placement-cache"
    config["input_import"] = {
        "base_config": str(Path(base_config).resolve()) if base_config else None,
        "base_config_sha256": base_hash,
        "new_paths": {"samples": "samples.tsv", "outdir": "results", "placement_cache_dir": "placement-cache"},
    }
    if not base_config:
        for module in ("amrfinder", "rgi", "vfdb", "antismash", "genomad", "mobsuite"):
            config["modules"][module]["database"] = f"/path/to/databases/{module}"
    if dry_run:
        result = preview_samples(file, root=root, sheet_name=sheet,
                                 input_mode=input_mode, config=config)
        if directory:
            destination = Path(directory).expanduser().absolute()
            result["proposed_directory"] = str(destination)
            if destination.exists() or destination.is_symlink():
                result.setdefault("errors", []).append(f"Import output already exists: {destination}")
                result["status"] = "blocked"
        result["base_config"] = base_config
        result["note"] = ("Preview checks the sample sheet and input files only. No study created. "
                          "Import rechecks the files; basis doctor checks sequence consistency and resources separately.")
        return result
    result = import_samples(file, directory, root=root, sheet_name=sheet,
                            input_mode=input_mode, config=config)
    result["note"] = ("Import prepares a manifest and configuration. Run basis doctor with the intended -d/-e options "
                      "to check sequence consistency, declared scope and resources before execution.")
    result["base_config"] = str(Path(base_config).resolve()) if base_config else None
    if any(v.get("enabled") and v.get("mode") == "import" for v in config["modules"].values()):
        result.setdefault("warnings", []).append(
            "Base configuration includes imported results. Verify their sample identities and input hashes; "
            "this import does not convert them into native analyses.")
    return result


def sync_hybas(config, sample_id=None, species=None, dry_run=False):
    """Update only taxonomy metadata after validating the entire sample sheet."""
    if species is not None and not sample_id:
        raise ValueError("--species requires --sample")
    if species is not None:
        species = species.strip()
        if len(species.split()) < 2 or any(char in species for char in "\t\r\n"):
            raise ValueError("--species must be a nonempty species name")
    path = Path(config["samples"]).resolve()
    original = path.read_bytes()
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if len(fields) != len(set(fields)) or any(None in row for row in rows):
        raise ValueError("Sample sheet has duplicate columns or extra values")
    loaded = {sample["sample_id"]: sample for sample in load_samples(path)}
    if sample_id is not None and sample_id not in loaded:
        raise ValueError(f"Unknown sample: {sample_id}")
    changed = False
    summaries = []
    from .hybas import enrich_sample
    for row in rows:
        sid = (row.get("sample_id") or "").strip()
        if not sid or (sample_id is not None and sid != sample_id):
            continue
        sample = loaded[sid]
        if species is not None:
            sample = enrich_sample({**sample, "taxon": species, "taxon_rank": "species",
                                    "taxon_source": "user_confirmed"})
        for key in ("taxon", "taxon_rank", "taxon_source", "hybas_dir"):
            value = str(sample.get(key) or "")
            if row.get(key, "") != value:
                row[key] = value
                changed = True
            if key not in fields:
                fields.append(key)
                changed = True
        context = sample.get("hybas_context") or {}
        summaries.append({"sample_id": sid, "taxon": sample.get("taxon"),
                          "taxon_rank": sample.get("taxon_rank"),
                          "taxon_source": sample.get("taxon_source"),
                          "hybas_status": context.get("status", "not_found"),
                          "taxonomy_status": context.get("taxonomy_status", "not_recorded"),
                          "selected_target": context.get("selected_target"),
                          "read_classification": {
                              platform: {"status": evidence.get("status"),
                                         "leading_species": next(iter(evidence.get("top_species") or []), None)}
                              for platform, evidence in context.get("read_taxonomy", {}).items()},
                          "warnings": context.get("warnings", [])})
    result = {"status": "dry_run" if dry_run else "unchanged", "samples": summaries,
              "backup": None, "samples_path": str(path)}
    if dry_run or not changed:
        return result
    if path.read_bytes() != original:
        raise ValueError("Sample sheet changed during import; no update written")
    # Keep the original bytes and unrelated columns; replace only after all rows validate.
    backup_fd, backup_name = tempfile.mkstemp(prefix=path.name + ".backup-", dir=path.parent)
    with os.fdopen(backup_fd, "wb") as stream:
        stream.write(original)
    shutil.copystat(path, backup_name)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
        if path.read_bytes() != original:
            raise ValueError("Sample sheet changed during import; no update written")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    result.update(status="updated", backup=backup_name)
    return result


def check(config, deep=False, extend=False, executables=False):
    """Read-only, per-sample input and resource preflight; never run native tools."""
    from .input_context import describe_input_context
    from .inputs import load_samples_diagnostic
    from .progress import emit
    from .sample_policy import plan_sample_modules, validate_sample_overrides
    from .pipeline import module_config

    def outcome(errors, warnings):
        return "failed" if errors else "passed_with_warnings" if warnings else "passed"

    def qualified(sid, message):
        return message if message.startswith(sid + ":") else f"{sid}: {message}"

    global_checks = {"errors": [], "warnings": []}
    checks, sample_checks, entries = [], [], []
    decisions, input_context = {}, {}
    resource_checks_performed = False
    try:
        entries = load_samples_diagnostic(config["samples"])
        # Unresolved samples still exist in this manifest. Never misreport a
        # valid exclusion for one of them as an unknown sample, nor probe tools
        # before all explicit declarations have been validated.
        validate_sample_overrides(config, [entry["declaration"] for entry in entries])
    except (OSError, ValueError, KeyError) as exc:
        global_checks["errors"].append(str(exc))
    global_input_failure = bool(global_checks["errors"])
    for entry in entries:
        sid, sample = entry["sample_id"], entry["sample"]
        context = describe_input_context(sample or entry["declaration"], resolved=sample is not None)
        input_context[sid] = context
        row = {"sample_id": sid, "source_row": entry["source_row"], "status": "not_checked",
               "input_status": "failed" if entry["errors"] else "not_checked",
               "input_context": context, "metrics": {}, "input_hashes": {},
               "errors": list(entry["errors"]), "warnings": [], "input_warnings": [],
               "module_checks": {}}
        sample_checks.append(row)
        if sample is None or global_input_failure:
            if row["errors"]:
                row["status"] = "failed"
            continue
        emit(f"[{sid}] Checking annotation file consistency ({sample['input_mode']} input)")
        decisions[sid] = plan_sample_modules(config, sample, extend)
        try:
            validation = validate_sample(sample)
            checks.append(validation)
            row["metrics"] = {key: validation[key] for key in ("contigs", "assembly_bp", "protein_count") if key in validation}
            row["input_hashes"] = copy.deepcopy(validation.get("input_hashes", {}))
            row["input_warnings"] = list(validation.get("warnings", []))
            row["warnings"].extend(row["input_warnings"])
            row["input_status"] = outcome([], row["input_warnings"])
        except (OSError, ValueError, KeyError) as exc:
            row["errors"].append(str(exc))
            row["input_status"] = "failed"
        row["warnings"].extend((sample.get("hybas_context") or {}).get("warnings", []))
        declared = config.get("sample_module_overrides", {}).get(sid, {})
        for module, decision in decisions[sid].items():
            label = f"{sid}:{module}"
            active_exclusion = (decision.get("action") == "withheld"
                                and decision.get("reason") == "sample_scope_exclusion"
                                and decision.get("requested") is False)
            status = ("excluded" if active_exclusion else "disabled" if decision["action"] == "disabled"
                      else "held" if decision["action"] == "withheld" else "not_checked")
            detail = {"decision": copy.deepcopy(decision), "status": status,
                      "errors": [], "warnings": list(decision.get("warnings", [])),
                      "declared_exclusion": copy.deepcopy(declared.get(module))}
            row["module_checks"][module] = detail
            if decision.get("scope_incomplete"):
                detail["errors" if executables else "warnings"].append(decision["reason"])
            if executables and decision["action"] == "run":
                resource_checks_performed = True
                try:
                    cfg = module_config(config, module, sample)
                    if cfg.get("mode") == "import":
                        path = cfg.get("result", "")
                        if not path or not Path(path).is_file():
                            detail["errors"].append("imported result missing")
                    else:
                        from .workflow_environments import backend_for_job
                        readiness_plan = {"config": config, "options": {"deep": deep, "extend": extend},
                                          "module_decisions": decisions}
                        backend = backend_for_job(readiness_plan, sid, module)
                        detail["environment_backend"] = backend
                        if backend == "managed":
                            detail["environment_readiness"] = "planned_snakemake_deployment"
                            if not shutil.which("conda"):
                                detail["errors"].append("Conda is required for Snakemake-managed deployment")
                            else:
                                detail["warnings"].append("Snakemake will deploy and verify the native environment; installation is not yet verified")
                        elif backend == "external":
                            prefix = cfg.get("command_prefix", [])
                            exe = prefix[0] if prefix else cfg["executable"]
                            if not shutil.which(exe):
                                detail["errors"].append(f"executable/prefix not found: {exe}")
                            elif prefix:
                                detail["warnings"].append("prefix found; executable inside its environment still needs native acceptance testing")
                        if module in {"rgi", "vfdb", "bacmet", "genomad"} and not cfg.get("database"):
                            detail["errors"].append("explicit database path required")
                        for key in ("database", "database_manifest", "prot_t5_path", "weights_model"):
                            if cfg.get(key) and not Path(cfg[key]).exists():
                                detail["errors"].append(f"{key} does not exist: {cfg[key]}")
                        if int(cfg.get("memory_mb", 1)) > int(config["execution"]["memory_mb"]):
                            detail["errors"].append("memory requirement exceeds configured total budget")
                        if int(cfg.get("gpus", 0)) > int(config["execution"].get("gpus", 0)):
                            detail["errors"].append("GPU requirement exceeds configured total budget")
                except (OSError, ValueError, KeyError) as exc:
                    detail["errors"].append(str(exc))
                detail["status"] = outcome(detail["errors"], detail["warnings"])
            row["errors"].extend(f"{label}: {message}" for message in detail["errors"])
            row["warnings"].extend(f"{label}: {message}" for message in detail["warnings"])
        row["errors"] = list(dict.fromkeys(row["errors"]))
        row["warnings"] = list(dict.fromkeys(row["warnings"]))
        row["status"] = outcome(row["errors"], row["warnings"])
    if deep and not global_input_failure:
        resource_checks_performed = True
        errors, warnings = global_checks["errors"], global_checks["warnings"]
        try:
            for key in ("reference_fasta", "reference_metadata"):
                if not config["deep"].get(key):
                    warnings.append(f"DeepSafe {key} is unset: candidate evidence will remain incomplete until references are supplied")
                elif not Path(config["deep"][key]).is_file():
                    errors.append(f"DeepSafe {key} does not exist")
            from .references import validate_profiles
            _, profile_errors = validate_profiles(Path(config["deep"]["profiles_dir"]) if config["deep"].get("profiles_dir") else None)
            errors.extend(profile_errors)
            if config["deep"].get("family_placement"):
                from .placement import DEFAULT_REGISTRY
                from .placement_panels import load_registry
                try:
                    load_registry(config["deep"].get("placement_registry") or DEFAULT_REGISTRY)
                except (ValueError, OSError) as exc:
                    errors.append("DeepSafe placement registry: " + str(exc))
            if executables:
                resource_checks_performed = True
                cfg = config["deep"]
                prefix = cfg.get("command_prefix", [])
                from .workflow_environments import backend_for_job
                backend = backend_for_job({"config": config, "options": {"deep": True}}, "", "deepsafe")
                if backend == "managed":
                    if not shutil.which("conda"):
                        errors.append("DeepSafe: Conda is required for Snakemake-managed deployment")
                    else:
                        warnings.append("DeepSafe environment is planned for Snakemake deployment; installation is not yet verified")
                elif backend == "none":
                    pass
                elif prefix and not shutil.which(prefix[0]):
                    errors.append(f"DeepSafe executable prefix not found: {prefix[0]}")
                elif prefix:
                    warnings.append("DeepSafe prefix found; executables within its environment require native acceptance testing")
                else:
                    names = []
                    if cfg.get("reference_search_tool", "blastp") != "metadata":
                        names.append(cfg.get("reference_search_tool", "blastp"))
                    if cfg.get("phylogeny"):
                        names += ["mafft", "iqtree"]
                    if cfg.get("family_placement"):
                        names += ["mafft", "iqtree", "epa-ng"]
                    if cfg.get("domain_database"):
                        names += ["hmmscan"]
                    for name in names:
                        exe = cfg.get("executables", {}).get(name, name)
                        if not shutil.which(exe):
                            errors.append(f"DeepSafe executable not found: {exe}")
        except (OSError, ValueError, KeyError) as exc:
            errors.append("DeepSafe configuration: " + str(exc))
    global_checks = {key: list(dict.fromkeys(messages)) for key, messages in global_checks.items()}
    errors = list(global_checks["errors"])
    warnings = list(global_checks["warnings"])
    for row in sample_checks:
        errors.extend(qualified(row["sample_id"], message) for message in row["errors"])
        warnings.extend(qualified(row["sample_id"], message) for message in row["warnings"])
    return {"schema": "basis-preflight-v1", "status": outcome(errors, warnings),
            "check_options": {"deep": bool(deep), "extend": bool(extend), "executables": bool(executables)},
            "resource_checks_performed": resource_checks_performed,
            "global_checks": global_checks, "sample_checks": sample_checks,
            "input_context": input_context,
            "checks": checks, "module_decisions": decisions,
            "warnings": list(dict.fromkeys(warnings)), "errors": list(dict.fromkeys(errors)),
            "scope": "Input/resource preflight; not a biological safety assessment or live model validation."}

def compare_reports(old_path, new_path, out):
    old, new = read_json(old_path), read_json(new_path)
    def records(data):
        values = data.get("records", data.get("module_records", []))
        return values if isinstance(values, list) else []
    def findings(data):
        found = []
        for record in records(data):
            found += record.get("findings", [])
        return {f.get("finding_id", f.get("id")): f for f in found}
    left, right = findings(old), findings(new)
    def record_map(data):
        return {(r.get("sample_id"), r.get("module", r.get("tool"))): r for r in records(data) + data.get("deep_records", [])}
    def scientific_record(record):
        return {key: record.get(key) for key in ("execution_status", "interpretation", "metrics", "error", "reason", "complete", "candidates", "provenance", "effective_config")}
    old_records, new_records = record_map(old), record_map(new)
    record_changes = []
    for key in sorted(old_records.keys() | new_records.keys()):
        a, b = scientific_record(old_records.get(key, {})), scientific_record(new_records.get(key, {}))
        if a != b:
            record_changes.append({"sample_id": key[0], "module": key[1], "old": a, "new": b})
    change = {"schema_version": 1, "old": str(Path(old_path).resolve()), "new": str(Path(new_path).resolve()),
              "added": [right[k] for k in right.keys() - left.keys()],
              "removed": [left[k] for k in left.keys() - right.keys()],
              "changed": [{"old": left[k], "new": right[k]} for k in left.keys() & right.keys() if left[k] != right[k]],
              "old_run": old.get("run", {}), "new_run": new.get("run", {}),
              "record_changes": record_changes,
              "note": "Differences may reflect changed inputs, databases, software or interpretation rules; inspect provenance."}
    atomic_json(out, change)
    return {"out": str(Path(out).resolve()), "added": len(change["added"]), "removed": len(change["removed"]), "changed": len(change["changed"]), "record_changes": len(record_changes)}


def _normalize_run_argv(argv):
    """Allow omitted ``run`` with any supported leading run option.

    Root help/version and unknown options remain root parser operations. Short
    Boolean clusters contain only d/e/v; -c also supports argparse's attached
    value notation. The parser still owns validation and reports bad arguments.
    """
    if not argv:
        return argv
    first = argv[0]
    long_option = first.split("=", 1)[0]
    run_options = {"--config", "--deep", "--extend", "--verbose", "--dry-run",
                   "--cores", "--engine", "--force", "--output"}
    short_flags = first.startswith("-") and not first.startswith("--") and len(first) > 1
    boolean_cluster = short_flags and all(letter in "dev" for letter in first[1:])
    if long_option in run_options or boolean_cluster or first.startswith("-c"):
        return ["run", *argv]
    return argv


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"gui", "jobs"}:
        if argv[0] == "gui":
            from .gui import main as delegated
        else:
            from .jobs import main as delegated
        return delegated(argv[1:])
    if argv and argv[0] == "setup-environments":
        from .environment_setup import main as setup_main
        return setup_main(argv[1:])
    if argv and argv[0] == "prepare-resources":
        from .resource_setup import main as resource_main
        return resource_main(argv[1:])
    args = parser().parse_args(_normalize_run_argv(argv))
    from .progress import verbosity
    with verbosity(getattr(args, "verbose", False)):
        return _main_parsed(args)


def _main_parsed(args):
    try:
        code = 0
        if args.command == "verify-install":
            from .release import verify_install
            result = verify_install()
            code = 0 if result["status"] == "passed" else 2
        elif args.command == "report":
            if args.sources:
                if args.expected_sha256:
                    raise ValueError("Use evidence_sha256 within each --sources entry")
                from .report_batch import refresh_saved_reports
                result = refresh_saved_reports(args.sources, args.outdir, artifact_roots=args.artifact_root)
            else:
                from .report_refresh import refresh_saved_report
                _, artifacts = refresh_saved_report(args.evidence, args.outdir,
                    artifact_roots=args.artifact_root, expected_sha256=args.expected_sha256)
                result = {"status": "rendered_saved_evidence", "artifacts": artifacts,
                          "native_analyses_run": False}
        elif args.command == "control-panel":
            from .control_panel import build_control_panel
            result = build_control_panel(results=args.results, pilot_dir=args.pilot_dir, outdir=args.outdir,
                                         artifact_roots=args.artifact_root)
        elif args.command == "audit-reproducibility":
            from .reproducibility_audit import audit_reproducibility
            if len({p for p, _ in args.conda_explicit}) != len(args.conda_explicit):
                raise ValueError("Only one --conda-explicit export may be selected per prefix")
            capture = audit_reproducibility(config_paths=args.config, evidence_paths=args.evidence,
                environment_prefixes=args.environment, outdir=args.outdir,
                hash_resources=args.hash_resources, conda_explicit_paths=dict(args.conda_explicit))
            result = {"status": capture["status"], "artifacts": capture["artifacts"],
                      "counts": {key: len(capture[key]) for key in ("sources", "inputs", "resources", "environments")},
                      "open_gaps": capture["open_gaps"], "native_analyses_run": False,
                      "portable_restore_verified": capture["portable_restore_verified"]}
        elif args.command == "init":
            result = initialize(args.directory, args.hybas_dir)
        elif args.command == "import-samples":
            from .progress import stage
            with stage("Preview batch inputs" if args.dry_run else "Import batch inputs"):
                result = import_study(args.file, args.directory, args.root, args.sheet,
                                      args.input_mode, args.base_config, args.dry_run)
            if args.dry_run:
                code = 0 if result["status"] == "ready_for_import" else 2
        elif args.command == "sync-hybas":
            result = sync_hybas(load_config(args.config), args.sample, args.species, args.dry_run)
        elif args.command in ("doctor", "validate"):
            from .progress import stage
            if args.report_dir and os.path.lexists(Path(args.report_dir).expanduser()):
                raise ValueError(f"Refusing to overwrite existing preflight report directory: {args.report_dir}")
            label = "Check inputs and configured resources" if args.command == "doctor" else "Check annotation file consistency"
            with stage(label):
                result = check(load_config(args.config), args.deep, args.extend, executables=args.command == "doctor")
            if args.report_dir:
                from .report_preflight import write_preflight_report
                with stage("Write per-sample preflight report"):
                    result["preflight_artifacts"] = write_preflight_report(
                        result, args.report_dir, config_path=args.config)
            code = 2 if result["errors"] else 0
        elif args.command == "prepare-references":
            from .references import prepare_accession_catalog
            for path in (args.fasta, args.metadata):
                if Path(path).exists():
                    raise ValueError(f"Refusing to overwrite existing reference catalogue {path}")
            if args.source_fasta:
                from .catalog import from_fasta
                result = from_fasta(Path(args.source_fasta), Path(args.fasta), Path(args.metadata), args.source_name)
            else:
                result = prepare_accession_catalog(Path(args.requests), Path(args.fasta), Path(args.metadata), maximum_accessions=args.maximum_accessions)
        elif args.command == "compare":
            result = compare_reports(args.old, args.new, args.out)
        elif args.command == "_worker":
            from .pipeline import run_worker
            run_worker(args.plan, args.sample, args.module, args.force,
                       receipt=args.receipt, upstream_receipts=args.upstream_receipts)
            return 0  # Biological engine failures are data; final launcher exits 2 for incomplete scope.
        else:
            from .pipeline import make_plan, run_pipeline
            if args.command == "demo":
                from .demo import create_demo
                config = load_config(create_demo(args.outdir))
                dry_run = force = False
            else:
                config = load_config(args.config)
                if args.cores is not None:
                    if args.cores < 1:
                        raise ValueError("--cores must be positive")
                    config["execution"]["cores"] = args.cores
                dry_run, force = args.dry_run, args.force
            plan = make_plan(config, args.deep, args.extend)
            result, code = run_pipeline(plan, args.engine, dry_run, force)
        if args.command in ("run", "demo"):
            from .run_summary import format_run_summary
            text = format_run_summary(result["summary"], artifacts=result.get("artifacts"))
            if args.output == "summary":
                print(text)
            else:
                print(json.dumps(result, indent=2, default=str))
                print(text, file=sys.stderr)
        else:
            print(json.dumps(result, indent=2, default=str))
        return code
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"BaSIS error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
