#!/usr/bin/env python3
"""Render saved family placements with tree views; do not rerun any analysis."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from basis.util import atomic_json, hash_object, sha256_file


def completed_placements(evidence):
    for deep in evidence.get("deep_records", []):
        if deep.get("execution_status") != "completed":
            continue
        for candidate in deep.get("candidates", []):
            layer = candidate.get("analyses", {}).get("family_placement", {})
            if (candidate.get("selected") is True and candidate.get("execution") == "completed"
                    and layer.get("status") == "completed"):
                yield deep, candidate, layer


def validate_evidence(evidence):
    if not isinstance(evidence, dict) or evidence.get("schema_version") != "basis-report-1":
        raise ValueError("Expected a basis-report-1 evidence bundle")
    if not isinstance(evidence.get("run"), dict):
        raise ValueError("Missing saved run metadata")
    for key in ("samples", "records", "deep_records"):
        if not isinstance(evidence.get(key), list) or any(not isinstance(x, dict) for x in evidence[key]):
            raise ValueError("Invalid saved evidence list: " + key)
    if not list(completed_placements(evidence)):
        raise ValueError("No completed selected family placement in this evidence bundle")
    review = evidence["run"].get("placement_review")
    if review:
        if not isinstance(review, dict) or review.get("schema") != "basis-placement-review-1":
            raise ValueError("Unrecognized family-placement review audit")
        for key in ("records", "samples"):
            if review.get(key + "_sha256") != hash_object(evidence[key]):
                raise ValueError("Saved placement-review seal differs: " + key)
        changes = review.get("deep_changes", [])
        if (not isinstance(changes, list) or any(not isinstance(x, dict) for x in changes)
                or len({x.get("sample_id") for x in changes}) != len(changes)):
            raise ValueError("Invalid placement-review DeepSafe seals")
        by_sample = {x.get("sample_id"): x for x in changes}
        if (len({d.get("sample_id") for d in evidence["deep_records"]}) != len(evidence["deep_records"])
                or set(by_sample) != {d.get("sample_id") for d in evidence["deep_records"]}):
            raise ValueError("Placement-review DeepSafe seal/sample mismatch")
        for deep in evidence["deep_records"]:
            if by_sample[deep.get("sample_id")].get("new_record_sha256") != hash_object(deep):
                raise ValueError("Saved placement-review DeepSafe record differs: " + str(deep.get("sample_id")))


def select_source(study):
    """Use recorded UTC review time, never filesystem mtime, to select a review."""
    options = []
    for path in sorted((Path(study) / "family-placement-reviews").glob("*/evidence.json")):
        try:
            evidence = json.loads(path.read_text(encoding="utf-8"))
            validate_evidence(evidence)
            review = evidence["run"].get("placement_review", {})
            stamp = datetime.fromisoformat(review["finished_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("Review finish time is missing its timezone")
            options.append((stamp, path))
        except (ValueError, TypeError, KeyError, OSError) as exc:
            print("Skipping saved review:", path, "|", exc, flush=True)
    if not options:
        raise ValueError("No valid completed family-placement review found; specify --evidence explicitly")
    latest_time = max(stamp for stamp, _ in options)
    latest = [path for stamp, path in options if stamp == latest_time]
    if len(latest) != 1:
        raise ValueError("Several placement reviews share the latest finish time; select --evidence explicitly")
    return latest[0].resolve()


def verify_placement_artifacts(evidence):
    """Verify only saved placement files, without inspecting tools or databases."""
    paths, seals = set(), {}

    def add_seal(path, digest):
        canonical = str(Path(path).expanduser().resolve())
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid saved artifact checksum: " + canonical)
        if canonical in seals and seals[canonical] != digest:
            raise ValueError("Conflicting saved artifact checksums: " + canonical)
        seals[canonical] = digest

    for deep, _, layer in completed_placements(evidence):
        artifacts = layer.get("artifacts", [])
        if not isinstance(artifacts, list) or any(not isinstance(p, str) or not p for p in artifacts):
            raise ValueError("Invalid family-placement artifact paths")
        paths.update(str(Path(p).expanduser().resolve()) for p in artifacts)
        recorded = [deep.get("placement_review", {}).get("artifact_checksums", {}),
                    deep.get("deep_cache", {}).get("checksums", {}),
                    layer.get("artifact_checksums", {})]
        for checks in recorded:
            if not isinstance(checks, dict):
                raise ValueError("Invalid family-placement artifact checksum mapping")
            for path, digest in checks.items():
                if str(Path(path).expanduser().resolve()) in paths:
                    add_seal(path, digest)
        cache = layer.get("reference_cache", {})
        directory = cache.get("directory")
        for relative, digest in cache.get("verified_artifact_hashes", {}).items():
            key = Path(relative)
            if not directory or key.is_absolute() or ".." in key.parts:
                raise ValueError("Invalid cached reference artifact path")
            path = Path(directory) / key
            if str(path.expanduser().resolve()) in paths:
                add_seal(path, digest)
    checks = []
    for path in sorted(paths):
        expected = seals.get(path)
        item = {"path": path, "expected_sha256": expected}
        if not Path(path).is_file():
            item["status"] = "missing"
        elif expected is None:
            item["status"] = "unrecorded_checksum"
        else:
            actual = sha256_file(path)
            if actual != expected:
                raise ValueError("Saved placement artifact checksum mismatch: " + path)
            item.update(status="verified", observed_sha256=actual)
        checks.append(item)
    return checks


def derive_placement_diagnostics(evidence):
    """Return a separate diagnostic audit, leaving historical records intact."""
    from basis.report_placement_tree import _validated

    derived = []
    for deep, candidate, layer in completed_placements(evidence):
        entry = {"sample_id": deep.get("sample_id"),
                 "candidate_id": candidate.get("candidate_id"),
                 "protein_id": candidate.get("protein_id"),
                 "source_scores_sha256": hash_object(layer.get("scores", {})),
                 "source_assessment_sha256": hash_object(candidate.get("assessment", {}))}
        try:
            checked = _validated(candidate, deep)
            entry["diagnostics"] = deepcopy(checked["diagnostics"])
            entry["verification"] = "native_artifacts_and_saved_scores_verified"
            entry["native_artifact_sha256"] = {
                name: hashlib.sha256(content).hexdigest()
                for name, content in sorted(checked["contents"].items())}
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            entry["verification"] = "unavailable"
            entry["diagnostics"] = {"status": "not_assessed",
                "reason": "saved_native_evidence_unavailable_or_invalid", "error": str(exc),
                "function_probability": None}
        derived.append(entry)
    return derived


def refresh_placement_report(source, outdir):
    from basis.report import write_report

    source, outdir = Path(source).expanduser().resolve(), Path(outdir).expanduser().resolve()
    if outdir.exists():
        raise FileExistsError("Choose a new, unused report directory: " + str(outdir))
    raw = source.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    evidence = json.loads(raw)
    validate_evidence(evidence)
    print("Source evidence:", source, flush=True)
    print("Verifying saved placement artifact checksums...", flush=True)
    checks = verify_placement_artifacts(evidence)
    print("Placement artifacts:", dict(Counter(x["status"] for x in checks)), flush=True)
    for check in checks:
        if check["status"] != "verified":
            print("Tree export may be unavailable:", check["status"], "|", check["path"], flush=True)
    print("Deriving placement boundary diagnostics from saved native evidence...", flush=True)
    diagnostic_rows = derive_placement_diagnostics(evidence)
    diagnostic_counts = dict(Counter(x["diagnostics"].get("status", "not_assessed") for x in diagnostic_rows))
    print("Placement diagnostics:", diagnostic_counts, flush=True)
    merged = {key: deepcopy(evidence[key]) for key in ("run", "samples", "records", "deep_records")}
    audit = {
        "schema": "basis-placement-report-refresh-1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_evidence": str(source), "source_sha256": source_sha,
        "native_analyses_run": False, "gpu_inference_run": False, "reference_downloads_run": False,
        "scope": "Report-only tree visualization and derived placement diagnostics; original analyses, scores and interpretations unchanged.",
        "records_sha256": hash_object(evidence["records"]),
        "deep_records_sha256": hash_object(evidence["deep_records"]),
        "samples_sha256": hash_object(evidence["samples"]),
        "artifact_checks": checks,
        "helper_sha256": sha256_file(__file__),
        "placement_diagnostics": {"path": "placement-diagnostics.json",
            "statuses": diagnostic_counts, "candidate_count": len(diagnostic_rows),
            "rows_sha256": hash_object(diagnostic_rows),
            "classification_policy_changed": False},
    }
    if "placement_report_refresh" in merged["run"]:
        audit["previous_refresh"] = deepcopy(merged["run"]["placement_report_refresh"])
    merged["run"]["placement_report_refresh"] = audit
    protected = hash_object(merged)
    outdir.mkdir(parents=True, exist_ok=False)
    diagnostic_path = outdir / "placement-diagnostics.json"
    atomic_json(diagnostic_path, {
        "schema": "basis-placement-diagnostics-report-refresh-v1",
        "source_evidence": str(source), "source_sha256": source_sha,
        "native_analyses_run": False, "classification_policy_changed": False,
        "function_probability": None, "candidates": diagnostic_rows})
    print("Rendering tree views and portable downloads; no native tools or GPU inference...", flush=True)
    artifacts = write_report(merged["run"], merged["samples"], merged["records"], merged["deep_records"], outdir)
    artifacts["placement_diagnostics_json"] = str(diagnostic_path)
    if hash_object(merged) != protected:
        raise RuntimeError("Renderer changed saved execution or scientific evidence")
    if sha256_file(source) != source_sha:
        raise RuntimeError("Source evidence changed during report generation")
    if verify_placement_artifacts(evidence) != checks:
        raise RuntimeError("Saved placement artifacts changed during report generation")
    audit_file = {**deepcopy(audit), "source_unchanged": True,
                  "output_file_sha256": {str(p.relative_to(outdir)): sha256_file(p)
                                         for p in sorted(outdir.rglob("*")) if p.is_file()}}
    atomic_json(outdir / "placement-report-refresh.json", audit_file)
    return merged, artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", default="study-isolate1")
    parser.add_argument("--evidence", help="Saved evidence.json with a completed family placement")
    parser.add_argument("--outdir", help="New, unused report directory")
    args = parser.parse_args()
    os.environ["BASIS_VERBOSE"] = "1"
    study = (Path(__file__).resolve().parent / Path(args.study).expanduser()).resolve()
    source = Path(args.evidence).expanduser().resolve() if args.evidence else select_source(study)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    outdir = (Path(args.outdir).expanduser().resolve() if args.outdir else
              source.parent / "reporting-reviews" / stamp)
    _, artifacts = refresh_placement_report(source, outdir)
    print("Report:", artifacts["html"])
    print("Evidence:", artifacts["evidence_json"])
    print("Placement diagnostics:", artifacts["placement_diagnostics_json"])
    print("Audit:", outdir / "placement-report-refresh.json")
    print("Original results preserved. No analyses, downloads or GPU inference were run.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit("Placement report refresh stopped: " + str(exc)) from exc
