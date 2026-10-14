"""Pure presentation of recorded execution and scope; no analysis decisions."""
from __future__ import annotations

import copy
import csv
import io
import json
import os
from pathlib import Path
import tempfile

from .deep_reporting import completion_summary
from .report_sample_scope import module_scope_rows, record_requested, scope_label


SCHEMA = "basis-run-summary-v1"
TSV_COLUMNS = (
    "kind", "run_id", "engine", "validation_mode", "deep_requested", "extend_requested",
    "sample_id", "completeness", "requested_modules", "completed_modules", "failed_modules",
    "not_assessed_modules", "planned_runnable_modules", "held", "excluded", "disabled",
    "not_requested", "imported_modules", "cache_reused", "deep_execution",
    "deep_selected_execution", "deep_selected", "deep_completed_selected", "deep_failed_selected",
    "deep_not_assessed_selected", "deep_deferred", "deep_upstream_incomplete", "incomplete_requested",
)


def _options(value, *, force=False, dry_run=False):
    return {"deep": bool(value.get("deep")), "extend": bool(value.get("extend")),
            "force": bool(force), "dry_run": bool(dry_run)}


def _scope_counts(rows):
    labels = [scope_label(row)[0] for row in rows]
    return {"held": sum(row.get("requested") is True and row.get("planned_action") == "withheld" for row in rows),
            "excluded": labels.count("Excluded"), "disabled": labels.count("Disabled"),
            "not_requested": labels.count("Not requested")}


def build_plan_summary(plan, engine="local", force=False):
    """Describe planned scope without creating execution/completion claims."""
    options = _options(plan.get("options", {}), force=force, dry_run=True)
    samples = plan.get("samples", [])
    rows = module_scope_rows(plan, samples, [])
    result = {"schema": SCHEMA, "kind": "plan", "engine": engine,
              "validation_mode": plan.get("config", {}).get("validation_mode", "not_recorded"),
              "options": options, "sample_count": len(samples), "samples": [],
              "plan_path": plan.get("plan_path"), "outdir": plan.get("outdir"),
              "execution": "not_executed"}
    for sample in samples:
        sid = sample["sample_id"]
        own = [row for row in rows if row["sample_id"] == sid]
        counts = _scope_counts(own)
        result["samples"].append({
            "sample_id": sid,
            "planned_modules": {"requested": sum(row["requested"] is True for row in own),
                                "runnable": sum(row["planned_action"] == "run" for row in own), **counts},
            "deepsafe": {"requested": options["deep"], "execution": "not_executed"},
        })
    return result


def _deep_summary(record, requested):
    record = record or {}
    if not requested:
        record = {"execution_status": "not_requested", "completion_summary": {"selected_execution": "not_requested"}}
    if "candidates" in record:
        saved = completion_summary(record)
        selected = [candidate for candidate in record["candidates"] if candidate.get("selected")]
        failed = sum(candidate.get("execution") == "failed" for candidate in selected)
        unassessed = sum(candidate.get("execution") not in {"completed", "failed"} for candidate in selected)
    else:
        saved = record.get("completion_summary") or {}
        failed = saved.get("failed_selected_candidates")
        unassessed = saved.get("not_assessed_selected_candidates")
    return {"requested": bool(requested),
            "execution": record.get("execution_status", "not_recorded"),
            "complete": record.get("complete"),
            "selected_execution": saved.get("selected_execution", "not_recorded"),
            "selected_candidates": saved.get("selected_candidates"),
            "completed_selected_candidates": saved.get("completed_selected_candidates"),
            "failed_selected_candidates": failed, "not_assessed_selected_candidates": unassessed,
            "deferred_candidates": saved.get("deferred_candidates"),
            "upstream_incomplete": copy.deepcopy(record.get("upstream_incomplete", saved.get("upstream_incomplete", []))),
            "cache_reused": bool(requested and record.get("execution_status") == "completed" and record.get("cache_hit") is True),
            "error": record.get("error")}


def build_run_summary(run, samples, records, deep_records):
    """Summarize saved stages while preserving requested-scope incompleteness.

    Upstream module counts exclude DeepSafe. Cache counts include requested,
    completed DeepSafe when reused. Imported counts include cached imports and
    are provenance descriptors, not an additional completion denominator.
    """
    options = _options(run.get("options", {}), force=run.get("force", False))
    result = {"schema": SCHEMA, "kind": "run", "run_id": run.get("run_id"),
              "completeness": run.get("completeness", "not_recorded"),
              "options": options, "engine": run.get("engine", "not_recorded"),
              "engine_error": run.get("engine_error"),
              "validation_mode": run.get("validation_mode", "not_recorded"),
              "sample_count": len(samples), "samples": [],
              "interpretation": "Execution completion does not establish function or organism safety."}
    rows = module_scope_rows(run, samples, records)
    by_key = {}
    for record in records:
        key = (str(record.get("sample_id")), str(record.get("module")))
        by_key.setdefault(key, []).append(record)
    deep_by_sample = {str(record.get("sample_id")): record for record in deep_records}
    for sample in samples:
        sid = sample["sample_id"]
        own = [copy.deepcopy(row) for row in rows if row["sample_id"] == sid]
        # Historical records retain the same legacy requested fallback as reports.
        for row in own:
            matching = by_key.get((sid, row["module"]), [])
            if row["requested"] is None and matching:
                row["requested"] = any(record_requested(record, run) for record in matching)
        counts = {"total": 0, "completed": 0, "failed": 0, "not_assessed": 0}
        incomplete, cache_reused, imported = [], 0, 0
        for row in own:
            if row["requested"] is not True:
                continue
            counts["total"] += 1
            statuses = set(row["execution_status"].split("; "))
            status = ("not_assessed" if row["planned_action"] == "withheld" else
                      "completed" if statuses == {"completed"} else "failed" if "failed" in statuses else "not_assessed")
            counts[status] += 1
            matching = by_key.get((sid, row["module"]), [])
            if status == "completed":
                cache_reused += any(record.get("cache_hit") is True for record in matching)
                imported += any((record.get("effective_config") or {}).get("mode") == "import" or record.get("imported_evidence") for record in matching)
            else:
                reasons = [str(record.get("error") or record.get("reason")) for record in matching if record.get("error") or record.get("reason")]
                reason = "; ".join(dict.fromkeys(reasons)) or (row["reason"] if row["planned_action"] == "withheld" else "Requested result not completed or not recorded")
                incomplete.append({"module": row["module"], "status": row["execution_status"], "reason": reason})
        deep = _deep_summary(deep_by_sample.get(sid), options["deep"])
        if options["deep"]:
            if deep["execution"] != "completed":
                incomplete.append({"module": "deepsafe", "status": deep["execution"], "reason": deep["error"] or "Requested DeepSafe execution not completed"})
            elif deep["complete"] is False or deep["upstream_incomplete"]:
                reasons = []
                if deep["complete"] is False:
                    reasons.append("Recorded DeepSafe scope incomplete")
                if deep["deferred_candidates"]:
                    reasons.append(f"{deep['deferred_candidates']} candidates deferred")
                if deep["upstream_incomplete"]:
                    reasons.append("incomplete upstream: " + ", ".join(map(str, deep["upstream_incomplete"])))
                incomplete.append({"module": "deepsafe", "status": deep["execution"], "reason": "; ".join(reasons)})
        # Keep authoritative run-level incompleteness even if old detail is absent.
        for identifier in run.get("incomplete_modules", []):
            if str(identifier).startswith(sid + ":"):
                module = str(identifier)[len(sid) + 1:]
                if not any(item["module"] == module for item in incomplete):
                    incomplete.append({"module": module, "status": "incomplete", "reason": "Recorded in run.incomplete_modules"})
        result["samples"].append({"sample_id": sid,
            "completeness": "incomplete" if incomplete else "completed_requested_scope",
            "requested_modules": counts, **_scope_counts(own),
            "imported_modules": int(imported), "cache_reused": int(cache_reused) + int(deep["cache_reused"]),
            "deepsafe": deep, "incomplete_requested": incomplete})
    return result


def _tsv_value(value):
    if value is None:
        return ""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
    return "'" + text if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")) else text


def _summary_rows(summary):
    for sample in summary.get("samples", []):
        deep = sample.get("deepsafe", {})
        counts = sample.get("requested_modules", {})
        planned = sample.get("planned_modules", {})
        yield {"kind": summary.get("kind"), "run_id": summary.get("run_id"), "engine": summary.get("engine"),
            "validation_mode": summary.get("validation_mode"),
            "deep_requested": summary.get("options", {}).get("deep"), "extend_requested": summary.get("options", {}).get("extend"),
            "sample_id": sample.get("sample_id"), "completeness": sample.get("completeness"),
            "requested_modules": counts.get("total", planned.get("requested")),
            "completed_modules": counts.get("completed"), "failed_modules": counts.get("failed"),
            "not_assessed_modules": counts.get("not_assessed"), "planned_runnable_modules": planned.get("runnable"),
            **{key: sample.get(key, planned.get(key)) for key in ("held", "excluded", "disabled", "not_requested")},
            "imported_modules": sample.get("imported_modules"), "cache_reused": sample.get("cache_reused"),
            "deep_execution": deep.get("execution"), "deep_selected_execution": deep.get("selected_execution"),
            "deep_selected": deep.get("selected_candidates"), "deep_completed_selected": deep.get("completed_selected_candidates"),
            "deep_failed_selected": deep.get("failed_selected_candidates"), "deep_not_assessed_selected": deep.get("not_assessed_selected_candidates"),
            "deep_deferred": deep.get("deferred_candidates"), "deep_upstream_incomplete": deep.get("upstream_incomplete"),
            "incomplete_requested": sample.get("incomplete_requested")}


def write_run_summary(summary, outdir):
    """Stage both exports, then replace each destination atomically."""
    root = Path(outdir).resolve()
    payload = json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=TSV_COLUMNS, delimiter="\t")
    writer.writeheader()
    writer.writerows({key: _tsv_value(row.get(key)) for key in TSV_COLUMNS} for row in _summary_rows(summary))
    root.mkdir(parents=True, exist_ok=True)
    staged = []
    try:
        for name, text in (("run-summary.json", payload), ("run-summary.tsv", stream.getvalue())):
            fd, path = tempfile.mkstemp(prefix="." + name + ".", dir=root)
            staged.append((Path(path), root / name))
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
        for temporary, target in staged:
            os.replace(temporary, target)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
    return {"summary_json": str(root / "run-summary.json"), "summary_tsv": str(root / "run-summary.tsv")}


def _display(value):
    return "not recorded" if value is None else str(value)


def format_run_summary(summary, artifacts=None):
    """Human output only; never inspect paths or reinterpret biological evidence."""
    options = summary.get("options", {})
    synthetic = "synthetic" in str(summary.get("validation_mode", "")).lower()
    heading = "BaSIS plan" if summary.get("kind") == "plan" else "BaSIS run"
    if synthetic:
        heading += " [SYNTHETIC — software check only]"
    lines = [heading + " | engine: " + _display(summary.get("engine")) +
             " | DeepSafe: " + ("on" if options.get("deep") else "off") +
             " | Extend: " + ("on" if options.get("extend") else "off") +
             " | force: " + ("yes" if options.get("force") else "no")]
    if summary.get("kind") == "plan":
        lines.append(f"Plan only — no analyses executed. Samples: {summary.get('sample_count', 0)}")
        for sample in summary.get("samples", []):
            counts = sample.get("planned_modules", {})
            lines.append(f"{sample['sample_id']}: requested modules {counts.get('requested', 0)}; runnable {counts.get('runnable', 0)}; held {counts.get('held', 0)}; excluded {counts.get('excluded', 0)}; disabled {counts.get('disabled', 0)}")
        if summary.get("plan_path"):
            lines.append("Plan: " + str(summary["plan_path"]))
        if summary.get("outdir"):
            lines.append("Output directory: " + str(summary["outdir"]))
    else:
        lines.append("Requested scope: " + _display(summary.get("completeness")) + f" | Samples: {summary.get('sample_count', 0)}")
        if summary.get("engine_error"):
            lines.append("Engine error: " + str(summary["engine_error"]))
        for sample in summary.get("samples", []):
            counts = sample.get("requested_modules", {})
            lines.append(f"{sample['sample_id']}: {sample.get('completeness', 'not recorded')} | modules {counts.get('completed', 0)}/{counts.get('total', 0)} completed, {counts.get('failed', 0)} failed, {counts.get('not_assessed', 0)} not assessed")
            lines.append(f"  Scope: {sample.get('held', 0)} held; {sample.get('excluded', 0)} excluded; {sample.get('disabled', 0)} disabled. Imported evidence: {sample.get('imported_modules', 0)} modules; cached stages: {sample.get('cache_reused', 0)}.")
            deep = sample.get("deepsafe", {})
            if deep.get("requested"):
                lines.append("  DeepSafe execution: " + _display(deep.get("execution")) +
                             "; selected " + _display(deep.get("completed_selected_candidates")) + "/" + _display(deep.get("selected_candidates")) +
                             " completed (" + _display(deep.get("selected_execution")) + "); failed selected: " + _display(deep.get("failed_selected_candidates")) +
                             "; deferred: " + _display(deep.get("deferred_candidates")) + ".")
                if deep.get("upstream_incomplete"):
                    lines.append("  Incomplete upstream: " + ", ".join(map(str, deep["upstream_incomplete"])))
            for item in sample.get("incomplete_requested", []):
                lines.append("  Incomplete requested " + str(item.get("module")) + ": " + _display(item.get("reason")))
        lines.append("Execution completion does not establish function or organism safety.")
    for key, label in (("html", "Report"), ("evidence_json", "Evidence"), ("summary_json", "Run summary JSON"), ("summary_tsv", "Run summary TSV")):
        if artifacts and artifacts.get(key):
            lines.append(label + ": " + str(artifacts[key]))
    return "\n".join(lines)
