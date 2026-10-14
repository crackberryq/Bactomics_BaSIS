"""Native AMR and protein-homology adapters; no organism safety classification.

All percentages in normalized findings and configuration use the range 0..100.
Native tables are retained, including exploratory rows and model metadata.
"""
from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from pathlib import Path


class OutputError(ValueError):
    """A native output is missing, malformed, or not a supported schema."""


NULLS = {"", "-", ".", "na", "n/a", "none", "null"}
DIAMOND_COLUMNS = (
    "qseqid", "sseqid", "pident", "length", "qlen", "slen", "qstart",
    "qend", "sstart", "send", "evalue", "bitscore", "qcovhsp", "scovhsp", "stitle",
)


def _key(value):
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _text(value):
    return None if value is None or value.strip().lower() in NULLS else value.strip()


def _required(value, label):
    result = _text(value)
    if result is None:
        raise OutputError(f"Missing required value: {label}")
    return result


def _number(value, label, *, minimum=0, maximum=None, integer=False, nullable=True):
    value = _text(value)
    if value is None:
        if nullable:
            return None
        raise OutputError(f"Missing numeric value: {label}")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise OutputError(f"Invalid number in {label}: {value!r}") from error
    if not math.isfinite(number) or number < minimum or (maximum is not None and number > maximum):
        raise OutputError(f"Out-of-range number in {label}: {value!r}")
    if integer:
        if number != int(number):
            raise OutputError(f"Expected integer in {label}: {value!r}")
        return int(number)
    return number


def _table(path, required_groups):
    path = Path(path)
    if not path.is_file():
        raise OutputError(f"Native output is missing or not a file: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t", strict=True)
        try:
            header = next(reader)
        except StopIteration as error:
            raise OutputError(f"Empty output has no valid header: {path}") from error
        names = [_key(column) for column in header]
        if not all(names) or len(names) != len(set(names)):
            raise OutputError(f"Empty or duplicate columns: {path}")
        for alternatives in required_groups:
            if not any(_key(item) in names for item in alternatives):
                raise OutputError(f"Missing required column {alternatives!r}: {path}")
        rows = []
        for line, values in enumerate(reader, 2):
            if not values or not any(value.strip() for value in values):
                raise OutputError(f"Blank result row {line}: {path}")
            if len(values) != len(header):
                raise OutputError(f"Wrong field count at row {line}: {path}")
            rows.append((dict(zip(names, values)), dict(zip(header, values))))
    return rows


def _get(row, *names):
    for name in names:
        if _key(name) in row:
            return row[_key(name)]
    return None


def _coordinates(row, start_name="Start", stop_name="Stop", strand_name="Strand"):
    start = _number(_get(row, start_name), start_name, minimum=1, integer=True)
    end = _number(_get(row, stop_name), stop_name, minimum=1, integer=True)
    if (start is None) != (end is None):
        raise OutputError("Coordinate pair is incomplete")
    native_strand = _get(row, strand_name)
    strand = native_strand.strip() if native_strand and native_strand.strip() in {"+", "-"} else _text(native_strand)
    if strand not in {None, "+", "-"}:
        raise OutputError(f"Unsupported strand: {strand!r}")
    return (min(start, end), max(start, end), strand) if start is not None else (None, None, strand)


def parse_amrfinder(path):
    """Parse combined-mode AMRFinderPlus 3.x/4.x TSV, including plus categories."""
    required = [(name,) for name in (
        "Protein id", "Contig id", "Start", "Stop", "Strand", "Scope", "Method",
        "% Coverage of reference", "% Identity to reference",
    )] + [("Element symbol", "Gene symbol"), ("Element name", "Sequence name", "Protein name"),
         ("Type", "Element type"), ("Subtype", "Element subtype")]
    findings = []
    for row, raw in _table(path, required):
        category_native = _required(_get(row, "Type", "Element type"), "Type").upper()
        category = {"AMR": "amr", "VIRULENCE": "virulence", "STRESS": "stress"}.get(category_native)
        if category is None:
            raise OutputError(f"Unknown AMRFinderPlus element type: {category_native}")
        gene = _required(_get(row, "Element symbol", "Gene symbol"), "Element symbol")
        method = _required(_get(row, "Method"), "Method")
        scope = _required(_get(row, "Scope"), "Scope").lower()
        if scope not in {"core", "plus"}:
            raise OutputError(f"Unknown AMRFinderPlus scope: {scope}")
        subtype = _required(_get(row, "Subtype", "Element subtype"), "Subtype")
        start, end, strand = _coordinates(row)
        contig = _text(_get(row, "Contig id"))
        if start is not None and contig is None:
            raise OutputError("AMRFinderPlus coordinates have no contig identifier")
        flags = []
        if "PARTIAL" in method.upper():
            flags.append("partial_sequence")
        if "INTERNAL_STOP" in method.upper():
            flags.append("internal_stop")
        if method.upper() == "HMM":
            flags.append("family_hmm_only")
        if subtype.upper() == "POINT_DISRUPT":
            flags.append("putative_disruptive_variant")
        findings.append({
            "tool": "amrfinder", "category": category, "gene": gene,
            "family": _text(_get(row, "HMM description")) or gene,
            "protein_id": _text(_get(row, "Protein id")), "contig": contig,
            "start": start, "end": end, "strand": strand,
            "reference": _text(_get(row, "Closest reference accession", "Accession of closest sequence", "Accession of closest protein")),
            "identity": _number(_get(row, "% Identity to reference"), "identity", maximum=100),
            "coverage": _number(_get(row, "% Coverage of reference"), "coverage", maximum=100),
            "evidence_level": "exploratory" if "putative_disruptive_variant" in flags else "curated_model",
            "interpretation": "native_gene_or_variant_call_requires_context_review",
            "attributes": {"raw": raw, "native_method": method, "scope": scope,
                           "subtype": subtype, "flags": flags,
                           "coverage_basis": "native_percent_reference_coverage",
                           "phenotypic_resistance_inferred": False},
        })
    return findings


def parse_rgi(path, input_type="protein"):
    """Parse native RGI tabular output; length ratio is NOT alignment coverage."""
    if input_type not in {"protein", "contig"}:
        raise ValueError("RGI input_type must be protein or contig")
    required = [(name,) for name in (
        "ORF_ID", "Contig", "Start", "Stop", "Orientation", "Cut_Off", "Best_Hit_ARO",
        "Best_Identities", "ARO", "Model_type", "AMR Gene Family",
    )]
    findings = []
    for row, raw in _table(path, required):
        orf = _required(_get(row, "ORF_ID"), "ORF_ID")
        cutoff = _required(_get(row, "Cut_Off"), "Cut_Off").lower()
        if cutoff not in {"perfect", "strict", "loose"}:
            raise OutputError(f"Unknown RGI Cut_Off: {cutoff}")
        nudged = (_text(_get(row, "Nudged")) or "false").lower()
        if nudged not in {"true", "false"}:
            raise OutputError(f"Invalid RGI Nudged value: {nudged}")
        exploratory = cutoff == "loose" or nudged == "true"
        start, end, strand = _coordinates(row, strand_name="Orientation")
        contig = _text(_get(row, "Contig"))
        if input_type == "protein":
            # Protein mode's ORF_ID is the source protein identifier; do not guess
            # genomic coordinates from numeric suffixes or Prodigal-like headers.
            protein_id = orf.split()[0]
            contig, start, end, strand = None, None, None, None
        else:
            protein_id = None  # RGI generated identifiers need not match annotation.
            if contig is None or start is None:
                raise OutputError("RGI contig result has no genomic location")
        coverage = None
        hit_start = _number(_get(row, "Hit_Start"), "Hit_Start", minimum=1, integer=True)
        hit_end = _number(_get(row, "Hit_End"), "Hit_End", minimum=1, integer=True)
        if (hit_start is None) != (hit_end is None):
            raise OutputError("RGI reference alignment coordinates are incomplete")
        reference_seq = _text(_get(row, "CARD_Protein_Sequence"))
        if reference_seq and hit_start is not None:
            reference_length = len(reference_seq.rstrip("*"))
            if not reference_length or max(hit_start, hit_end) > reference_length:
                raise OutputError("RGI reference alignment is outside CARD protein sequence")
            coverage = 100.0 * (abs(hit_end - hit_start) + 1) / reference_length
        length_ratio = _number(_get(row, "Percentage Length of Reference Sequence"), "reference length ratio")
        accession = _required(_get(row, "ARO"), "ARO")
        predicted_protein = _text(_get(row, "Predicted_Protein"))
        if predicted_protein is not None:
            predicted_protein = predicted_protein.upper()
            if not re.fullmatch(r"[ABCDEFGHIKLMNPQRSTVWXYZUOJ*]+", predicted_protein):
                raise OutputError("RGI Predicted_Protein contains invalid protein characters")
        findings.append({
            "tool": "rgi", "category": "amr", "gene": _required(_get(row, "Best_Hit_ARO"), "Best_Hit_ARO"),
            "family": _text(_get(row, "AMR Gene Family")), "protein_id": protein_id,
            "contig": contig, "start": start, "end": end, "strand": strand,
            "reference": accession if accession.startswith("ARO:") else "ARO:" + accession,
            "identity": _number(_get(row, "Best_Identities"), "Best_Identities", maximum=100, nullable=False),
            "coverage": coverage,
            "evidence_level": "exploratory" if exploratory else "curated_model",
            "interpretation": "exploratory_homologue" if exploratory else "native_model_call_requires_context_review",
            "attributes": {"raw": raw, "cutoff": cutoff, "nudged": nudged == "true",
                           "model_type": _required(_get(row, "Model_type"), "Model_type"),
                           "rgi_orf_id": orf, "input_type": input_type,
                           "protein_sequence": predicted_protein,
                           "protein_sequence_source": "native_RGI_Predicted_Protein" if predicted_protein else None,
                           "reference_length_ratio_percent": length_ratio,
                           "coverage_basis": "reference_HSP_span" if coverage is not None else None,
                           "phenotypic_resistance_inferred": False},
        })
    return findings


def parse_diamond(path, module, min_identity=80.0, min_coverage=70.0, min_query_coverage=0.0):
    """Parse explicitly ordered, headerless DIAMOND output (one HSP/row).

    A genuinely empty file is native no-hit output. The run adapter accepts it
    only after Runner verifies successful process exit and freshness.
    """
    if module not in {"vfdb", "bacmet"}:
        raise ValueError("DIAMOND module must be vfdb or bacmet")
    thresholds = {
        "min_identity": _number(str(min_identity), "min_identity", maximum=100, nullable=False),
        "min_coverage": _number(str(min_coverage), "min_coverage", maximum=100, nullable=False),
        "min_query_coverage": _number(str(min_query_coverage), "min_query_coverage", maximum=100, nullable=False),
    }
    path = Path(path)
    if not path.is_file():
        raise OutputError(f"Missing DIAMOND output: {path}")
    findings = []
    with path.open(encoding="utf-8", newline="") as handle:
        for line, values in enumerate(csv.reader(handle, delimiter="\t", strict=True), 1):
            if len(values) != len(DIAMOND_COLUMNS):
                raise OutputError(f"Wrong DIAMOND field count at row {line}: {path}")
            raw = dict(zip(DIAMOND_COLUMNS, values))
            query = _required(raw["qseqid"], "qseqid")
            reference = _required(raw["sseqid"], "sseqid")
            identity = _number(raw["pident"], "pident", maximum=100, nullable=False)
            coverage = _number(raw["scovhsp"], "scovhsp", maximum=100, nullable=False)
            qcoverage = _number(raw["qcovhsp"], "qcovhsp", maximum=100, nullable=False)
            numbers = {key: _number(raw[key], key, minimum=1, integer=True, nullable=False)
                       for key in ("length", "qlen", "slen", "qstart", "qend", "sstart", "send")}
            for prefix in ("q", "s"):
                if max(numbers[prefix + "start"], numbers[prefix + "end"]) > numbers[prefix + "len"]:
                    raise OutputError(f"DIAMOND {prefix} alignment coordinates exceed sequence length")
            evalue = _number(raw["evalue"], "evalue", nullable=False)
            bitscore = _number(raw["bitscore"], "bitscore", nullable=False)
            passes = (identity >= thresholds["min_identity"] and coverage >= thresholds["min_coverage"]
                      and qcoverage >= thresholds["min_query_coverage"])
            gene = reference
            source_attributes = {}
            if module == "bacmet":
                from ..optional_bacmet import parse_bacmet_header
                try:
                    annotation = parse_bacmet_header(raw["stitle"])
                except ValueError:
                    source_attributes["reference_header_status"] = "unrecognized_format_retained_as_recorded"
                else:
                    if annotation["reference_id"] != reference:
                        # Never join BacMet annotations by their numeric BAC label:
                        # five such labels are reused for different source records.
                        source_attributes["reference_header_status"] = "reference_title_mismatch_retained_as_recorded"
                    else:
                        source_attributes["reference_header_status"] = "parsed_exact_reference_id"
                        source_attributes["source_reference_annotation"] = annotation
                        gene = annotation["gene"] or reference
            findings.append({
                "tool": module, "category": "virulence" if module == "vfdb" else "stress",
                "gene": gene, "family": None, "protein_id": query,
                "contig": None, "start": None, "end": None, "strand": None,
                "reference": reference, "identity": identity, "coverage": coverage,
                "evidence_level": "homology" if passes else "exploratory",
                "interpretation": "homology_requires_functional_review" if passes else "below_reporting_threshold",
                "attributes": {"raw": raw, "passes_threshold": passes, "thresholds": thresholds.copy(),
                               "query_coverage": qcoverage, "coverage_basis": "native_subject_coverage_per_HSP",
                               "evalue": evalue, "bitscore": bitscore, "subject_title": raw["stitle"],
                               "function_confirmed": False, **source_attributes},
            })
    return findings


def _fresh(paths):
    for path in paths:
        if path.exists():
            raise RuntimeError(f"Refusing to reuse existing native output: {path}")


def _boolean(config, name, default):
    value = config.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a YAML/JSON boolean, not a string")
    return value


def _database(config, *, directory=False):
    value = config.get("database")
    if not value:
        raise ValueError("An explicit versioned database path is required")
    path = Path(value).expanduser().resolve()
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError(f"Database {'directory' if directory else 'file'} is missing: {path}")
    return path


def _result(findings, artifacts, config, notes):
    substantive = [item for item in findings if item["evidence_level"] != "exploratory"]
    interpretation = "detected" if substantive else "inconclusive" if findings else "not_detected"
    return {
        "findings": findings,
        "metrics": {"native_rows": len(findings), "non_exploratory_findings": len(substantive),
                    "exploratory_findings": len(findings) - len(substantive),
                    "categories": dict(Counter(item["category"] for item in findings)),
                    "configured_database_version": config.get("database_version"),
                    "identity_units": "percent", "coverage_units": "percent"},
        "artifacts": [str(path) for path in artifacts], "notes": notes,
        "interpretation": interpretation,
    }


def run(module, sample, config, outdir, runner):
    """Execute one native module using the shared checked Runner."""
    outdir = Path(outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    threads = config.get("threads", 1)
    if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
        raise ValueError("threads must be a positive integer")
    if module == "amrfinder":
        database = _database(config, directory=True)
        output = outdir / "amrfinder.tsv"
        _fresh([output])
        for key in ("assembly", "proteins", "gff"):
            if not sample.get(key):
                raise ValueError(f"AMRFinderPlus combined mode requires {key}")
        argv = [config.get("executable", "amrfinder"), "-n", str(sample["assembly"]),
                "-p", str(sample["proteins"]), "-g", str(sample["gff"]), "--plus",
                "--database", str(database), "--threads", str(threads), "-o", str(output)]
        if config.get("annotation_format"):
            argv += ["--annotation_format", str(config["annotation_format"])]
        if sample.get("amrfinder_organism"):
            argv += ["--organism", str(sample["amrfinder_organism"])]
        runner.run(argv, cwd=outdir)
        return _result(parse_amrfinder(output), [output], config, [
            "Combined nucleotide/protein/GFF analysis uses native curated detection thresholds.",
            "No organism-specific point-mutation coverage is assumed unless a supported organism was explicitly configured.",
            "AMR, virulence and stress annotations are separate; gene presence does not establish phenotype.",
        ])
    if module == "rgi":
        database = _database(config)
        with database.open(encoding="utf-8") as handle:
            card = json.load(handle)
        if not isinstance(card, dict) or not card:
            raise ValueError("RGI database must be a nonempty CARD JSON object")
        prefix = outdir / "rgi"
        output, json_output = Path(str(prefix) + ".txt"), Path(str(prefix) + ".json")
        _fresh([output, json_output, outdir / "localDB"])
        executable = config.get("executable", "rgi")
        input_type = config.get("input_type", "protein")
        if input_type not in {"protein", "contig"}:
            raise ValueError("RGI input_type must be protein or contig")
        aligner = str(config.get("alignment_tool", "BLAST")).upper()
        if aligner not in {"BLAST", "DIAMOND"}:
            raise ValueError("RGI alignment_tool must be BLAST or DIAMOND")
        include_loose = _boolean(config, "include_loose", True)
        low_quality = _boolean(config, "low_quality", False)
        runner.run([executable, "load", "--card_json", str(database), "--local"], cwd=outdir)
        argv = [executable, "main", "--input_sequence", str(sample["proteins" if input_type == "protein" else "assembly"]),
                "--output_file", str(prefix), "--input_type", input_type, "--alignment_tool", aligner,
                "--threads", str(threads), "--local"]
        if include_loose:
            argv.append("--include_loose")
        if low_quality:
            argv.append("--low_quality")
        runner.run(argv, cwd=outdir)
        if not json_output.is_file():
            raise OutputError(f"RGI native JSON output is missing: {json_output}")
        with json_output.open(encoding="utf-8") as handle:
            if not isinstance(json.load(handle), dict):
                raise OutputError("RGI native JSON output must be an object")
        return _result(parse_rgi(output, input_type=input_type), [output, json_output], config, [
            "CARD loaded into the run-local RGI database; global installations are not modified.",
            "Perfect/Strict are native model calls; Loose or nudged calls remain exploratory. No nudge option is used.",
            "Protein input excludes rRNA mutation models." if input_type == "protein" else "RGI contig-mode ORFs may differ from supplied annotation.",
            "RGI reference-length ratio is retained separately and is not treated as alignment coverage.",
        ])
    if module in {"vfdb", "bacmet"}:
        database = _database(config)
        output = outdir / (module + ".diamond.tsv")
        _fresh([output])
        # Validate thresholds even on an empty native result; do not send these
        # as search filters, so weaker evidence remains inspectable.
        thresholds = {name: config.get(name, default) for name, default in (
            ("min_identity", 80.0), ("min_coverage", 70.0), ("min_query_coverage", 0.0))}
        for name, value in thresholds.items():
            _number(str(value), name, maximum=100, nullable=False)
        evalue = _number(str(config.get("evalue", 1e-5)), "evalue", nullable=False)
        targets = _number(str(config.get("max_target_seqs", 25)), "max_target_seqs", integer=True, nullable=False)
        sensitivity = config.get("sensitivity", "sensitive")
        if sensitivity not in {"fast", "mid-sensitive", "sensitive", "more-sensitive", "very-sensitive", "ultra-sensitive"}:
            raise ValueError("Unsupported DIAMOND sensitivity")
        argv = [config.get("executable", "diamond"), "blastp", "--query", str(sample["proteins"]),
                "--db", str(database), "--out", str(output), "--threads", str(threads),
                "--evalue", str(evalue), "--max-target-seqs", str(targets), "--max-hsps", "1",
                "--" + sensitivity, "--outfmt", "6", *DIAMOND_COLUMNS]
        if module == "bacmet":
            argv.insert(argv.index("--outfmt"), "--no-parse-seqids")
        runner.run(argv, cwd=outdir)
        return _result(parse_diamond(output, module, **thresholds), [output], config, [
            "Protein homology screening; database association alone does not establish virulence, stress resistance or hazard.",
            "Coverage is subject coverage per HSP; separate query coverage and all native rows are retained.",
            "Below-threshold alignments remain exploratory; candidate/reference search is limited by configured search parameters.",
        ])
    raise ValueError(f"Unsupported AMR/homology module: {module}")
