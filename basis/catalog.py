"""Prepare an unreviewed catalogue from an existing protein FASTA release."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from .references import CATALOG_FIELDS, PROTEIN_ALPHABET, read_fasta, sequence_hash, write_fasta
from .util import now, sha256_file


def from_fasta(source: Path, fasta: Path, metadata: Path, source_name: str | None = None) -> dict:
    sequences = read_fasta(source)
    if any(not PROTEIN_ALPHABET.fullmatch(s) for s in sequences.values()):
        raise ValueError("Source FASTA contains invalid protein characters")
    headers = {}
    with source.open(encoding="utf-8") as handle:
        for line in handle:
            if line.startswith(">"):
                headers[line[1:].split()[0]] = line[1:].strip()
    rows = []
    source_hash = sha256_file(source)
    for ref, sequence in sequences.items():
        fields = ref.split("|")
        accession = fields[1] if len(fields) >= 3 and fields[0] in ("sp", "tr", "ref") else ref
        gene = re.search(r"(?:^|\s)GN=([^\s]+)", headers[ref])
        rows.append({"reference_id": ref, "accession": accession, "family": gene.group(1) if gene else "",
                     "comparison_group": "unknown", "role": "reference", "review_status": "unreviewed",
                     "source": f"{source_name or source.name};source_sha256={source_hash}",
                     "sequence_sha256": sequence_hash(sequence), "retrieved_at": now(),
                     "source_annotation": headers[ref]})
    write_fasta(sequences, fasta)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    with metadata.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CATALOG_FIELDS) + ["source_annotation"], delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return {"reference_count": len(rows), "fasta": str(fasta.resolve()), "metadata": str(metadata.resolve()),
            "source_sha256": source_hash,
            "interpretation": "Reference retrieval catalogue prepared. Functional labels remain unreviewed; database headers are not experimental validation."}
