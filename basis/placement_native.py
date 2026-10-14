"""Auditable, reference-only trees and EPA-ng placement; no functional probability.

Reference trees are independent of the query.  The cache is keyed by normalized
reference content, model settings, and the resolved native tool identities, and
every cached artifact is rehashed before use.  Placement likelihood weight is
conditional on this panel, alignment and model, not a probability of resistance.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

from .deep_alignment_review import alignment_metrics, normalize_alignment_sequence
from .deep_trees import parse_splits
from .references import read_fasta, write_fasta


POLICY = "reference_tree_placement_v1"
CACHE_SCHEMA = "basis-placement-reference-cache-v1"
INFERENCE_SCOPE = (
    "Placement likelihood weight conditional on the reviewed reference panel, "
    "reference alignment, reference tree and evolutionary model; not a calibrated "
    "probability of AMR function, isolate resistance or organism safety."
)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1048576), b""):
            result.update(block)
    return result.hexdigest()


def iqtree_epa_model(report):
    """Convert the supported IQ-TREE protein model to an explicit EPA model.

    EPA-ng's generic IQ-TREE report reader attempts nucleotide exchangeability
    fields even for LG reports. Transfer only the verified LG + four-category,
    mean-discretized gamma case. Alpha precision is that of the native report.
    """
    model = re.findall(r"^Model of substitution:\s*(\S+)\s*$", report, re.M)
    if model != ["LG+G4"]:
        raise ValueError("IQ-TREE report does not confirm the requested LG+G4 model")
    if not re.search(r"^State frequencies:\s*\(model\)\s*$", report, re.M):
        raise ValueError("EPA model conversion requires fixed LG model state frequencies")
    if not re.search(r"^Model of rate heterogeneity:\s*Gamma with 4 categories\s*$", report, re.M):
        raise ValueError("EPA model conversion requires exactly four gamma categories")
    text = " ".join(report.split())
    if "Relative rates are computed as MEAN of the portion of the Gamma distribution falling in the category." not in text:
        raise ValueError("EPA model conversion requires mean-discretized gamma categories")
    alpha = re.findall(r"^Gamma shape alpha:\s*(\S+)\s*$", report, re.M)
    number = r"[+]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
    if len(alpha) != 1 or not re.fullmatch(number, alpha[0]):
        raise ValueError("IQ-TREE gamma alpha is missing or malformed")
    fitted = float(alpha[0])
    if not math.isfinite(fitted) or fitted <= 0:
        raise ValueError("IQ-TREE reported gamma alpha must be finite and positive")
    match = re.search(r"Category\s+Relative_rate\s+Proportion\s*\n((?:[^\n]*\n){4})", report)
    if match is None:
        raise ValueError("IQ-TREE gamma rate-category audit table is missing")
    categories = []
    for index, line in enumerate(match.group(1).splitlines(), 1):
        fields = line.split()
        if len(fields) != 3 or fields[0] != str(index):
            raise ValueError("Malformed IQ-TREE gamma rate-category audit table")
        rate, weight = map(float, fields[1:])
        if not math.isfinite(rate) or rate <= 0 or not math.isfinite(weight) or abs(weight - 0.25) > 1e-6:
            raise ValueError("IQ-TREE gamma category rate or proportion is invalid")
        categories.append({"category": index, "reported_relative_rate": rate, "reported_proportion": weight})
    return {"policy": "iqtree_fixed_lg_gamma_mean_v1", "descriptor": "LG+G4{" + alpha[0] + "}",
            "reported_alpha": alpha[0], "alpha": fitted, "categories": categories,
            "substitution_model": "LG", "state_frequencies": "fixed_LG_model",
            "gamma_discretization": "mean", "category_count": 4,
            "parameter_precision": "IQ-TREE report text precision; not full-precision checkpoint parameters",
            "note": "EPA recomputes mean gamma category rates from the reported alpha; native report and converted model are sealed."}


# The Python process runs inside the same CommandRunner prefix as native tools.
# Capturing stderr here is necessary because MAFFT prints its version to stderr.
_IDENTITY_SCRIPT = r'''
import hashlib, json, pathlib, shutil, subprocess, sys
executable = shutil.which(sys.argv[1])
if not executable:
    raise RuntimeError("Native executable unavailable: " + sys.argv[1])
path = pathlib.Path(executable).resolve(strict=True)
version = subprocess.run([str(path), sys.argv[2]], check=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, timeout=60).stdout.strip()
if not version:
    raise RuntimeError("Native version probe returned no version: " + str(path))
h = hashlib.sha256()
with path.open("rb") as handle:
    for block in iter(lambda: handle.read(1048576), b""):
        h.update(block)
print(json.dumps({"resolved_executable": str(path), "executable_sha256": h.hexdigest(),
                  "version_output": version}, allow_nan=False))
'''


def _identities(executables, runner):
    identities = {}
    for name in ("mafft", "iqtree", "epa-ng"):
        executable = str(executables.get(name, name))
        # IQ-TREE supports --version as well as -version; use its documented flag.
        flag = "-version" if name == "iqtree" else "--version"
        output = runner.run(["python", "-c", _IDENTITY_SCRIPT, executable, flag]).stdout
        row = json.loads(output)
        if not re.fullmatch(r"[0-9a-f]{64}", row.get("executable_sha256", "")):
            raise ValueError("Invalid native executable checksum: " + name)
        if not row.get("resolved_executable") or not row.get("version_output"):
            raise ValueError("Incomplete native executable identity: " + name)
        identities[name] = row
    match = re.search(r"(?:v(?:ersion)?\s*:?\s*)?(\d+)\.(\d+)\.(\d+)",
                      identities["epa-ng"]["version_output"], re.I)
    if not match or tuple(map(int, match.groups())) < (0, 3, 4):
        raise ValueError("EPA-ng >=0.3.4 is required; older versions have a rooted-tree correctness bug")
    return identities


@contextmanager
def _cache_lock(path, timeout=300):
    import fcntl
    start = time.monotonic()
    with Path(path).open("a") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - start >= timeout:
                    raise RuntimeError("Timed out waiting for placement reference-cache lock")
                time.sleep(0.25)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _validate_cache(directory, expected_key):
    """Reject corruption rather than quietly rebuild or trust stale reference data."""
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("schema") != CACHE_SCHEMA or manifest.get("cache_key") != expected_key:
        raise ValueError("Placement reference-cache identity mismatch")
    if _digest(manifest.get("identity")) != expected_key:
        raise ValueError("Placement reference-cache manifest identity changed")
    required = {"references.faa", "reference_alignment.faa", "labels.json",
                "reference.treefile", "reference.iqtree", "reference.log", "epa_model.json"}
    files = manifest.get("files", {})
    if set(files) != required:
        raise ValueError("Placement reference-cache artifact inventory mismatch")
    for relative, digest in files.items():
        path = directory / relative
        if path.is_symlink() or not path.is_file() or _file_hash(path) != digest:
            raise ValueError("Placement reference-cache artifact changed: " + relative)
    return manifest


def _reference_cache(sequences, labels, panel, config, runner, identities, work):
    model = config.get("placement_model", "LG+G4")
    # A single supported homogeneous protein model avoids EPA/IQ-TREE mixture
    # model incompatibilities. Other models need an explicit validated adapter.
    if model != "LG+G4":
        raise ValueError("Placement currently supports only the verified LG+G4 model")
    threads = int(config.get("threads", 1))
    if threads < 1:
        raise ValueError("Placement threads must be positive")
    identity = {
        "schema": CACHE_SCHEMA, "policy": POLICY,
        "implementation_sha256": _file_hash(Path(__file__)),
        "panel_id": panel.get("panel_id"), "declared_panel_sha256": panel.get("panel_sha256"),
        "sequences": sequences, "labels": labels, "model": model,
        "threads": threads, "seed": 42, "ultrafast_bootstraps": 1000,
        "sh_alrt_replicates": 1000, "bnni": True,
        "reference_alignment_method": "mafft --auto",
        "tools": identities,
    }
    key = _digest(identity)
    cache = Path(config.get("placement_cache_dir") or work / "reference_cache").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    directory = cache / key
    with _cache_lock(cache / (key + ".lock"), int(config.get("placement_cache_lock_timeout", 300))):
        if directory.exists():
            return directory, _validate_cache(directory, key), True
        staged = Path(tempfile.mkdtemp(prefix=".build-" + key[:12] + "-", dir=cache))
        try:
            write_fasta(sequences, staged / "references.faa")
            (staged / "labels.json").write_text(json.dumps(labels, sort_keys=True, indent=2) + "\n")
            executable = {name: identities[name]["resolved_executable"] for name in identities}
            alignment = staged / "reference_alignment.faa"
            runner.run([executable["mafft"], "--auto", "--thread", str(threads),
                        str(staged / "references.faa")], stdout_path=alignment, cwd=staged)
            aligned = read_fasta(alignment)
            metrics = alignment_metrics(sequences, aligned, next(iter(sequences)))
            minimum = int(config.get("placement_min_columns", 50))
            if metrics["alignment_columns"] < minimum:
                raise InsufficientEvidence("reference_alignment_below_minimum_columns")
            prefix = staged / "reference"
            runner.run([executable["iqtree"], "-s", str(alignment), "-st", "AA",
                        "-m", model, "-B", "1000", "-alrt", "1000", "-bnni",
                        "-seed", "42", "-nt", str(threads), "-pre", str(prefix)], cwd=staged)
            tree = staged / "reference.treefile"
            parse_splits(tree.read_text(), sequences, next(iter(sequences)))
            report = (staged / "reference.iqtree").read_text()
            fitted_model = iqtree_epa_model(report)
            fitted_model["iqtree_report_sha256"] = _file_hash(staged / "reference.iqtree")
            (staged / "epa_model.json").write_text(json.dumps(fitted_model, sort_keys=True, indent=2) + "\n")
            files = {name: _file_hash(staged / name) for name in (
                "references.faa", "reference_alignment.faa", "labels.json",
                "reference.treefile", "reference.iqtree", "reference.log", "epa_model.json")}
            manifest = {"schema": CACHE_SCHEMA, "cache_key": key, "identity": identity,
                        "files": files, "alignment_metrics": metrics,
                        "note": "Query-independent reference tree; bootstrap support is not function probability."}
            (staged / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
            _validate_cache(staged, key)
            os.replace(staged, directory)
        finally:
            if staged.exists():
                shutil.rmtree(staged)
    return directory, manifest, False


class InsufficientEvidence(ValueError):
    """A biological/input adequacy hold, not a native execution failure."""


def validate_query_mapping(query_id, sequence, aligned, map_path):
    """Check every input residue, including explicitly dropped insertions."""
    current = None
    mapping = {}
    for line in Path(map_path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(">"):
            if current is not None:
                raise ValueError("MAFFT map contains more than one query block")
            current = line[1:].split()[0]
            if current != query_id:
                raise ValueError("MAFFT map query identifier changed")
            continue
        if current is None:
            raise ValueError("MAFFT map lacks query header")
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 3:
            raise ValueError("Malformed MAFFT residue map")
        letter, position, column = fields
        position = int(position)
        if position in mapping or not 1 <= position <= len(sequence):
            raise ValueError("MAFFT map has duplicate or invalid query positions")
        if letter.upper() != sequence[position - 1]:
            raise ValueError("MAFFT map altered an input residue")
        column = None if column == "-" else int(column)
        if column is not None:
            if not 1 <= column <= len(aligned) or aligned[column - 1] != sequence[position - 1]:
                raise ValueError("MAFFT map and aligned query disagree")
        mapping[position] = column
    if set(mapping) != set(range(1, len(sequence) + 1)):
        raise ValueError("MAFFT map does not account for every query residue")
    retained = [mapping[pos] for pos in sorted(mapping) if mapping[pos] is not None]
    if retained != sorted(set(retained)):
        raise ValueError("MAFFT residue mapping is not ordered and unique")
    actual = [i + 1 for i, residue in enumerate(aligned) if residue not in "-."]
    if actual != retained:
        raise ValueError("Aligned query contains residues absent from the MAFFT map")
    dropped = [position for position in sorted(mapping) if mapping[position] is None]
    return {"query_length": len(sequence), "retained_query_residues": len(retained),
            "retained_query_fraction": len(retained) / len(sequence),
            "dropped_insertion_positions_1based": dropped,
            "dropped_insertion_count": len(dropped), "mapped_columns_1based": retained,
            "all_residues_accounted_for": True}


def run_placement(candidate, panel, config, directory, runner):
    """Run one candidate against a fixed reviewed reference tree.

    Native errors return ``failed``; inadequate data return
    ``insufficient_evidence``. Neither outcome is interpreted as AMR absence.
    """
    result = {"status": "not_assessed", "policy": POLICY, "scores": {},
              "function_probability": None, "inference_scope": INFERENCE_SCOPE,
              "artifacts": [], "panel_id": panel.get("panel_id"),
              "panel_sha256": panel.get("panel_sha256"),
              "panel_adequacy": panel.get("adequacy", {}),
              "panel_provenance": panel.get("provenance", {})}
    work = Path(directory).resolve() / "placement"
    created_work = False
    try:
        if candidate.get("taxonomy_review", {}).get("status") == "review_required":
            raise InsufficientEvidence("taxonomic_scope_review_required")
        if candidate.get("analyses", {}).get("integrity", {}).get("flags"):
            raise InsufficientEvidence("sequence_integrity_review_required")
        sequences, labels, normalizations = {}, {}, {}
        for member in panel.get("members", []):
            tip = member["tip_id"]
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", tip) or tip in sequences:
                raise ValueError("Invalid or duplicate reference tip identifier")
            original = member["sequence"]
            if hashlib.sha256(original.encode("ascii")).hexdigest() != member["sequence_sha256"]:
                raise ValueError("Reference sequence checksum changed: " + tip)
            sequence, normalizations[tip] = normalize_alignment_sequence(original)
            sequences[tip] = sequence
            labels[tip] = {key: member.get(key) for key in ("group", "role", "accessions", "sequence_sha256")}
            if labels[tip]["role"] not in {"amr", "alternative", "context"}:
                raise ValueError("Invalid placement reference role")
        if len(set(sequences.values())) < 4:
            raise InsufficientEvidence("fewer_than_four_distinct_reference_sequences")
        if len(set(sequences.values())) != len(sequences):
            raise ValueError("Placement panel contains duplicate normalized reference sequences")
        if not {"amr", "alternative"}.issubset({row["role"] for row in labels.values()}):
            raise InsufficientEvidence("amr_and_alternative_references_required")
        query = candidate["query"]
        query_id = query["id"]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", query_id) or query_id in sequences:
            raise ValueError("Invalid or colliding query identifier")
        query_records = read_fasta(Path(query["path"]))
        if set(query_records) != {query_id}:
            raise ValueError("Expected a single matching query FASTA record")
        original = query_records[query_id]
        if hashlib.sha256(original.encode("ascii")).hexdigest() != query["sequence_sha256"]:
            raise ValueError("Query sequence checksum changed")
        sequence, normalization = normalize_alignment_sequence(original)
        result["normalization"] = {"query": normalization, "references": normalizations}
        minimum_columns = int(config.get("placement_min_columns", 50))
        minimum_coverage = float(config.get("placement_min_query_coverage", 0.7))
        if minimum_columns < 1 or not 0 < minimum_coverage <= 1:
            raise ValueError("Invalid placement minimum columns or query coverage")
        if len(sequence) < minimum_columns:
            raise InsufficientEvidence("query_below_minimum_columns")
        work.mkdir(parents=True, exist_ok=False)
        created_work = True
        identities = _identities(config.get("executables", {}), runner)
        result["tool_identities"] = identities
        references, manifest, reused = _reference_cache(
            sequences, labels, panel, config, runner, identities, work)
        result["reference_cache"] = {"directory": str(references), "cache_key": manifest["cache_key"],
                                     "reused": reused, "verified_artifact_hashes": manifest["files"]}
        fitted_model = json.loads((references / "epa_model.json").read_text())
        if fitted_model["iqtree_report_sha256"] != manifest["files"]["reference.iqtree"]:
            raise ValueError("Converted EPA model is detached from its fitted IQ-TREE report")
        result["fitted_model"] = fitted_model
        result["artifacts"].extend(str(references / item) for item in [*manifest["files"], "manifest.json"])
        reference_alignment = read_fasta(references / "reference_alignment.faa")
        columns = len(next(iter(reference_alignment.values())))
        if columns < minimum_columns:
            raise InsufficientEvidence("reference_alignment_below_minimum_columns")
        query_fasta = work / "query.faa"
        write_fasta({query_id: sequence}, query_fasta)
        combined = work / "combined_alignment.faa"
        runner.run([identities["mafft"]["resolved_executable"], "--auto", "--thread",
                    str(int(config.get("threads", 1))), "--addfull", str(query_fasta),
                    "--keeplength", "--mapout", str(references / "reference_alignment.faa")],
                   stdout_path=combined, cwd=work)
        aligned = read_fasta(combined)
        if set(aligned) != set(sequences) | {query_id}:
            raise ValueError("Query addition changed alignment identifiers")
        if any(aligned[tip] != reference_alignment[tip] for tip in reference_alignment):
            raise ValueError("Query addition altered the reference alignment")
        if len(aligned[query_id]) != columns:
            raise ValueError("Query alignment changed reference column count")
        map_path = Path(str(query_fasta) + ".map")
        mapping = validate_query_mapping(query_id, sequence, aligned[query_id], map_path)
        mapping["columns_with_query_and_reference_residue"] = sum(
            any(row[column - 1] not in "-." for row in reference_alignment.values())
            for column in mapping["mapped_columns_1based"])
        result["query_alignment"] = mapping
        result["artifacts"].extend(str(path) for path in (query_fasta, combined, map_path))
        if mapping["retained_query_fraction"] < minimum_coverage:
            raise InsufficientEvidence("query_retained_coverage_below_threshold")
        if mapping["columns_with_query_and_reference_residue"] < minimum_columns:
            raise InsufficientEvidence("query_reference_overlap_below_minimum_columns")
        placed_query = work / "query_alignment.faa"
        write_fasta({query_id: aligned[query_id]}, placed_query)
        native = work / "epa"
        native.mkdir()
        max_edges = 2 * len(sequences) - 3
        result["output_filter_policy"] = {"preplacement_heuristic": False,
            "minimum_likelihood_weight": 0.0, "maximum_placements": max_edges,
            "precision": 12, "missing_mass_policy": "retain_and_report_without_renormalization"}
        runner.run([identities["epa-ng"]["resolved_executable"], "--ref-msa",
                    str(references / "reference_alignment.faa"), "--tree",
                    str(references / "reference.treefile"), "--query", str(placed_query),
                    "--model", fitted_model["descriptor"], "--outdir", str(native),
                    "--threads", str(int(config.get("threads", 1))), "--no-heur",
                    "--filter-min-lwr", "0", "--filter-max", str(max_edges),
                    "--precision", "12"], cwd=work)
        jplace = native / "epa_result.jplace"
        from .placement_scoring import analyze_jplace
        jplace_data = json.loads(jplace.read_text())
        result["scores"] = analyze_jplace(jplace_data, labels, query_id)
        # Recheck the sealed shared reference artifacts after all commands.
        _validate_cache(references, manifest["cache_key"])
        result["artifacts"].extend(str(path) for path in (placed_query, jplace))
        result.update(status="completed", reason="reference_tree_placement_completed")
        # This descriptive layer cannot promote a group, alter scores, or turn
        # an otherwise completed native placement into an execution failure.
        try:
            from .placement_diagnostics import diagnose_jplace
            result["diagnostics"] = diagnose_jplace(
                jplace_data, labels, query_id, expected_scores=result["scores"])
        except Exception as exc:
            result["diagnostics"] = {
                "schema": "basis-placement-diagnostics-v1", "status": "failed",
                "reason": "diagnostic_generation_failed",
                "error": f"{type(exc).__name__}: {exc}", "function_probability": None,
                "classification_policy_changed": False,
                "interpretation": "Diagnostic generation failed; original placement scores and decisions are unchanged.",
            }
    except InsufficientEvidence as exc:
        result.update(status="insufficient_evidence", reason=str(exc))
    except (OSError, ValueError, RuntimeError) as exc:
        result.update(status="failed", reason="placement_execution_or_validation_failed", error=str(exc))
    if created_work:
        audit = work / "placement.json"
        result["artifacts"].append(str(audit))
        audit.write_text(json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n")
    return result
