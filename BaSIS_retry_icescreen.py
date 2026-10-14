#!/usr/bin/env python3
"""Retry only ICEscreen in a new directory, preserving the complete optional run."""
import argparse
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

from basis.config import load_config
from basis.pipeline import make_plan, run_pipeline
from basis.util import hash_object


def find_config(study, source, explicit=None):
    if explicit:
        config = load_config(explicit)
        if Path(config["outdir"]).resolve() != source:
            raise ValueError("The supplied configuration does not belong to the source result directory")
        return config
    recorded = json.loads((source / "run.json").read_text())
    matches = []
    for path in study.glob("config.optional-*.yaml"):
        config = load_config(path)
        if hash_object(config) == recorded.get("config_hash"):
            matches.append(config)
    if len(matches) != 1:
        raise ValueError("Cannot uniquely identify the configuration used by this run; pass --config explicitly")
    return matches[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", default="study-isolate1")
    parser.add_argument("--config", help="Optional explicit configuration for the recorded optional run")
    args = parser.parse_args(argv)
    os.environ["BASIS_VERBOSE"] = "1"
    project = Path(__file__).resolve().parent
    study = (project / Path(args.study).expanduser()).resolve()
    source = study / "results-optional"
    config = copy.deepcopy(find_config(study, source, args.config))
    prior = json.loads((source / "results/isolate1/icescreen/result.json").read_text())
    if prior.get("sample_id") != "isolate1" or prior.get("module") != "icescreen":
        raise ValueError("Recorded result is not isolate1 ICEscreen evidence")
    if config["modules"]["icescreen"].get("mode") != "native":
        raise ValueError("ICEscreen retry requires native mode")
    for module, settings in config["modules"].items():
        settings["enabled"] = module == "icescreen"
    config["modules"]["icescreen"]["cache"] = False
    config.pop("config_path", None)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = study / "icescreen-retries" / stamp
    directory.mkdir(parents=True, exist_ok=False)
    config["outdir"] = str(directory / "results")
    config_path = directory / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    plan = make_plan(load_config(config_path), deep=False, extend=False)
    if len(plan["samples"]) != 1 or plan["samples"][0]["sample_id"] != "isolate1":
        raise ValueError("This retry helper expects the single isolate1 study")
    validation = plan["validations"]["isolate1"]
    if validation.get("errors"):
        raise ValueError("Input validation failed: " + str(validation["errors"]))
    if not prior.get("input_hashes") or validation["input_hashes"] != prior["input_hashes"]:
        raise ValueError("Inputs differ from the recorded ICEscreen attempt; retry stopped")
    print("Retrying ICEscreen only. Core, BacMet, ISEScan, MinCED, DeepSafe and GPU analyses are not rerun.", flush=True)
    print("Original optional report and results preserved:", source, flush=True)
    result, code = run_pipeline(plan)
    evidence = Path(config["outdir"]) / "results/isolate1/icescreen/result.json"
    record = json.loads(evidence.read_text())
    print("\nICEscreen:", record.get("execution_status"), flush=True)
    print("Metrics:", json.dumps(record.get("metrics", {})), flush=True)
    if record.get("error"):
        print("ERROR:", record["error"], flush=True)
        for value in record.get("artifacts", []):
            if isinstance(value, str) and Path(value).name == "commands.log" and Path(value).is_file():
                print("COMMAND LOG:", value, flush=True)
                print("\n".join(Path(value).read_text(errors="replace").splitlines()[-100:]), flush=True)
    print("Evidence:", evidence, flush=True)
    print("Report (ICEscreen only):", result["artifacts"]["html"], flush=True)
    print("The existing combined report still describes the original run; this retry is recorded separately.", flush=True)
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        raise SystemExit("ICEscreen retry stopped: " + str(exc)) from exc
