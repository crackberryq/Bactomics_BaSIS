#!/usr/bin/env python3
"""Add MOB-suite to isolate1's completed six-module assessment.

Run inside the basis environment after copying this file into the BaSIS
checkout. Requires the BaSIS_run_context.py launcher from Context update 4.
Package source and existing study configurations are left intact.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def prepare_config(project: Path, database: Path) -> tuple[Path, dict]:
    import yaml
    from basis.config import load_config
    from BaSIS_run_context import clean_config

    source = project / "study-isolate1/config.amr-vfdb-context-antismash.yaml"
    target = source.with_name("config.core.yaml")
    config = clean_config(load_config(source))
    allowed = {"amrfinder", "rgi", "vfdb", "genomad", "integronfinder", "antismash", "mobsuite"}
    enabled = {name for name, cfg in config["modules"].items()
               if cfg.get("enabled") and name != "pathogenfinder2"}
    if enabled - allowed:
        raise ValueError("Unexpected enabled modules: " + ", ".join(sorted(enabled - allowed)))
    env = project / ".basis-envs/basis-mobsuite"
    if not os.access(env / "bin/mob_recon", os.X_OK):
        raise FileNotFoundError(f"MOB-suite executable missing: {env / 'bin/mob_recon'}")
    database = database.resolve()
    if not database.is_dir() or not any(database.iterdir()):
        raise FileNotFoundError(f"Prepared MOB-suite database directory missing or empty: {database}")
    required = ["ncbi_plasmid_full_seqs.fas", "ncbi_plasmid_full_seqs.fas.msh",
                "clusters.txt", "rep.dna.fas", "repetitive.dna.fas",
                "mob.proteins.faa", "mpf.proteins.faa", "orit.fas",
                "taxa.sqlite", "status.txt"]
    missing = [name for name in required if not (database / name).is_file()
               or not (database / name).stat().st_size]
    if missing:
        raise FileNotFoundError("MOB-suite database incomplete: " + ", ".join(missing))
    if (database / ".lock").exists():
        raise ValueError("MOB-suite initialization lock exists; let initialization finish first.")
    config["modules"]["mobsuite"].update(
        enabled=True, mode="native", executable="mob_recon", version_args=["--version"],
        command_prefix=["conda", "run", "--no-capture-output", "-p", str(env)],
        database=str(database),
        database_version="mob_init snapshot; file paths and SHA256 hashes recorded in provenance",
    )
    if target.exists():
        if clean_config(load_config(target)) != config:
            raise ValueError(f"{target} already contains different settings; leaving it "
                             "unchanged. Share this message for review.")
    else:
        with target.open("x", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, sort_keys=False)
    return target, load_config(target)


def show_mob(result: dict) -> None:
    evidence = json.loads(Path(result["artifacts"]["evidence_json"]).read_text(encoding="utf-8"))
    for record in evidence["records"]:
        if record["module"] != "mobsuite" or record.get("execution_status") != "completed":
            continue
        metrics = record.get("metrics", {})
        print(f"\n[{record['sample_id']}] MOB-suite classifications:"
              f" chromosome contigs: {metrics.get('chromosome_contigs', 'unknown')}"
              f" | plasmid contigs: {metrics.get('plasmid_contigs', 'unknown')}")
        for finding in record.get("findings", []):
            print("  Plasmid candidate:", finding.get("contig"),
                  "| predicted mobility:", finding.get("attributes", {}).get("predicted_mobility"))
        print("  Reference-dependent classifications; a negative call does not establish plasmid absence.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--database-dir", type=Path, help="Prepared MOB-suite database directory")
    parser.add_argument("--prepare-only", action="store_true", help="Write config without running analyses")
    args = parser.parse_args(argv)
    project = args.project.expanduser().resolve()
    if not (project / "basis/config.py").is_file() or not (project / "BaSIS_run_context.py").is_file():
        print("ERROR: Copy this file into the BaSIS checkout with Context update 4 installed.", file=sys.stderr)
        return 2
    sys.path.insert(0, str(project))
    try:
        from basis.pipeline import make_plan, run_pipeline
        from BaSIS_run_context import print_summary

        database = (args.database_dir.expanduser() if args.database_dir else
                    project.parent / "db/mobsuite")
        target, config = prepare_config(project, database)
        print("Config:", target, flush=True)
        print("MOB-suite database:", config["modules"]["mobsuite"]["database"], flush=True)
        if args.prepare_only:
            print("Prepared; no analyses were run.")
            return 0
        plan = make_plan(config, deep=False, extend=False)
        print("Running combined assessment with MOB-suite; valid prior caches can be reused.", flush=True)
        print("Database hashing and native analysis may be quiet for a while.", flush=True)
        result, code = run_pipeline(plan, engine="local", force=False)
        print_summary(result, code, plan["enabled_modules"])
        show_mob(result)
        return code
    except KeyboardInterrupt:
        print("\nInterrupted. Re-running can reuse completed results.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
