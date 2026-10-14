"""Explicitly synthetic, non-biological fixtures for exercising the application."""
from __future__ import annotations

import csv
from pathlib import Path

import yaml

from .config import ALL_MODULES, default_config
from .inputs import validate_sample
from .util import atomic_json


def create_demo(directory: str | Path) -> Path:
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    inputs = directory / "inputs"
    inputs.mkdir(exist_ok=True)
    config = default_config()
    config.update(project="BaSIS SYNTHETIC SOFTWARE DEMO — not biological evidence",
                  samples="samples.tsv", outdir="results", validation_mode="synthetic_fixture")
    for module in ALL_MODULES:
        config["modules"][module]["enabled"] = module in {"amrfinder", "rgi", "vfdb", "pathogenfinder2"}
        config["modules"][module].update(mode="import", result=f"inputs/{{sample_id}}.{module}.json")
    config["deep"].update(phylogeny=False, reference_search_tool="metadata", profiles_dir=None,
                           reference_fasta="inputs/references.faa", reference_metadata="inputs/references.tsv")
    refs = {"DEMO_REF_A": "M" + "A" * 29, "DEMO_REF_B": "M" + "G" * 29}
    (inputs / "references.faa").write_text("".join(f">{k}\n{v}\n" for k, v in refs.items()))
    # Schema intentionally mirrors the reference builder but makes no biological reference claims.
    from .references import CATALOG_FIELDS
    with (inputs / "references.tsv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CATALOG_FIELDS, delimiter="\t")
        writer.writeheader()
        from .references import sequence_hash
        for ref, seq in refs.items():
            row = {k: "" for k in CATALOG_FIELDS}
            row.update(reference_id=ref, accession=ref, family="demo_family", comparison_group="unknown", role="reference",
                       review_status="unreviewed", source="synthetic_fixture", evidence="Synthetic software fixture only",
                       sequence_sha256=sequence_hash(seq))
            writer.writerow({k: row.get(k, "") for k in CATALOG_FIELDS})
    rows = []
    for sid in ("DEMO_A", "DEMO_B"):
        paths = {key: inputs / f"{sid}.{suffix}" for key, suffix in (("assembly", "fna"), ("proteins", "faa"), ("gff", "gff"))}
        # Two artificial CDSs, not sequences representing actual hazard genes.
        paths["assembly"].write_text(f">{sid}_chr\n" + "ATG" + "GCT" * 29 + "TAA" + "N" * 27 + "ATG" + "GGT" * 29 + "TAA" + "\n")
        paths["proteins"].write_text(f">{sid}_0001\nM{'A'*29}\n>{sid}_0002\nM{'G'*29}\n")
        paths["gff"].write_text(f"##gff-version 3\n{sid}_chr\tdemo\tCDS\t1\t93\t.\t+\t0\tID={sid}_0001\n{sid}_chr\tdemo\tCDS\t121\t213\t.\t+\t0\tID={sid}_0002\n")
        sample = {"sample_id": sid, **{k: str(p) for k, p in paths.items()}}
        validation = validate_sample(sample)
        rows.append({"sample_id": sid, **{k: str(p.relative_to(directory)) for k, p in paths.items()}, "taxon": "Synthetic fixture"})
        for module in ("amrfinder", "rgi", "vfdb", "pathogenfinder2"):
            findings = []
            if sid == "DEMO_A" and module in ("amrfinder", "rgi"):
                findings = [{"tool": module, "category": "amr", "gene": "DEMO_CANDIDATE", "family": "demo_family",
                             "protein_id": f"{sid}_0001", "reference": "DEMO_REF_A", "identity": 83.0,
                             "coverage": 90.0, "evidence_level": "exploratory", "interpretation": "synthetic_ambiguous_match",
                             "attributes": {"synthetic": True}}]
            if module == "pathogenfinder2":
                findings = [{"tool": module, "category": "ml_attention", "gene": None, "family": "demo_family",
                             "protein_id": f"{sid}_0002", "reference": None, "identity": None, "coverage": None,
                             "evidence_level": "exploratory", "interpretation": "synthetic_model_attention",
                             "attributes": {"rank": 1, "attention_max": 0.75, "synthetic": True}}]
            metrics = {"prediction_mean": 0.4 if sid == "DEMO_A" else 0.2, "prediction_std": 0.03,
                       "synthetic": True} if module == "pathogenfinder2" else {"synthetic": True}
            record = {"schema_version": 1, "sample_id": sid, "module": module,
                      "execution_status": "completed", "interpretation": "inconclusive" if module == "pathogenfinder2" else "detected" if findings else "not_detected",
                      "input_hashes": validation["input_hashes"], "findings": findings, "metrics": metrics,
                      "artifacts": [], "notes": ["SYNTHETIC FIXTURE: no biological engine was executed."]}
            atomic_json(inputs / f"{sid}.{module}.json", record)
    with (directory / "samples.tsv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["sample_id", "assembly", "proteins", "gff", "taxon"], delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    path = directory / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path
