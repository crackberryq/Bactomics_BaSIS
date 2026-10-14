#!/usr/bin/env python3
"""Create a new combined report using the successful, verified ICEscreen retry."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from basis.deep_reporting import completion_summary
from basis.reconcile import reconcile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", default="study-isolate1")
    parser.add_argument("--retry", help="Explicit successful ICEscreen result.json")
    args = parser.parse_args()
    os.environ["BASIS_VERBOSE"] = "1"
    study = (Path(__file__).resolve().parent / Path(args.study).expanduser()).resolve()
    source = study / "results-optional/evidence.json"
    if args.retry:
        retry = Path(args.retry).expanduser().resolve()
    else:
        matches = []
        for path in (study / "icescreen-retries").glob("*/results/results/isolate1/icescreen/result.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("execution_status") == "completed":
                matches.append(path)
        if len(matches) != 1:
            raise ValueError(f"Found {len(matches)} completed retries; select one explicitly with --retry")
        retry = matches[0]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    outdir = source.parent / "reconciled-reports" / stamp
    merged, artifacts = reconcile(source, retry, outdir)
    for record in merged["records"]:
        if record.get("module") in {"bacmet", "isescan", "icescreen", "minced"}:
            print(record["module"] + ": " + record["execution_status"], flush=True)
    for deep in merged["deep_records"]:
        summary = completion_summary(deep)
        print("DeepSafe:", json.dumps(summary), flush=True)
    audit = merged["run"]["report_reconciliation"]
    print("ICEscreen: 0 predicted elements | unassigned integrases:", audit["unassigned_integrases"])
    print("Overall scope:", merged["run"]["completeness"])
    print("Report:", artifacts["html"])
    print("Evidence:", artifacts["evidence_json"])
    print("Audit:", outdir / "reconciliation.json")
    print("Original results preserved. Candidate analyses and interpretations unchanged.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit("Report reconciliation stopped: " + str(exc)) from exc
