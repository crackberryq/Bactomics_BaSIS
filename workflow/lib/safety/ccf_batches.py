"""Bounded scheduling and verified restart records for existing contig calls.

Only batch receipts are scheduler outputs. Native contig folders remain outside
those outputs, so a later failure cannot erase earlier completed contigs.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from basis import inputs
from basis.adapters import crisprcasfinder as ccf
from safety import extend, runtime

PLAN_SCHEMA = "basis-ccf-batches-v1"
CONTIG_SCHEMA = "basis-ccf-contig-completion-v1"
BATCH_SCHEMA = "basis-ccf-batch-completion-v1"
COMPLETION = "basis-contig-completion.json"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def batch_size(value=32):
    if type(value) is not int or not 1 <= value <= 1024:
        raise ValueError("modules.crisprcasfinder.batch_size must be an integer from 1 to 1024")
    return value


def make_plan(mapping, size=32):
    size = batch_size(size)
    keys = list(mapping)
    if not keys or any(not re.fullmatch(r"CCF[0-9]{6,}", key) for key in keys):
        raise ValueError("Invalid CRISPR contig identifiers")
    batches = {f"CCB{index // size + 1:06d}": keys[index:index + size]
               for index in range(0, len(keys), size)}
    return {"schema": PLAN_SCHEMA, "batch_size": size,
            "contig_count": len(keys), "batches": batches}


def load_plan(contigs):
    contigs = Path(contigs)
    mapping = extend.read_json(contigs / "contigs.json")
    plan = extend.read_json(contigs / "batches.json")
    if plan != make_plan(mapping, plan.get("batch_size")):
        raise ValueError("CRISPR batch plan does not cover the contig map exactly")
    return mapping, plan


def check_receipt_outputs(receipt, members, native):
    """Cheap, read-only DAG check for durable outputs outside scheduler cleanup."""
    receipt, native = Path(receipt), Path(native)
    if not receipt.is_file():
        return
    row = extend.read_json(receipt)
    # Changed grouping is scheduled from the new checkpoint/config dependencies.
    if row.get("members") != members:
        return
    expected = [native / safe / COMPLETION for safe in members]
    signature = row.get("execution_identity", "")
    if not re.fullmatch(r"[0-9a-f]{64}", signature):
        raise ValueError(f"Invalid CRISPR batch identity in {receipt}")
    for path in expected:
        if not path.is_file():
            raise ValueError(f"CRISPR batch references missing native output: {path}. "
                             f"Remove only its batch receipt {receipt} and resume with "
                             "--forcerun crisprcasfinder_collect to repair this batch.")
    identity = native / ".execution-identities" / (signature + ".json")
    if not identity.is_file():
        raise ValueError(f"CRISPR shared execution identity is missing: {identity}. "
                         f"Restore it, or remove the batch JSON receipts in {receipt.parent} "
                         "and resume with --forcerun crisprcasfinder_collect. Native contig outputs are retained.")


def execution_identity(full_config):
    """Exclude scheduler grouping and receipt timestamps from native identity."""
    config = extend.settings(full_config, "crisprcasfinder")
    resources = []
    for entry in full_config.get("resource_receipts", []):
        path = entry["path"] if isinstance(entry, dict) else entry
        row = extend.read_json(path)
        if row.get("resource") == "crisprcasfinder":
            if row.get("status") != "completed" or not row.get("artifacts"):
                raise ValueError("CRISPR resource receipt is incomplete")
            # Setup verifies these bytes before scheduling. Retain the content
            # identity, not creation times or the location of the outer receipt.
            resources.append(row["artifacts"])
    environments = []
    for path in full_config.get("environment_receipts", []):
        if Path(path).name == "crisprcasfinder.json":
            row = extend.read_json(path)
            if row.get("status") != "completed":
                raise ValueError("CRISPR environment receipt is incomplete")
            environments.append({key: value for key, value in row.items()
                                 if key not in {"created_at", "created_at_utc"}})
    if len(resources) != 1 or len(environments) != 1:
        raise ValueError("Exactly one verified CRISPR resource and environment receipt are required")
    execution = {
        "executable": config.get("executable", "perl"),
        "database": str(Path(config["database"]).resolve()),
        "genetic_code": config.get("genetic_code", 11),
        "min_evidence_level": config.get("min_evidence_level", 3),
    }
    sources = {"run_contig": hashlib.sha256(inspect.getsource(extend.run_contig).encode()).hexdigest(),
               "adapter": extend.pf2._sha256(Path(ccf.__file__)),
               "input_reader": extend.pf2._sha256(Path(inputs.__file__)),
               "runner": extend.pf2._sha256(Path(runtime.__file__))}
    return {"execution": execution, "resource_artifacts": resources[0],
            "environment": environments[0], "sources": sources}


def output_inventory(directory):
    """Record files and symlinks without traversing linked resource folders."""
    directory = Path(directory)
    result = {}
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory).as_posix()
        if relative in {COMPLETION, COMPLETION + ".part", ".snakemake_timestamp"}:
            continue
        if path.is_symlink():
            result[relative] = {"link": os.readlink(path)}
        elif path.is_file():
            result[relative] = {"bytes": path.stat().st_size,
                                "sha256": extend.pf2._sha256(path)}
    if not result:
        raise ValueError("CRISPR contig output is empty")
    return result


def contig_identity(safe, info, signature):
    return digest({"contig": safe, "input": info, "execution": signature})


def verify_contig(work, safe, info, sequence, signature, config):
    if Path(work).is_symlink() or not Path(work).is_dir():
        raise ValueError("CRISPR contig output must be an owned directory")
    row = extend.read_json(Path(work) / COMPLETION)
    if (row.get("schema") != CONTIG_SCHEMA or row.get("status") != "completed"
            or row.get("identity") != contig_identity(safe, info, signature)
            or row.get("execution_identity") != signature
            or row.get("artifacts") != output_inventory(work)):
        raise ValueError("CRISPR contig completion record does not match its inputs and outputs")
    _, status = ccf.parse_contig(Path(work) / "native", safe, info["contig"], sequence,
                                ccf._read(Path(work) / "stdout.txt"),
                                int(config.get("min_evidence_level", 3)))
    ccf._verify_runtime_isolation(Path(work), status)
    return row


@contextmanager
def work_lock(native, name):
    lock = Path(native) / ".batch-locks" / (name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"Another CRISPR worker owns {name}; leave its outputs intact") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def staged_sequence(contigs, safe, info):
    sequences = inputs.read_fasta(Path(contigs) / f"{safe}.fna")
    if list(sequences) != [safe]:
        raise ValueError("CRISPR staged contig identity mismatch")
    sequence = sequences[safe]
    if (len(sequence) != info["length"]
            or hashlib.sha256(sequence.encode("ascii")).hexdigest() != info["sequence_sha256"]):
        raise ValueError("CRISPR staged contig differs from the recorded input")
    return sequence


def run_batch(full_config, contigs, batch, native, receipt, log_directory, threads=None):
    mapping, plan = load_plan(contigs)
    if batch not in plan["batches"]:
        raise ValueError("Unknown CRISPR batch")
    config = extend.settings(full_config, "crisprcasfinder")
    if threads is not None:
        config["threads"] = threads
    context = execution_identity(full_config)
    signature = digest(context)
    native, receipt, log_directory = Path(native), Path(receipt), Path(log_directory)
    native.mkdir(parents=True, exist_ok=True)
    log_directory.mkdir(parents=True, exist_ok=True)
    execution_record = native / ".execution-identities" / (signature + ".json")
    if not execution_record.is_file():
        execution_record.parent.mkdir(parents=True, exist_ok=True)
        # Different batches can publish the same immutable identity at once.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                         dir=execution_record.parent, prefix="identity-", suffix=".part") as handle:
            temporary = Path(handle.name)
            json.dump(context, handle, indent=2, allow_nan=False)
            handle.write("\n")
        temporary.replace(execution_record)
    elif extend.read_json(execution_record) != context:
        raise ValueError("CRISPR execution identity record was modified")
    with work_lock(native, batch):
        receipt.unlink(missing_ok=True)
        completed, reused = {}, []
        for safe in plan["batches"][batch]:
            info = mapping[safe]
            sequence = staged_sequence(contigs, safe, info)
            work = native / safe
            with work_lock(native, safe):
                if work.exists():
                    try:
                        row = verify_contig(work, safe, info, sequence, signature, config)
                    except (OSError, ValueError, KeyError, TypeError):
                        # Retain prior/legacy outputs, but never infer their
                        # original configuration from a parseable result alone.
                        archive = native / ".previous-contigs"
                        archive.mkdir(exist_ok=True)
                        work.rename(archive / (safe + "-" + uuid.uuid4().hex))
                    else:
                        completed[safe] = digest(row)
                        reused.append(safe)
                        print(f"{batch}: reused verified {safe}", flush=True)
                        continue
                print(f"{batch}: running {safe}", flush=True)
                extend.run_contig(config, Path(contigs) / f"{safe}.fna", safe, work,
                                  log_directory / f"{safe}.commands.log")
                row = {"schema": CONTIG_SCHEMA, "status": "completed",
                       "identity": contig_identity(safe, info, signature),
                       "input": info, "execution_identity": signature,
                       "allocated_threads": config.get("threads", 1),
                       "artifacts": output_inventory(work)}
                extend.write_json(work / COMPLETION, row)
                verify_contig(work, safe, info, sequence, signature, config)
                completed[safe] = digest(row)
        extend.write_json(receipt, {"schema": BATCH_SCHEMA, "status": "completed",
                                   "batch": batch, "members": plan["batches"][batch],
                                   "contigs": completed, "reused_contigs": reused,
                                   "execution_identity": signature,
                                   "execution_record": str(execution_record.resolve())})


def verify_batches(full_config, contigs, native, receipts):
    """Require current completion evidence for every contig exactly once."""
    mapping, plan = load_plan(contigs)
    config = extend.settings(full_config, "crisprcasfinder")
    context = execution_identity(full_config)
    signature = digest(context)
    execution_record = Path(native) / ".execution-identities" / (signature + ".json")
    if extend.read_json(execution_record) != context:
        raise ValueError("CRISPR execution identity record was modified")
    observed = set()
    for path in receipts:
        row = extend.read_json(path)
        batch = row.get("batch")
        if (row.get("schema") != BATCH_SCHEMA or row.get("status") != "completed"
                or batch in observed or batch not in plan["batches"]
                or row.get("members") != plan["batches"][batch]
                or set(row.get("contigs", {})) != set(plan["batches"][batch])
                or row.get("execution_identity") != signature):
            raise ValueError("CRISPR batch completion does not match the current batch plan")
        observed.add(batch)
        for safe in plan["batches"][batch]:
            info = mapping[safe]
            sequence = staged_sequence(contigs, safe, info)
            completed = verify_contig(Path(native) / safe, safe, info, sequence, signature, config)
            if row["contigs"][safe] != digest(completed):
                raise ValueError("CRISPR contig completion changed after its batch completed")
    if observed != set(plan["batches"]):
        raise ValueError("CRISPR batch receipts do not cover every supplied contig")
    return {"batch_count": len(observed), "batch_size": plan["batch_size"],
            "artifacts": [str(Path(path)) for path in receipts] + [str(execution_record)]}
