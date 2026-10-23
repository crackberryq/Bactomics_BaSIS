"""One checked native command inside a Snakemake job, with durable results.

Snakemake remains the scheduler and selects the environment. This boundary
keeps attempt artifacts outside disposable readiness receipts and uses the
same native command construction and result verification as the native rules.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil

from . import native
from .result_store import execution_identity, run_cached, verify_receipt
from .runtime import DirectRunner, read_json


def execute(module, sample, config, destination, receipt, store_root, *, threads,
            sources=(), database_receipt=None, load_database=False):
    config = copy.deepcopy(config)
    settings = config["modules"][module]
    settings["threads"] = int(threads)
    retention = settings.get("failed_attempts_to_keep", 3)
    if type(retention) is not int or not 1 <= retention <= 100:
        raise ValueError("failed_attempts_to_keep must be an integer from 1 to 100")
    loaded_database = None
    extra_inputs = []
    if database_receipt:
        loaded_database = verify_receipt(database_receipt, Path(destination).parent / "database")
        extra_inputs.append(database_receipt)
    identity_config = copy.deepcopy(config)
    identity_sample = sample
    if load_database:
        # Database indexing does not depend on sample sequences or analysis policy.
        identity_config["modules"][module] = {key: value for key, value in settings.items()
            if key in {"database", "executable", "database_version"}}
        identity_sample = {}
    identity = execution_identity(module, identity_sample, identity_config,
                                  sources=sources, extra_inputs=extra_inputs,
                                  executable=settings.get("executable") or native.EXECUTABLES[module],
                                  require_sample_inputs=not load_database)

    def run(payload):
        local_database = None
        if loaded_database:
            # Native analysis can create indexes beneath localDB. Give this
            # attempt its own real copies, including dereferenced linked files;
            # writes must never reach the verified loader snapshot. Cache hits
            # skip this callback and therefore need no database copy.
            local_database = payload.parent / "database-work"
            shutil.copytree(loaded_database["payload_root"], local_database)
        body = (native.rgi_load_shell(settings, payload) if load_database else
                native.command_shell(module, sample, settings, payload, int(threads),
                                     local_database=local_database))
        runner = DirectRunner(settings, payload.parent / "commands.log")
        try:
            runner.run(["bash", "-euo", "pipefail", "-c", body])
        finally:
            (payload.parent / "commands.json").write_text(
                json.dumps(runner.commands, indent=2, allow_nan=False) + "\n")

    def check(payload):
        if load_database:
            if not (payload / "localDB").is_dir():
                raise ValueError("RGI local database preparation did not create localDB")
        else:
            native.verify(module, sample, settings, payload)
            if loaded_database:
                # This workspace is reproducible from the loader snapshot. Keep
                # it on failure for diagnostics, but do not retain a database
                # duplicate after the native output has passed verification.
                shutil.rmtree(payload.parent / "database-work")

    record, reused = run_cached(module + ("_database" if load_database else ""), identity,
                                destination, receipt, store_root, run, check,
                                failed_retention=retention,
                                allow_reuse=settings.get("cache", True))
    print(json.dumps({"status": "completed", "module": module,
                      "reused": reused, "receipt": str(receipt),
                      "payload_root": record["payload_root"]}))
    return record, reused


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", choices=native.MODULES, required=True)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--native", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--threads", required=True, type=int)
    parser.add_argument("--sources", nargs="*", default=[])
    parser.add_argument("--database-receipt")
    parser.add_argument("--load-database", action="store_true")
    args = parser.parse_args(argv)
    if args.load_database and args.module != "rgi":
        parser.error("--load-database applies only to rgi")
    execute(args.module, read_json(args.sample), read_json(args.config), args.native,
            args.receipt, args.store, threads=args.threads, sources=args.sources,
            database_receipt=args.database_receipt, load_database=args.load_database)


if __name__ == "__main__":
    main()
