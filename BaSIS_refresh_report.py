#!/usr/bin/env python3
"""Render a new BaSIS report from saved evidence without running analyses."""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from basis.report import write_report
import basis.report as report_module


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_evidence(source):
    raw = source.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    evidence = json.loads(raw)
    if not isinstance(evidence, dict) or evidence.get("schema_version") != "basis-report-1":
        raise ValueError("Expected a BaSIS basis-report-1 evidence.json bundle")
    if not isinstance(evidence.get("run"), dict):
        raise ValueError("Evidence bundle must contain a run object")
    for key in ("samples", "records", "deep_records"):
        value = evidence.get(key)
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError(f"Evidence bundle {key} must be a list of objects")
    return evidence, source_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default="study-isolate1/results-combined/evidence.json",
                        help="Saved BaSIS evidence bundle (default: combined native run)")
    parser.add_argument("--outdir", help="New output directory; must not already exist")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show report-generation progress")
    args = parser.parse_args()
    if args.verbose:
        os.environ["BASIS_VERBOSE"] = "1"

    source = Path(args.evidence).expanduser().resolve()
    evidence, source_hash = load_evidence(source)
    generated_at = datetime.now(timezone.utc)
    outdir = (Path(args.outdir).expanduser().resolve() if args.outdir else
              source.parent / "reporting-reviews" / generated_at.strftime("%Y%m%dT%H%M%S%fZ"))
    outdir.mkdir(parents=True, exist_ok=False)
    print("Rendering saved evidence; no native analyses or GPU inference.", flush=True)
    print("Source:", source, flush=True)
    artifacts = write_report(evidence["run"], evidence["samples"], evidence["records"],
                             evidence["deep_records"], outdir)
    if file_sha256(source) != source_hash:
        raise RuntimeError("Source evidence changed during report generation; review was not finalized")

    renderer_dir = Path(report_module.__file__).resolve().parent
    renderer_sources = {}
    for name in ("report.py", "report_dashboard.py", "report_assets.py", "report_recommendations.py",
                 "report_links.py", "report_genome.py", "report_explorer.py", "report_explorer_assets.py",
                 "report_evidence_views.py", "report_tree_views.py", "report_citation.py", "report_motif_views.py"):
        path = renderer_dir / name
        renderer_sources[name] = {"path": str(path), "sha256": file_sha256(path)}
    output_files = {Path(path).resolve() for path in artifacts.values()
                    if isinstance(path, str) and Path(path).is_file()}
    output_files.update(path.resolve() for path in (outdir / "report-data").rglob("*")
                        if path.is_file())
    deferred = outdir / "deferred-candidates.tsv"
    if deferred.is_file():
        output_files.add(deferred.resolve())

    review = {
        "schema_version": "basis-report-refresh-1",
        "report_only": True,
        "native_analyses_run": False,
        "source_evidence": str(source),
        "source_sha256_before": source_hash,
        "source_sha256_after": source_hash,
        "source_unchanged": True,
        "source_schema_version": evidence["schema_version"],
        "original_run_id": evidence["run"].get("run_id"),
        "original_run_finished_at": evidence["run"].get("finished_at"),
        "report_generated_at_utc": generated_at.isoformat(),
        "report_finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifacts": artifacts,
        "artifact_sha256": {name: file_sha256(Path(path)) for name, path in artifacts.items()
                            if isinstance(path, str) and Path(path).is_file()},
        "renderer_source_fingerprints": renderer_sources,
        "output_file_sha256": {str(path.relative_to(outdir)): file_sha256(path)
                               for path in sorted(output_files)},
        "note": ("Original run, sample, module and DeepSafe records are preserved. "
                 "Presentation and derived reporting tables use the installed report code. "
                 "Genome annotations and optional tree/alignment views are derived from recorded "
                 "coordinates or checksum-verified existing inputs and native artifacts. "
                 "Unavailable native files are reported as unavailable; retain the original run directory."),
    }
    manifest = outdir / "review.json"
    manifest.write_text(json.dumps(review, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Report:", artifacts["html"])
    print("Evidence:", artifacts["evidence_json"])
    print("Review manifest:", manifest)
    print("Original evidence preserved. No analyses rerun.")


if __name__ == "__main__":
    main()
