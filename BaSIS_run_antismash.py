#!/usr/bin/env python3
"""Add antiSMASH to isolate1's completed AMR/VFDB/context assessment.

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

    source = project / "study-isolate1/config.amr-vfdb-context.yaml"
    target = source.with_name("config.amr-vfdb-context-antismash.yaml")
    config = clean_config(load_config(source))
    allowed = {"amrfinder", "rgi", "vfdb", "genomad", "integronfinder", "antismash"}
    enabled = {name for name, cfg in config["modules"].items()
               if cfg.get("enabled") and name != "pathogenfinder2"}
    if enabled - allowed:
        raise ValueError("Unexpected enabled modules: " + ", ".join(sorted(enabled - allowed)))
    env = project / ".basis-envs/basis-antismash"
    if not os.access(env / "bin/antismash", os.X_OK):
        raise FileNotFoundError(f"antiSMASH executable missing: {env / 'bin/antismash'}")
    database = database.resolve()
    if not database.is_dir() or not any(database.iterdir()):
        raise FileNotFoundError(f"Prepared antiSMASH database directory missing or empty: {database}")
    config["modules"]["antismash"].update(
        enabled=True, mode="native", executable="antismash", version_args=["--version"],
        command_prefix=["conda", "run", "--no-capture-output", "-p", str(env)],
        database=str(database),
        database_version="Mixed component releases; file paths and SHA256 hashes recorded in provenance",
    )
    if target.exists():
        if clean_config(load_config(target)) != config:
            raise ValueError(f"{target} already contains different settings; leaving it "
                             "unchanged. Share this message for review.")
    else:
        with target.open("x", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, sort_keys=False)
    return target, load_config(target)


def show_regions(result: dict) -> None:
    path = Path(result["artifacts"]["evidence_json"])
    evidence = json.loads(path.read_text(encoding="utf-8"))
    for record in evidence["records"]:
        if record["module"] != "antismash" or record.get("execution_status") != "completed":
            continue
        print(f"\n[{record['sample_id']}] antiSMASH predicted regions:",
              record.get("metrics", {}).get("region_count", "unknown"))
        for finding in record.get("findings", []):
            attributes = finding.get("attributes", {})
            region = attributes.get("native_region_id", finding.get("gene", "region"))
            segment = ""
            if attributes.get("segment_count", 1) > 1:
                segment = f" | segment {attributes['segment_index']}/{attributes['segment_count']}"
            print(f"  {region} | {finding['start']}-{finding['end']}"
                  f" | {finding.get('family') or 'unspecified product class'}{segment}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--database-dir", type=Path, help="Prepared antiSMASH database directory")
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
                    project.parent / "db/antismash")
        target, config = prepare_config(project, database)
        print("Config:", target, flush=True)
        print("antiSMASH database:", config["modules"]["antismash"]["database"], flush=True)
        if args.prepare_only:
            print("Prepared; no analyses were run.")
            return 0
        plan = make_plan(config, deep=False, extend=False)
        print("Running combined assessment with antiSMASH; valid prior caches can be reused.", flush=True)
        print("Database hashing and native analysis may be quiet for a while.", flush=True)
        result, code = run_pipeline(plan, engine="local", force=False)
        print_summary(result, code, plan["enabled_modules"])
        show_regions(result)
        return code
    except KeyboardInterrupt:
        print("\nInterrupted. Re-running can reuse completed results.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
