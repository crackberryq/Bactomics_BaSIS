from __future__ import annotations

import copy
import math
import re
import statistics

from .util import hash_object

CATEGORIES = {"amr", "virulence", "stress", "bgc", "plasmid", "prophage", "integron", "is", "ice", "ml_attention", "crispr", "typing"}
INTERPRETATIONS = {"detected", "not_detected", "inconclusive", "not_applicable"}
_COORDINATE_KEYS = ("contig", "start", "end", "strand")


def _has_normalized_ids(finding: dict) -> bool:
    """Recognize the existing ID triplet, not an arbitrary adapter ``id``.

    This is a format marker only, not proof of source authenticity. Imported
    record identity and input hashes are checked by the import layer.
    """
    locus = finding.get("locus_id")
    identity = finding.get("finding_id")
    return (isinstance(locus, str) and re.fullmatch(r"locus_[0-9a-f]{16}", locus) is not None
            and isinstance(identity, str) and re.fullmatch(r"finding_[0-9a-f]{16}", identity) is not None
            and finding.get("id") == identity)


def _validate_coordinate_provenance(attributes: dict, module: str, row: int) -> None:
    """Check the audit fields' shape without treating them as canonical location.

    A native alignment span may differ from the annotated CDS, so historical
    coordinates are retained as reported, not checked against current bounds.
    The canonical finding coordinates are independently validated below.
    """
    if "native_contig_alias" in attributes:
        alias = attributes["native_contig_alias"]
        if not isinstance(alias, str) or not alias:
            raise ValueError(f"Invalid native_contig_alias in {module} finding {row}")
    if "reported_coordinates" in attributes:
        reported = attributes["reported_coordinates"]
        if not isinstance(reported, dict) or set(reported) != set(_COORDINATE_KEYS):
            raise ValueError(f"Invalid reported_coordinates in {module} finding {row}")
        for key in _COORDINATE_KEYS:
            value = reported[key]
            expected = int if key in {"start", "end"} else str
            if value is not None and type(value) is not expected:
                raise ValueError(f"Invalid reported_coordinates.{key} in {module} finding {row}")


def normalize_result(result: dict, module: str, sample: dict, validation: dict) -> dict:
    if not isinstance(result, dict) or not isinstance(result.get("findings"), list):
        raise ValueError("Adapter must return a findings list")
    if result.get("interpretation") not in INTERPRETATIONS:
        raise ValueError("Adapter returned an invalid interpretation")
    normalized = copy.deepcopy(result)
    if module == "pathogenfinder2":
        metrics = normalized.get("metrics", {})
        for field, maximum in (("prediction_mean", 1.0), ("prediction_std", 0.5)):
            value = metrics.get(field)
            if not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError(f"Invalid or missing PathogenFinder2 {field}")
        members = metrics.get("ensemble_member_scores", metrics.get("prediction_members"))
        if members is not None:
            if not isinstance(members, list) or len(members) != 4 or any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in members):
                raise ValueError("PathogenFinder2 requires four finite ensemble predictions")
            if not math.isclose(statistics.mean(members), metrics["prediction_mean"], abs_tol=.001, rel_tol=.005):
                raise ValueError("PathogenFinder2 ensemble mean is inconsistent")
            if not math.isclose(statistics.pstdev(members), metrics["prediction_std"], abs_tol=.001, rel_tol=.005):
                raise ValueError("PathogenFinder2 ensemble spread is inconsistent")
        normalized["interpretation"] = "inconclusive"
    coordinates = validation["protein_coordinates"]
    contigs = validation["contig_lengths"]
    aliases = validation.get("contig_aliases", {})
    inventory_key = {"antismash": "record_ids", "mobsuite": "contig_ids"}.get(module)
    inventory = normalized.get("metrics", {}).get(inventory_key) if inventory_key else None
    if inventory is not None and {aliases.get(cid, cid) for cid in inventory} != set(contigs):
        raise ValueError(f"{module}: native output does not account for every input contig")
    for index, finding in enumerate(normalized["findings"]):
        if not isinstance(finding, dict) or finding.get("category") not in CATEGORIES:
            raise ValueError(f"Invalid finding category in {module}, row {index + 1}")
        previously_normalized = _has_normalized_ids(finding)
        finding["sample_id"] = sample["sample_id"]
        finding.setdefault("tool", module)
        attributes = finding.setdefault("attributes", {})
        if not isinstance(attributes, dict):
            raise ValueError(f"Invalid finding attributes in {module}, row {index + 1}")
        if finding.get("contig") in aliases:
            # Self aliases add no native identity information. In particular,
            # a protein-only finding must not acquire one on its second pass.
            if not previously_normalized and aliases[finding["contig"]] != finding["contig"]:
                attributes.setdefault("native_contig_alias", finding["contig"])
            finding["contig"] = aliases[finding["contig"]]
        pid = finding.get("protein_id")
        if pid in coordinates:
            feature = coordinates[pid]
            # Imported normalized findings already carry the original native
            # snapshot. Preserve it, including all-None protein-only locations.
            # Retain the historical first-pass convention: snapshot the contig
            # after alias resolution, with the raw alias recorded separately.
            # A legacy normalized record may lack the snapshot entirely. Its
            # native location cannot be reconstructed from canonical fields.
            if not previously_normalized:
                attributes.setdefault("reported_coordinates", {k: finding.get(k) for k in _COORDINATE_KEYS})
            for key in _COORDINATE_KEYS:
                finding[key] = feature[key]
        _validate_coordinate_provenance(attributes, module, index + 1)
        for key in ("identity", "coverage"):
            val = finding.get(key)
            if val is not None and (not isinstance(val, (int, float)) or not math.isfinite(val) or not 0 <= val <= 100):
                raise ValueError(f"Invalid {key} in {module} finding {index + 1}")
        start, end, contig = finding.get("start"), finding.get("end"), finding.get("contig")
        if contig is not None and contig not in contigs:
            raise ValueError(f"{module}: unresolved contig identifier {contig!r}")
        if start is not None or end is not None:
            if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                raise ValueError(f"Invalid coordinates in {module} finding {index + 1}")
            if contig not in contigs or end > contigs[contig]:
                raise ValueError(f"Finding outside validated assembly in {module}: {contig}:{start}-{end}")
        location = [sample["sample_id"], contig, start, end, finding.get("strand")]
        if start is None:
            location += [pid or "unresolved", finding.get("gene"), finding.get("reference")]
        finding["locus_id"] = "locus_" + hash_object(location)[:16]
        finding["finding_id"] = "finding_" + hash_object([module, location, finding.get("gene"), finding.get("reference"), finding.get("attributes")])[:16]
        finding["id"] = finding["finding_id"]
    return normalized


def build_registry(records: list[dict]) -> list[dict]:
    loci = {}
    for record in records:
        for finding in record.get("findings", []):
            lid = finding["locus_id"]
            locus = loci.setdefault(lid, {"locus_id": lid, "sample_id": finding["sample_id"],
                                         "protein_id": finding.get("protein_id"), "contig": finding.get("contig"),
                                         "start": finding.get("start"), "end": finding.get("end"),
                                         "strand": finding.get("strand"), "findings": [], "tools": []})
            locus["findings"].append(finding)
            if finding["tool"] not in locus["tools"]:
                locus["tools"].append(finding["tool"])
    return list(loci.values())
