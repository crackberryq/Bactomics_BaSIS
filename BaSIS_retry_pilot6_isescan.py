#!/usr/bin/env python3
"""Retry only isolate6 ISEScan in a new directory; preserve the original pilot."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
import uuid

import yaml

DEFAULT_PILOT = "study-pilot456/20261011T173722801483Z-9f8afc"
EXPECTED_ERROR = "ISEScan output table missing without an explicit native zero-result marker"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check_hash(path, expected):
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError(f"Missing or invalid recorded checksum: {path}")
    if not Path(path).is_file() or sha(path) != expected:
        raise ValueError(f"File missing or changed since the pilot: {path}")


def require_version(record):
    version = record.get("provenance", {}).get("tool_version", {})
    if version.get("status") != "completed" or not re.search(
            r"\bisescan\s+1\.7\.3\b", str(version.get("output", "")), re.I):
        raise ValueError("This targeted retry requires recorded ISEScan 1.7.3")


def verify_source(project, batch, load_config, load_samples):
    if not batch.is_relative_to(project / "study-pilot456"):
        raise ValueError("Pilot directory must be inside this project's study-pilot456 directory")
    manifest_path = batch / "pilot.json"
    manifest = read_json(manifest_path)
    if (manifest.get("schema") != "basis-pilot456-runner-v1"
            or manifest.get("project") != str(project)
            or manifest.get("batch") != str(batch)):
        raise ValueError("Pilot manifest identity mismatch")
    matches = [s for s in manifest.get("samples", []) if s.get("sample_id") == "isolate6"]
    if len(matches) != 1:
        raise ValueError("Expected exactly one isolate6 pilot entry")
    item = matches[0]
    directory = batch / "isolate6"
    config_path, sample_path = directory / "config.yaml", directory / "samples.tsv"
    if (Path(item["config"]).resolve() != config_path
            or Path(item["outdir"]).resolve() != directory / "results"):
        raise ValueError("Recorded isolate6 paths do not belong to this pilot")
    check_hash(config_path, item.get("config_sha256"))
    check_hash(sample_path, item.get("samples_sha256"))
    review_path = batch / "input-review.json"
    check_hash(review_path, manifest.get("input_review_sha256"))
    review = read_json(review_path)
    matches = [s for s in review.get("samples", []) if s.get("sample_id") == "isolate6"]
    if review.get("schema") != "basis-pilot456-input-review-v1" or len(matches) != 1:
        raise ValueError("Input inspection identity mismatch")
    inspected = matches[0]
    expected = item.get("input_hashes")
    if (not expected or expected != inspected.get("validation", {}).get("input_hashes")
            or inspected.get("file_consistency") != "passed" or inspected.get("errors")):
        raise ValueError("Pilot and inspected input checksums or validation disagree")
    config = load_config(config_path)
    if (Path(config["samples"]).resolve() != sample_path
            or Path(config["outdir"]).resolve() != directory / "results"
            or config.get("validation_mode") != "research"):
        raise ValueError("Configuration identity or mode differs from the pilot")
    ise = config["modules"].get("isescan", {})
    if ise.get("mode") != "native" or ise.get("enabled") is not True:
        raise ValueError("The original pilot must request native ISEScan")
    samples = load_samples(str(sample_path))
    if len(samples) != 1 or samples[0].get("sample_id") != "isolate6":
        raise ValueError("This helper accepts only the original single-sample isolate6 sheet")
    sample = samples[0]
    for key, digest in expected.items():
        if (not sample.get(key) or not inspected.get("paths", {}).get(key)
                or Path(sample[key]).resolve() != Path(inspected["paths"][key]).resolve()):
            raise ValueError(f"Inspected input path mismatch: {key}")
        check_hash(sample[key], digest)
    prior_path = directory / "results/results/isolate6/isescan/result.json"
    prior = read_json(prior_path)
    if (prior.get("sample_id") != "isolate6" or prior.get("module") != "isescan"
            or prior.get("input_hashes") != expected
            or prior.get("execution_status") != "failed"
            or prior.get("error") != EXPECTED_ERROR):
        raise ValueError("Original ISEScan result does not match the known missing-marker failure")
    require_version(prior)
    protected = {str(p): sha(p) for p in
                 (manifest_path, review_path, config_path, sample_path, prior_path)}
    protected.update({str(Path(sample[key])): digest for key, digest in expected.items()})
    return config, sample, expected, prior_path, protected


def retry_config(source, directory):
    config = copy.deepcopy(source)
    config.pop("config_path", None)
    config["outdir"] = str(directory / "results")
    for name, settings in config["modules"].items():
        settings["enabled"] = name == "isescan"
    config["modules"]["isescan"]["cache"] = False
    return config


def verify_plan(plan, expected, sample, directory):
    if (plan.get("enabled_modules") != ["isescan"]
            or plan.get("options") != {"deep": False, "extend": False}
            or Path(plan["outdir"]).resolve() != directory / "results"):
        raise ValueError("Retry plan exceeds the ISEScan-only scope")
    samples = plan.get("samples", [])
    if len(samples) != 1 or samples[0].get("sample_id") != "isolate6":
        raise ValueError("Unexpected retry sample")
    validation = plan.get("validations", {}).get("isolate6", {})
    if validation.get("errors") or validation.get("input_hashes") != expected:
        raise ValueError("Retry input validation failed or differs from the original pilot")
    for key, digest in expected.items():
        if Path(samples[0][key]).resolve() != Path(sample[key]).resolve():
            raise ValueError(f"Retry input path changed: {key}")
        check_hash(sample[key], digest)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-dir", type=Path, default=Path(DEFAULT_PILOT))
    args = parser.parse_args(argv)
    os.environ["BASIS_VERBOSE"] = "1"
    import basis
    from basis.config import load_config
    from basis.inputs import load_samples
    from basis.pipeline import make_plan, run_pipeline

    project = Path(__file__).resolve().parent
    if Path.cwd().resolve() != project or Path(basis.__file__).resolve().parent != project / "basis":
        raise ValueError("Run this helper from the BaSIS project directory with its basis environment active")
    batch = args.pilot_dir.expanduser().resolve()
    print("Verifying isolate6 pilot identity and saved input checksums...", flush=True)
    source, sample, expected, prior_path, protected = verify_source(project, batch, load_config, load_samples)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = batch / "isolate6/isescan-retries" / (stamp + "-" + uuid.uuid4().hex[:6])
    directory.mkdir(parents=True, exist_ok=False)
    config_path = directory / "config.yaml"
    config_path.write_text(yaml.safe_dump(retry_config(source, directory), sort_keys=False), encoding="utf-8")
    summary = {"schema": "basis-isolate6-isescan-retry-v1", "sample_id": "isolate6",
               "module": "isescan", "created_at_utc": datetime.now(timezone.utc).isoformat(),
               "original_pilot": str(batch / "pilot.json"), "original_result": str(prior_path),
               "protected_source_files": protected, "input_hashes": expected,
               "retry_config": str(config_path), "retry_config_sha256": sha(config_path),
               "status": "preparing", "original_pilot_reconciled": False}
    code = 1
    try:
        plan = make_plan(load_config(config_path), deep=False, extend=False)
        verify_plan(plan, expected, sample, directory)
        summary.update(source_hash=plan.get("source_hash"), plan_hash=plan.get("plan_hash"))
        print("Retrying ISEScan only in a fresh output directory.", flush=True)
        print("Original pilot preserved:", batch, flush=True)
        result, code = run_pipeline(plan)
        path = directory / "results/results/isolate6/isescan/result.json"
        record = read_json(path)
        summary.update(pipeline_exit_code=code, pipeline_scope=result.get("run", {}).get("completeness"),
                       result=str(path), result_sha256=sha(path),
                       report=result.get("artifacts", {}).get("html"),
                       execution_status=record.get("execution_status"), metrics=record.get("metrics", {}),
                       error=record.get("error"))
        if (record.get("sample_id") != "isolate6" or record.get("module") != "isescan"
                or record.get("input_hashes") != expected):
            raise ValueError("Retry result identity mismatch")
        require_version(record)
        summary["status"] = "completed" if record.get("execution_status") == "completed" and code == 0 else "needs_review"
        print("\nISEScan:", record.get("execution_status"), flush=True)
        print("Metrics:", json.dumps(record.get("metrics", {})), flush=True)
        if record.get("error"):
            print("ERROR:", record["error"], flush=True)
            logs = sorted(path.parent.glob("attempts/*/commands.log"))
            for log in logs[-1:]:
                print("COMMAND LOG:", log, flush=True)
                print("\n".join(log.read_text(errors="replace").splitlines()[-100:]), flush=True)
        print("Evidence:", path, flush=True)
        print("Report (ISEScan only):", summary["report"], flush=True)
        if record.get("execution_status") != "completed" and code == 0:
            code = 2
    except Exception as exc:
        summary.update(status="stopped", error=str(exc))
        print("Retry stopped:", exc, flush=True)
        code = 1
    finally:
        changes = [path for path, digest in protected.items()
                   if not Path(path).is_file() or sha(path) != digest]
        summary["original_files_unchanged"] = not changes
        if changes:
            summary.update(status="stopped", changed_original_files=changes)
            print("STOP: original files changed during the retry:", changes, flush=True)
            code = 1
        summary["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        audit = directory / "retry-summary.json"
        audit.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print("Retry summary:", audit, flush=True)
    print("The combined pilot and its DeepSafe upstream status are unchanged; this retry is recorded separately.", flush=True)
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        raise SystemExit("ISEScan retry stopped: " + str(exc)) from exc
