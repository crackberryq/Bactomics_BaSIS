"""Report-only reconciliation of a failed ICEscreen run with a verified zero retry.

Deliberately excludes positive retries: those need renewed context assessment.
This module never invokes a biological tool or writes into a native result tree.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from .adapters.context import _icescreen_input_accessions, parse_icescreen
from .deep_reporting import completion_summary
from .util import atomic_json, hash_object, sha256_file


def _one(items, label):
    if len(items) != 1:
        raise ValueError(f"Expected exactly one {label}; found {len(items)}")
    return items[0]


def _input_records(evidence, retry):
    if evidence.get("schema_version") != "basis-report-1":
        raise ValueError("Expected a basis-report-1 source bundle")
    if retry.get("schema_version") != 1 or retry.get("module") != "icescreen":
        raise ValueError("Expected an ICEscreen schema_version 1 result")
    sid = retry.get("sample_id")
    sample = _one([s for s in evidence["samples"] if s.get("sample_id") == sid], "matching sample")
    original = _one([r for r in evidence["records"] if r.get("sample_id") == sid
                     and r.get("module") == "icescreen"], "original ICEscreen record")
    if original.get("execution_status") != "failed" or original.get("findings") != []:
        raise ValueError("This helper replaces only a failed, empty ICEscreen record")
    if (retry.get("execution_status") != "completed" or retry.get("error")
            or retry.get("interpretation") != "not_detected" or retry.get("findings") != []
            or retry.get("metrics", {}).get("predicted_element_count") != 0):
        raise ValueError("Retry must be completed with zero predicted elements; positive results require context reassessment")
    return sid, sample, original


def verify_retry(evidence, retry):
    """Verify identity, settings and sealed native outputs using local files only."""
    sid, sample, original = _input_records(evidence, retry)
    hashes = original.get("input_hashes") or {}
    if not {"assembly", "proteins", "gff", "genbank"}.issubset(hashes) or retry.get("input_hashes") != hashes:
        raise ValueError("Retry input hashes do not match the source, including GenBank")
    verified = {}
    for key, expected in hashes.items():
        path = Path(sample[key])
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"Current input changed or missing: {key}")
        verified[str(path.resolve())] = expected
    deep = _one([r for r in evidence["deep_records"] if r.get("sample_id") == sid], "matching DeepSafe record")
    if deep.get("execution_status") != "completed":
        raise ValueError("This report-only reconciliation requires completed DeepSafe execution")
    if deep.get("input_hashes") != hashes:
        raise ValueError("DeepSafe and ICEscreen input hashes differ")
    settings = retry.get("effective_config") or {}
    clean = lambda value: {k: v for k, v in value.items() if k != "cache"}
    if (settings.get("mode") != "native" or settings.get("phylum") != "bacillota"
            or clean(settings) != clean(original.get("effective_config") or {})):
        raise ValueError("ICEscreen settings differ from the source attempt")
    provenance = retry.get("provenance") or {}
    resources = provenance.get("resources") or {}
    if not resources or resources != (original.get("provenance") or {}).get("resources"):
        raise ValueError("ICEscreen recorded resource fingerprints differ")
    version = provenance.get("tool_version") or {}
    if version.get("status") != "completed" or version.get("output", "").strip() != "1.3.3":
        raise ValueError("Expected verified ICEscreen 1.3.3 output")
    commands = provenance.get("commands") or []
    if not commands or any(c.get("returncode") != 0 or c.get("error") for c in commands):
        raise ValueError("Retry native commands were not all successful")
    command = _one([c for c in commands if "--galaxy" in c.get("argv", [])], "ICEscreen analysis command")
    argv = command["argv"]
    exe = str(settings.get("executable") or "icescreen")
    if exe not in argv or argv[argv.index("--phylum") + 1] != "bacillota":
        raise ValueError("Unexpected ICEscreen analysis command")
    native = Path(argv[argv.index("-o") + 1]).resolve()
    staged = Path(argv[argv.index("-g") + 1]) / "basis.gbk"
    if not staged.is_file() or sha256_file(staged) != hashes["genbank"]:
        raise ValueError("Staged ICEscreen GenBank does not match the source input")
    verified[str(staged.resolve())] = hashes["genbank"]
    checksums = retry.get("artifact_checksums") or {}
    if not checksums:
        raise ValueError("Retry lacks sealed native artifacts")
    for name, expected in checksums.items():
        path = Path(name)
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"Retry artifact changed or missing: {name}")
        verified[str(path.resolve())] = expected
    paths = [Path(p).resolve() for p in checksums]
    _one([p for p in paths if p.name == "commands.log" and p.parent == native.parent], "sealed command log")
    table = _one([p for p in paths if p.name.endswith("_detected_ME.tsv") and native in p.parents], "sealed element table")
    summary = _one([p for p in paths if p.name.endswith("_detected_ME.summary") and native in p.parents], "sealed native summary")
    accessions = _icescreen_input_accessions(staged)
    parsed = parse_icescreen(table, summary, accessions)
    if parsed["findings"] != [] or parsed["metrics"] != retry["metrics"]:
        raise ValueError("Native ICEscreen outputs disagree with the recorded zero-result metrics")
    return {"sample_id": sid, "verified_files": verified,
            "assessed_genome_accessions": sorted(accessions),
            "recorded_resources_match": True, "native_outputs_reparsed": True,
            "predicted_element_count": 0,
            "unassigned_integrases": sum(v.get("Unassigned Integrase", 0)
                                         for v in parsed["metrics"]["summary"].values())}


def merge_zero_ice(evidence, retry, audit, generated_at):
    """Create a reporting copy; retain candidate evidence and biological holds."""
    sid, _, original = _input_records(evidence, retry)
    merged = copy.deepcopy(evidence)
    review = copy.deepcopy(audit)
    review.update(schema_version="basis-ice-reconciliation-1", native_analyses_run=False,
                  report_generated_at_utc=generated_at, original_run=copy.deepcopy(evidence["run"]),
                  original_ice_record=copy.deepcopy(original), retry_finished_at=retry.get("finished_at"),
                  retry_record_sha256=hash_object(retry), deep_changes=[])
    merged["records"] = [copy.deepcopy(retry) if r.get("module") == "icescreen" and r.get("sample_id") == sid
                         else r for r in merged["records"]]
    for deep in merged["deep_records"]:
        if deep.get("sample_id") != sid:
            continue
        before = hash_object(deep)
        candidates_hash = hash_object(deep.get("candidates", []))
        upstream = deep.get("upstream_incomplete", [])
        removed = {key: deep.pop(key) for key in ("cache_key", "cache_hit", "cache_checked_at", "deep_cache") if key in deep}
        old_notes = copy.deepcopy(deep.get("notes", []))
        old_completion = copy.deepcopy(deep.get("completion_summary"))
        deep["upstream_incomplete"] = [m for m in upstream if m != "icescreen"]
        deep["completion_summary"] = completion_summary(deep)
        historical_note = "Candidate selection is incomplete because requested upstream analyses failed."
        deep["notes"] = ["Historical execution note: " + n if n == historical_note else n for n in old_notes]
        deep["notes"].append("Report-only reconciliation: verified ICEscreen retry has zero element findings. "
                             "ICEscreen availability resolved; candidate analyses, interpretations and deferred scope are unchanged.")
        change = {"sample_id": sid, "original_record_sha256": before,
                  "candidate_evidence_sha256": candidates_hash, "candidates_unchanged": True,
                  "original_upstream_incomplete": upstream, "original_notes": old_notes,
                  "original_completion_summary": old_completion,
                  "removed_cache_metadata": removed, "native_analyses_run": False}
        deep["report_reconciliation"] = copy.deepcopy(change)
        review["deep_changes"].append(change)
        if hash_object(deep.get("candidates", [])) != candidates_hash:
            raise RuntimeError("Candidate evidence changed during reconciliation")
    old_run = evidence["run"]
    run = merged["run"]
    for key in ("source_hash", "config_hash", "evidence_snapshot"):
        run.pop(key, None)
    run.update(run_id="report-reconciliation-" + generated_at,
               engine="saved_evidence_reconciliation", started_at=generated_at, finished_at=generated_at,
               report_reconciliation=review)
    run["incomplete_modules"] = [m for m in old_run.get("incomplete_modules", []) if m != f"{sid}:icescreen"]
    # Retain all pre-existing incompleteness except the verified ICEscreen failure.
    # Never promote budget-deferred DeepSafe work to complete scope.
    for deep in merged["deep_records"]:
        if (deep.get("execution_status") != "completed" or deep.get("complete") is False
                or deep.get("upstream_incomplete") or any(c.get("selected") is False for c in deep.get("candidates", []))):
            label = f"{deep['sample_id']}:deepsafe"
            if label not in run["incomplete_modules"]:
                run["incomplete_modules"].append(label)
    run["completeness"] = "incomplete" if run["incomplete_modules"] else "completed_requested_scope"
    return merged


def reconcile(source, retry_path, outdir):
    from .report import write_report

    source, retry_path, outdir = (Path(p).resolve() for p in (source, retry_path, outdir))
    source_hash, retry_hash = sha256_file(source), sha256_file(retry_path)
    evidence = json.loads(source.read_text(encoding="utf-8"))
    retry = json.loads(retry_path.read_text(encoding="utf-8"))
    print("Verifying saved inputs, ICEscreen commands and native artifact checksums...", flush=True)
    audit = verify_retry(evidence, retry)
    audit.update(source_evidence=str(source), source_sha256=source_hash,
                 retry_evidence=str(retry_path), retry_sha256=retry_hash)
    generated_at = datetime.now(timezone.utc).isoformat()
    merged = merge_zero_ice(evidence, retry, audit, generated_at)
    outdir.mkdir(parents=True, exist_ok=False)
    print("Rendering reconciled report; no native analyses or GPU inference...", flush=True)
    artifacts = write_report(merged["run"], merged["samples"], merged["records"], merged["deep_records"], outdir)
    # Check for concurrent changes before publishing the completion manifest.
    watched = {**audit["verified_files"], str(source): source_hash, str(retry_path): retry_hash}
    if any(not Path(p).is_file() or sha256_file(p) != h for p, h in watched.items()):
        raise RuntimeError("Source/input/artifact changed during rendering; report reconciliation not finalized")
    review = copy.deepcopy(merged["run"]["report_reconciliation"])
    review.update(source_unchanged=True, retry_unchanged=True, artifacts=artifacts,
                  report_finished_at_utc=datetime.now(timezone.utc).isoformat(),
                  renderer_source_sha256={p.name: sha256_file(p) for p in Path(__file__).parent.glob("report*.py")},
                  reconciliation_source_sha256=sha256_file(Path(__file__)),
                  output_file_sha256={str(p.relative_to(outdir)): sha256_file(p) for p in sorted(outdir.rglob("*")) if p.is_file()})
    atomic_json(outdir / "reconciliation.json", review)
    return merged, artifacts
