#!/usr/bin/env python3
"""Compare AWARE admission on saved findings, without running or changing a study."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from basis.aware import audit
from basis.config import default_config, merge_dict
from basis.deep import select_candidates


def preview(isolate, preset="all", configfile=None):
    root = Path(isolate).resolve() / "safety"
    sample = json.loads((root / "metadata/sample.json").read_text())
    saved = json.loads((root / "metadata/config.json").read_text())
    config = merge_dict(default_config()["deep"], saved["deep"])
    if "scope" not in saved["deep"].get("aware", {}):
        config["aware"].pop("scope", None)
    if configfile:
        import yaml
        supplied = yaml.safe_load(Path(configfile).read_text())
        if not isinstance(supplied, dict) or not isinstance(supplied.get("deep", {}), dict):
            raise ValueError("Preview configfile must contain a deep mapping")
        config = merge_dict(config, supplied.get("deep", {}))
    records, sources = [], []
    for path in sorted((root / "evidence").glob("*.json")):
        if path.name == "deepsafe.json":
            continue
        raw = path.read_bytes()
        row = json.loads(raw)
        if row.get("sample_id") != sample["sample_id"] or not row.get("module"):
            raise ValueError("Invalid or mismatched saved evidence: " + str(path))
        if row.get("execution_status") in {"failed", "blocked", "not_started"}:
            raise ValueError("Saved evidence has not completed: " + str(path))
        records.append(row)
        sources.append({"path": str(path), "module": row["module"],
                        "status": row.get("execution_status"), "sha256": hashlib.sha256(raw).hexdigest()})
    if not records:
        raise ValueError("No saved upstream evidence found")
    present = {row["module"] for row in records}
    if len(present) != len(records):
        raise ValueError("Duplicate upstream evidence module")
    missing = [name for name, section in saved.get("modules", {}).items()
               if section.get("enabled") and name not in present]
    if missing:
        raise ValueError("Missing enabled upstream evidence modules: " + ", ".join(sorted(missing)))
    presets = ["auto-strict", "auto-medium", "auto-loose"] if preset == "all" else [preset]
    result = {"sample_id": sample["sample_id"], "sources": sources, "presets": {},
              "scope": "Read-only admission preview of saved findings; manual overrides and budgets remain active. "
                       "No query extraction, native analysis, config edits, or checkpoint changes."}
    for name in presets:
        effective = copy.deepcopy(config)
        effective.setdefault("aware", {}).update(enabled=True, preset=name)
        candidates = select_candidates(records, effective, sample)
        result["presets"][name] = audit(candidates, records, effective)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolate", required=True, type=Path)
    parser.add_argument("--preset", choices=["all", "auto-strict", "auto-medium", "auto-loose"], default="all")
    parser.add_argument("--configfile", type=Path, help="Optional study config or AWARE overlay to preview")
    parser.add_argument("--json", action="store_true", help="Print full policy and counts to standard output")
    args = parser.parse_args(argv)
    result = preview(args.isolate, args.preset, args.configfile)
    if args.json:
        print(json.dumps(result, indent=2, allow_nan=False))
    else:
        print("BaSIS AWARE preview: " + result["sample_id"])
        print(result["scope"])
        print(f"{'Preset':<14} {'Findings':>9} {'Unique':>8} {'Eligible':>9} {'Selected':>9} {'Admission deferred':>19} {'Budget deferred':>16}")
        for name, row in result["presets"].items():
            c = row["counts"]
            print(f"{name:<14} {c['findings_received']:>9} {c['unique_candidates']:>8} "
                  f"{c['eligible_candidates']:>9} {c['selected_candidates']:>9} "
                  f"{c['admission_deferred']:>19} {c['budget_deferred']:>16}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
