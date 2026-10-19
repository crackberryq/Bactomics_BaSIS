from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from urllib.parse import unquote

from .util import resolve_path, sha256_file

SAMPLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
INPUT_MODES = {"auto", "hybas", "external"}


def normalize_input_mode(value: str | None) -> str:
    if value is not None and not isinstance(value, str):
        raise ValueError("input_mode must be text: auto, hybas or external")
    mode = (value or "auto").strip().lower()
    if mode not in INPUT_MODES:
        raise ValueError(f"Invalid input_mode {value!r}; choose auto, hybas or external")
    return mode


def read_fasta(path: str | Path) -> dict[str, str]:
    records: dict[str, str] = {}
    current = None
    with open(path, encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if len(line) == 1:
                    raise ValueError(f"{path}:{lineno}: empty FASTA identifier")
                current = line[1:].split()[0]
                if current in records:
                    raise ValueError(f"{path}:{lineno}: duplicate FASTA identifier {current}")
                records[current] = ""
            else:
                if current is None:
                    raise ValueError(f"{path}:{lineno}: sequence before FASTA header")
                records[current] += "".join(line.split()).upper()
    if not records or any(not seq for seq in records.values()):
        raise ValueError(f"{path}: FASTA is empty or contains an empty sequence")
    return records


def read_cds(path: str | Path) -> list[dict]:
    cds = []
    with open(path, encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            if line.startswith("##FASTA"):
                break
            if not line.strip() or line.startswith("#"):
                continue
            cells = line.rstrip("\r\n").split("\t")
            if len(cells) != 9:
                raise ValueError(f"{path}:{lineno}: expected nine GFF columns")
            if cells[2] != "CDS":
                continue
            attrs = {}
            for item in cells[8].split(";"):
                if "=" in item:
                    key, value = item.split("=", 1)
                    attrs[key] = unquote(value)
            try:
                start, end = int(cells[3]), int(cells[4])
            except ValueError as exc:
                raise ValueError(f"{path}:{lineno}: invalid CDS coordinates") from exc
            if start < 1 or end < start or cells[6] not in ("+", "-"):
                raise ValueError(f"{path}:{lineno}: invalid CDS interval or strand")
            aliases = [attrs[k] for k in ("ID", "protein_id", "locus_tag") if attrs.get(k)]
            cds.append({"contig": cells[0], "start": start, "end": end,
                        "strand": cells[6], "phase": cells[7], "aliases": aliases,
                        "attributes": attrs})
    if not cds:
        raise ValueError(f"{path}: no CDS records found")
    return cds


def _sample_sheet_rows(handle, path):
    reader = csv.reader(handle, delimiter="\t", strict=True)
    try:
        yield from reader
    except csv.Error as exc:
        raise ValueError(f"Sample sheet {path}:{reader.line_num}: malformed quoted TSV: {exc}") from exc


def _read_sample_manifest(path: str | Path) -> tuple[Path, list[dict]]:
    """Read all structural identities before resolving any row-specific files."""
    path = Path(path).resolve()
    if path.suffix.lower() in {".xlsx", ".xls", ".csv"}:
        raise ValueError("Execution requires a TSV sample manifest; use basis import-samples to import Excel/CSV")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = _sample_sheet_rows(handle, path)
        headers = next(reader, [])
        if (not headers or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key) for key in headers)
                or len({key.casefold() for key in headers}) != len(headers)):
            raise ValueError("Sample sheet has empty, malformed or duplicate column headers")
        required = {"sample_id", "assembly", "proteins", "gff"}
        if not required.issubset(headers):
            if {"sample_id", "assembly"}.issubset(headers):
                missing = ", ".join(sorted(required - set(headers)))
                raise ValueError(f"annotation_required: sample sheet is missing required columns: {missing}; "
                                 "provide matching protein FASTA (proteins) and GFF annotations; BaSIS does not run annotation")
            raise ValueError(f"Sample sheet requires columns: {', '.join(sorted(required))}")
        rows, seen = [], set()
        for line, cells in enumerate(reader, 2):
            if not any(cell.strip() for cell in cells):
                continue
            if len(cells) != len(headers):
                raise ValueError(f"Sample sheet row {line}: expected {len(headers)} columns, found {len(cells)}")
            row = dict(zip(headers, cells))
            sid = (row.get("sample_id") or "").strip()
            if not SAMPLE_ID.fullmatch(sid) or sid.casefold() in seen or sid in (".", ".."):
                raise ValueError(f"Invalid or duplicate sample ID: {sid!r}")
            seen.add(sid.casefold())
            declaration = {k: (v or "").strip() for k, v in row.items() if k}
            declaration["sample_id"] = sid
            rows.append({"sample_id": sid, "source_row": line, "declaration": declaration})
    if not rows:
        raise ValueError("Sample sheet contains no samples")
    return path, rows


def _resolve_sample_declaration(declaration: dict, parent: Path) -> dict:
    """Resolve one annotation declaration identically for execution and preflight."""
    sample = dict(declaration)
    sid = sample["sample_id"]
    sample["input_mode"] = normalize_input_mode(sample.get("input_mode"))
    for key in ("assembly", "proteins", "gff"):
        if not sample.get(key):
            raise ValueError(f"{sid}: annotation_required: missing {key} path; supply matching assembly, proteins and GFF annotations")
        sample[key] = resolve_path(sample[key], parent)
    if sample.get("hybas_dir"):
        sample["hybas_dir"] = resolve_path(sample["hybas_dir"], parent)
    if sample.get("genbank"):
        sample["genbank"] = resolve_path(sample["genbank"], parent)
        if not Path(sample["genbank"]).is_file():
            raise ValueError(f"{sid}: optional GenBank file does not exist")
    if sample.get("phenotypes"):
        phenotype_path = resolve_path(sample["phenotypes"], parent)
        with open(phenotype_path, encoding="utf-8") as ph:
            sample["phenotypes"] = json.load(ph)
        sample["phenotype_source"] = phenotype_path
        sample["phenotype_sha256"] = sha256_file(phenotype_path)
    else:
        sample["phenotypes"] = []
    from .hybas import enrich_sample
    return enrich_sample(sample)


def load_samples(path: str | Path) -> list[dict]:
    """Strict execution loader: never return a partially resolved manifest."""
    path, rows = _read_sample_manifest(path)
    return [_resolve_sample_declaration(row["declaration"], path.parent) for row in rows]


def load_samples_diagnostic(path: str | Path) -> list[dict]:
    """Read-only preflight loader retaining independent row-resolution failures.

    Malformed structure and duplicate sample identities still raise globally.
    Resolved rows use the strict loader's resolver; failed rows are never
    represented as executable sample dictionaries.
    """
    path, rows = _read_sample_manifest(path)
    result = []
    for row in rows:
        entry = {**row, "sample": None, "errors": []}
        try:
            entry["sample"] = _resolve_sample_declaration(row["declaration"], path.parent)
        except (OSError, ValueError, KeyError) as exc:
            entry["errors"].append(str(exc))
        result.append(entry)
    return result


def validate_sample(sample: dict) -> dict:
    assembly = read_fasta(sample["assembly"])
    proteins = read_fasta(sample["proteins"])
    for cid, seq in assembly.items():
        if not set(seq) <= set("ACGTRYSWKMBDHVN"):
            raise ValueError(f"{sample['sample_id']}: non-nucleotide character in contig {cid}")
    for pid, seq in proteins.items():
        if not set(seq) <= set("ABCDEFGHIKLMNPQRSTVWXYZUOJ*"):
            raise ValueError(f"{sample['sample_id']}: invalid protein sequence {pid}")
    cds = read_cds(sample["gff"])
    mapping = {}
    for feature in cds:
        cid = feature["contig"]
        if cid not in assembly or feature["end"] > len(assembly[cid]):
            raise ValueError(f"{sample['sample_id']}: GFF CDS absent from or outside assembly: {cid}")
        matches = [p for p in feature["aliases"] if p in proteins]
        for pid in set(matches):
            if pid in mapping:
                raise ValueError(f"{sample['sample_id']}: protein {pid} maps to multiple CDS features")
            mapping[pid] = {k: feature[k] for k in ("contig", "start", "end", "strand", "phase")}
    missing = set(proteins) - set(mapping)
    if missing:
        raise ValueError(f"{sample['sample_id']}: {len(missing)} protein IDs absent from GFF CDS records; e.g. {sorted(missing)[:3]}")
    warnings = []
    internal_stops = [pid for pid, seq in proteins.items() if "*" in seq.rstrip("*")]
    if internal_stops:
        warnings.append(f"Internal stop characters in {len(internal_stops)} proteins; candidate integrity requires review")
    from .sequence_validation import validate_cds_translations
    translation_check = validate_cds_translations(assembly, proteins, cds)
    warnings.extend(translation_check["warnings"])
    hashes = {key: sha256_file(sample[key]) for key in ("assembly", "proteins", "gff")}
    contig_aliases = {}
    if sample.get("genbank"):
        hashes["genbank"] = sha256_file(sample["genbank"])
        gb_sequences = []
        collecting = False
        sequence = ""
        gb_aliases = []
        gb_records = []
        with open(sample["genbank"], encoding="utf-8") as gb:
            for line in gb:
                if line.startswith("LOCUS"):
                    gb_aliases = line.split()[1:2]
                elif line.startswith(("ACCESSION", "VERSION")):
                    gb_aliases.extend(line.split()[1:2])
                elif line.startswith("ORIGIN"):
                    collecting, sequence = True, ""
                elif line.startswith("//") and collecting:
                    gb_sequences.append(sequence.upper())
                    gb_records.append((list(gb_aliases), sequence.upper()))
                    collecting = False
                elif collecting:
                    sequence += "".join(re.findall(r"[A-Za-z]+", line))
        if sorted(gb_sequences) != sorted(assembly.values()):
            raise ValueError(f"{sample['sample_id']}: GenBank sequences differ from the supplied assembly")
        for aliases, sequence in gb_records:
            matches = [cid for cid, seq in assembly.items() if seq == sequence]
            direct = [cid for cid in matches if cid in aliases]
            canonical = direct[0] if len(direct) == 1 else matches[0] if len(matches) == 1 else None
            if canonical is None:
                raise ValueError(f"{sample['sample_id']}: ambiguous GenBank-to-assembly identifier mapping")
            for alias in aliases:
                if alias in contig_aliases and contig_aliases[alias] != canonical:
                    raise ValueError(f"{sample['sample_id']}: conflicting GenBank accession {alias}")
                contig_aliases[alias] = canonical
    return {"sample_id": sample["sample_id"], "contigs": len(assembly),
            "assembly_bp": sum(map(len, assembly.values())), "protein_count": len(proteins),
            "protein_coordinates": mapping, "contig_lengths": {k: len(v) for k, v in assembly.items()},
            "contig_aliases": contig_aliases,
            "translation_validation": translation_check,
            "input_hashes": hashes,
            "warnings": warnings,
            "limitations": ["File consistency checks do not establish culture purity or recovery of all original replicons."]}
