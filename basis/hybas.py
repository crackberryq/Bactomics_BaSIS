"""Read the inspected HybAs AWARE outputs without treating targets as species calls."""
from __future__ import annotations

import copy
import csv
import json
import math
import re
from collections import Counter
from pathlib import Path

from .util import sha256_file
from .input_context import taxonomy_is_missing

OPERATIONAL_SOURCE = "hybas_kraken_operational_target"
PHYLUM_SOURCE = "hybas_selected_target_ancestry"
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
_HASH = re.compile(r"[0-9a-fA-F]{64}\Z")
_MISSING = {"", "NA", "N/A", "none", "None", "null"}
_TARGET_FIELDS = (
    "selected_target_taxid", "selected_target_name", "selected_target_rank",
    "selected_target_source", "selected_target_percent_for_decision",
    "illumina_selected_target_percent", "ont_selected_target_percent",
    "inferred_target_taxid", "inferred_target_name", "inferred_target_rank",
    "inferred_target_percent", "manual_target_taxid", "target_selection_reason",
    "runner_up_taxid", "runner_up_name", "runner_up_percent",
    "selected_vs_runner_up_delta_percent", "platform_agreement_score",
    "platform_agreement_reason", "decision_confidence", "awareness_state",
)
_DECISION_FIELDS = _TARGET_FIELDS + (
    "kraken_decision_mode", "kraken_decision", "kraken_decision_reason",
    "illumina_kraken_decision", "illumina_kraken_decision_reason",
    "ont_kraken_decision", "ont_kraken_decision_reason", "kraken_include_children",
)


def _text(value):
    return "" if value is None else str(value).strip()


def _present(value):
    return _text(value) not in _MISSING


def _source(path, role, sources):
    if not path.is_file():
        raise ValueError(f"HybAs {role} is not a file: {path}")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError(f"HybAs metadata/report exceeds 64 MiB: {path}")
    sources.append({"role": role, "path": str(path), "sha256": sha256_file(path)})


def _pairs(path, header):
    result = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = csv.reader(handle, delimiter="\t")
        if next(rows, None) != list(header):
            raise ValueError(f"HybAs {path}: expected TSV header {header}")
        for line, row in enumerate(rows, 2):
            if not row:
                continue
            if len(row) != 2 or not _KEY.fullmatch(row[0].strip()):
                raise ValueError(f"HybAs {path}:{line}: malformed key/value row")
            key, value = (item.strip() for item in row)
            if key in result:
                raise ValueError(f"HybAs {path}:{line}: duplicate key {key}")
            result[key] = value
    return result


def _json_summary(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"HybAs {path}: duplicate key {key}")
            result[key] = value
        return result

    with path.open(encoding="utf-8-sig") as handle:
        result = json.load(handle, object_pairs_hook=unique,
                           parse_constant=lambda value: (_ for _ in ()).throw(
                               ValueError(f"HybAs {path}: invalid number {value}")))
    if not isinstance(result, dict) or any(
        not _KEY.fullmatch(key) or isinstance(value, (dict, list))
        or (isinstance(value, float) and not math.isfinite(value))
        for key, value in result.items()
    ):
        raise ValueError(f"HybAs {path}: summary must be a flat object with valid keys")
    return result


def _identity(data, path, sample_id):
    if _text(data.get("isolate")) != sample_id:
        raise ValueError(f"HybAs {path}: isolate must match sample_id {sample_id!r}")


def _summary_decision(summary):
    data = {key: summary["kraken_" + key] for key in _TARGET_FIELDS
            if "kraken_" + key in summary}
    for key in ("kraken_decision_mode", "kraken_decision", "kraken_decision_reason"):
        if key in summary:
            data[key] = summary[key]
    return data


def _percentage(value, label):
    if isinstance(value, bool):
        raise ValueError(f"HybAs {label}: invalid percentage")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"HybAs {label}: invalid percentage") from exc
    if not math.isfinite(result) or not 0 <= result <= 100:
        raise ValueError(f"HybAs {label}: percentage must be between 0 and 100")
    return result


def _read_report(path, role, sources, disabled, warnings, target_taxid=""):
    result = {"status": "disabled" if disabled else "not_found", "top_species": [],
              "target_match": None, "target_ancestry": []}
    if not path.exists():
        return result
    _source(path, role + "_report", sources)
    result["source"] = dict(sources[-1])
    if disabled:
        if path.stat().st_size:
            warnings.append(f"{role}: nonempty report ignored because HybAs declares Kraken disabled")
        return result
    species, seen, count, ancestors = [], set(), 0, []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for line, row in enumerate(csv.reader(handle, delimiter="\t"), 1):
            if not row:
                continue
            if len(row) != 6:
                raise ValueError(f"HybAs {path}:{line}: expected six Kraken report columns")
            pct, clade, direct, rank, taxid, name = (item.strip() for item in row)
            if (not clade.isdigit() or not direct.isdigit() or int(direct) > int(clade)
                    or not taxid.isdigit() or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", rank)
                    or not name or taxid in seen):
                raise ValueError(f"HybAs {path}:{line}: malformed or duplicate Kraken record")
            percentage = _percentage(pct, f"{path}:{line}")
            seen.add(taxid)
            count += 1
            record = {"percentage": percentage, "clade_reads": int(clade),
                      "direct_reads": int(direct), "rank": rank,
                      "taxid": taxid, "name": name}
            depth = len(row[5]) - len(row[5].lstrip(" "))
            while ancestors and ancestors[-1][0] >= depth:
                ancestors.pop()
            ancestors.append((depth, {"rank": rank, "taxid": taxid, "name": name}))
            if target_taxid and taxid == target_taxid:
                result["target_match"] = record
                result["target_ancestry"] = [dict(node) for _, node in ancestors]
            if rank == "S":
                species.append(record)
    if sha256_file(path) != result["source"]["sha256"]:
        raise ValueError(f"HybAs Kraken report changed while being read: {path}")
    result.update(status="available" if count else "empty", rows=count,
                  top_species=sorted(species, key=lambda r: (-r["percentage"], r["taxid"]))[:3])
    return result


def _phylum_resolution(selected, taxonomy_status, reads):
    """Resolve only the recorded target's lineage, never a leading read hit.

    Every available enabled report must contain the identical selected target,
    positive clade support and the same phylum ancestor. Missing platforms are
    disclosed; an available report missing the target or hierarchy blocks reuse.
    """
    result = {"status": "unresolved", "reason": "selected_target_not_resolved",
              "source": PHYLUM_SOURCE, "selected_target": copy.deepcopy(selected),
              "phylum": None, "evidence": [], "ignored_reports": []}
    for platform, data in reads.items():
        if data["status"] != "available":
            result["ignored_reports"].append({"platform": platform, "status": data["status"],
                                               "source": copy.deepcopy(data.get("source"))})
            continue
        ancestors = data.get("target_ancestry", [])
        phylum = next((dict(node) for node in reversed(ancestors) if node["rank"] == "P"), None)
        result["evidence"].append({"platform": platform, "source": copy.deepcopy(data.get("source")),
                                   "target_match": copy.deepcopy(data.get("target_match")),
                                   "ancestors": copy.deepcopy(ancestors), "phylum": phylum})
    if taxonomy_status != "resolved" or selected.get("rank") not in {"G", "S"}:
        return result
    if not result["evidence"]:
        result["reason"] = "no_available_enabled_kraken_reports"
        return result
    for evidence in result["evidence"]:
        match = evidence["target_match"]
        if not match:
            result["reason"] = "selected_target_missing_from_available_report"
            return result
        if any(match[key] != selected.get(key) for key in ("taxid", "rank", "name")):
            result["reason"] = "selected_target_identity_conflict"
            return result
        if match["clade_reads"] <= 0:
            result["reason"] = "selected_target_without_positive_clade_support"
            return result
        if not evidence["phylum"]:
            result["reason"] = "selected_target_phylum_ancestor_missing"
            return result
    identities = {(e["phylum"]["taxid"], e["phylum"]["name"].casefold()) for e in result["evidence"]}
    # These two recorded names are taxonomic synonyms for the same phylum ID.
    if identities and all(taxid == "1239" and name in {"bacillota", "firmicutes"}
                          for taxid, name in identities):
        identities = {("1239", "bacillota")}
    if len(identities) != 1:
        result["reason"] = "phylum_ancestry_conflict_between_reports"
        return result
    result.update(status="resolved", reason="selected_target_ancestry_agrees",
                  phylum=dict(result["evidence"][0]["phylum"]))
    return result


def _enrich_phylum(result, context):
    resolution = _phylum_resolution(context["selected_target"], context["taxonomy_status"],
                                    context["read_taxonomy"])
    context["phylum_resolution"] = resolution
    old_auto = result.get("taxon_phylum_source") == PHYLUM_SOURCE
    declared = not taxonomy_is_missing(result.get("taxon_phylum")) and not old_auto
    if declared:
        if not _present(result.get("taxon_phylum_source")):
            result["taxon_phylum_source"] = "sample_sheet"
        resolution["sample_assignment"] = "explicit_declaration_preserved"
        if resolution["status"] == "resolved":
            recorded = resolution["phylum"]
            names = {_text(result["taxon_phylum"]).casefold(), recorded["name"].casefold()}
            agrees = len(names) == 1 or (recorded["taxid"] == "1239" and names <= {"bacillota", "firmicutes"})
            if not agrees:
                context["warnings"].append("Declared phylum conflicts with HybAs selected-target ancestry; "
                                           "the explicit declaration was retained and requires review")
        return
    target = context["selected_target"]
    taxon, selected_name = _text(result.get("taxon")).casefold(), target.get("name", "").casefold()
    if (not taxonomy_is_missing(taxon) and taxon != selected_name
            and not (target.get("rank") == "G" and taxon.startswith(selected_name + " "))):
        resolution["sample_assignment"] = "held_declared_taxon_differs_from_selected_target"
    elif resolution["status"] == "resolved":
        result.update(taxon_phylum=resolution["phylum"]["name"], taxon_phylum_source=PHYLUM_SOURCE,
                      taxon_phylum_taxid=resolution["phylum"]["taxid"])
        resolution["sample_assignment"] = "verified_target_ancestry"
        return
    else:
        resolution["sample_assignment"] = "not_assigned"
    if old_auto:
        for key in ("taxon_phylum", "taxon_phylum_source", "taxon_phylum_taxid"):
            result.pop(key, None)
    context["warnings"].append("Phylum could not be assigned automatically from the selected HybAs target: "
                               + resolution.get("sample_assignment", resolution["reason"]) + " ("
                               + resolution["reason"] + "). Review the target ancestry in available Kraken reports "
                               "or supply an independently reviewed taxon_phylum; phylum-scoped modules remain held.")


def _legacy_target(summaries):
    """Read the recorded 8.7 target; never discover a target from abundant reads."""
    recorded = None
    for path, summary in summaries:
        if "target_taxid" not in summary:
            continue
        value = _text(summary["target_taxid"]) if _present(summary["target_taxid"]) else ""
        if recorded is not None and value != recorded:
            raise ValueError(f"HybAs summary conflict for legacy target_taxid: {path}")
        if value and not value.isdigit():
            raise ValueError(f"HybAs {path}: legacy target_taxid must be numeric")
        recorded = value
    return recorded


def _resolve_legacy(selected, reads, warnings):
    matches = [data["target_match"] for data in reads.values() if data.get("target_match")]
    selected["positive_clade_support"] = any(row["clade_reads"] > 0 for row in matches)
    if not matches:
        selected["resolution"] = "unresolved"
        warnings.append(f"Legacy HybAs target taxid {selected['taxid']} has no matching available Kraken report row; name and rank remain unresolved")
        return "unresolved"
    identities = {(row["name"], row["rank"]) for row in matches}
    if len(identities) != 1:
        selected["resolution"] = "conflicting_kraken_rows"
        warnings.append(f"Kraken reports disagree on the name or rank for legacy target taxid {selected['taxid']}; no operational taxon was assigned")
        return "unresolved"
    selected["name"], selected["rank"] = next(iter(identities))
    selected["resolution"] = "kraken_report_exact_taxid"
    if not selected["positive_clade_support"]:
        warnings.append(f"Legacy HybAs target taxid {selected['taxid']} has only zero-clade-read matches; no operational taxon was assigned")
    elif selected["rank"] not in {"G", "S"}:
        warnings.append(f"Legacy HybAs target resolves at Kraken rank {selected['rank']}; only genus or species targets populate the sample taxon")
    return "resolved"


def _checksums(path, root, sources, warnings):
    if not path.exists():
        return
    _source(path, "checksums", sources)
    relevant = {"final_assembly": root / "work/final/assembly.final.fasta",
                "run_summary_json": root / "metadata/run_summary.json"}
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = csv.reader(handle, delimiter="\t")
        if next(rows, None) != ["label", "path", "sha256"]:
            raise ValueError(f"HybAs {path}: malformed checksum header")
        for line, row in enumerate(rows, 2):
            if not row:
                continue
            if len(row) != 3:
                raise ValueError(f"HybAs {path}:{line}: malformed checksum row")
            label, recorded_path, digest = (item.strip() for item in row)
            if label not in relevant:
                continue  # Raw reads and databases are deliberately not opened.
            if label in seen:
                raise ValueError(f"HybAs {path}: duplicate checksum label {label}")
            seen.add(label)
            if not _present(digest):
                warnings.append(f"HybAs checksum unavailable for {label}")
                continue
            if not _HASH.fullmatch(digest) or not recorded_path:
                raise ValueError(f"HybAs {path}: malformed checksum for {label}")
            local = relevant[label]
            if not local.is_file():
                warnings.append(f"HybAs checksum for {label} cannot be checked: local source missing")
            elif sha256_file(local) != digest.lower():
                raise ValueError(f"HybAs checksum mismatch for {label}: {local}")


def _binding(root, sample, warnings):
    # Lazy import avoids a cycle when load_samples calls enrich_sample.
    from .inputs import read_fasta

    final = root / "work/final/assembly.final.fasta"
    if not final.exists():
        warnings.append("HybAs final assembly missing; binding uses the sample ID and annotation directory only")
        return {"status": "sample_id_directory_only"}
    if Counter(read_fasta(final).values()) != Counter(read_fasta(sample["assembly"]).values()):
        raise ValueError(f"HybAs final assembly sequences do not match annotation assembly for {sample['sample_id']}")
    return {"status": "sequence_verified", "final_assembly": str(final),
            "final_assembly_sha256": sha256_file(final),
            "annotation_assembly": str(sample["assembly"]),
            "annotation_assembly_sha256": sha256_file(sample["assembly"])}


def enrich_sample(sample: dict) -> dict:
    """Return a copy enriched from matching on-disk HybAs outputs, never write them."""
    result = copy.deepcopy(sample)
    from .inputs import normalize_input_mode
    mode = normalize_input_mode(sample.get("input_mode"))
    result["input_mode"] = mode
    sid = _text(sample.get("sample_id"))
    explicit = _text(sample.get("hybas_dir"))
    if mode == "external":
        if explicit:
            raise ValueError("input_mode external conflicts with hybas_dir; remove hybas_dir or choose hybas/auto")
        if result.get("taxon_source") == OPERATIONAL_SOURCE:
            raise ValueError("External input cannot claim an imported HybAs taxonomy source")
        if result.get("taxon_phylum_source") == PHYLUM_SOURCE:
            raise ValueError("External input cannot claim an imported HybAs phylum source")
        result.pop("hybas_context", None)
        if not taxonomy_is_missing(result.get("taxon")):
            result.setdefault("taxon_source", "sample_sheet")
            if not _present(result.get("taxon_source")):
                result["taxon_source"] = "sample_sheet"
            result["taxon_status"] = "declared"
        else:
            result["taxon_status"] = "not_recorded"
        return result
    gff = Path(sample.get("gff", "")).resolve()
    root = Path(explicit).expanduser().resolve() if explicit else gff.parent.parent
    matches = gff.parent.name == "annotation" and gff.name == sid + ".gff" and root.name == sid
    if mode == "hybas" and not explicit and not matches:
        raise ValueError(f"{sid}: input_mode hybas requires a matching isolate/annotation layout or explicit hybas_dir")
    if not explicit and not matches:
        if _present(result.get("taxon")) and result.get("taxon_source") != OPERATIONAL_SOURCE:
            if not _present(result.get("taxon_source")):
                result["taxon_source"] = "sample_sheet"
            result["taxon_status"] = "declared"
        return result
    if not root.is_dir() or root.name != sid:
        raise ValueError(f"HybAs isolate directory must exist and be named {sid!r}: {root}")
    for key, suffix in (("gff", ".gff"), ("proteins", ".faa"), ("assembly", ".fna")):
        expected = (root / "annotation" / (sid + suffix)).resolve()
        if Path(sample.get(key, "")).resolve() != expected:
            raise ValueError(f"HybAs {key} must belong to the same annotation set: {expected}")
    if sample.get("genbank") and Path(sample["genbank"]).resolve() not in {
        (root / "annotation" / (sid + suffix)).resolve() for suffix in (".gbk", ".gbff")
    }:
        raise ValueError("HybAs GenBank must belong to the same annotation set")

    sources, warnings = [], []
    metadata = root / "metadata"
    decision_path = metadata / "kraken_decision.tsv"
    decision, summary, summaries = {}, {}, []
    if decision_path.exists():
        _source(decision_path, "decision", sources)
        decision = _pairs(decision_path, ("field", "value"))
        _identity(decision, decision_path, sid)
    for filename, role in (("run_summary.json", "summary_json"), ("run_summary.tsv", "summary_tsv")):
        path = metadata / filename
        if path.exists():
            _source(path, role, sources)
            value = _json_summary(path) if path.suffix == ".json" else _pairs(path, ("metric", "value"))
            _identity(value, path, sid)
            summaries.append((path, value))
            if not summary:
                summary = value
    combined = dict(decision)
    for path, value in summaries:
        for key, value in _summary_decision(value).items():
            if key in combined and _text(combined[key]) != _text(value):
                # Exported numeric fields are strings in the native JSON, too.
                if key.endswith("percent") or key.endswith("percent_for_decision") or key == "platform_agreement_score":
                    if _present(combined[key]) and _present(value) and float(combined[key]) == float(value):
                        continue
                raise ValueError(f"HybAs decision/summary conflict for {key}: {path}")
            combined[key] = value

    # A legacy configured target has no recorded AWARE selection method. Explicit
    # modern fields, including a recorded no-selection, always remain authoritative.
    modern_selected = any(key.startswith("selected_target_") for key in decision) or any(
        key.startswith("kraken_selected_target_") for _, value in summaries for key in value)
    legacy_taxid = _legacy_target(summaries)
    off = combined.get("kraken_decision_mode") == "off"
    disabled = off or any(value.get("run_kraken") in (False, "False", "false")
                          for _, value in summaries)
    legacy = bool(legacy_taxid) and not modern_selected and not off

    selected = {name: _text(combined.get("selected_target_" + name))
                for name in ("name", "taxid", "rank", "source")}
    for field, name in (("selected_target_percent_for_decision", "percent_for_decision"),
                        ("illumina_selected_target_percent", "illumina_percent"),
                        ("ont_selected_target_percent", "ont_percent")):
        if _present(combined.get(field)):
            selected[name] = _percentage(combined[field], field)
    for name in ("target_selection_reason", "decision_confidence", "awareness_state"):
        if _present(combined.get(name)):
            selected[name] = _text(combined[name])
    if _present(selected["taxid"]) and not selected["taxid"].isdigit():
        raise ValueError("HybAs selected target taxid must be numeric")
    if _present(selected["rank"]) and selected["rank"] not in {"G", "S", "MANUAL"}:
        raise ValueError("HybAs selected target rank must be G, S or MANUAL")

    if legacy:
        selected.update(taxid=legacy_taxid, source="legacy_run_summary_target",
                        selection_method="not_recorded", resolution="unresolved",
                        positive_clade_support=False)
    reads = {platform: _read_report(root / "reports/kraken2" / filename, platform, sources,
                                   disabled, warnings, selected["taxid"])
             for platform, filename in (("illumina", "illumina.report.txt"), ("nanopore", "nanopore.report.txt"))}
    if disabled:
        taxonomy_status = "disabled"
        if legacy:
            selected["resolution"] = "disabled"
    elif legacy:
        taxonomy_status = _resolve_legacy(selected, reads, warnings)
    elif modern_selected:
        resolved = (_present(selected["name"]) and selected["taxid"].isdigit()
                    and int(selected["taxid"]) > 0 and selected["rank"] in {"G", "S"}
                    and not selected["name"].upper().startswith("MANUAL_TARGET"))
        taxonomy_status = "resolved" if resolved else "unresolved"
        if not resolved:
            warnings.append("HybAs modern target metadata does not record a resolved genus or species target")
    else:
        taxonomy_status = "not_recorded"
        warnings.append("HybAs metadata does not record an operational taxonomy target")
    _checksums(metadata / "checksums.tsv", root, sources, warnings)
    binding = _binding(root, sample, warnings)
    found = bool(decision or summary)
    if not found:
        warnings.append("HybAs decision and run-summary metadata were not found; no taxonomy target was assigned")
    upstream = {name: summary[key] for name, key in (
        ("workflow_version", "workflow_version"), ("kraken2_version", "tool_kraken2_version"),
        ("kraken_db_dir", "kraken_db_dir"), ("timestamp", "timestamp")) if _present(summary.get(key))}
    context = {"schema_version": 1, "status": "imported" if found else "not_found",
               "taxonomy_status": taxonomy_status,
               "sample_id": sid, "root": str(root), "sources": sources,
               "selected_target": selected, "read_taxonomy": reads, "upstream": upstream,
               "binding": binding, "warnings": warnings,
               "decision": {k: _text(v)[:4096] for k, v in combined.items() if k in _DECISION_FIELDS}}
    if legacy_taxid is not None:
        context["legacy_target_taxid"] = legacy_taxid
    result.update(hybas_dir=str(root), hybas_context=context)
    old_auto = result.get("taxon_source") == OPERATIONAL_SOURCE
    valid = (not disabled and taxonomy_status == "resolved"
             and (selected["source"] == "inferred_from_kraken"
                  or (legacy and selected["positive_clade_support"]))
             and selected["rank"] in {"G", "S"} and selected["taxid"].isdigit()
             and int(selected["taxid"]) > 0 and _present(selected["name"])
             and not selected["name"].upper().startswith("MANUAL_TARGET"))
    if not _present(result.get("taxon")) or old_auto:
        if valid:
            result.update(taxon=selected["name"], taxon_rank={"G": "genus", "S": "species"}[selected["rank"]],
                          taxon_source=OPERATIONAL_SOURCE, taxon_status="read_based_operational_target")
        elif old_auto:
            for key in ("taxon", "taxon_rank", "taxon_source", "taxon_status"):
                result.pop(key, None)
    else:
        if not _present(result.get("taxon_source")):
            result["taxon_source"] = "sample_sheet"
        result["taxon_status"] = "declared"
        declared = result["taxon"].strip().casefold()
        target = selected["name"].casefold()
        agrees = declared == target or (selected["rank"] == "G" and declared.startswith(target + " "))
        if valid and not agrees:
            warnings.append(f"Declared taxon {result['taxon']!r} differs from HybAs operational target {selected['name']!r}; declared taxon retained")
    _enrich_phylum(result, context)
    return result
