#!/usr/bin/env python3
"""Inspect three existing HybAs inputs and save a compact local audit ZIP."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED


def main():
    from basis.config import load_config
    from basis.hybas import enrich_sample
    from basis.inputs import read_fasta, read_cds, validate_sample

    project = Path.cwd().resolve()
    if not (project / "basis").is_dir():
        raise SystemExit("Run this from ~/bactomics/BaSIS_v0.1.0")
    root = project.parent
    audit = {"schema": "basis-pilot456-input-review-v1", "project": str(project),
             "created_at_utc": datetime.now(timezone.utc).isoformat(),
             "samples": [], "configurations": [], "native_analyses_run": False}
    for number in (4, 5, 6):
        sid = f"isolate{number}"
        base = root / sid
        sample = {"sample_id": sid, "hybas_dir": str(base)}
        sample.update({key: str(base / "annotation" / (sid + suffix))
                       for key, suffix in (("assembly", ".fna"), ("proteins", ".faa"),
                                           ("gff", ".gff"), ("genbank", ".gbk"))})
        row = {"sample_id": sid, "paths": dict(sample), "errors": []}
        print(f"[{sid}] Reading inputs and checking consistency...", flush=True)
        try:
            dna = read_fasta(sample["assembly"])
            aa = read_fasta(sample["proteins"])
            cds = read_cds(sample["gff"])
            row["counts"] = {"contigs": len(dna), "assembly_bp": sum(map(len, dna.values())),
                "protein_count": len(aa), "total_amino_acids": sum(len(s.rstrip('*')) for s in aa.values()),
                "gff_cds_count": len(cds), "n_bases": sum(s.count("N") for s in dna.values())}
            row["contig_lengths"] = {k: len(v) for k, v in dna.items()}
        except Exception as exc:
            row["errors"].append("Input read: " + str(exc))
        try:
            enriched = enrich_sample(sample)
            row["taxonomy"] = {k: enriched.get(k) for k in
                               ("taxon", "taxon_rank", "taxon_source", "taxon_status")}
            row["hybas_context"] = enriched.get("hybas_context", {})
        except Exception as exc:
            row["errors"].append("HybAs context: " + str(exc))
        try:
            checked = validate_sample(sample)
            row["validation"] = {k: checked.get(k) for k in
                ("sample_id", "contigs", "assembly_bp", "protein_count", "input_hashes", "limitations")}
            warnings = checked.get("warnings", [])
            row["validation"]["warning_count"] = len(warnings)
            row["validation"]["warning_examples"] = warnings[:5]
            row["file_consistency"] = "passed"
        except Exception as exc:
            row["file_consistency"] = "failed"
            row["errors"].append("Consistency: " + str(exc))
        summary = base / "metadata/run_summary.json"
        if summary.is_file():
            try:
                data = json.loads(summary.read_text())
                row["hybas_summary_metrics"] = {k: v for k, v in data.items()
                    if re.search(r"assembly|contig|length|busco|complet|tax|target|species|genus|circular|topology|mode", k, re.I)}
            except Exception as exc:
                row["errors"].append("Summary: " + str(exc))
        print(" ", json.dumps(row.get("counts", {})), flush=True)
        print("  Consistency:", row.get("file_consistency"), "| taxon:",
              row.get("taxonomy", {}).get("taxon") or "not resolved", flush=True)
        for error in row["errors"]:
            print("  CHECK:", error, flush=True)
        audit["samples"].append(row)

    for name in ("config.family-placement-20261010T221923999021Z.yaml",
                 "config.expanded-native-20261010T082305725671Z.yaml"):
        path = project / "study-isolate1" / name
        row = {"path": str(path)}
        try:
            row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            row["effective_config"] = load_config(path)
            print("Config captured:", name, flush=True)
        except Exception as exc:
            row["error"] = str(exc)
            print("Config CHECK:", name, "|", exc, flush=True)
        audit["configurations"].append(row)

    audit["interpretation"] = "File consistency does not establish assembly completeness, functional accuracy or organism safety. No sample is excluded based on file size alone."
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = Path.home() / f"BaSIS_pilot456_check-{stamp}.zip"
    with ZipFile(dest, "x", ZIP_DEFLATED) as archive:
        archive.writestr("pilot456-input-review.json", json.dumps(audit, indent=2) + "\n")
    print("\nUpload:", dest, flush=True)
    try:
        windows = subprocess.check_output(["wslpath", "-w", str(dest)], text=True).strip()
        print("Windows path:", windows, flush=True)
    except (OSError, subprocess.CalledProcessError):
        pass
    print("No native analyses run. Source inputs and configurations were not changed.", flush=True)


if __name__ == "__main__":
    main()
