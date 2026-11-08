"""Cross-export annotation coverage checks, without inferring missing biology.

Non-pseudogene CDSs (including partial CDSs) must be represented in all supplied
annotation exports. Explicit pseudogene exclusions remain visible warnings.
Joined CDSs are not silently flattened into a different location model.
"""
from __future__ import annotations

from collections import Counter


def is_pseudogene(attributes: dict) -> bool:
    for key, value in attributes.items():
        if str(key).lower() not in {"pseudo", "pseudogene"}:
            continue
        values = value if isinstance(value, list) else [value]
        if not values or any(str(v).strip().lower() not in {"false", "no", "0", "none", "null", "."} for v in values):
            return True
    return False


def _location(feature: dict) -> tuple:
    return tuple(feature[key] for key in ("contig", "start", "end", "strand"))


def validate_genbank_coverage(sample: dict, assembly: dict, proteins: dict, cds: list[dict]) -> dict:
    """Check GenBank sequence, CDS coverage, locations and supplied translations.

    Contig aliases are resolved by identical nucleotide sequence, with record IDs
    used to break ties. CDS aliases may differ across exporters only when the
    single-interval location/strand uniquely matches and supplied peptides are
    identical (an optional terminal stop is ignored). This checks file agreement,
    not the correctness of the annotation or phenotypic properties.
    """
    from Bio import SeqIO

    sid = sample["sample_id"]
    try:
        with open(sample["genbank"], encoding="utf-8") as handle:
            records = list(SeqIO.parse(handle, "genbank"))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{sid}: invalid GenBank annotation: {exc}") from exc
    if not records or Counter(str(record.seq).upper() for record in records) != Counter(assembly.values()):
        raise ValueError(f"{sid}: GenBank sequences differ from the supplied assembly")
    expected = {}
    pseudogene_locations = set()
    for feature in cds:
        key = _location(feature)
        if is_pseudogene(feature["attributes"]):
            pseudogene_locations.add(key)
            continue
        matches = set(feature["aliases"]) & proteins.keys()
        if len(matches) != 1 or key in expected:
            raise ValueError(f"{sid}: ambiguous eligible GFF CDS coverage at {key}")
        expected[key] = next(iter(matches))
    aliases, used_contigs, observed = {}, set(), set()
    excluded = 0
    warnings = []
    for record in records:
        record_aliases = {record.id, record.name, *record.annotations.get("accessions", [])} - {"<unknown id>", "<unknown name>", ""}
        sequence = str(record.seq).upper()
        matches = [cid for cid, seq in assembly.items() if seq == sequence]
        direct = set(matches) & record_aliases
        canonical = next(iter(direct)) if len(direct) == 1 else matches[0] if len(matches) == 1 else None
        if canonical is None or canonical in used_contigs:
            raise ValueError(f"{sid}: ambiguous GenBank-to-assembly identifier mapping")
        used_contigs.add(canonical)
        for alias in record_aliases:
            if alias in aliases and aliases[alias] != canonical:
                raise ValueError(f"{sid}: conflicting GenBank accession {alias}")
            aliases[alias] = canonical
        for feature in record.features:
            if feature.type != "CDS":
                continue
            loc = feature.location
            if loc is None or len(loc.parts) != 1 or loc.ref or loc.strand not in {1, -1}:
                raise ValueError(f"{sid}: GenBank CDS has unsupported joined/remote/unknown location; supply matching supported single-CDS annotations rather than flattening it")
            try:
                start, end = int(loc.start) + 1, int(loc.end)
            except TypeError as exc:
                raise ValueError(f"{sid}: GenBank CDS has unknown coordinates") from exc
            if start < 1 or end < start or end > len(sequence):
                raise ValueError(f"{sid}: GenBank CDS lies outside its assembly contig")
            key = (canonical, start, end, "+" if loc.strand == 1 else "-")
            if is_pseudogene(feature.qualifiers):
                if key in expected or key not in pseudogene_locations:
                    raise ValueError(f"{sid}: GenBank/GFF pseudogene annotation disagrees at {key}")
                excluded += 1
                continue
            if key not in expected:
                raise ValueError(f"{sid}: GenBank CDS is absent from or differs from eligible GFF CDS annotation: {key}")
            if key in observed:
                raise ValueError(f"{sid}: duplicate GenBank CDS annotation: {key}")
            pid = expected[key]
            native_ids = {value for name in ("protein_id", "locus_tag") for value in feature.qualifiers.get(name, [])}
            conflicting = (native_ids & proteins.keys()) - {pid}
            if conflicting:
                raise ValueError(f"{sid}: GenBank protein identifier conflicts with GFF CDS location: {sorted(conflicting)}")
            translations = feature.qualifiers.get("translation", [])
            if len(translations) != 1:
                raise ValueError(f"{sid}: GenBank CDS {pid} requires one supplied translation to verify protein annotation coverage")
            peptide = "".join(translations[0].split()).upper().rstrip("*")
            if peptide != proteins[pid].rstrip("*"):
                raise ValueError(f"{sid}: GenBank translation differs from protein FASTA for {pid}")
            observed.add(key)
    missing = set(expected) - observed
    if missing:
        examples = ", ".join(expected[key] for key in sorted(missing)[:3])
        raise ValueError(f"{sid}: incomplete GenBank annotation: {len(missing)} eligible GFF CDS feature(s) missing; examples: {examples}")
    if pseudogene_locations:
        warnings.append(f"{len(pseudogene_locations)} explicitly tagged GFF pseudogene CDS feature(s) excluded from required GenBank CDS coverage ({excluded} also present as GenBank pseudogenes); downstream tools may not assess these features")
    return {"contig_aliases": aliases,
            "coverage": {"status": "complete_eligible_cds", "eligible_cds_count": len(expected),
                         "matched_eligible_cds_count": len(observed), "excluded_pseudogene_cds_count": excluded,
                         "gff_pseudogene_cds_count": len(pseudogene_locations)},
            "warnings": warnings}
