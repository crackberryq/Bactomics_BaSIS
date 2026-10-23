"""One pinned CRISPRCasFinder invocation for the complete supplied assembly.

The native program already iterates FASTA records. Keep that loop inside one
process while checking every record's identity, coordinates and Cas completion.
Only a verified result-store receipt is a scheduler output; failed attempts keep
their raw diagnostics and cannot become negative findings.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from basis import inputs
from basis.adapters import crisprcasfinder as ccf
from . import extend, result_store, runtime


_POLICY = "disable_system_and_user_config_readers"
_CONFIG = "[directories]\nmodels_dir = models\n"

# Retain the existing native-environment configuration isolation verbatim and
# change only its audit destination/binding. CCF calls MacSyFinder repeatedly
# before relocating the per-record native folders; a single overwritten audit
# cannot establish that every invocation used the required isolated settings.
_AUDIT_BINDING = r'''def option(name):
    values = []
    for index, item in enumerate(sys.argv[1:], 1):
        if item == name:
            if index + 1 >= len(sys.argv) or sys.argv[index + 1].startswith("--"):
                raise RuntimeError("Missing native argument: " + name)
            values.append(sys.argv[index + 1])
        elif item.startswith(name + "="):
            values.append(item.split("=", 1)[1])
    if len(values) != 1 or not values[0]:
        raise RuntimeError("Expected one native argument: " + name)
    return values[0]
sequence_db = Path(option("--sequence-db")).resolve(strict=True)
out_dir = Path(option("--out-dir")).resolve()
safe = sequence_db.stem
if (not re.fullmatch(r"CCF[0-9]{6,}", safe)
        or sequence_db != root / "native" / ("prodigal_" + safe) / (safe + ".faa")
        or out_dir != root / "native" / ("casfinder_" + safe)):
    raise RuntimeError("MacSyFinder invocation does not match a staged assembly record")
record.update(safe_id=safe, sequence_db=str(sequence_db), out_dir=str(out_dir),
              sequence_db_sha256=hashlib.sha256(sequence_db.read_bytes()).hexdigest())
audits = root / "macsyfinder-audits"
audits.mkdir(exist_ok=True)
audit = audits / (safe + ".json")
# Exclusive creation makes a duplicate invocation an error, not an overwrite.
with audit.open("x"):
    pass
'''


def _adapt(source, old, new):
    if source.count(old) != 1:
        raise ValueError("Pinned CRISPR isolation wrapper contract changed")
    return source.replace(old, new, 1)


ASSEMBLY_MACSY_SHIM = _adapt(_adapt(ccf._MACSY_SHIM,
    "import hashlib, json, os, runpy, sys", "import hashlib, json, os, re, runpy, sys"),
    'audit = root / "macsyfinder_isolation.json"\n', _AUDIT_BINDING)
ASSEMBLY_LAUNCHER = _adapt(ccf._CCF_LAUNCHER,
    '"home_environment_unchanged": True}',
    '"home_environment_unchanged": True, "working_directory": str(root)}')


def contig_map(sequences):
    if not sequences or any(not ccf._BASES.fullmatch(seq) for seq in sequences.values()):
        raise ValueError("CRISPRCasFinder requires nonempty supported assembly DNA records")
    return {f"CCF{number:06d}": {"contig": name, "length": len(sequence),
            "sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest()}
            for number, (name, sequence) in enumerate(sequences.items(), 1)}


def _option(argv, name):
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
        raise ValueError("Malformed MacSyFinder invocation audit")
    values = []
    for index, item in enumerate(argv):
        if item == name:
            if index + 1 == len(argv) or argv[index + 1].startswith("--"):
                raise ValueError(f"Missing MacSyFinder argument: {name}")
            values.append(argv[index + 1])
        elif item.startswith(name + "="):
            values.append(item.split("=", 1)[1])
    if len(values) != 1 or not values[0]:
        raise ValueError(f"Expected exactly one MacSyFinder argument: {name}")
    return values[0]


def _invocation_path(value, launched):
    # Upstream passes paths relative to its original working directory and
    # subsequently moves the files. Resolve lexically, never against today's cwd.
    path = Path(value)
    return Path(os.path.normpath(path if path.is_absolute() else launched / path))


def verify_isolation(work, statuses):
    """Bind each completed Cas search to its own isolated native invocation."""
    work = Path(work)
    manifest = json.loads(ccf._read(work / "runtime_isolation.json"))
    expected = {"launcher_sha256": hashlib.sha256(ASSEMBLY_LAUNCHER.encode()).hexdigest(),
                "wrapper_sha256": hashlib.sha256(ASSEMBLY_MACSY_SHIM.encode()).hexdigest(),
                "config_sha256": hashlib.sha256(_CONFIG.encode()).hexdigest()}
    if (not isinstance(manifest, dict) or manifest.get("policy") != _POLICY
            or manifest.get("home_environment_unchanged") is not True
            or any(manifest.get(key) != value for key, value in expected.items())
            or result_store.sha256(work / "basis_ccf_launch.py") != expected["launcher_sha256"]
            or result_store.sha256(work / "bin/macsyfinder") != expected["wrapper_sha256"]
            or result_store.sha256(work / "macsyfinder.conf") != expected["config_sha256"]):
        raise ValueError("CRISPRCasFinder runtime configuration isolation was not verified")
    launched = Path(manifest.get("working_directory", ""))
    actual = Path(manifest.get("macsyfinder_executable", ""))
    if (not launched.is_absolute() or not manifest.get("python")
            or not actual.is_absolute() or not actual.is_file()
            or result_store.sha256(actual) != manifest.get("macsyfinder_executable_sha256")):
        raise ValueError("CRISPRCasFinder native runtime executable identity was not verified")
    audits = work / "macsyfinder-audits"
    expected_audits = {safe + ".json" for safe, row in statuses.items() if row["status"] == "completed"}
    found = {path.name for path in audits.iterdir()} if audits.is_dir() else set()
    if found != expected_audits or (audits.exists() and not audits.is_dir()):
        raise ValueError("MacSyFinder invocation audits do not cover completed contigs exactly")
    for safe, status in statuses.items():
        if status["status"] != "completed":
            continue
        audit = json.loads(ccf._read(audits / (safe + ".json")))
        sequence_db = launched / "native" / f"prodigal_{safe}" / f"{safe}.faa"
        out_dir = launched / "native" / f"casfinder_{safe}"
        explicit_config = launched / "macsyfinder.conf"
        proteome = work / "native/Prodigal" / f"prodigal_{safe}" / f"{safe}.faa"
        expected_row = {"safe_id": safe, "exit_status": 0, "policy": _POLICY,
            "macsyfinder_version": ccf.MACSYFINDER_VERSION, "python": manifest["python"],
            "actual_executable": str(actual), "explicit_config": str(explicit_config),
            "wrapper_sha256": expected["wrapper_sha256"], "config_sha256": expected["config_sha256"],
            "sequence_db": str(sequence_db), "out_dir": str(out_dir),
            "sequence_db_sha256": result_store.sha256(proteome)}
        if (not isinstance(audit, dict) or any(audit.get(key) != value for key, value in expected_row.items())):
            raise ValueError(f"MacSyFinder did not complete with verified isolation for {safe}")
        argv = audit.get("argv")
        if (not argv or argv[0] != str(actual)
                or _invocation_path(_option(argv, "--sequence-db"), launched) != sequence_db
                or _invocation_path(_option(argv, "--out-dir"), launched) != out_dir
                or _invocation_path(_option(argv, "--cfg-file"), launched) != explicit_config
                or any(item == "--previous-run" or item.startswith("--previous-run=") for item in argv)):
            raise ValueError(f"MacSyFinder invocation does not match its contig: {safe}")


def parse_assembly(work, sequences, *, min_evidence_level=3, genetic_code=11, artifact_root=None):
    """Verify complete aggregate output while retaining the existing parsers."""
    work = Path(work)
    mapping = contig_map(sequences)
    if runtime.read_json(work / "contig_map.json") != mapping:
        raise ValueError("CRISPR contig map differs from the complete supplied assembly")
    staged = {safe: sequences[row["contig"]] for safe, row in mapping.items()}
    if inputs.read_fasta(work / "input.fna") != staged:
        raise ValueError("CRISPR staged assembly differs from the supplied assembly")
    stdout = ccf._ANSI.sub("", ccf._read(work / "stdout.txt"))
    if ccf._ERROR.search(stdout) or "Thank you for using CRISPRCasFinder.pl!" not in stdout:
        raise ValueError("CRISPRCasFinder lacks successful completion or reported an error")
    markers = list(re.finditer(r"(?m)^\s*\( Input file: (CCF[0-9]{6,})\.fna, "
                              r"Sequence ID: (CCF[0-9]{6,}), Sequence name = [^\r\n]* \)\s*$", stdout))
    if ([match.group(1) for match in markers] != list(mapping)
            or any(match.group(1) != match.group(2) for match in markers)):
        raise ValueError("CRISPR stdout does not cover every assembly record exactly in input order")
    data = json.loads(ccf._read(work / "native/result.json"))
    if (not isinstance(data, dict) or data.get("Version") != ccf.VERSION
            or not isinstance(data.get("Sequences"), list)
            or any(not isinstance(row, dict) for row in data["Sequences"])
            or [row.get("Id") for row in data["Sequences"]] != list(mapping)):
        raise ValueError("CRISPR result JSON does not cover every assembly record exactly in input order")
    native = work / "native"
    analyzed = native / "rawFASTA/analyzedSequences"
    if not analyzed.is_dir() or {path.name for path in analyzed.iterdir()} != {safe + ".fna" for safe in mapping}:
        raise ValueError("CRISPR analyzed sequences do not cover the complete supplied assembly")
    findings, statuses, by_safe = [], {}, {}
    for number, (safe, info) in enumerate(mapping.items()):
        sequence = sequences[info["contig"]]
        record = data["Sequences"][number]
        if record.get("Version") != safe or ccf._int(record.get("Length"), "sequence length", 1) != len(sequence):
            raise ValueError("CRISPRCasFinder sequence identity/length mismatch")
        if inputs.read_fasta(analyzed / f"{safe}.fna") != {safe: sequence}:
            raise ValueError("CRISPRCasFinder analyzed sequence differs from staged input")
        end = markers[number + 1].start() if number + 1 < len(markers) else len(stdout)
        block = stdout[markers[number].end():end]
        arrays = ccf._parse_arrays(record.get("Crisprs"), info["contig"], sequence, min_evidence_level)
        counts = re.findall(r"(?m)^Nb of CRISPRs in this sequence = (\d+)\s*$", block)
        if counts != [str(len(arrays))]:
            raise ValueError(f"CRISPRCasFinder stdout/JSON array counts disagree for {safe}")
        cas, status = ccf._parse_cas(native, safe, info["contig"], sequence, record.get("Cas"), block)
        findings.extend(arrays + cas)
        statuses[info["contig"]] = status
        by_safe[safe] = status
    arrays = [row for row in findings if row["attributes"]["feature_type"] == "array"]
    if re.findall(r"(?m)^All CRISPRs = (\d+)\s*$", stdout) != [str(len(arrays))]:
        raise ValueError("CRISPRCasFinder aggregate stdout/JSON array counts disagree")
    verify_isolation(work, by_safe)
    complete = all(row["status"] == "completed" for row in statuses.values())
    artifacts = Path(artifact_root) if artifact_root is not None else work
    artifact_paths = [str(artifacts / name) for name in
                      ("native", "contig_map.json", "stdout.txt", "runtime_isolation.json")]
    if (work / "macsyfinder-audits").is_dir():
        artifact_paths.append(str(artifacts / "macsyfinder-audits"))
    return {"findings": findings, "artifacts": artifact_paths,
        "interpretation": "detected" if findings else ("not_detected" if complete else "inconclusive"),
        "metrics": {"crisprcasfinder_version": ccf.VERSION, "source_sha256": ccf.SOURCE_SHA256,
            "macsyfinder_version": ccf.MACSYFINDER_VERSION, "casfinder_models_version": ccf.CAS_MODELS_VERSION,
            "array_count": len(arrays),
            "array_count_at_reporting_level": sum(row["attributes"]["passes_threshold"] for row in arrays),
            "repeat_count": sum(row["attributes"]["repeat_count"] for row in arrays),
            "spacer_count": sum(row["attributes"]["spacer_count"] for row in arrays),
            "cas_system_count": (sum(row["system_count"] or 0 for row in statuses.values())
                                 if any(row.get("mode") == "gembase" for row in statuses.values()) else None),
            "typed_cas_assessed_contig_count": sum(row.get("mode") == "gembase" for row in statuses.values()),
            "cas_unordered_candidate_model_count": sum(row["candidate_model_count"] or 0 for row in statuses.values()),
            "cas_assessment": "completed" if complete else "partially_assessed",
            "cas_contig_assessments": statuses, "assessed_contigs": list(sequences),
            "assessed_contig_count": len(sequences), "genetic_code": genetic_code,
            "execution_granularity": "whole_assembly", "native_invocation_count": 1,
            "completion_check": "all_contig_JSON_native_Cas_artifacts_and_coordinates_verified"},
        "notes": ["CRISPR/Cas predictions describe genomic context, not activity, pathogenicity or organism safety.",
            "Upstream circular topology is an algorithm setting, not confirmed assembly topology.",
            "Short-contig unordered candidates are retained without asserting typed systems.",
            "No predicted CDS means Cas was not assessed; it does not establish Cas absence.",
            "All supplied assembly records share one pinned native invocation; each Cas invocation has its own configuration-isolation audit."]}


def run_assembly(sample, full_config, destination, receipt, store_root, *, threads, sources=()):
    config = copy.deepcopy(full_config)
    settings = config["modules"]["crisprcasfinder"]
    settings["threads"] = ccf._int(threads, "threads", 1)
    code = ccf._int(settings.get("genetic_code", 11), "genetic code", 1)
    level = ccf._int(settings.get("min_evidence_level", 3), "minimum array evidence level", 1)
    if level > 4:
        raise ValueError("Minimum CRISPR evidence level must be 1..4")
    resource_rows = [runtime.read_json(entry["path"] if isinstance(entry, dict) else entry)
                     for entry in config.get("resource_receipts", [])]
    if sum(row.get("resource") == "crisprcasfinder" for row in resource_rows) != 1:
        raise ValueError("Exactly one verified CRISPR resource receipt is required")
    source_paths = {str(Path(path).resolve()) for path in sources}
    source_paths.update(str(Path(module.__file__).resolve()) for module in
                        (inputs, ccf, extend, result_store, runtime))
    source_paths.add(str(Path(__file__).resolve()))
    executables = []
    for program in ("python", "macsyfinder"):
        resolved = shutil.which(program)
        if resolved is None:
            raise ValueError(f"Cannot verify CRISPR native-environment executable: {program}")
        executables.append(str(Path(resolved).resolve(strict=True)))
    identity = result_store.execution_identity("crisprcasfinder", sample, config,
        sources=sorted(source_paths), extra_inputs=executables,
        executable=settings.get("executable", "perl"))
    sequences = inputs.read_fasta(sample["assembly"])
    mapping = contig_map(sequences)
    destination = Path(destination).absolute()

    def execute(payload):
        prepared = extend.prepare_ccf_execution(settings)
        runner = runtime.DirectRunner(settings, payload.parent / "commands.log")
        work = Path(tempfile.mkdtemp(prefix="basis-ccf-assembly-", dir="/tmp"))
        try:
            shutil.copyfile(prepared["script"], work / "CRISPRCasFinder.pl")
            for name in ("COPYING", "COPYRIGHT"):
                if (prepared["root"] / name).is_file():
                    shutil.copyfile(prepared["root"] / name, work / name)
            shutil.copyfile(prepared["library"], work / "sel392v2.so")
            (work / "models").symlink_to(prepared["models"].parent, target_is_directory=True)
            with (work / "input.fna").open("w", encoding="ascii") as stream:
                for safe, row in mapping.items():
                    stream.write(f">{safe}\n{sequences[row['contig']]}\n")
            runtime.write_json(work / "contig_map.json", mapping)
            (work / "macsyfinder.conf").write_text(_CONFIG, encoding="ascii")
            (work / "bin").mkdir()
            shim = work / "bin/macsyfinder"
            shim.write_text(ASSEMBLY_MACSY_SHIM, encoding="utf-8")
            shim.chmod(0o700)
            (work / "basis_ccf_launch.py").write_text(ASSEMBLY_LAUNCHER, encoding="utf-8")
            runner.run(["python", "basis_ccf_launch.py", settings.get("executable", "perl"),
                "CRISPRCasFinder.pl", "-in", "input.fna", "-out", "native", "-so", "./sel392v2.so",
                "-cas", "-keep", "-log", "-cpuM", str(settings["threads"]), "-gcode", str(code)],
                cwd=work, stdout_path=work / "stdout.txt")
            logged = ccf._ANSI.sub("", runner.logfile.read_text(encoding="utf-8", errors="replace"))
            if ccf._ERROR.search(logged):
                raise ValueError("CRISPRCasFinder command log records a native error")
        finally:
            try:
                runtime.write_json(payload.parent / "commands.json", runner.commands)
            finally:
                # CCF uses unquoted shell paths internally. Keep execution in /tmp,
                # then retain successful and failed raw attempts in the durable store.
                try:
                    payload.rmdir()
                    shutil.move(str(work), payload)
                except BaseException as error:
                    raise RuntimeError(f"Could not retain CRISPR diagnostics; temporary work: {work}; "
                                       f"destination: {payload}") from error

    def verify(payload):
        if result_store.sha256(payload / "CRISPRCasFinder.pl") != ccf.SOURCE_SHA256:
            raise ValueError("Executed CRISPRCasFinder source differs from the pinned release")
        parsed = parse_assembly(payload, sequences, min_evidence_level=level,
                                genetic_code=code, artifact_root=destination)
        runtime.write_json(payload / "parsed.json", parsed)
        # The completed snapshot is self-contained. The already verified model
        # resource is identity-bound above; do not copy it into every result.
        (payload / "models").unlink()

    record, reused = result_store.run_cached("crisprcasfinder", identity, destination, receipt,
        store_root, execute, verify, failed_retention=settings.get("failed_attempts_to_keep", 3),
        allow_reuse=settings.get("cache", True))
    print(json.dumps({"status": "completed", "module": "crisprcasfinder", "reused": reused,
                      "receipt": str(receipt), "payload_root": record["payload_root"]}))
    return record, reused


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("sample", "config", "native", "receipt", "store"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--sources", nargs="*", default=[])
    args = parser.parse_args(argv)
    run_assembly(runtime.read_json(args.sample), runtime.read_json(args.config), args.native,
                 args.receipt, args.store, threads=args.threads, sources=args.sources)


if __name__ == "__main__":
    main()
