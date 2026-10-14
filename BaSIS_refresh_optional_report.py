#!/usr/bin/env python3
"""Refresh the reconciled optional-module report without rerunning analyses."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from basis.deep_reporting import completion_summary
from basis.report_context import finding_context
from basis.report_refresh import refresh_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", default="study-isolate1")
    parser.add_argument("--evidence", help="Original update-27 reconciled evidence.json")
    parser.add_argument("--outdir", help="New, unused report directory")
    args = parser.parse_args()
    os.environ["BASIS_VERBOSE"] = "1"
    study = (Path(__file__).resolve().parent / Path(args.study).expanduser()).resolve()
    if args.evidence:
        source = Path(args.evidence).expanduser().resolve()
    else:
        matches = list((study / "results-optional/reconciled-reports").glob("*/evidence.json"))
        if len(matches) != 1:
            raise ValueError(f"Found {len(matches)} reconciled bundles; select one explicitly with --evidence")
        source = matches[0]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    outdir = (Path(args.outdir).expanduser().resolve() if args.outdir else
              source.parent / "reporting-reviews" / stamp)
    merged, artifacts = refresh_report(source, outdir)
    audit = merged["run"]["report_refresh"]
    print("Canonical candidate labels corrected:", audit["canonical_label_corrections"])
    for change in audit["deep_changes"]:
        for candidate in change["candidate_label_changes"]:
            fields = candidate["changed_fields"]
            if "gene" in fields:
                print(candidate["protein_id"], "|", fields["gene"]["before"], "→", fields["gene"]["after"])
    for record in merged["records"]:
        if record.get("module") == "bacmet":
            print("BacMet qualifying records:", sum(
                f.get("attributes", {}).get("passes_threshold") is True
                for f in record.get("findings", [])))
        elif record.get("module") == "isescan":
            print("ISEScan completeness:", dict(Counter(
                finding_context({**f, "module": "isescan"})["is_prediction_type"] or "not_recorded"
                for f in record.get("findings", []))))
    for deep in merged["deep_records"]:
        print("DeepSafe:", json.dumps(completion_summary(deep)))
    print("Overall scope:", merged["run"].get("completeness"))
    print("Report:", artifacts["html"])
    print("Evidence:", artifacts["evidence_json"])
    print("Audit:", outdir / "review.json")
    print("Original results preserved. Analyses, selection and interpretations unchanged.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit("Report refresh stopped: " + str(exc)) from exc
