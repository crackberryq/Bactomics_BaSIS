"""Direct-tool command construction and strict native-result parsing.

This module never launches a process, activates an environment or schedules work.
Snakemake owns those operations. Parsers retain the RC's scientific contracts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
from pathlib import Path

MODULES = ("amrfinder", "rgi", "vfdb", "bacmet", "antismash", "genomad",
           "mobsuite", "integronfinder", "isescan", "icescreen", "minced", "mlst")
EXECUTABLES = {"amrfinder": "amrfinder", "rgi": "rgi", "vfdb": "diamond",
               "bacmet": "diamond", "antismash": "antismash", "genomad": "genomad",
               "mobsuite": "mob_recon", "integronfinder": "integron_finder",
               "isescan": "isescan.py", "icescreen": "icescreen", "minced": "minced", "mlst": "mlst"}


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return str(value)


def native_dir(isolate, module):
    path = Path(isolate) / "safety/native" / module
    return path / "results" if module == "rgi" else path


def command_argv(module, sample, settings, native, threads):
    """Return one native tool argv, without performing filesystem reads or work."""
    if module not in MODULES:
        raise ValueError(f"Unknown native module: {module}")
    threads = _positive(threads, "threads")
    native = Path(native)
    exe = str(settings.get("executable") or EXECUTABLES[module])
    assembly = str(sample["assembly"])
    database = str(settings.get("database") or "")
    if module in {"amrfinder", "rgi", "vfdb", "bacmet", "genomad", "mobsuite", "mlst"} and not database:
        raise ValueError(f"{module} requires its declared database path")
    if module == "amrfinder":
        argv = [exe, "-n", assembly, "-p", str(sample["proteins"]), "-g", str(sample["gff"]),
                "--plus", "--database", database, "--threads", threads, "-o", str(native / "amrfinder.tsv")]
        if settings.get("annotation_format"):
            argv += ["--annotation_format", str(settings["annotation_format"])]
        if sample.get("amrfinder_organism"):
            argv += ["--organism", str(sample["amrfinder_organism"])]
        return argv
    if module == "rgi":
        input_type = settings.get("input_type", "protein")
        aligner = str(settings.get("alignment_tool", "BLAST")).upper()
        if input_type not in {"protein", "contig"} or aligner not in {"BLAST", "DIAMOND"}:
            raise ValueError("Unsupported RGI input_type or alignment_tool")
        argv = [exe, "main", "--input_sequence", str(sample["proteins" if input_type == "protein" else "assembly"]),
                "--output_file", str(native / "rgi"), "--input_type", input_type,
                "--alignment_tool", aligner, "--threads", threads, "--local"]
        if settings.get("include_loose", True):
            argv += ["--include_loose"]
        if settings.get("low_quality", False):
            argv += ["--low_quality"]
        return argv
    if module in {"vfdb", "bacmet"}:
        from basis.adapters.amr import DIAMOND_COLUMNS
        sensitivity = settings.get("sensitivity", "sensitive")
        if sensitivity not in {"fast", "mid-sensitive", "sensitive", "more-sensitive", "very-sensitive", "ultra-sensitive"}:
            raise ValueError("Unsupported DIAMOND sensitivity")
        argv = [exe, "blastp", "--query", str(sample["proteins"]), "--db", database,
                "--out", str(native / f"{module}.diamond.tsv"), "--threads", threads,
                "--evalue", str(settings.get("evalue", 1e-5)), "--max-target-seqs",
                _positive(settings.get("max_target_seqs", 25), "max_target_seqs"), "--max-hsps", "1", "--" + sensitivity]
        if module == "bacmet":
            argv += ["--no-parse-seqids"]
        return argv + ["--outfmt", "6", *DIAMOND_COLUMNS]
    if module == "antismash":
        argv = [exe, assembly, "--output-dir", str(native / "output"), "--output-basename", "basis",
                "--cpus", threads, "--taxon", "bacteria", "--abort-on-invalid-records", "--minlength", "1"]
        argv += (["--genefinding-gff3", str(sample["gff"])] if sample.get("gff") else ["--genefinding-tool", "prodigal"])
        if database:
            argv += ["--databases", database]
        return argv
    if module == "genomad":
        argv = [exe, "end-to-end", "--threads", threads]
        if "splits" in settings:
            argv += ["--splits", _positive(settings["splits"], "splits")]
        return argv + [assembly, str(native / "output"), database]
    if module == "mobsuite":
        return [exe, "-i", assembly, "-o", str(native / "output"), "-n", threads,
                "--database_directory", database, "-s", str(sample["sample_id"])]
    if module == "integronfinder":
        topology = settings.get("topology", "linear")
        if topology not in {"linear", "circular"}:
            raise ValueError("IntegronFinder topology must be explicitly linear or circular")
        argv = [exe, assembly, "--outdir", str(native / "output"), "--cpu", threads,
                "--circ" if topology == "circular" else "--lin"]
        return argv + (["--local-max"] if settings.get("local_max", True) else [])
    if module == "isescan":
        return [exe, "--seqfile", assembly, "--output", str(native / "output"), "--nthread", threads]
    if module == "icescreen":
        if settings.get("phylum") != "bacillota":
            raise ValueError("ICEscreen requires explicit supported phylum: bacillota")
        if not sample.get("genbank"):
            raise ValueError("ICEscreen requires an annotated GenBank input")
        return [exe, "--galaxy", "-g", str(native / "input"), "-o", str(native / "output"),
                "-j", threads, "--phylum", "bacillota"]
    if module == "minced":
        return [exe, "-gffFull", assembly, str(native / "arrays.txt"), str(native / "arrays.gff")]
    scheme = settings.get("scheme") or sample.get("mlst_scheme")
    if scheme and (not isinstance(scheme, str) or not re.fullmatch(r"[A-Za-z0-9_]+", scheme)):
        raise ValueError("Invalid MLST scheme")
    argv = [exe, "--threads", threads, "--datadir", str(Path(database) / "pubmlst"),
            "--blastdb", str(Path(database) / "blast/mlst.fa"), "--label", str(sample["sample_id"]),
            "--json", str(native / "mlst.json"), "--full", "--outfile", str(native / "mlst.tsv")]
    return argv + (["--scheme", scheme] if scheme else []) + [assembly]


def command_shell(module, sample, settings, native, threads, *, local_database=None):
    """A transparent shell body executed directly by the owning Snakemake rule."""
    native = Path(native)
    q = shlex.quote
    command = shlex.join(command_argv(module, sample, settings, native, threads))
    tail = "cat -- " + q(str(native / "stdout.txt")) + " " + q(str(native / "stderr.txt")) + " 2>/dev/null || true"
    lines = [f"mkdir -p {q(str(native))}", "trap " + q(tail) + " EXIT",
             "unset PYTHONPATH PYTHONHOME", "export PYTHONNOUSERSITE=1",
             f"export OMP_NUM_THREADS={threads} OPENBLAS_NUM_THREADS={threads} MKL_NUM_THREADS={threads} NUMEXPR_NUM_THREADS={threads}"]
    if module == "icescreen":
        lines += [f"mkdir -p {q(str(native / 'input'))} {q(str(native / 'output'))}",
                  f"cp -- {q(str(sample['genbank']))} {q(str(native / 'input/basis.gbk'))}"]
    cwd = (Path(local_database) if local_database else native.parent / "database") if module == "rgi" else native
    lines += [f"printf '%s\\n' {q(command)} > {q(str(native / 'command.sh'))}",
              f"cd {q(str(cwd))}", f"{command} > {q(str(native / 'stdout.txt'))} 2> {q(str(native / 'stderr.txt'))}"]
    return "\n".join(lines)


def rgi_load_shell(settings, output):
    output = Path(output)
    q = shlex.quote
    argv = [str(settings.get("executable") or "rgi"), "load", "--card_json", str(settings["database"]), "--local"]
    return "\n".join([f"mkdir -p {q(str(output))}", f"cd {q(str(output))}",
                      "unset PYTHONPATH PYTHONHOME", "export PYTHONNOUSERSITE=1",
                      shlex.join(argv), "test -d localDB"])


def parse_payload(module, sample, settings, native):
    from basis.adapters import amr, context, minced, typing_defense
    from basis.inputs import read_fasta
    native = Path(native)
    if module in {"amrfinder", "rgi", "vfdb", "bacmet"}:
        if module == "amrfinder":
            path = native / "amrfinder.tsv"
            findings, artifacts = amr.parse_amrfinder(path), [path]
            notes = ["Native curated detection thresholds retained; gene or variant calls do not establish phenotype."]
        elif module == "rgi":
            path, raw_json = native / "rgi.txt", native / "rgi.json"
            if not isinstance(json.loads(raw_json.read_text()), dict):
                raise ValueError("RGI native JSON must contain an object")
            findings = amr.parse_rgi(path, input_type=settings.get("input_type", "protein"))
            artifacts = [path, raw_json]
            notes = ["Perfect/Strict retain native model status. Loose and nudged calls remain exploratory.",
                     "Reference-length ratios are not alignment coverage; no phenotype is inferred."]
        else:
            path = native / f"{module}.diamond.tsv"
            findings = amr.parse_diamond(path, module, **{k: settings.get(k, d) for k, d in
                    (("min_identity", 80.0), ("min_coverage", 70.0), ("min_query_coverage", 0.0))})
            artifacts = [path]
            notes = ["Protein homology screening; database membership does not establish function or organism hazard.",
                     "Below-threshold alignments remain exploratory. Subject and query coverage are retained separately."]
        return amr._result(findings, artifacts, settings, notes)
    output = native / "output"
    if module == "antismash":
        return context.parse_antismash(output / "basis.json")
    if module == "genomad":
        return context.parse_genomad(context._one(output, "*_virus_summary.tsv"), context._one(output, "*_plasmid_summary.tsv"))
    if module == "mobsuite":
        return context.parse_mobsuite(output / "contig_report.txt")
    if module == "integronfinder":
        payload = context.parse_integronfinder(context._one(output, "*.integrons"), context._one(output, "*.summary"))
        if {row["ID_replicon"] for row in payload["metrics"]["summary"]} != set(read_fasta(sample["assembly"])):
            raise ValueError("IntegronFinder summary does not account for exactly the input contigs")
        return payload
    if module == "isescan":
        tables = sorted(output.rglob(Path(sample["assembly"]).name + ".tsv"))
        if len(tables) > 1:
            raise ValueError("Ambiguous ISEScan result tables")
        if tables:
            return context.parse_isescan(tables[0])
        transcript = native / "stdout.txt"
        return context._isescan_native_zero(output, Path(sample["assembly"]), transcript.read_text(), transcript)
    if module == "icescreen":
        accessions = context._icescreen_input_accessions(Path(sample["genbank"]))
        return context.parse_icescreen(context._one(output, "*_detected_ME.tsv"),
                                      context._one(output, "*_detected_ME.summary"), accessions)
    if module == "minced":
        diagnostics = "\n".join((native / name).read_text(errors="replace") for name in ("stdout.txt", "stderr.txt"))
        if minced._ERROR.search(diagnostics):
            raise ValueError("MinCED reported an error despite successful exit; inspect native stdout/stderr")
        payload = minced.parse_minced(native / "arrays.gff", native / "arrays.txt", Path(sample["assembly"]))
        payload["metrics"]["completion_check"] = "exit_log_and_all_contig_blocks_verified"
        return payload
    if module == "mlst":
        return typing_defense.parse_mlst(native / "mlst.json", native / "mlst.tsv", sample["assembly"],
                                         sample["sample_id"], requested_scheme=settings.get("scheme") or sample.get("mlst_scheme"))
    raise ValueError(f"Unknown module: {module}")


def _load(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def verify(module, sample, settings, native):
    """Fail the owning native rule if its process emitted unusable output."""
    payload = parse_payload(module, sample, settings, native)
    _write(Path(native) / "parsed.json", payload)
    return payload


def normalized_record(module, sample, config, validation, native):
    from basis.normalize import normalize_result
    settings = config.get("modules", {}).get(module, {})
    decision = config.get("module_decisions", {}).get(module, {})
    record = {"schema_version": 1, "sample_id": sample["sample_id"], "module": module,
              "tool": module, "record_id": f"{sample['sample_id']}:{module}", "version": config.get("version", "snakemake-development"),
              "validation_mode": "native", "input_hashes": validation["input_hashes"],
              "effective_config": settings, "findings": [], "metrics": {}, "notes": [], "artifacts": [],
              "execution_status": "not_assessed", "interpretation": "inconclusive"}
    if decision.get("action", "run") != "run":
        record["reason"] = decision.get("reason", "module_scope_not_applicable")
        record["scope_decision"] = decision
        return record
    native = Path(native)
    payload = _load(native / "parsed.json")
    record.update(normalize_result(payload, module, sample, validation))
    record["execution_status"] = "completed"
    record["artifacts"] = sorted(set(record.get("artifacts", []) + [str(native)]))
    checks = {}
    for path in sorted(native.rglob("*")):
        if path.is_file() and path.name != ".snakemake_timestamp":
            with path.open("rb") as handle:
                checks[str(path)] = hashlib.file_digest(handle, "sha256").hexdigest()
    record["artifact_hashes"] = checks
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "parse"))
    parser.add_argument("--module", choices=MODULES, required=True)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--native", required=True)
    parser.add_argument("--validation")
    parser.add_argument("--output")
    parser.add_argument("--receipt")
    args = parser.parse_args(argv)
    sample, config = _load(args.sample), _load(args.config)
    settings = config.get("modules", {}).get(args.module, {})
    if args.action == "verify":
        verify(args.module, sample, settings, args.native)
    else:
        if not args.output or not args.validation:
            parser.error("parse requires --output and --validation")
        decision = config.get("module_decisions", {}).get(args.module, {})
        if args.receipt and decision.get("action", "run") == "run":
            from .result_store import verify_receipt
            verify_receipt(args.receipt, args.native)
        _write(args.output, normalized_record(args.module, sample, config, _load(args.validation), args.native))


if __name__ == "__main__":
    main()
