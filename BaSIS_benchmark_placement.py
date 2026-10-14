#!/usr/bin/env python3
"""Run the separate internal Cfr/RlmN seed-panel benchmark (CPU only)."""
from datetime import datetime, timezone
import argparse
import os
from pathlib import Path
import shutil

from basis.placement_benchmark_native import run_seed_benchmark


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-prefix", type=Path, default=root / ".basis-envs/basis-deepsafe")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--outdir", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--panel", default="cfr_rlmn_source_audited_v1")
    parser.add_argument("--prepare-only", action="store_true", help="Run sequence clustering and freeze folds, without tree or placement predictions")
    args = parser.parse_args()
    if not 1 <= args.threads <= 64:
        parser.error("--threads must be between 1 and 64")
    if not (args.env_prefix / "conda-meta").is_dir():
        parser.error("DeepSafe conda environment not found: " + str(args.env_prefix))
    if not shutil.which("conda"):
        parser.error("conda is unavailable; activate the basis environment first")
    os.environ["BASIS_VERBOSE"] = "1"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    outdir = args.outdir or root / "study-isolate1/placement-benchmarks" / stamp
    print("Internal seed-panel benchmark; labels are not independent test truth.", flush=True)
    print("CPU: reference clustering, BLAST baseline, training-only trees and EPA-ng.", flush=True)
    print("Existing isolate results are preserved. No model or reference downloads.", flush=True)
    bundle, artifacts = run_seed_benchmark(outdir, {
        "command_prefix": ["conda", "run", "--no-capture-output", "-p", str(args.env_prefix.resolve())],
        "threads": args.threads, "timeout_seconds": 3600,
    }, registry_path=args.registry, panel_id=args.panel, prepare_only=args.prepare_only)
    print("Benchmark:", bundle["status"])
    for method, metrics in bundle.get("evaluation", {}).get("methods", {}).items():
        print(f"{method}: planned={metrics['planned_count']} | assigned={metrics['accepted_count']} | "
              f"correct={metrics['correct_count']} | abstained after analysis={metrics['completed_abstained_count']} | "
              f"held={metrics['held_count']} | failed={metrics['failed_count']}")
    print("Report:", artifacts["html"])
    print("Evidence:", artifacts["benchmark_json"])
    archive = shutil.make_archive(str(Path(outdir).resolve()) + "-review", "zip", outdir)
    print("Upload ZIP:", archive)
    return 2 if bundle["status"] == "incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())
