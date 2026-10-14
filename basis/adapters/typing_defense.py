"""Pinned MLST typing and CRISPR-Cas adapter dispatch.

MLST schema/CLI audited against upstream v2.35.0 bin/mlst. Both machine JSON
and --full TSV must account for the input and agree; unknown schemes are an
unresolved typing result, never proof of an absent trait or tool failure.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import re

from ..inputs import read_fasta

MLST_VERSION = "2.35.0"
_ALLELE = re.compile(r"(?:-|~?\d+(?:\.\d+)?\??(?:,~?\d+(?:\.\d+)?\??)*)")


def parse_mlst(json_path, tsv_path, assembly, sample_id, *, requested_scheme=None):
    records = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise ValueError("MLST JSON must account for exactly one submitted assembly")
    row = records[0]
    if row.get("filename") != str(Path(assembly).resolve()) or row.get("id") != sample_id:
        raise ValueError("MLST output input identity mismatch")
    scheme, st, alleles = row.get("scheme"), row.get("sequence_type"), row.get("alleles")
    if not isinstance(scheme, str) or not re.fullmatch(r"[A-Za-z0-9_]+|-", scheme):
        raise ValueError("Invalid MLST scheme")
    if not isinstance(st, (str, int)) or isinstance(st, bool) or not re.fullmatch(r"\d+|-", str(st)):
        raise ValueError("Invalid MLST sequence type")
    st = str(st)
    if requested_scheme and scheme != requested_scheme:
        raise ValueError("MLST returned a different scheme than explicitly requested")
    if scheme == "-":
        if st != "-" or alleles is not None:
            raise ValueError("Unresolved MLST scheme has contradictory genotype fields")
        allele_map = {}
    else:
        if not isinstance(alleles, dict) or not alleles:
            raise ValueError("MLST scheme result lacks allele observations")
        allele_map = {}
        for locus, value in alleles.items():
            if not isinstance(locus, str) or not locus or any(c in locus for c in "\t\n\r;()"):
                raise ValueError("Invalid MLST locus identifier")
            if not isinstance(value, (str, int)) or isinstance(value, bool) or not _ALLELE.fullmatch(str(value)):
                raise ValueError("Invalid MLST allele notation")
            allele_map[locus] = str(value)
    with Path(tsv_path).open(encoding="utf-8", newline="") as handle:
        table = csv.DictReader(handle, delimiter="\t")
        if table.fieldnames != ["FILE", "SCHEME", "ST", "STATUS", "SCORE", "ALLELES"]:
            raise ValueError("Unsupported MLST full-output header")
        rows = list(table)
    if len(rows) != 1 or None in rows[0] or any(value is None for value in rows[0].values()):
        raise ValueError("Incomplete or duplicate MLST TSV result")
    full = rows[0]
    if (full["FILE"], full["SCHEME"], full["ST"]) != (sample_id, scheme, st):
        raise ValueError("MLST JSON/TSV identity or genotype mismatch")
    observed = {}
    for token in full["ALLELES"].split(";") if full["ALLELES"] else []:
        match = re.fullmatch(r"([^();]+)\(([^()]*)\)", token)
        if not match or match[1] in observed:
            raise ValueError("Invalid or duplicate MLST TSV allele")
        observed[match[1]] = match[2]
    if observed != allele_map:
        raise ValueError("MLST JSON/TSV allele observations disagree")
    try:
        score = float(full["SCORE"])
    except ValueError as exc:
        raise ValueError("Invalid MLST score") from exc
    if not math.isfinite(score) or not 0 <= score <= 100:
        raise ValueError("MLST score outside 0–100")
    status = full["STATUS"]
    if status not in {"PERFECT", "NONE", "NOVEL", "MIXED", "MISSING", "BAD", "OK"}:
        raise ValueError("Unsupported MLST native status")
    expected = ("PERFECT" if st.isdigit() else "NONE" if score <= 0 else
                "NOVEL" if all(v.isdigit() for v in allele_map.values()) else
                "MIXED" if any("," in v for v in allele_map.values()) else
                "MISSING" if any("-" in v for v in allele_map.values()) else "BAD" if score < 70 else "OK")
    if status != expected or (scheme == "-" and (status != "NONE" or score != 0)):
        raise ValueError("MLST native status contradicts genotype")
    assigned = st.isdigit()
    typing_status = "sequence_type_assigned" if assigned else "no_matching_scheme" if scheme == "-" else "sequence_type_unresolved"
    findings = []
    if scheme != "-":
        findings.append({"tool": "mlst", "category": "typing", "gene": f"ST{st}" if assigned else scheme,
                         "family": scheme, "protein_id": None, "contig": None, "start": None, "end": None,
                         "strand": None, "reference": None, "identity": None, "coverage": None,
                         "evidence_level": "typing", "interpretation": "typing_context",
                         "attributes": {"native": row, "native_full": full, "scheme": scheme,
                                        "sequence_type": st if assigned else None, "alleles": allele_map,
                                        "typing_status": typing_status, "score": score,
                                        "score_scope": "MLST native genotype score; not pathogenicity probability"}})
    return {"findings": findings, "artifacts": [str(json_path), str(tsv_path)],
            "interpretation": "detected" if assigned else "inconclusive",
            "metrics": {"mlst_version": MLST_VERSION, "typing_status": typing_status,
                        "scheme": scheme if scheme != "-" else None, "sequence_type": st if assigned else None,
                        "native_status": status, "native_score": score, "allele_count": len(allele_map),
                        "assessed_input_count": 1, "completion_check": "successful_process_and_matching_json_full_tsv"},
            "notes": ["MLST is strain-typing context, not evidence of virulence or organism safety.",
                      "No matching scheme or unresolved sequence type is a completed, inconclusive typing assessment.",
                      "The bundled PubMLST snapshot is versioned with this installation; no live database update is implied."]}


def run(module, sample, config, outdir, runner):
    if module == "crisprcasfinder":
        from .crisprcasfinder import run as run_crispr
        return run_crispr(module, sample, config, outdir, runner)
    if module != "mlst":
        raise ValueError("Unsupported typing/defense module: " + str(module))
    outdir = Path(outdir).resolve()
    assembly = Path(sample["assembly"]).resolve()
    read_fasta(assembly)
    database = Path(config.get("database") or "")
    if not config.get("database") or not (database / "pubmlst").is_dir() or not (database / "blast/mlst.fa").is_file():
        raise ValueError("MLST requires a prepared database with pubmlst/ and blast/mlst.fa")
    executable = config.get("executable") or "mlst"
    version = runner.run([executable, "--version"], cwd=outdir)
    if not re.search(r"(?m)^mlst 2\.35\.0\s*$", version.stdout):
        raise ValueError("This MLST adapter requires verified native version 2.35.0")
    threads = config.get("threads", 1)
    if type(threads) is not int or threads < 1:
        raise ValueError("MLST threads must be a positive integer")
    scheme = config.get("scheme") or sample.get("mlst_scheme")
    if scheme and (not isinstance(scheme, str) or not re.fullmatch(r"[A-Za-z0-9_]+", scheme)):
        raise ValueError("Invalid explicitly requested MLST scheme")
    native = outdir / "native"
    native.mkdir(exist_ok=False)
    output, table = native / "mlst.json", native / "mlst.tsv"
    argv = [executable, "--threads", str(threads), "--datadir", str((database / "pubmlst").resolve()),
            "--blastdb", str((database / "blast/mlst.fa").resolve()), "--label", sample["sample_id"],
            "--json", str(output), "--full", "--outfile", str(table)]
    if scheme:
        argv += ["--scheme", scheme]
    runner.run([*argv, str(assembly)], cwd=outdir)
    result = parse_mlst(output, table, assembly, sample["sample_id"], requested_scheme=scheme)
    result["artifacts"].append(str(native))
    return result
