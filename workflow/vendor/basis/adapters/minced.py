"""MinCED 0.4.2 CRISPR-array adapter; Cas genes/function are not assessed.

The upstream CRISPRFinder writer emits one GFF header per input sequence,
including sequences without arrays. It can swallow exceptions, so an exit code
alone is insufficient: the adapter checks log errors and all per-contig blocks.
Source: https://github.com/ctSkennerton/minced/blob/master/CRISPRFinder.java
"""
from __future__ import annotations

import re
from pathlib import Path

from ..inputs import read_fasta


VERSION = "0.4.2"
_BASES = re.compile(r"[ACGTRYSWKMBDHVN]+", re.I)
_ERROR = re.compile(r"(?:\bException\b|[\w.$]+Exception\b|[\w.$]+Error\b|"
                    r"--Error writing to file--|Error processing input file|"
                    r"does not seem to be FASTA-formatted|File name does not exist)", re.I)


def _read(path: Path, *, empty: bool = False) -> str:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Required MinCED output missing: {path}")
    value = path.read_text(encoding="utf-8-sig")
    if not empty and not value.strip():
        raise ValueError(f"Required MinCED output empty: {path}")
    if value.lstrip().lower().startswith(("<html", "<!doctype html")):
        raise ValueError(f"HTML is not MinCED output: {path}")
    return value


def _integer(value: str, label: str, minimum: int = 1) -> int:
    if not re.fullmatch(r"[0-9]+", value) or int(value) < minimum:
        raise ValueError(f"Invalid MinCED {label}: {value!r}")
    return int(value)


def parse_minced(gff_path: Path, text_path: Path, assembly_path: Path) -> dict:
    """Parse the exact two-file output from ``minced -gffFull``.

    GFF coordinates are already 1-based inclusive. Header-only GFF is accepted
    only when it contains a completed writer block for every input contig, and
    the text report agrees. The caller must also check process/log success.
    """
    sequences = read_fasta(assembly_path)
    contigs = list(sequences)
    gff = _read(gff_path)
    report = _read(text_path, empty=True)
    arrays, children, seen_ids = [], {}, set()
    block = -1
    for line_number, line in enumerate(gff.splitlines(), 1):
        if not line.strip():
            continue
        if line == "##gff-version 3":
            block += 1
            if block >= len(contigs):
                raise ValueError("MinCED has more contig output blocks than the assembly")
            continue
        if line.startswith("#"):
            raise ValueError(f"Unsupported MinCED GFF directive at line {line_number}")
        cells = line.split("\t")
        if len(cells) != 9 or block < 0:
            raise ValueError(f"Malformed MinCED GFF at line {line_number}")
        contig, source, feature, left, right, score, strand, phase, raw_attrs = cells
        if contig != contigs[block]:
            raise ValueError("MinCED contig output order/identity does not match input assembly")
        if source != f"minced:{VERSION}" or strand != "." or phase != ".":
            raise ValueError("Unsupported MinCED version, strand or phase in GFF")
        start, end = _integer(left, "start"), _integer(right, "end")
        if end < start or end > len(sequences[contig]):
            raise ValueError("MinCED coordinates exceed or contradict the assembly")
        attrs = {}
        for item in raw_attrs.split(";"):
            if item.count("=") != 1:
                raise ValueError("Malformed MinCED GFF attribute")
            key, value = item.split("=", 1)
            if not key or not value or key in attrs:
                raise ValueError("Missing or duplicate MinCED GFF attribute")
            attrs[key] = value
        identifier = attrs.get("ID")
        if not identifier or identifier in seen_ids:
            raise ValueError("Missing or duplicate MinCED feature ID")
        seen_ids.add(identifier)
        native = {"contig": contig, "source": source, "feature": feature,
                  "start": start, "end": end, "score": score,
                  "strand": strand, "phase": phase, "attributes": attrs}
        if feature == "repeat_region":
            if identifier != f"CRISPR{len(arrays) + 1}":
                raise ValueError("Unexpected MinCED array numbering")
            if attrs.get("rpt_family") != "CRISPR" or attrs.get("rpt_type") != "direct":
                raise ValueError("MinCED array lacks CRISPR/direct-repeat annotation")
            repeat = attrs.get("rpt_unit_seq", "")
            if not _BASES.fullmatch(repeat):
                raise ValueError("Invalid MinCED repeat sequence")
            native["repeat_count"] = _integer(score, "repeat count", 2)
            arrays.append(native)
            children[identifier] = []
        elif feature == "repeat_unit":
            parent = attrs.get("Parent")
            if not arrays or parent != arrays[-1]["attributes"]["ID"] or score != "1":
                raise ValueError("MinCED repeat has no preceding parent array or invalid score")
            children[parent].append(native)
        else:
            raise ValueError(f"Unsupported MinCED feature type: {feature}")
    if block + 1 != len(contigs):
        raise ValueError("MinCED output is incomplete: missing per-contig GFF blocks")

    # MinCED's text file is legitimately empty when all contigs have zero calls.
    ranges = re.findall(r"^CRISPR ([0-9]+)\s+Range: ([0-9]+) - ([0-9]+)$", report, re.M)
    counts = re.findall(r"^Repeats: ([0-9]+)\tAverage Length: [0-9]+\t\tAverage Length: [0-9]+$", report, re.M)
    if len(ranges) != len(arrays) or len(counts) != len(arrays):
        raise ValueError("MinCED text/GFF array counts disagree")
    if not arrays and report.strip():
        raise ValueError("Unexpected nonempty MinCED text report with zero GFF arrays")

    findings = []
    for index, array in enumerate(arrays):
        attrs = array["attributes"]
        repeats = children[attrs["ID"]]
        expected = (str(index + 1), str(array["start"]), str(array["end"]))
        if ranges[index] != expected or int(counts[index]) != array["repeat_count"]:
            raise ValueError("MinCED text/GFF intervals or repeat counts disagree")
        if len(repeats) != array["repeat_count"]:
            raise ValueError("MinCED array is missing repeat-unit features")
        last_end = array["start"] - 1
        repeat_length = len(attrs["rpt_unit_seq"])
        for repeat in repeats:
            if (repeat["contig"] != array["contig"] or repeat["start"] <= last_end
                    or repeat["start"] < array["start"] or repeat["end"] > array["end"]
                    or repeat["end"] - repeat["start"] + 1 != repeat_length):
                raise ValueError("MinCED repeat coordinates contradict the parent array")
            last_end = repeat["end"]
        if repeats[0]["start"] != array["start"] or repeats[-1]["end"] != array["end"]:
            raise ValueError("MinCED array bounds do not match its terminal repeats")
        second = repeats[1]
        actual_repeat = sequences[array["contig"]][second["start"] - 1:second["end"]]
        if actual_repeat != attrs["rpt_unit_seq"].upper():
            raise ValueError("MinCED representative repeat does not match the input assembly")
        findings.append({
            "tool": "minced", "category": "crispr", "gene": attrs["ID"],
            "family": "predicted CRISPR array", "protein_id": None,
            "contig": array["contig"], "start": array["start"], "end": array["end"],
            "strand": None, "reference": None, "identity": None, "coverage": None,
            "evidence_level": "prediction", "interpretation": "predicted_context",
            "attributes": {"native": array, "repeat_count": array["repeat_count"],
                           "spacer_count": array["repeat_count"] - 1,
                           "repeat_sequence": attrs["rpt_unit_seq"].upper(),
                           "repeat_intervals": [{"start": r["start"], "end": r["end"]} for r in repeats],
                           "cas_assessment": "not_assessed",
                           "coordinate_basis": "1-based inclusive"}})
    return {
        "findings": findings, "artifacts": [str(gff_path), str(text_path)],
        "metrics": {"array_count": len(findings), "assessed_contigs": contigs,
                    "assessed_contig_count": len(contigs), "gff_contig_blocks": block + 1,
                    "repeat_count": sum(a["repeat_count"] for a in arrays),
                    "spacer_count": sum(a["repeat_count"] - 1 for a in arrays),
                    "minced_version": VERSION, "cas_assessment": "not_assessed"},
        "interpretation": "detected" if findings else "not_detected",
        "notes": ["MinCED predicts CRISPR arrays only; Cas genes, CRISPR-Cas subtype and activity were not assessed.",
                  "CRISPR predictions are genomic context and do not establish organism safety or transfer resistance."]}


def run(module: str, sample: dict, config: dict, outdir: Path, runner) -> dict:
    if module != "minced":
        raise ValueError(f"Unsupported MinCED adapter module: {module}")
    assembly = Path(sample["assembly"]).resolve()
    read_fasta(assembly)  # Fail before native execution for empty/duplicate input.
    executable = config.get("executable", "minced")
    version = runner.run([executable, "--version"], cwd=outdir)
    if not re.search(r"(?m)^minced 0\.4\.2\s*$", version.stdout):
        raise ValueError("This native MinCED adapter requires verified version 0.4.2")
    native = Path(outdir) / "native"
    native.mkdir(exist_ok=False)
    text_path, gff_path = native / "arrays.txt", native / "arrays.gff"
    # Upstream catches exceptions without reliably setting a nonzero exit code.
    logfile = Path(runner.logfile)
    offset = logfile.stat().st_size if logfile.exists() else 0
    result = runner.run([executable, "-gffFull", str(assembly), str(text_path), str(gff_path)], cwd=outdir)
    if result.returncode != 0:
        raise ValueError("MinCED native execution failed")
    with logfile.open("rb") as handle:
        handle.seek(offset)
        tail = handle.read().decode("utf-8", errors="replace")
    diagnostics = "\n".join(line for line in tail.splitlines() if not re.match(r"^\[\d{4}-", line))
    if _ERROR.search(diagnostics) or _ERROR.search(result.stdout):
        raise ValueError(f"MinCED reported an error despite successful exit; see {logfile}")
    payload = parse_minced(gff_path, text_path, assembly)
    payload["artifacts"].append(str(native))
    payload["metrics"]["completion_check"] = "exit_log_and_all_contig_blocks_verified"
    return payload
