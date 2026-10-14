#!/usr/bin/env python3
"""Add geNomad and IntegronFinder to the existing isolate1 AMR + VFDB study.

Copy into the BaSIS checkout and run in the basis environment. This launcher
preserves package source, the original config, output location and prior module
settings. It downloads nothing and reuses only caches accepted by BaSIS.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def clean_config(config: dict) -> dict:
    result = copy.deepcopy(config)
    result.pop("config_path", None)
    return result


def integron_resources(env: Path) -> dict:
    code = (
        "import json; from pathlib import Path; "
        "from importlib.resources import files; "
        "from importlib.metadata import version; "
        "p=Path(str(files('integron_finder')/'data'/'Models')).resolve(); "
        "print(json.dumps({'path':str(p),'version':version('integron_finder')}))"
    )
    process = subprocess.run(
        ["conda", "run", "--no-capture-output", "-p", str(env), "python", "-c", code],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
    )
    if process.returncode:
        raise RuntimeError("Cannot locate IntegronFinder's installed models:\n" + process.stderr)
    resources = json.loads(process.stdout.strip().splitlines()[-1])
    model_dir = Path(resources["path"])
    for name in ("integron_integrase.hmm", "phage-int.hmm", "attc_4.cm"):
        if not (model_dir / name).is_file() or not (model_dir / name).stat().st_size:
            raise FileNotFoundError(f"Missing IntegronFinder model: {model_dir / name}")
    return resources


def choose_topology(config: dict, override: str | None) -> tuple[str, str]:
    from basis.inputs import load_samples

    configured = config["modules"]["integronfinder"].get("topology")
    if override or configured:
        value = override or configured
        if value not in {"linear", "circular"}:
            raise ValueError("IntegronFinder topology must be linear or circular")
        return value, "command-line choice" if override else "existing configuration"
    states = []
    for sample in load_samples(config["samples"]):
        genbank = sample.get("genbank")
        if not genbank or not Path(genbank).is_file():
            states.append("unknown")
            continue
        loci = []
        with Path(genbank).open(encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("LOCUS "):
                    values = set(line.lower().split()) & {"linear", "circular"}
                    loci.append(next(iter(values)) if len(values) == 1 else "unknown")
        states.extend(loci or ["unknown"])
    if states and set(states) == {"circular"}:
        return "circular", "all supplied GenBank records declare circular topology"
    if "circular" in states:
        raise ValueError("Mixed or incomplete topology declarations: this adapter needs "
                         "one topology per run. Share this message before proceeding.")
    return "linear", "BaSIS default; physical chromosome circularity is unconfirmed"


def prepare_config(project: Path, database: Path, topology: str | None) -> tuple[Path, dict]:
    import yaml
    from basis.config import load_config

    source = project / "study-isolate1/config.amr-vfdb.yaml"
    target = source.with_name("config.amr-vfdb-context.yaml")
    config = clean_config(load_config(source))
    allowed = {"amrfinder", "rgi", "vfdb", "genomad", "integronfinder"}
    enabled = {name for name, cfg in config["modules"].items()
               if cfg.get("enabled") and name != "pathogenfinder2"}
    if enabled - allowed:
        raise ValueError("Unexpected enabled modules: " + ", ".join(sorted(enabled - allowed)))
    env = project / ".basis-envs/basis-context"
    for executable in ("genomad", "integron_finder"):
        if not os.access(env / "bin" / executable, os.X_OK):
            raise FileNotFoundError(f"Missing executable: {env / 'bin' / executable}")
    database = database.resolve()
    if not database.is_dir() or not any(p.is_file() and p.stat().st_size for p in database.iterdir()):
        raise FileNotFoundError(f"Extracted geNomad database missing or empty: {database}")
    database_version = (database / "version.txt").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", database_version):
        raise ValueError("Cannot read the geNomad database release from version.txt")
    models = integron_resources(env)
    selected_topology, topology_source = choose_topology(config, topology)
    prefix = ["conda", "run", "--no-capture-output", "-p", str(env)]
    for name, executable in (("genomad", "genomad"), ("integronfinder", "integron_finder")):
        config["modules"][name].update(
            enabled=True, mode="native", executable=executable,
            version_args=["--version"], command_prefix=list(prefix),
        )
    config["modules"]["genomad"].update(
        database=str(database), database_version=database_version,
        database_version_source="database version.txt", splits=8,
    )
    # The native adapter uses the installed models directly. Recording their
    # actual directory here makes BaSIS fingerprint those same model files.
    config["modules"]["integronfinder"].update(
        database=models["path"],
        database_version="bundled with IntegronFinder " + models["version"],
        topology=selected_topology, topology_source=topology_source, local_max=True,
    )
    if target.exists():
        if clean_config(load_config(target)) != config:
            raise ValueError(f"{target} already contains different settings; leaving it "
                             "unchanged. Share this message for review.")
    else:
        with target.open("x", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, sort_keys=False)
    print("IntegronFinder topology:", selected_topology, "|", topology_source, flush=True)
    print("geNomad database:", database, "| version:", database_version, "| splits: 8", flush=True)
    print("IntegronFinder models:", models["path"], flush=True)
    return target, load_config(target)


def print_summary(result: dict, code: int, enabled: list[str]) -> None:
    print(f"\nExit code: {code}")
    print("Run status:", result["run"]["completeness"])
    evidence = json.loads(Path(result["artifacts"]["evidence_json"]).read_text(encoding="utf-8"))
    for record in evidence["records"]:
        module = record["module"]
        if module not in enabled:
            continue
        status = record.get("execution_status", "unknown")
        print(f"[{record['sample_id']}] {module} | status: {status}"
              f" | reused: {record.get('cache_hit', False)}")
        if status != "completed":
            print("  Error:", record.get("error", record.get("reason", "See evidence")))
            for artifact in record.get("artifacts", []):
                if isinstance(artifact, str) and Path(artifact).name == "commands.log":
                    print("  Log:", artifact)
                    path = Path(artifact)
                    if path.is_file():
                        print("  Last log lines:")
                        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]:
                            print("   ", line)
            continue
        findings = record.get("findings", [])
        if module == "vfdb":
            passing = [f for f in findings if f.get("attributes", {}).get("passes_threshold") is True]
            proteins = {f["protein_id"] for f in passing if f.get("protein_id")}
            print(f"  Alignments: {len(findings)} | Passing: {len(passing)} | Proteins: {len(proteins)}")
        elif module == "genomad":
            metrics = record.get("metrics", {})
            integrated = sum(f.get("attributes", {}).get("integrated") is True for f in findings)
            print(f"  Virus predictions: {metrics.get('virus_count', 'unknown')}"
                  f" | Integrated proviruses: {integrated}"
                  f" | Plasmid predictions: {metrics.get('plasmid_count', 'unknown')}")
        elif module == "integronfinder":
            counts = Counter(f.get("family") for f in findings)
            print(f"  Predicted loci: {len(findings)} | Complete: {counts['complete']}"
                  f" | CALIN: {counts['CALIN']} | In0: {counts['In0']}")
        else:
            print("  Finding records:", len(findings))
    print("Evidence:", result["artifacts"]["evidence_json"])
    print("Report:", result["artifacts"]["html"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--genomad-db", type=Path, help="Extracted geNomad database directory")
    parser.add_argument("--topology", choices=("linear", "circular"), help="Explicit IntegronFinder topology")
    parser.add_argument("--prepare-only", action="store_true", help="Write configuration without running analyses")
    args = parser.parse_args(argv)
    project = args.project.expanduser().resolve()
    if not (project / "basis/config.py").is_file():
        print("ERROR: Copy this launcher into the BaSIS checkout before running it.", file=sys.stderr)
        return 2
    sys.path.insert(0, str(project))
    try:
        from basis.pipeline import make_plan, run_pipeline

        database = (args.genomad_db.expanduser() if args.genomad_db else
                    project.parent / "db/genomad/genomad_db")
        target, config = prepare_config(project, database, args.topology)
        print("Config:", target, flush=True)
        if args.prepare_only:
            print("Prepared; no analyses were run.")
            return 0
        plan = make_plan(config, deep=False, extend=False)
        print("Running AMR + VFDB + mobile-element assessment...", flush=True)
        print("Database hashing and native analyses may be quiet for a while; logs are saved with each attempt.", flush=True)
        result, code = run_pipeline(plan, engine="local", force=False)
        print_summary(result, code, plan["enabled_modules"])
        return code
    except KeyboardInterrupt:
        print("\nInterrupted. Re-running can reuse completed results.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
