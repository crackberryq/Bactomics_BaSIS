#!/usr/bin/env python3
"""Prepare integrated placement and assess saved candidates without rerunning core/Extend."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import yaml

from basis.config import load_config
from basis.placement import DEFAULT_REGISTRY
from basis.placement_review import review_saved_placement


def unique(paths, label):
    paths = sorted(paths)
    if len(paths) != 1:
        raise ValueError(f"Found {len(paths)} {label}; select explicitly using --config or --evidence")
    return paths[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", default="study-isolate1")
    parser.add_argument("--config", help="Existing expanded native YAML configuration")
    parser.add_argument("--evidence", help="Existing combined evidence.json")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    os.environ["BASIS_VERBOSE"] = "1"
    root = Path(__file__).resolve().parent
    study = (root / args.study).resolve()
    base = Path(args.config).resolve() if args.config else unique(study.glob("config.expanded-native-*.yaml"), "expanded native configs")
    cfg = load_config(base)
    cfg.pop("config_path", None)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    cfg["deep"].update(family_placement=True, placement_registry=str(DEFAULT_REGISTRY.resolve()),
                       placement_cache_dir=str(study / "placement-cache"))
    cfg["outdir"] = str(study / ("results-placement-full-" + stamp))
    target = study / ("config.family-placement-" + stamp + ".yaml")
    with target.open("x") as handle:
        yaml.safe_dump(cfg, handle, sort_keys=False)
    print("Integrated configuration:", target, flush=True)
    print("Future full run: basis run -c", target, "-d -e -v", flush=True)
    if args.prepare_only:
        print("Configuration prepared. No analyses run.")
        return
    source = Path(args.evidence).resolve() if args.evidence else unique(
        (study / "results-optional/reconciled-reports").glob("*/evidence.json"), "reconciled evidence bundles")
    print("Running only the new placement layer on verified saved candidates.", flush=True)
    merged, artifacts = review_saved_placement(source, study / "family-placement-reviews" / stamp, load_config(target))
    print("Report:", artifacts["html"])
    print("Evidence:", artifacts["evidence_json"])
    failed = any(d.get("metrics", {}).get("family_placement", {}).get("statuses", {}).get("failed", 0)
                 for d in merged["deep_records"])
    if failed:
        raise SystemExit("Placement has execution failures; inspect the report and command logs.")


if __name__ == "__main__":
    main()
