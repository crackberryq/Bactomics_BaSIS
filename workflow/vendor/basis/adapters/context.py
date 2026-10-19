"""Strict native context adapters. These calls describe loci, never organism safety.

Interfaces were checked against upstream documentation/source; live engines are
not bundled. See docs/CONTEXT_ADAPTERS.md for supported formats and limitations.
"""
from __future__ import annotations

import ast
import csv
import io
import json
import math
import re
import shutil
from collections import Counter
from pathlib import Path

from ..inputs import read_fasta


SUPPORTED_NATIVE = {"antismash", "genomad", "mobsuite", "integronfinder", "isescan", "icescreen"}


def _text(path: Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Required native output missing: {path}")
    value = path.read_text(encoding="utf-8-sig")
    if not value.strip():
        raise ValueError(f"Required native output empty: {path}")
    if value.lstrip().lower().startswith(("<!doctype html", "<html")):
        raise ValueError(f"HTML is not a native result: {path}")
    return value


def _rows(path: Path, required: set[str], *, delimiter: str = "\t", comments: bool = False) -> list[dict]:
    value = _text(path)
    if comments:
        value = "\n".join(line for line in value.splitlines() if not line.startswith("#"))
    reader = csv.DictReader(io.StringIO(value), delimiter=delimiter)
    fields = reader.fieldnames
    if not fields or len(fields) != len(set(fields)) or not required.issubset(fields):
        raise ValueError(f"Unsupported/malformed header in {path}; requires {sorted(required)}")
    result = []
    for index, row in enumerate(reader, 2):
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f"Wrong number of fields in {path}, row {index}")
        result.append(row)
    return result


def _integer(value, label: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError(f"{label} must be an integer: {value!r}")
    value = int(value)
    if value < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    return value


def _number(value, label: str, low: float | None = None, high: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or (low is not None and number < low) or (high is not None and number > high):
        raise ValueError(f"Invalid {label}: {value!r}")
    return number


def _required(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip() in {"NA", "None", "-"}:
        raise ValueError(f"Missing {label}")
    return value


def _interval(start, end, label: str) -> tuple[int, int]:
    left, right = _integer(start, label + " start"), _integer(end, label + " end")
    if right < left:
        raise ValueError(f"Reversed or unsupported origin-spanning interval in {label}")
    return left, right


def _strand(value) -> str | None:
    if value in ("+", "1", 1):
        return "+"
    if value in ("-", "-1", -1):
        return "-"
    if value in (None, "", ".", "NA", "0", 0):
        return None
    raise ValueError(f"Unsupported strand {value!r}")


def _finding(tool: str, category: str, row: dict, **values) -> dict:
    finding = dict(tool=tool, category=category, gene=None, family=None,
                   protein_id=None, contig=None, start=None, end=None,
                   strand=None, reference=None, identity=None, coverage=None,
                   evidence_level="prediction", interpretation="predicted_context",
                   attributes={"native": row})
    finding.update(values)
    return finding


def _result(findings: list, paths: list[Path], *, metrics=None, notes=None, interpretation=None) -> dict:
    return {"findings": findings, "metrics": metrics or {},
            "artifacts": [str(p) for p in paths], "notes": notes or [],
            "interpretation": interpretation or ("detected" if findings else "not_detected")}


def parse_antismash(path: Path) -> dict:
    """antiSMASH >=6 result JSON; areas are 0-based half-open intervals."""
    data = json.loads(_text(path))
    if not isinstance(data, dict) or not isinstance(data.get("records"), list) or not data["records"]:
        raise ValueError("antiSMASH JSON requires a nonempty records array")
    findings, record_ids, region_count = [], [], 0
    for record in data["records"]:
        if not isinstance(record, dict) or not isinstance(record.get("areas"), list):
            raise ValueError("antiSMASH record lacks a supported areas list")
        contig = _required(record.get("original_id") or record.get("id"), "antiSMASH record ID")
        if contig in record_ids:
            raise ValueError("Duplicate antiSMASH record ID")
        record_ids.append(contig)
        if record.get("skip"):
            raise ValueError(f"antiSMASH skipped record {contig}: {record['skip']}")
        for n, area in enumerate(record["areas"], 1):
            region_count += 1
            if not isinstance(area, dict) or not isinstance(area.get("products"), list):
                raise ValueError("antiSMASH region lacks product list")
            for product in area["products"]:
                _required(product, "antiSMASH product")
            left = _integer(area.get("start"), "antiSMASH start", 0)
            right = _integer(area.get("end"), "antiSMASH end", 1)
            if right == left:
                raise ValueError("antiSMASH region has empty or ambiguous full-circle coordinates")
            segments = [(left + 1, right)]
            if right < left:
                sequence = record.get("seq", {}).get("data")
                if not isinstance(sequence, str) or len(sequence) <= left:
                    raise ValueError("Origin-spanning antiSMASH region requires record sequence length")
                segments = [(left + 1, len(sequence)), (1, right)]
            for segment_index, (start, end) in enumerate(segments, 1):
                finding = _finding("antismash", "bgc", area, contig=contig,
                                   start=start, end=end,
                                   gene=f"region_{n}", family=";".join(area["products"]))
                finding["attributes"].update({"coordinate_conversion": "0-based half-open to 1-based inclusive",
                                               "native_region_id": f"{contig}:region_{n}",
                                               "segment_index": segment_index, "segment_count": len(segments)})
                findings.append(finding)
    return _result(findings, [path], metrics={"region_count": region_count, "record_ids": record_ids,
                                             "antismash_version": data.get("version")},
                   notes=["Biosynthetic regions describe predicted capacity, not toxin production or organism hazard."])


def parse_genomad(virus_path: Path, plasmid_path: Path) -> dict:
    findings = []
    counts = {}
    for kind, path in (("virus", virus_path), ("plasmid", plasmid_path)):
        required = {"seq_name", "length", "topology", kind + "_score"}
        if kind == "virus":
            required.add("coordinates")
        rows = _rows(path, required)
        counts[kind + "_count"] = len(rows)
        seen = set()
        for row in rows:
            name = _required(row["seq_name"], "geNomad sequence")
            if name in seen:
                raise ValueError(f"Duplicate geNomad sequence: {name}")
            seen.add(name)
            length = _integer(row["length"], "geNomad length")
            _number(row[kind + "_score"], "geNomad score", 0, 1)
            left, right, contig = 1, length, name
            topology = _required(row["topology"], "geNomad topology")
            if kind == "virus" and topology == "Provirus":
                match = re.fullmatch(r"(.+)\|provirus_(\d+)_(\d+)", name)
                coords = re.fullmatch(r"(\d+)-(\d+)", row["coordinates"])
                if not match or not coords or match.groups()[1:] != coords.groups():
                    raise ValueError("geNomad provirus ID and coordinate column disagree")
                left, right = _interval(*coords.groups(), "geNomad provirus")
                if right - left + 1 != length:
                    raise ValueError("geNomad provirus length disagrees with coordinates")
                contig = match.group(1)
            elif kind == "virus" and row["coordinates"] not in {"NA", "", "None"}:
                raise ValueError("Unexpected coordinates for a non-integrated geNomad virus")
            finding = _finding("genomad", "prophage" if kind == "virus" else "plasmid", row,
                               gene=name, contig=contig, start=left, end=right,
                               family=row.get("taxonomy") if kind == "virus" else None)
            finding["attributes"]["integrated"] = topology == "Provirus" if kind == "virus" else None
            findings.append(finding)
    return _result(findings, [virus_path, plasmid_path], metrics=counts,
                   notes=["Virus rows distinguish integrated proviruses from complete viral contigs in attributes.integrated.",
                          "Classification scores are not biosafety probabilities; predicted MGEs do not establish transfer."])


def parse_mobsuite(path: Path) -> dict:
    rows = _rows(path, {"molecule_type", "contig_id", "size", "predicted_mobility"})
    if not rows:
        raise ValueError("MOB-recon contig_report must report classified input contigs")
    findings, seen = [], set()
    chromosome_count = 0
    for row in rows:
        contig = _required(row["contig_id"], "MOB-recon contig")
        if contig in seen:
            raise ValueError("Duplicate MOB-recon contig")
        seen.add(contig)
        size = _integer(row["size"], "MOB-recon size")
        molecule = row["molecule_type"].lower()
        if molecule not in {"chromosome", "plasmid"}:
            raise ValueError(f"Unknown MOB-recon molecule_type: {molecule}")
        if molecule == "chromosome":
            chromosome_count += 1
            continue
        finding = _finding("mobsuite", "plasmid", row, contig=contig, start=1, end=size,
                           gene=row.get("primary_cluster_id") or "predicted_plasmid",
                           family=row.get("rep_type(s)"), reference=row.get("mash_nearest_neighbor"))
        finding["attributes"]["predicted_mobility"] = row["predicted_mobility"]
        findings.append(finding)
    return _result(findings, [path], metrics={"plasmid_contigs": len(findings),
                                             "chromosome_contigs": chromosome_count, "contig_ids": sorted(seen)},
                   notes=["MOB-suite calls are reference-dependent predictions; absence does not establish plasmid absence from the culture."])


def parse_integronfinder(integrons_path: Path, summary_path: Path) -> dict:
    # IntegronFinder 2.0.6 writes only comments for a completed no-hit run.
    # Accept its explicit marker, never a missing file or arbitrary comments;
    # the summary/count reconciliation below must independently confirm zero.
    detail_lines = [line for line in _text(integrons_path).splitlines() if line.strip()]
    if "# No Integron found" in detail_lines:
        if any(not line.startswith("#") for line in detail_lines):
            raise ValueError("IntegronFinder no-integron marker accompanies tabular data")
        rows = []
    else:
        rows = _rows(integrons_path, {"ID_integron", "ID_replicon", "element", "pos_beg", "pos_end", "strand", "type"}, comments=True)
    summaries = _rows(summary_path, {"ID_replicon", "CALIN", "complete", "In0"}, comments=True)
    if not summaries:
        raise ValueError("IntegronFinder summary contains no assessed replicons")
    expected, observed, findings = {}, Counter(), []
    groups = {}
    for row in summaries:
        contig = _required(row["ID_replicon"], "IntegronFinder summary replicon")
        if contig in expected:
            raise ValueError("Duplicate IntegronFinder summary replicon")
        expected[contig] = {k: _integer(row[k], k, 0) for k in ("CALIN", "complete", "In0")}
    for row in rows:
        contig = _required(row["ID_replicon"], "IntegronFinder replicon")
        ident = _required(row["ID_integron"], "IntegronFinder element ID")
        kind = row["type"]
        if contig not in expected or kind not in {"CALIN", "complete", "In0"}:
            raise ValueError("IntegronFinder element has unsupported type or unassessed replicon")
        left, right = _interval(row["pos_beg"], row["pos_end"], "integron component")
        _strand(row["strand"])
        key = (contig, ident)
        if key in groups and groups[key]["kind"] != kind:
            raise ValueError("IntegronFinder components disagree on element type")
        group = groups.setdefault(key, {"kind": kind, "components": []})
        group["components"].append(dict(row, canonical_start=left, canonical_end=right))
    for (contig, ident), group in groups.items():
        components = group["components"]
        observed[(contig, group["kind"])] += 1
        finding = _finding("integronfinder", "integron", {"components": components},
                           contig=contig, gene=ident, family=group["kind"],
                           start=min(c["canonical_start"] for c in components),
                           end=max(c["canonical_end"] for c in components))
        finding["attributes"]["coordinate_semantics"] = "span_of_detected_components_not_verified_element_boundary"
        if any(c.get("considered_topology", "").lower() in {"circ", "circular"} for c in components):
            finding["start"] = None
            finding["end"] = None
            finding["attributes"].update({"coordinate_semantics": "circular_components_require_boundary_review",
                                          "component_intervals": [[c["canonical_start"], c["canonical_end"]] for c in components]})
        findings.append(finding)
    for contig, values in expected.items():
        for kind, count in values.items():
            if observed[(contig, kind)] != count:
                raise ValueError("IntegronFinder summary and component table counts disagree")
    return _result(findings, [integrons_path, summary_path], metrics={"integron_count": len(groups), "summary": summaries},
                   notes=["Integron component spans are predictions, not demonstrated mobile or resistance-bearing structures.",
                          "For circular replicons, canonical element boundaries remain unknown; inspect native component intervals."])


def parse_isescan(path: Path) -> dict:
    rows = _rows(path, {"seqID", "family", "isBegin", "isEnd", "strand"})
    findings = []
    for row in rows:
        left, right = _interval(row["isBegin"], row["isEnd"], "ISEScan")
        if "isLen" in row and _integer(row["isLen"], "ISEScan length") != right - left + 1:
            raise ValueError("ISEScan interval and length disagree")
        findings.append(_finding("isescan", "is", row,
                                 contig=_required(row["seqID"], "ISEScan sequence"),
                                 family=_required(row["family"], "ISEScan family"),
                                 start=left, end=right, strand=_strand(row["strand"])))
    return _result(findings, [path], metrics={"insertion_sequence_count": len(findings)},
                   notes=["A predicted insertion sequence near a locus does not prove transfer of that locus."])


def _isescan_native_zero(native: Path, assembly: Path, stdout: str, stdout_path: Path) -> dict:
    """Recognize ISEScan 1.7.3's intentional omission of result tables on zero IS.

    The later no-IS marker and the earlier all-contig no-significant-hit return
    are distinct native paths. Missing output alone is never a negative result.
    Both require completed searches and a nonempty generated proteome. The
    early return additionally requires exact input-contig coverage and binding
    of the searches to this attempt's proteome.
    """
    if not isinstance(stdout, str):
        raise ValueError("ISEScan output table missing; no verifiable completion transcript")
    lines = stdout.splitlines()
    markers = [(i, line[len("No IS element was found for "):]) for i, line in enumerate(lines)
               if line.startswith("No IS element was found for ")]
    expected = str(Path(assembly.parent.name) / assembly.name)
    no_hit_format = "isescan_1.7.3_terminal_marker"
    assessed_contigs = None
    if len(markers) == 1:
        index, literal = markers[0]
        try:
            names = ast.literal_eval(literal)
        except (ValueError, SyntaxError) as exc:
            raise ValueError("Malformed ISEScan zero-result marker") from exc
        if names != [expected]:
            raise ValueError("ISEScan zero-result marker does not identify the requested assembly")
    elif markers:
        raise ValueError("Ambiguous ISEScan zero-result markers")
    else:
        # v1.7.3 pred.py returns before creating tables when every contig has
        # no significant protein-search hit. Recognize this exact format only.
        prefix = "Warning: no significant hit with E-value <= 1e-10 found for "
        warnings = [(i, line[len(prefix):]) for i, line in enumerate(lines)
                    if line.startswith(prefix)]
        contigs = read_fasta(assembly)
        if any("," in cid for cid in contigs):
            raise ValueError("Ambiguous contig identifier in ISEScan no-hit transcript")
        if (len(warnings) != len(contigs) + 1
                or Counter(value for _, value in warnings[:-1]) != Counter(contigs.keys())
                or Counter(warnings[-1][1].split(",")) != Counter(contigs.keys())):
            raise ValueError("ISEScan missing table: no-hit transcript does not cover every input contig")
        starts = [i for i, line in enumerate(lines) if line.startswith("ISEScan starts at ")]
        begins = [i for i, line in enumerate(lines) if line.startswith("pred begins at ")]
        ends = [i for i, line in enumerate(lines) if line.startswith("End in pred ")]
        finishes = [i for i, line in enumerate(lines) if line.startswith("ISEScan ends at ")]
        if (not (len(starts) == len(begins) == len(ends) == len(finishes) == 1)
                or not (starts[0] < begins[0] < warnings[0][0]
                        <= warnings[-1][0] < ends[0] < finishes[0])
                or any(line.strip() for line in lines[finishes[0] + 1:])
                or any(re.match(r"(?:Traceback|ERROR\b|Error:)", line) for line in lines)):
            raise ValueError("ISEScan all-contig no-hit transcript is incomplete or conflicting")
        translation = f"Translating genome into proteome for {assembly} , return  0"
        if sum(line == translation for line in lines[:begins[0]]) != 1:
            raise ValueError("ISEScan no-hit transcript does not bind successful translation to this assembly")
        index = warnings[-1][0]
        assessed_contigs = sorted(contigs)
        no_hit_format = "isescan_1.7.3_all_contigs_no_significant_hits"
    tail = lines[index + 1:]
    if not any(line.startswith("End in pred ") for line in tail) or not any(
            line.startswith("ISEScan ends at ") for line in tail):
        raise ValueError("ISEScan zero-result transcript lacks successful terminal markers")
    proteins = native / "proteome" / assembly.parent.name / (assembly.name + ".faa")
    proteome = read_fasta(proteins)
    if not proteome:
        raise ValueError("ISEScan generated no proteins; cannot assert an assessed negative")
    if assessed_contigs is not None:
        translated_contigs = set()
        for protein in proteome:
            parts = protein.rsplit("_", 3)
            if (len(parts) != 4 or parts[3] not in {"+", "-"}
                    or not parts[1].isdigit() or not parts[2].isdigit()):
                raise ValueError("Unrecognized ISEScan generated-protein identifier")
            translated_contigs.add(parts[0])
        if translated_contigs != set(assessed_contigs):
            raise ValueError("ISEScan generated proteins do not cover the declared assessed contigs")
    searches = [native / "hmm" / assembly.parent.name / (name + "." + assembly.name + ".faa")
                for name in ("clusters.single.faa", "clusters.faa.hmm")]
    raw_hit_contigs = set()
    for path, program, query_name in zip(searches, ("phmmer", "hmmsearch"),
                                         ("clusters.single.faa", "clusters.faa.hmm")):
        table = _text(path)
        if not table.rstrip().endswith("# [ok]"):
            raise ValueError(f"ISEScan HMMER search is incomplete: {path}")
        if assessed_contigs is not None:
            targets = re.findall(r"^#\s*Target file:\s*(.*?)\s*$", table, re.MULTILINE)
            if len(targets) != 1 or Path(targets[0]).resolve() != proteins.resolve():
                raise ValueError(f"ISEScan HMMER search target does not match this proteome: {path}")
            programs = re.findall(r"^#\s*Program:\s*(.*?)\s*$", table, re.MULTILINE)
            queries = re.findall(r"^#\s*Query file:\s*(.*?)\s*$", table, re.MULTILINE)
            if (programs != [program] or len(queries) != 1
                    or Path(queries[0]).name != query_name):
                raise ValueError(f"ISEScan HMMER search provenance does not match its role: {path}")
            if not any(f" against {proteins} , output {path}" in line
                       and line.startswith(("Finish phmmer searching ", "Finish Profile HMM searching "))
                       for line in lines[:begins[0]]):
                raise ValueError(f"ISEScan no-hit transcript lacks completed search for {path}")
            for row in table.splitlines():
                if not row.strip() or row.startswith("#"):
                    continue
                fields = row.split()
                if len(fields) < 18 or fields[0] not in proteome:
                    raise ValueError(f"ISEScan no-hit HMMER rows are malformed or name unknown proteins: {path}")
                # ISEScan 1.7.3 process_tblout uses the best-domain E-value,
                # tblout column 8 (zero-based 7), in its filtering path.
                evalue = _number(fields[7], "ISEScan best-domain E-value", low=0)
                if evalue <= 1e-10:
                    raise ValueError(f"ISEScan no-hit transcript conflicts with a significant HMMER hit: {path}")
                raw_hit_contigs.add(fields[0].rsplit("_", 3)[0])
    if assessed_contigs is not None and raw_hit_contigs != set(assessed_contigs):
        raise ValueError("ISEScan raw search hits do not cover every no-hit warning contig")
    metrics = {"insertion_sequence_count": 0, "native_no_hit_format": no_hit_format}
    if assessed_contigs is not None:
        metrics.update(assessed_contigs=assessed_contigs,
                       assessed_contig_count=len(assessed_contigs),
                       completion_check="all_contig_no_hit_transcript_and_completed_searches")
    return _result([], [stdout_path, proteins, *searches],
                   metrics=metrics,
                   notes=["ISEScan reported a verified native no-hit result after completed protein searches; native zero-hit runs omit their result TSV.",
                          "A negative prediction does not establish absence of all insertion sequences."])


_ICE_CATEGORIES = ("Complete ICE", "Complete IME", "Conjugation module", "Mobilizable element",
                   "Partial ICE", "Other partial element")


def _icescreen_summary(path: Path, findings: list, expected_accessions: set[str] | None) -> dict:
    summaries, current = {}, None
    for line in _text(path).splitlines():
        header = re.fullmatch(r"#+ ICEscreen statistics for genome accession (\S+) #+", line.strip())
        if header:
            current = header.group(1)
            if current in summaries:
                raise ValueError("Duplicate ICEscreen summary genome accession")
            summaries[current] = {}
        elif current and ":\t" in line:
            label, number = line.rsplit(":\t", 1)
            if label in summaries[current]:
                raise ValueError("Duplicate ICEscreen summary metric")
            summaries[current][label] = _integer(number.strip(), "ICEscreen summary count", 0)
    if not summaries:
        raise ValueError("ICEscreen summary has no genome statistics")
    if expected_accessions is not None and set(summaries) != expected_accessions:
        raise ValueError("ICEscreen summary accessions do not match the GenBank input")
    observed = Counter()
    for f in findings:
        if f["contig"] not in summaries:
            raise ValueError("ICEscreen finding accession is absent from the summary")
        if f["family"] == "Unsure, to check":
            continue  # Native summary does not include a category count for these.
        categories = [name for name in _ICE_CATEGORIES if f["family"].startswith(name)]
        if len(categories) != 1:
            raise ValueError(f"Unsupported ICEscreen element category: {f['family']}")
        observed[(f["contig"], categories[0])] += 1
    for accession, values in summaries.items():
        for category in _ICE_CATEGORIES:
            matching = [v for label, v in values.items() if label.startswith(category + " (")]
            if len(matching) != 1 or matching[0] != observed[(accession, category)]:
                raise ValueError(f"ICEscreen summary and element table disagree: {accession}, {category}")
        if "Total SPs" not in values or "Number of segments with exclusively isolated SPs to manually verify" not in values:
            raise ValueError("ICEscreen summary is incomplete")
    return summaries


def _icescreen_input_accessions(path: Path) -> set[str]:
    """Read flatfile record identities and basic annotation presence without BioPython.

    This supplements the pipeline's sequence-consistency validation. It does not
    reinterpret CDS translations or mutate the input GenBank.
    """
    accessions, current, has_cds = set(), None, False
    for line in _text(path).splitlines():
        if line.startswith("LOCUS "):
            if current is not None:
                raise ValueError("ICEscreen input has an unterminated GenBank record")
            current = {"locus": line.split()[1], "origin": False}
        elif current is not None:
            if line.startswith("ACCESSION ") and len(line.split()) > 1:
                current["accession"] = line.split()[1]
            elif line.startswith("VERSION ") and len(line.split()) > 1:
                current["version"] = line.split()[1]
            elif line.startswith("     CDS "):
                has_cds = True
            elif line.startswith("ORIGIN"):
                current["origin"] = True
            elif line.strip() == "//":
                accession = current.get("version") or current.get("accession") or current["locus"]
                if not current["origin"] or accession in accessions:
                    raise ValueError("ICEscreen input needs unique GenBank records with ORIGIN sequence")
                accessions.add(accession)
                current = None
    if current is not None or not accessions or not has_cds:
        raise ValueError("ICEscreen input needs complete GenBank records and annotated CDS features")
    return accessions


def parse_icescreen(path: Path, summary_path: Path | None = None,
                    expected_accessions: set[str] | None = None) -> dict:
    """Parse ICEscreen 1.3 detected_ME.tsv; retain signature spans as raw data.

    Its reported extremal signature-protein positions are NOT element borders.
    Canonical element coordinates remain unknown, preventing false overlap calls.
    """
    rows = _rows(path, {"ICE_IME_id", "Genome_accession", "Category_of_element",
                        "Start_of_most_upstream_SP", "Stop_of_most_downstream_SP"})
    findings, seen = [], set()
    for row in rows:
        contig = _required(row["Genome_accession"], "ICEscreen genome")
        ident = _required(row["ICE_IME_id"], "ICEscreen element ID")
        if (contig, ident) in seen:
            raise ValueError("Duplicate ICEscreen element")
        seen.add((contig, ident))
        # These values remain explicitly native, not silently treated as GFF coordinates.
        left = _integer(row["Start_of_most_upstream_SP"], "ICEscreen signature start")
        right = _integer(row["Stop_of_most_downstream_SP"], "ICEscreen signature end")
        finding = _finding("icescreen", "ice", row, gene=ident, contig=contig,
                           family=_required(row["Category_of_element"], "ICEscreen category"))
        finding["attributes"].update({"coordinate_semantics": "unknown_element_boundaries",
                                      "signature_extremities": {"upstream_start": left, "downstream_stop": right,
                                                                 "coordinate_basis": "1-based inclusive"},
                                      "signature_proteins": row.get("List_SP_ordered_genomic_position", ""),
                                      "manual_review_required": any(
                                          row.get(key, "").strip() not in {"", "-", "NA", "None"}
                                          for key in ("Other_potential_SP_conj_module_need_manual_curation_and_review",
                                                      "Other_potential_integrase_need_manual_curation_and_review"))
                                      or bool(re.search(r"manual|review|unsure|to check", row.get("Comments_regarding_structure", ""), re.I))
                                      or row["Category_of_element"] == "Unsure, to check"})
        if right >= left:
            finding["attributes"]["signature_span"] = {"start": left, "end": right, "coordinate_basis": "1-based inclusive"}
        else:
            # Native bundled fixtures include non-monotonic extremities. Retain
            # them without inventing an origin-crossing interval or topology.
            finding["attributes"].update(manual_review_required=True,
                                           signature_order_review="non_monotonic_native_extremities")
        findings.append(finding)
    metrics = {"predicted_element_count": len(findings)}
    paths = [path]
    if summary_path is not None:
        metrics["summary"] = _icescreen_summary(summary_path, findings, expected_accessions)
        metrics["assessed_genome_accessions"] = sorted(metrics["summary"])
        paths.append(summary_path)
    return _result(findings, paths, metrics=metrics,
                   notes=["ICEscreen predicts ICE/IME structures in Bacillota; taxonomic scope is explicit.",
                          "Signature-protein extremities are retained in native attributes, not misreported as full element boundaries. Locus-overlap assessment requires curated boundaries."])


def _one(root: Path, pattern: str) -> Path:
    found = sorted(root.rglob(pattern))
    if len(found) != 1:
        raise ValueError(f"Expected exactly one {pattern} under {root}, found {len(found)}")
    return found[0]


def run(module: str, sample: dict, config: dict, outdir: Path, runner) -> dict:
    """Run one native engine, preserving native outputs and checked failures."""
    if module not in SUPPORTED_NATIVE:
        raise NotImplementedError(f"Native adapter unavailable for {module}; use the documented validated result import")
    outdir = Path(outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    native = outdir / "native"
    if native.exists():
        raise ValueError(f"Refusing stale native results at {native}; use a new run directory")
    threads = str(_integer(config.get("threads", 1), "threads"))
    assembly = str(sample["assembly"])
    database = config.get("database")
    defaults = {"antismash": "antismash", "genomad": "genomad", "mobsuite": "mob_recon",
                "integronfinder": "integron_finder", "isescan": "isescan.py", "icescreen": "icescreen"}
    exe = str(config.get("executable") or defaults[module])
    if module == "antismash":
        argv = [exe, assembly, "--output-dir", str(native), "--output-basename", "basis",
                "--cpus", threads, "--taxon", "bacteria", "--abort-on-invalid-records",
                "--minlength", "1"]
        if sample.get("gff"):
            argv += ["--genefinding-gff3", str(sample["gff"])]
        else:
            argv += ["--genefinding-tool", "prodigal"]
        if database:
            argv += ["--databases", str(database)]
        runner.run(argv, cwd=outdir)
        result = parse_antismash(native / "basis.json")
    elif module == "genomad":
        if not database:
            raise ValueError("geNomad requires an explicitly prepared database directory")
        argv = [exe, "end-to-end", "--threads", threads]
        if "splits" in config:
            argv += ["--splits", str(_integer(config["splits"], "genomad.splits"))]
        argv += [assembly, str(native), str(database)]
        runner.run(argv, cwd=outdir)
        result = parse_genomad(_one(native, "*_virus_summary.tsv"), _one(native, "*_plasmid_summary.tsv"))
    elif module == "mobsuite":
        if not database:
            raise ValueError("MOB-suite requires an explicitly prepared database directory")
        runner.run([exe, "-i", assembly, "-o", str(native), "-n", threads,
                    "--database_directory", str(database), "-s", str(sample["sample_id"])], cwd=outdir)
        result = parse_mobsuite(native / "contig_report.txt")
    elif module == "integronfinder":
        topology = config.get("topology", "linear")
        if topology not in {"linear", "circular"}:
            raise ValueError("integronfinder.topology must be linear or circular")
        assembly_contigs = set(read_fasta(assembly))
        argv = [exe, assembly, "--outdir", str(native), "--cpu", threads,
                "--circ" if topology == "circular" else "--lin"]
        if config.get("local_max", True):
            argv.append("--local-max")
        runner.run(argv, cwd=outdir)
        result = parse_integronfinder(_one(native, "*.integrons"), _one(native, "*.summary"))
        assessed_contigs = {row["ID_replicon"] for row in result["metrics"]["summary"]}
        if assessed_contigs != assembly_contigs:
            raise ValueError("IntegronFinder summary replicons do not match assembly: "
                             f"missing={sorted(assembly_contigs - assessed_contigs)}, "
                             f"unexpected={sorted(assessed_contigs - assembly_contigs)}")
    elif module == "isescan":
        completed = runner.run([exe, "--seqfile", assembly, "--output", str(native), "--nthread", threads], cwd=outdir)
        tables = sorted(native.rglob(Path(assembly).name + ".tsv"))
        if len(tables) == 1:
            result = parse_isescan(tables[0])
        elif tables:
            raise ValueError("Ambiguous ISEScan native result tables")
        else:
            stdout = getattr(completed, "stdout", None)
            transcript = outdir / "isescan_stdout.txt"
            if isinstance(stdout, str):
                transcript.write_text(stdout, encoding="utf-8")
            result = _isescan_native_zero(native, Path(assembly), stdout, transcript)
    else:
        genbank = sample.get("genbank")
        if not genbank or not Path(genbank).is_file():
            raise ValueError("ICEscreen requires sample.genbank: annotated GenBank with ORIGIN sequence")
        phylum = config.get("phylum")
        if phylum != "bacillota":
            raise ValueError("ICEscreen native v0.1 requires explicit phylum: bacillota; other taxa are unsupported")
        input_accessions = _icescreen_input_accessions(Path(genbank))
        inputs = outdir / "genbank_input"
        inputs.mkdir(exist_ok=False)
        shutil.copy2(genbank, inputs / "basis.gbk")
        # ICEscreen 1.3.3 requires its output directory to exist. Create it
        # exclusively here, after the shared stale-results guard above.
        native.mkdir(exist_ok=False)
        # Prevent ICEscreen from switching to a globally named environment: the
        # configured command_prefix already selects the verified tool environment.
        runner.run([exe, "--galaxy", "-g", str(inputs), "-o", str(native), "-j", threads, "--phylum", phylum], cwd=outdir)
        summary = _one(native, "*_detected_ME.summary")
        result = parse_icescreen(_one(native, "*_detected_ME.tsv"), summary, input_accessions)
    result["artifacts"].append(str(native))
    result["notes"].append("Native interface checked against upstream documentation; live-engine validation is required for this installation.")
    return result
