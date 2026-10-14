"""Staged optional-screen setup; never modifies existing study inputs or results."""
from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .config import CORE, OPTIONAL, load_config
from .inputs import load_samples, validate_sample
from .environment_setup import recipe_resource, workspace_path
from importlib.resources import as_file
from .progress import stage
from .util import atomic_json, hash_object, sha256_file

MODULES = ("bacmet", "isescan", "icescreen", "minced")
PINS = {
    "isescan": {"isescan": "1.7.3", "python": "3.10", "numpy": "1.26",
                "scipy": "1.10", "fastcluster": "1.2.6", "biopython": "1.81"},
    "icescreen": {"icescreen": "1.3.3", "python": "3.11", "snakemake-minimal": "8.18.0",
                  "pandas": "2.2", "biopython": "1.85", "blast": "2.16", "hmmer": "3.4"},
    "minced": {"minced": "0.4.2", "openjdk": "17"},
}


def say(message):
    print(message, flush=True)


def run(argv, *, capture=False, cwd=None):
    say("$ " + __import__("shlex").join(map(str, argv)))
    env = os.environ.copy()
    env.update(CONDA_FETCH_THREADS="1", CONDA_REMOTE_MAX_RETRIES="5",
               CONDA_REMOTE_READ_TIMEOUT_SECS="120", CONDA_CHANNEL_PRIORITY="strict")
    with stage("Setup: " + Path(str(argv[0])).name):
        try:
            return subprocess.run(list(map(str, argv)), check=True, text=True, cwd=cwd, env=env,
                                  stdout=subprocess.PIPE if capture else None,
                                  stderr=subprocess.STDOUT if capture else None)
        except subprocess.CalledProcessError as exc:
            if capture and exc.stdout:
                raise RuntimeError(f"Setup command failed (exit {exc.returncode}): {exc.stdout[-6000:]}") from exc
            raise


def package_list(prefix):
    return json.loads(run(["conda", "list", "--prefix", prefix, "--json"], capture=True).stdout)


def pins_match(packages, pins):
    versions = {p["name"]: p["version"] for p in packages}
    return all(versions.get(name) == pin or versions.get(name, "").startswith(pin + ".")
               for name, pin in pins.items())


def ensure_environment(project, module, install):
    project = workspace_path(project)
    prefix = workspace_path(project / ".basis-envs" / ("basis-" + module))
    packages = package_list(prefix) if (prefix / "conda-meta/history").is_file() else []
    if not pins_match(packages, PINS[module]):
        if not install:
            raise ValueError(f"{module}: environment missing or pins differ; rerun with --install")
        # Update interrupted/existing prefixes; never remove an environment to retry a download.
        operation = "update" if prefix.exists() else "create"
        with as_file(recipe_resource(module)) as recipe:
            command = ["conda", "env", operation, "--prefix", prefix, "--file", recipe]
            if operation == "create":
                command.append("--yes")
            run(command)
        packages = package_list(prefix)
    if not pins_match(packages, PINS[module]):
        raise ValueError(f"{module}: installed versions do not match the tested interface pins")
    return prefix, packages


def save_immutable(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError("Existing file differs and was preserved: " + str(path))
    else:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)


def verify_environment(project, module, prefix, packages, threads):
    project = workspace_path(project)
    executable = {"isescan": "isescan.py", "icescreen": "icescreen", "minced": "minced"}[module]
    command_prefix = ["conda", "run", "--no-capture-output", "-p", str(prefix)]
    version_args = ["--version"]
    version = run(command_prefix + [executable] + version_args, capture=True).stdout.strip()
    if PINS[module][module] not in version:
        raise ValueError(f"Unexpected {module} version output: {version}")
    say(version)
    required = [prefix / "bin" / executable]
    config = {"enabled": True, "mode": "native", "cache": True, "executable": executable,
              "command_prefix": command_prefix, "version_args": version_args,
              "threads": threads, "memory_mb": 4000, "timeout_seconds": 14400}
    if module == "isescan":
        database = prefix / "bin/pHMMs"
        required += [database / "clusters.single.faa", database / "clusters.faa.hmm", prefix / "lib/libssw.so",
                     prefix / "bin/run_FragGeneScan.pl"]
        config["database"] = str(database.resolve())
        for tool, args in ((executable, ["--help"]), ("hmmsearch", ["-h"]), ("phmmer", ["-h"]),
                           ("blastn", ["-version"])):
            run(command_prefix + [tool] + args, capture=True)
        run(command_prefix + ["python", "-c", "import numpy, scipy, fastcluster; from Bio import SeqIO"], capture=True)
        run(command_prefix + ["python", "-c", "import ctypes,sys; ctypes.CDLL(sys.argv[1])",
                              str(prefix / "lib/libssw.so")], capture=True)
    elif module == "icescreen":
        database = prefix / "bin/icescreen_detection_SP/database"
        if not (database / "blastdb").is_dir() or not (database / "hmmdb").is_dir():
            raise ValueError("ICEscreen bundled BLAST/HMM database folders missing")
        profile = prefix / "bin/icescreen_pipelines/mode/bacillota.yml"
        required.append(profile)
        config.update(database=str(database.resolve()), phylum="bacillota")
        for tool, args in ((executable, ["--help"]), ("snakemake", ["--version"]),
                           ("blastp", ["-version"]), ("hmmsearch", ["-h"])):
            run(command_prefix + [tool] + args, capture=True)
    else:
        run(command_prefix + ["java", "-version"], capture=True)
        # MinCED has no database; fingerprint its installed executable/JAR files explicitly.
        jar_files = sorted((prefix / "share").rglob("minced*.jar"))
        if not jar_files:
            raise ValueError("MinCED installed JAR not found under environment share/")
        required += jar_files
    for path in required:
        if not path.is_file() or not path.stat().st_size:
            raise ValueError("Missing/empty installed resource: " + str(path))
    manifest = {"schema": "basis-optional-runtime-1", "module": module,
                "prefix": str(prefix.resolve()), "version_output": version,
                "packages": sorted(packages, key=lambda p: p["name"]),
                "files": {str(p.resolve()): sha256_file(p) for p in required}}
    path = project / "resources/optional-runtime" / f"{module}-{hash_object(manifest)[:16]}.json"
    save_immutable(path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    config["metadata"] = str(path.resolve())
    return config


def merged_catalogue(study, deep, bacmet_fasta):
    """Keep existing reviewed anchors and append unreviewed BacMet search references."""
    from .catalog import from_fasta
    from .references import load_catalog, write_fasta
    old_fasta, old_metadata = Path(deep["reference_fasta"]), Path(deep["reference_metadata"])
    key = hash_object({"fasta": sha256_file(old_fasta), "metadata": sha256_file(old_metadata),
                       "bacmet": sha256_file(bacmet_fasta)})[:20]
    root = study / "optional-reference-catalogues" / key
    fasta, metadata, manifest = root / "references.faa", root / "references.tsv", root / "provenance.json"
    if manifest.is_file():
        record = json.loads(manifest.read_text())
        if any(sha256_file(root / name) != digest for name, digest in record["sha256"].items()):
            raise ValueError("Optional reference catalogue changed; refusing reuse")
        load_catalog(fasta, metadata)
        return str(fasta), str(metadata)
    if root.exists():
        # A prior interrupted build is retained for inspection; create a distinct replacement.
        root = root.with_name(root.name + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        fasta, metadata, manifest = root / "references.faa", root / "references.tsv", root / "provenance.json"
    root.mkdir(parents=True)
    old = load_catalog(old_fasta, old_metadata)
    from_fasta(Path(bacmet_fasta), root / "bacmet.faa", root / "bacmet.tsv", "BacMet 2.0 experimental source; comparator labels unreviewed")
    extra = load_catalog(root / "bacmet.faa", root / "bacmet.tsv")
    if old.sequences.keys() & extra.sequences.keys():
        raise ValueError("BacMet reference identifiers collide with existing catalogue")
    sequences = {**old.sequences, **extra.sequences}
    rows = list(old.metadata.values()) + list(extra.metadata.values())
    fields = list(dict.fromkeys(k for row in rows for k in row))
    write_fasta(sequences, fasta)
    with metadata.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    load_catalog(fasta, metadata)
    atomic_json(manifest, {"schema": "basis-optional-catalogue-1", "sources": {
        str(old_fasta): sha256_file(old_fasta), str(old_metadata): sha256_file(old_metadata),
        str(bacmet_fasta): sha256_file(bacmet_fasta)}, "added_references": len(extra.sequences),
        "added_review_status": "unreviewed", "sha256": {
            "references.faa": sha256_file(fasta), "references.tsv": sha256_file(metadata)}})
    return str(fasta), str(metadata)


def prepare_configs(study, base, native_configs, bacmet_fasta=None):
    from .pipeline import _import_result
    samples = load_samples(base["samples"])
    if len(samples) != 1 or samples[0]["sample_id"] != "isolate1":
        raise ValueError("This setup helper expects the isolate1 study; the underlying pipeline remains multi-sample")
    sample = samples[0]
    validation = validate_sample(sample)
    if validation.get("errors"):
        raise ValueError("Input validation failed: " + str(validation["errors"]))
    if "icescreen" in native_configs and (sample.get("taxon") or "").strip().lower() != "lysinibacillus irui":
        raise ValueError("Study ICEscreen preset requires the confirmed Lysinibacillus irui taxon; review phylum for other samples")
    if "icescreen" in native_configs and not sample.get("genbank"):
        raise ValueError("ICEscreen requires the matching annotated GenBank file")
    config = copy.deepcopy(base)
    config.pop("config_path", None)
    for module in OPTIONAL:
        config["modules"][module]["enabled"] = False
    if "minced" in native_configs:
        config["modules"]["crisprcasfinder"]["disabled_reason"] = "alternative_not_selected_minced"
    if (sample.get("taxon") or "").strip().lower() == "lysinibacillus irui":
        config["modules"]["mlst"]["disabled_reason"] = "no_applicable_scheme_identified"
    for module, values in native_configs.items():
        config["modules"][module].update(values)
        config["modules"][module].pop("result", None)
        config["modules"][module].pop("disabled_reason", None)
    config["deep"]["include_stress"] = "bacmet" in native_configs
    if bacmet_fasta:
        a, b = merged_catalogue(study, config["deep"], bacmet_fasta)
        config["deep"].update(reference_fasta=a, reference_metadata=b)
    # Do not restore old reviewed tree paths after the user's results cleanup.
    config["deep"]["tree_review_registry"] = None
    config["deep"]["cache"] = True
    source_outdir = Path(base["outdir"])
    config["outdir"] = str(study / "results-expanded-native")
    for module in CORE + ["pathogenfinder2"]:
        cfg = config["modules"][module]
        if not cfg.get("enabled") or cfg.get("mode") != "native":
            raise ValueError("Use the full native base configuration; module is disabled/imported: " + module)
        cfg["cache"] = True
        for key in ("result", "embeddings", "embeddings_manifest"):
            cfg.pop(key, None)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    native_path = study / f"config.expanded-native-{stamp}.yaml"
    supplemental = copy.deepcopy(config)
    supplemental["outdir"] = str(study / "results-optional")
    for module in CORE + ["pathogenfinder2"]:
        path = source_outdir / "results/isolate1" / module / "result.json"
        with stage("Verify saved " + module + " evidence"):
            source = json.loads(path.read_text())
            _import_result(str(path), sample, module, validation)
            checksums = source.get("artifact_checksums", {})
            if not checksums:
                raise ValueError("Recorded artifact checksums missing: " + str(path))
            for name, digest in checksums.items():
                if sha256_file(name) != digest:
                    raise ValueError("Saved artifact changed: " + name)
        supplemental["modules"][module].update(mode="import", result=str(path), cache=True, gpus=0)
    supplemental_path = study / f"config.optional-{stamp}.yaml"
    save_immutable(native_path, yaml.safe_dump(config, sort_keys=False))
    save_immutable(supplemental_path, yaml.safe_dump(supplemental, sort_keys=False))
    return native_path, supplemental_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Create/update isolated tool environments and download pinned BacMet data")
    parser.add_argument("--run", action="store_true", help="Run optional tools and DeepSafe; import verified prior core/Extend evidence")
    parser.add_argument("--modules", nargs="+", choices=MODULES, default=list(MODULES))
    parser.add_argument("--project", "--workspace", dest="project", default=".",
                        help="Writable project workspace; default: current working directory")
    parser.add_argument("--study", default="study-isolate1")
    parser.add_argument("--config", help="Native full-run configuration; defaults to config.fresh.yaml then config.combined.yaml")
    parser.add_argument("--database-root", default=str(Path.home() / "bactomics/db"))
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args(argv)
    os.environ["BASIS_VERBOSE"] = "1"
    try:
        project = workspace_path(args.project)
        study = Path(args.study).expanduser()
        study = workspace_path(study if study.is_absolute() else project / study)
    except ValueError as exc:
        parser.error(str(exc))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    summary_path = study / "optional-setup" / stamp / "setup.json"
    summary = {"schema": "basis-optional-setup-1", "modules": {}, "native_validation": "pending"}
    try:
        if not shutil.which("conda"):
            raise ValueError("conda is not on PATH; activate basis first")
        if args.threads < 1:
            raise ValueError("--threads must be positive")
        config_path = (project / Path(args.config).expanduser()).resolve() if args.config else next(
            (p for p in (study / "config.fresh.yaml", study / "config.combined.yaml") if p.is_file()), None)
        if config_path is None:
            raise ValueError("Native base configuration not found; pass --config")
        base = load_config(config_path)
        threads = min(args.threads, int(base["execution"]["cores"]))
        say("Base configuration: " + str(config_path))
        native_configs, bacmet_fasta = {}, None
        for module in dict.fromkeys(args.modules):
            say("\nPreparing " + module)
            try:
                if module == "bacmet":
                    from .optional_bacmet import prepare_bacmet
                    env = project / ".basis-envs/basis-rgi"
                    if not (env / "conda-meta/history").is_file():
                        raise ValueError("Expected previously installed DIAMOND in " + str(env))
                    prepared = prepare_bacmet(workspace_path(Path(args.database_root).expanduser() / "bacmet"), env,
                                              threads=threads, log=say, reuse=True, allow_download=args.install)
                    bacmet_fasta = prepared["reference_fasta"]
                    native_configs[module] = {"enabled": True, "mode": "native", "cache": True,
                        "database": prepared["database"], "database_manifest": prepared["provenance"],
                        "database_version": prepared["database_version"], "executable": "diamond",
                        "command_prefix": prepared["command_prefix"], "version_args": ["version"],
                        "threads": threads, "memory_mb": 4000, "timeout_seconds": 14400,
                        "min_identity": 80.0, "min_coverage": 70.0, "min_query_coverage": 70.0}
                else:
                    env, packages = ensure_environment(project, module, args.install)
                    native_configs[module] = verify_environment(project, module, env, packages, threads)
                summary["modules"][module] = {"status": "ready", "config": native_configs[module]}
            except Exception as exc:
                summary["modules"][module] = {"status": "failed", "error": str(exc)}
                say("SETUP FAILED: " + module + " | " + str(exc))
            atomic_json(summary_path, summary)
        if any(v["status"] != "ready" for v in summary["modules"].values()):
            say("\nSetup incomplete. Successful installations are retained; rerun the same command to resume.")
            say("Setup log: " + str(summary_path))
            return 2
        native_path, supplemental_path = prepare_configs(study, base, native_configs, bacmet_fasta)
        summary.update(native_config=str(native_path), supplemental_config=str(supplemental_path))
        atomic_json(summary_path, summary)
        say("\nOptional tools and resources ready. Native format validation occurs during the run.")
        say("Full native configuration: " + str(native_path))
        say("Supplemental configuration: " + str(supplemental_path))
        if not args.run:
            say(f"Run: basis run -c {supplemental_path} -d -e -v")
            return 0
        from .pipeline import make_plan, run_pipeline
        say("Running new screens + DeepSafe. Prior core and GPU results are imported with verified inputs/artifacts.")
        result, code = run_pipeline(make_plan(load_config(supplemental_path), deep=True, extend=True))
        outdir = Path(result["artifacts"]["evidence_json"]).parent
        statuses = {}
        for module in list(native_configs) + ["deepsafe"]:
            r = json.loads((outdir / "results/isolate1" / module / "result.json").read_text())
            statuses[module] = r.get("execution_status")
            say(f"{module}: {r.get('execution_status')} | metrics: {r.get('metrics', {})}")
            if r.get("error"):
                say("ERROR: " + str(r["error"]))
        summary.update(native_validation=statuses, exit_code=code, report=result["artifacts"]["html"])
        atomic_json(summary_path, summary)
        say("Report: " + result["artifacts"]["html"])
        say("DeepSafe may retain an incomplete overall scope for deferred candidates; inspect selected execution separately.")
        say(f"Future complete native run: basis run -c {native_path} -d -e -v")
        return code
    except Exception as exc:
        summary["error"] = str(exc)
        atomic_json(summary_path, summary)
        say("STOPPED: " + str(exc))
        say("Setup log: " + str(summary_path))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
