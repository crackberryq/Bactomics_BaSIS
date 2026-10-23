"""Verify complete and explicitly partial IntegronFinder output inventories.

Native command construction is unchanged. A successful native process may skip
a record after writing an empty protein file; that record remains not assessed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import re

from basis.adapters import context
from basis.inputs import read_fasta
from . import native, result_store, runtime

MODULE = "integronfinder"
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_PROCESSING = re.compile(r"^INFO\s*:\s*############ Processing replicon (.+) \((\d+)/(\d+)\) ############\s*$")
_WRITTEN = re.compile(r"^INFO\s*:\s*Writing out results for replicon (.+?)\s*$")
_EMPTY = re.compile(r"^WARNING\s*:\s*The protein file: '(.+)' is empty cannot perform hmmsearch on it\.\s*$")
_SKIP = re.compile(r"^WARNING\s*:\s*############ Skip replicon (.+) ############\s*$")
_ERROR = re.compile(r"^(?:ERROR|CRITICAL)\s*:|^Traceback \(most recent call last\):", re.MULTILINE)


def parse_payload(sample, settings, payload, *, origin_payload=None):
    """Reconcile rows with the input inventory and exact native skip records.

    ``origin_payload`` is used only by verified recovery of an existing attempt:
    copied native logs still contain the original absolute temporary paths.
    """
    del settings  # Search settings belong to unchanged native.command_shell.
    payload = Path(payload).absolute()
    origin = Path(origin_payload).absolute() if origin_payload is not None else payload
    sequences = read_fasta(sample["assembly"])
    expected = set(sequences)
    if any(Path(name).name != name or name in {".", ".."} for name in expected):
        raise ValueError("Input identifiers cannot be bound safely to native diagnostic paths")
    output = payload / "output"
    detail = context._one(output, "*.integrons")
    summary = context._one(output, "*.summary")
    parsed = context.parse_integronfinder(detail, summary)
    observed = {row["ID_replicon"] for row in parsed["metrics"]["summary"]}
    if extra := observed - expected:
        raise ValueError("IntegronFinder summary contains unexpected contigs: " + ", ".join(sorted(extra)))

    stdout = (payload / "stdout.txt").read_text(encoding="utf-8", errors="strict")
    stderr = (payload / "stderr.txt").read_text(encoding="utf-8", errors="strict")
    if _ERROR.search(_ANSI.sub("", stdout + "\n" + stderr)):
        raise ValueError("IntegronFinder native diagnostics contain an error")
    processed, written, positions, skipped = Counter(), Counter(), set(), {}
    current, pending = None, None
    original_result_dir = origin / summary.parent.relative_to(payload)
    for raw in stdout.splitlines():
        line = _ANSI.sub("", raw).strip()
        if match := _PROCESSING.fullmatch(line):
            current, position, total = match.group(1), int(match.group(2)), int(match.group(3))
            if (current not in expected or total != len(expected) or not 1 <= position <= total
                    or position in positions or pending is not None):
                raise ValueError("IntegronFinder processing log disagrees with input inventory")
            positions.add(position)
            processed[current] += 1
        elif match := _EMPTY.fullmatch(line):
            if current is None or pending is not None:
                raise ValueError("Unpaired IntegronFinder empty-protein warning")
            path = Path(match.group(1))
            wanted = original_result_dir / ("tmp_" + current) / (current + ".prt")
            if not path.is_absolute() or path != wanted:
                raise ValueError("IntegronFinder empty-protein warning belongs to a different record or attempt")
            pending = {"contig": current, "protein_path": str(path), "warning": line}
        elif match := _SKIP.fullmatch(line):
            contig = match.group(1)
            if (pending is None or contig != current or contig != pending["contig"]
                    or contig in skipped):
                raise ValueError("Unexplained or duplicate IntegronFinder skip")
            skipped[contig] = {**pending, "execution_status": "not_assessed",
                                "reason": "native_empty_protein_file", "skip_warning": line}
            pending = None
        elif match := _WRITTEN.fullmatch(line):
            contig = match.group(1)
            if contig != current or pending is not None or contig in skipped:
                raise ValueError("IntegronFinder result-writing log conflicts with processing or skip records")
            written[contig] += 1
        elif re.match(r"^WARNING\s*:.*\b[Ss]kipp?(?:ing)?\b", line):
            raise ValueError("Unsupported IntegronFinder skip requires review: " + line)
    if pending is not None:
        raise ValueError("Unpaired IntegronFinder empty-protein warning")
    if set(processed) != expected or any(n != 1 for n in processed.values()):
        raise ValueError("IntegronFinder processing log does not account for exactly the input contigs")
    if set(written) != observed or any(n != 1 for n in written.values()):
        raise ValueError("IntegronFinder summary and result-writing log disagree")
    missing = expected - observed
    if missing != set(skipped):
        raise ValueError("IntegronFinder missing summary rows are not exactly explained by native skip records")

    unassessed = [skipped[contig] for contig in sorted(skipped)]
    parsed["scope_incomplete"] = bool(unassessed)
    parsed["metrics"]["contig_assessment"] = {
        "schema": "basis-integron-contig-assessment-v1",
        "status": "partial" if unassessed else "complete",
        "input_contigs": len(expected), "assessed_contigs": len(observed),
        "not_assessed_contigs": len(unassessed),
        "assessed_contig_ids": sorted(observed), "unassessed": unassessed,
    }
    parsed["artifacts"] = sorted(set(parsed.get("artifacts", []) +
                                     [str(payload / "stdout.txt"), str(payload / "stderr.txt")]))
    if unassessed:
        parsed["reason"] = "integronfinder_skipped_contigs"
        if not parsed["findings"]:
            parsed["interpretation"] = "inconclusive"
        parsed.setdefault("notes", []).append(
            f"Native execution completed, but {len(unassessed)} of {len(expected)} contigs were not assessed "
            "because the native tool reported an empty protein file. These are not negative results. "
            "Unassessed contigs: " + ", ".join(row["contig"] for row in unassessed))
    return parsed


def verify(sample, settings, payload, *, origin_payload=None):
    parsed = parse_payload(sample, settings, payload, origin_payload=origin_payload)
    runtime.write_json(Path(payload) / "parsed.json", parsed)
    return parsed


def execute(sample, config, destination, receipt, store_root, *, threads, sources=()):
    config = copy.deepcopy(config)
    settings = config["modules"][MODULE]
    settings["threads"] = int(threads)
    identity = result_store.execution_identity(MODULE, sample, config, sources=sources,
        executable=settings.get("executable") or native.EXECUTABLES[MODULE])

    def run(payload):
        runner = runtime.DirectRunner(settings, payload.parent / "commands.log")
        try:
            runner.run(["bash", "-euo", "pipefail", "-c",
                        native.command_shell(MODULE, sample, settings, payload, int(threads))])
        finally:
            runtime.write_json(payload.parent / "commands.json", runner.commands)

    record, reused = result_store.run_cached(MODULE, identity, destination, receipt, store_root,
        run, lambda payload: verify(sample, settings, payload),
        failed_retention=settings.get("failed_attempts_to_keep", 3),
        allow_reuse=settings.get("cache", True))
    print(json.dumps({"status": "completed", "module": MODULE, "reused": reused,
                      "receipt": str(receipt), "payload_root": record["payload_root"]}))
    return record, reused


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", choices=(MODULE,), default=MODULE)
    for flag in ("sample", "config", "native", "receipt", "store"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--sources", nargs="*", default=[])
    args = parser.parse_args(argv)
    execute(runtime.read_json(args.sample), runtime.read_json(args.config), args.native,
            args.receipt, args.store, threads=args.threads, sources=args.sources)


if __name__ == "__main__":
    main()
