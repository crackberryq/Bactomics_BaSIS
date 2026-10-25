"""Read-only genomic context payloads for the offline report.

Coordinates are copied from recorded normalized evidence. Optional sequence and
annotation enrichment is allowed only after matching the recorded input digest.
This module does not infer genomic islands, topology, or biological function.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

from .report import _all_findings, _execution, _exploratory_finding, _module, _sample_id
from .report_links import candidate_anchor
from .report_context import finding_context


TRACKS = (
    ("amr", "AMR screening calls", {"amrfinder", "rgi"}),
    ("vfdb", "VFDB homology matches", {"vfdb"}),
    ("stress", "BacMet biocide / metal homologs", {"bacmet"}),
    ("provirus", "Virus / provirus predictions", {"genomad"}),
    ("bgc", "Predicted biosynthetic regions", {"antismash"}),
    ("integron", "Predicted integron loci", {"integronfinder"}),
    ("insertion_sequence", "Predicted insertion sequences", {"isescan"}),
    ("ice", "ICE / IME predictions", {"icescreen"}),
    ("crispr", "Predicted CRISPR arrays", {"minced", "crisprcasfinder"}),
    ("plasmid", "Predicted plasmid regions", {"genomad", "mobsuite"}),
    ("ml_attention", "ML attention priorities", {"pathogenfinder2"}),
    ("selected", "Selected DeepSafe candidates", {"deepsafe"}),
    ("genomic_islands", "Predicted genomic islands", {"islandpath", "islandpath_dimob", "islandviewer", "genomic_islands"}),
)


def _integer(value):
    """Positive JavaScript-safe integer, without truncation or boolean coercion."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        parsed = int(value)
    else:
        return None
    return parsed if 0 < parsed <= 9007199254740991 else None


def _interval(value, lengths, aliases):
    if not isinstance(value, dict) or not value.get("contig"):
        return None
    contig = str(value["contig"])
    contig = str(aliases.get(contig, contig))
    start, end = _integer(value.get("start")), _integer(value.get("end"))
    if start is None or end is None or end < start:
        return None
    length = lengths.get(contig)
    if length is not None and end > length:
        return None
    return contig, start, end


def _strand(value):
    return value if value in ("+", "-") else "?"


def _stable_id(*parts):
    content = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return "region-" + hashlib.sha256(content.encode()).hexdigest()[:16]


def _track(finding):
    module, category = finding["module"], finding.get("category")
    if module in ("amrfinder", "rgi") and category == "amr":
        return "amr"
    if module == "vfdb":
        return "vfdb"
    if module == "bacmet" and category == "stress":
        return "stress"
    if module == "genomad" and category == "prophage":
        return "provirus"
    if module == "antismash" and category == "bgc":
        return "bgc"
    if module == "integronfinder" and category == "integron":
        return "integron"
    if module == "isescan" and category == "is":
        return "insertion_sequence"
    if module == "icescreen" and category == "ice":
        return "ice"
    if module == "minced" and category == "crispr":
        return "crispr"
    if module == "crisprcasfinder" and category == "crispr" and (finding.get("attributes") or {}).get("feature_type") in ("array", "crispr_array"):
        # Imported CRISPRCasFinder findings can include Cas genes. A generic
        # CRISPR-system category alone does not establish an array interval.
        return "crispr"
    if module in ("genomad", "mobsuite") and category == "plasmid":
        return "plasmid"
    if module == "pathogenfinder2" and category == "ml_attention":
        return "ml_attention"
    if module in TRACKS[-1][2] and category in ("genomic_island", "genomic_islands"):
        return "genomic_islands"
    return None


def _track_status(records):
    if not records:
        return "not_assessed"
    states = {_execution(r) for r in records}
    if states == {"completed"}:
        return "completed"
    if "completed" in states:
        return "partial"
    if "failed" in states:
        return "failed"
    if "blocked" in states:
        return "blocked"
    if "not_started" in states:
        return "not_started"
    return "not_assessed"


def _read_verified(sample, validation, records, key, reader, sources, notes):
    """Read one input only if all supplied recorded hashes agree with its bytes."""
    path = sample.get(key)
    hashes = set()
    for container in (validation, *records):
        value = (container.get("input_hashes") or {}).get(key)
        if isinstance(value, str) and value:
            hashes.add(value.lower())
    source = {"role": key, "path": str(path or ""), "status": "unavailable"}
    sources.append(source)
    if not path or not Path(path).is_file():
        notes.append(f"Optional {key} input is unavailable locally; saved coordinates remain usable.")
        return None
    if len(hashes) != 1 or not re.fullmatch(r"[a-f0-9]{64}", next(iter(hashes), "")):
        source["status"] = "recorded_hash_unavailable_or_conflicting"
        notes.append(f"Optional {key} enrichment omitted: recorded input hashes are missing or conflicting.")
        return None
    expected = next(iter(hashes))
    source["recorded_sha256"] = expected
    try:
        from .util import sha256_file
        observed = sha256_file(path)
        if observed != expected:
            source["status"] = "checksum_mismatch"
            notes.append(f"Optional {key} enrichment omitted: current file differs from the recorded input.")
            return None
        value = reader(path)
        # Refuse enrichment if an input changed while it was being parsed.
        if sha256_file(path) != expected:
            source["status"] = "changed_during_read"
            notes.append(f"Optional {key} enrichment omitted: input changed while being read.")
            return None
        source.update(status="verified", sha256=observed)
        return value
    except (OSError, ValueError, UnicodeError) as exc:
        source["status"] = "unreadable"
        notes.append(f"Optional {key} enrichment omitted: {type(exc).__name__} while reading the verified input.")
        return None


def _gc_bins(sequence):
    # About 400 bins per contig, with at least 1 kb per bin. Ambiguous bases do
    # not enter the denominator; all-N windows are explicitly unavailable.
    size = max(1000, (len(sequence) + 399) // 400)
    bins = []
    for offset in range(0, len(sequence), size):
        part = sequence[offset:offset + size].upper()
        g, c = part.count("G"), part.count("C")
        known = g + c + part.count("A") + part.count("T")
        bins.append({"start": offset + 1, "end": offset + len(part),
                     "gc_pct": round(100 * (g + c) / known, 4) if known else None,
                     "gc_skew": round((g - c) / (g + c), 6) if g + c else None})
    return bins


def build_genome_data(run, samples, records, deep_records):
    """Build a compact, sample-isolated payload without altering source objects."""
    from .inputs import read_cds, read_fasta
    from .report_scope import deep_requested
    include_deep = deep_requested(run, deep_records) and run.get("report_view") != "screening"
    sample_map = {_sample_id(s): s for s in samples if isinstance(s, dict)}
    combined = [r for r in list(records) + list(deep_records) if isinstance(r, dict)]
    by_sample = defaultdict(list)
    for record in combined:
        by_sample[_sample_id(record)].append(record)
    validation_map = run.get("input_validations") or {}
    if not isinstance(validation_map, dict):
        validation_map = {}
    output = []
    for sid in sorted(set(sample_map) | set(by_sample) | set(validation_map)):
        sample, current = sample_map.get(sid, {}), by_sample[sid]
        validation = validation_map.get(sid) or {}
        if not isinstance(validation, dict):
            validation = {}
        aliases = validation.get("contig_aliases") or {}
        if not isinstance(aliases, dict):
            aliases = {}
        raw_lengths = validation.get("contig_lengths") or {}
        lengths = {str(k): _integer(v) for k, v in raw_lengths.items()} if isinstance(raw_lengths, dict) else {}
        length_sources = {k: "recorded_input_validation" if v else "unavailable" for k, v in lengths.items()}
        notes = ["Linear coordinate display; contig circularity is not inferred.",
                 "Tracks show recorded predictions or screening calls, not confirmed function or organism safety."]
        sources = [{"role": "protein_coordinates", "source": "run.input_validations", "sample_id": sid}]
        rejected = Counter()
        assembly = _read_verified(sample, validation, current, "assembly", read_fasta, sources, notes)
        usable_sequence = {}
        if assembly:
            for contig, sequence in assembly.items():
                contig = str(aliases.get(contig, contig))
                if lengths.get(contig) is not None and lengths[contig] != len(sequence):
                    notes.append(f"Sequence track omitted for {contig}: recorded and verified FASTA lengths conflict.")
                    continue
                if contig not in lengths or lengths[contig] is None:
                    lengths[contig], length_sources[contig] = len(sequence), "verified_assembly_fasta"
                usable_sequence[contig] = sequence
        contigs = {}

        def ensure_contig(contig):
            if contig not in contigs:
                contigs[contig] = {"id": contig, "length": lengths.get(contig),
                                   "length_source": length_sources.get(contig, "unavailable"),
                                   "genes": [], "regions": []}
            return contigs[contig]

        for contig in lengths:
            ensure_contig(contig)
        genes = {}
        coordinates = validation.get("protein_coordinates") or {}
        if isinstance(coordinates, dict):
            for pid, raw in coordinates.items():
                interval = _interval(raw, lengths, aliases)
                if interval is None:
                    rejected["invalid gene coordinates"] += 1
                    continue
                contig, start, end = interval
                gene = {"id": str(pid), "start": start, "end": end,
                        "strand": _strand(raw.get("strand")), "labels": []}
                genes[str(pid)] = (contig, gene)
                ensure_contig(contig)["genes"].append(gene)
        selected = {}
        for record in current:
            if _module(record) != "deepsafe":
                continue
            for candidate in record.get("candidates", []) or []:
                if not isinstance(candidate, dict) or candidate.get("selected") is not True:
                    continue
                # A candidate from another sample must not acquire this sample's coordinates.
                if candidate.get("sample_id") not in (None, "", sid):
                    rejected["cross-sample candidate"] += 1
                    continue
                pid = str(candidate.get("protein_id") or "")
                if pid:
                    selected[pid] = candidate
        regions = {}

        def add_region(track, row, module, label, source_ids):
            if track == "ice" and (row.get("attributes") or {}).get("coordinate_semantics") == "unknown_element_boundaries":
                # Signature-protein extremes are not reviewed element bounds.
                # Do not draw these as ICE spans, even if an imported record
                # accidentally supplies its signature coordinates as start/end.
                rejected["ICE / IME predictions without reviewed element boundaries"] += 1
                return
            pid = str(row.get("protein_id") or "")
            interval = _interval(row, lengths, aliases)
            if pid in genes:
                contig, gene = genes[pid]
                recorded = (contig, gene["start"], gene["end"])
                if interval is not None and interval != recorded:
                    rejected["conflicting finding coordinates"] += 1
                    return
                # Missing coordinates can use an explicit input protein mapping;
                # malformed coordinates must not be silently repaired.
                if interval is None and any(row.get(k) is not None for k in ("start", "end")):
                    rejected["invalid finding coordinates"] += 1
                    return
                interval = recorded
            if interval is None:
                rejected["unmappable regions"] += 1
                return
            contig, start, end = interval
            if pid and pid not in genes:
                gene = {"id": pid, "start": start, "end": end,
                        "strand": _strand(row.get("strand")), "labels": []}
                genes[pid] = (contig, gene)
                ensure_contig(contig)["genes"].append(gene)
            # Same protein + same track becomes one interval while source calls
            # remain individually labeled. Distinct regions retain their locus.
            key = (track, contig, pid or str(row.get("locus_id") or row.get("finding_id") or label), start, end)
            if key not in regions:
                regions[key] = {"id": _stable_id(sid, *key), "track": track, "module": module,
                                "start": start, "end": end, "label": "", "protein_id": pid,
                                "source_ids": [], "_labels": set(), "_modules": set()}
                ensure_contig(contig)["regions"].append(regions[key])
            region = regions[key]
            if track == "provirus":
                integrated = (row.get("attributes") or {}).get("integrated")
                if isinstance(integrated, bool):
                    region["integrated"] = integrated
                    region["kind"] = "integrated_provirus" if integrated else "viral_contig"
            region["_labels"].add(label)
            region["_modules"].add(module)
            region["source_ids"] = sorted(set(region["source_ids"]) | {str(i) for i in source_ids if i})
            if pid in genes:
                gene = genes[pid][1]
                if track not in ("selected", "ml_attention") and label not in gene["labels"]:
                    gene["labels"].append(label)
                if pid in selected:
                    anchor = candidate_anchor(sid, pid)
                    region["candidate_anchor"] = anchor
                    gene["candidate_anchor"] = anchor
                    gene["queue"] = str(selected[pid].get("selection_queue") or "unspecified")
                    assessment = selected[pid].get("assessment") or {}
                    if isinstance(assessment, dict):
                        if assessment.get("interpretation"):
                            region["interpretation"] = str(assessment["interpretation"])
                        if str(assessment.get("followup", "")).startswith("hold_"):
                            region["review_hold"] = str(assessment["followup"])
                            gene["review_hold"] = str(assessment["followup"])

        for finding in _all_findings(current):
            if finding["execution_status"] != "completed":
                continue
            track = _track(finding)
            if track is None or (track != "ml_attention" and _exploratory_finding(finding)):
                continue
            # Explicitly weak native levels are excluded even when old records
            # omit the generic exploratory flag.
            attributes = finding.get("attributes") or {}
            if track == "amr" and str(attributes.get("cutoff", "")).lower() == "loose":
                continue
            if track == "ml_attention":
                rank = attributes.get("rank")
                label = "ML attention priority" + (f" (rank {rank})" if _integer(rank) else "")
            elif track == "bgc":
                label = "antiSMASH: " + str(finding.get("family") or finding.get("gene") or "predicted region")
            elif track == "provirus":
                integrated = attributes.get("integrated")
                kind = "integrated provirus" if integrated is True else "viral contig" if integrated is False else "virus prediction; integration not recorded"
                label = "geNomad: " + kind + " · " + str(finding.get("gene") or "predicted region")
            elif track == "stress":
                context = finding_context(finding)
                label = "BacMet homology: " + context["source_call"]
                if context["reference_fragment"]:
                    label += " · fragment reference"
                label += " · " + context["coverage_text"]
            elif track == "insertion_sequence":
                label = "ISEScan: " + finding_context(finding)["source_call"]
            elif track == "crispr":
                label = finding["module"] + ": predicted CRISPR array · " + str(finding.get("gene") or finding.get("locus_id") or "array")
            else:
                label = finding["module"] + ": " + str(finding.get("gene") or finding.get("family") or track)
            add_region(track, finding, finding["module"], label, [finding["finding_id"]])
        for pid, candidate in selected.items():
            add_region("selected", candidate, "deepsafe", "Selected DeepSafe candidate: " + pid,
                       candidate.get("finding_ids") or [])
            if candidate.get("selection_queue") == "ml_attention" and not any(
                    r["track"] == "ml_attention" and r["protein_id"] == pid for r in regions.values()):
                add_region("ml_attention", candidate, "deepsafe", "ML attention priority: " + pid,
                           [f.get("finding_id") for f in candidate.get("source_evidence", [])
                            if isinstance(f, dict) and f.get("category") == "ml_attention"])

        gff = _read_verified(sample, validation, current, "gff", read_cds, sources, notes)
        if gff:
            for feature in gff:
                interval = _interval(feature, lengths, aliases)
                for pid in feature["aliases"]:
                    if pid not in genes:
                        continue
                    contig, gene = genes[pid]
                    if interval != (contig, gene["start"], gene["end"]):
                        rejected["GFF coordinate mismatch"] += 1
                        continue
                    product = feature["attributes"].get("product")
                    if product:
                        gene["product"] = str(product)
        for region in regions.values():
            region["label"] = "; ".join(sorted(region.pop("_labels")))
            region["module"] = "; ".join(sorted(region.pop("_modules")))
        for contig, data in contigs.items():
            data["genes"].sort(key=lambda g: (g["start"], g["end"], g["id"]))
            data["regions"].sort(key=lambda r: (r["start"], r["end"], r["track"], r["id"]))
            if contig in usable_sequence:
                data["gc"] = _gc_bins(usable_sequence[contig])
            if data["length"] is None:
                notes.append(f"Contig {contig}: full length is unavailable; visible coordinate extent is not assembly length.")
        counts = Counter(region["track"] for region in regions.values())
        tracks = []
        for track, label, modules in TRACKS:
            if track == "selected" and not include_deep:
                continue
            relevant = [r for r in current if _module(r) in modules]
            if track == "crispr":
                # These are alternative array workflows; an explicitly
                # disabled alternative is not a partially failed selected run.
                active = [r for r in relevant if not (
                    (r.get("effective_config") or {}).get("enabled") is False
                    or r.get("reason") in ("module_disabled", "alternative_not_selected_minced"))]
                if active:
                    relevant = active
            status = _track_status(relevant)
            # Selected candidates can preserve an attention priority even when
            # the source PF2 record is absent from a historical report bundle.
            if track == "ml_attention" and counts[track] and not relevant:
                status = "recorded_priorities"
            tracks.append({"id": track, "label": label, "status": status, "count": counts[track]})
        if not any(_module(r) in TRACKS[-1][2] for r in current):
            notes.append("General genomic-island prediction was not assessed. Proviruses, biosynthetic regions and plasmid calls are separate tracks.")
        if any(_module(r) == "bacmet" and _execution(r) == "completed" for r in current):
            notes.append("BacMet matches are biocide / metal resistance homologs; they do not establish resistance phenotype, antibiotic resistance or exposure risk.")
            notes.append("A BacMet fragment-reference label describes the reference sequence, not query completeness; reference coverage is relative to the recorded reference fragment.")
        if any(_module(r) == "isescan" and _execution(r) == "completed" for r in current):
            notes.append("ISEScan native type p denotes a partial IS prediction and c a complete IS prediction. These labels do not establish element activity or transferability.")
        if rejected["ICE / IME predictions without reviewed element boundaries"]:
            notes.append("ICEscreen signature-protein coordinates are not complete element boundaries. These predictions remain in screening evidence and are not drawn as genomic spans.")
        if any(_module(r) == "minced" and _execution(r) == "completed" for r in current):
            notes.append("MinCED predicts CRISPR repeat-spacer arrays; a detected array does not establish an active CRISPR-Cas system.")
        notes.append("Zero mapped predictions do not establish absence; failed or unassessed tracks are explicitly labeled.")
        notes.extend(f"Omitted {count} {reason}." for reason, count in sorted(rejected.items()))
        if usable_sequence:
            notes.append("GC percentage uses A/C/G/T bases only; GC skew is (G−C)/(G+C). Undefined windows have no value.")
        output.append({"id": sid, "contigs": [contigs[k] for k in sorted(contigs)],
                       "tracks": tracks, "notes": notes, "sources": sources})
    return {"schema_version": "basis-genome-view-1", "coordinate_basis": "1-based inclusive", "samples": output}
