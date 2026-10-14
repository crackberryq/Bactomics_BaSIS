#!/usr/bin/env python3
"""Recover the saved benchmark HTML report without repeating native analyses."""
import argparse
import os
from pathlib import Path
import shutil

from basis.placement_benchmark_recovery import recover_benchmark_report


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir
    if run_dir is None:
        folders = [p for p in (root / "study-isolate1/placement-benchmarks").glob("*")
                   if p.is_dir() and (p / "checkpoint.json").is_file()]
        if not folders:
            parser.error("No saved benchmark found; supply --run-dir")
        run_dir = max(folders, key=lambda p: (p / "checkpoint.json").stat().st_mtime_ns)
    os.environ["BASIS_VERBOSE"] = "1"
    print("Recovering report from:", run_dir.resolve(), flush=True)
    print("Using saved results only; no BLAST, alignment, tree or EPA-ng reruns.", flush=True)
    bundle, artifacts = recover_benchmark_report(run_dir)
    for method, metrics in bundle["evaluation"]["methods"].items():
        print(f"{method}: planned={metrics['planned_count']} | assigned={metrics['accepted_count']} | "
              f"correct={metrics['correct_count']} | abstained={metrics['completed_abstained_count']} | "
              f"held={metrics['held_count']} | failed={metrics['failed_count']}")
    print("Report:", artifacts["html"])
    print("Evidence:", artifacts["benchmark_json"])
    if artifacts.get("placement_diagnostics_json"):
        print("Placement diagnostics:", artifacts["placement_diagnostics_json"])
    folder = Path(artifacts["html"]).parent
    archive = shutil.make_archive(str(folder) + "-review", "zip", folder)
    print("Upload ZIP:", archive)
    return 2 if bundle["status"] == "incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())
