"""Render saved Snakemake evidence without invoking native tools or a scheduler.

The native analysis jobs own their evidence records. This module only checks
that the requested records exist, aggregates them and reuses BaSIS's offline
visualizations. Deferred candidates and explicit applicability holds retain
incomplete scope even when every selected analysis has finished.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


RELOCATIONS = {
    "findings.tsv": "tables/findings.tsv",
    "recommendations.tsv": "tables/recommendations.tsv",
    "module-scope.tsv": "tables/module-scope.tsv",
    "deferred-candidates.tsv": "tables/deferred-candidates.tsv",
    "run-summary.tsv": "tables/run-summary.tsv",
    "run-summary.json": "metadata/run-summary.json",
    "methods.txt": "metadata/methods.txt",
    "report-navigation.json": "metadata/report-navigation.json",
    "CITATION.cff": "metadata/citations/CITATION.cff",
    "CITATION.txt": "metadata/citations/CITATION.txt",
    "BaSIS.bib": "metadata/citations/BaSIS.bib",
    "BaSIS.ris": "metadata/citations/BaSIS.ris",
}
STATUSES = {"completed", "failed", "blocked", "not_started", "not_assessed", "not_requested"}


def _read_snapshot(path: Path) -> tuple[dict, str]:
    contents = path.read_bytes()
    value = json.loads(contents.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value, hashlib.sha256(contents).hexdigest()


def _read(path: Path) -> dict:
    return _read_snapshot(path)[0]


def _sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _enabled(value) -> bool:
    return value is True or isinstance(value, dict) and value.get("enabled") is True


def _decision(module: str, record: dict, config: dict, sid: str) -> dict:
    decisions = config.get("module_decisions") or {}
    if sid in decisions:
        decisions = decisions[sid]
    value = decisions.get(module) if isinstance(decisions, dict) else None
    if value is None:
        value = record.get("module_decision")
    if value is not None:
        if not isinstance(value, dict) or not isinstance(value.get("requested"), bool):
            raise ValueError(f"Invalid scope decision for {module}")
        return copy.deepcopy(value)
    requested = module in config.get("requested_modules", []) or _enabled((config.get("modules") or {}).get(module))
    if module == "deepsafe":
        requested = _enabled(config["deep"]) if "deep" in config else True
    # Evidence supplied by an explicit report invocation is requested by default
    # unless the configured module is explicitly disabled.
    if module not in (config.get("modules") or {}) and module != "deepsafe":
        requested = True
    return {"requested": requested,
            "action": "run" if requested else "disabled",
            "scope_incomplete": requested and record.get("execution_status") != "completed",
            "reason": record.get("reason") or ("requested" if requested else "module_disabled")}


def _view_config(config: dict, view: str) -> dict:
    """Derive presentation scope without changing the saved execution config."""
    if view not in {"combined", "screening", "deep"}:
        raise ValueError("Report view must be combined, screening or deep")
    result = copy.deepcopy(config)
    if view == "screening":
        result["deep"] = {"enabled": False}
        result["requested_modules"] = [name for name in result.get("requested_modules", [])
                                       if name != "deepsafe"]
        for decisions in [result.get("module_decisions", {})]:
            if isinstance(decisions, dict):
                decisions.pop("deepsafe", None)
                for value in decisions.values():
                    if isinstance(value, dict):
                        value.pop("deepsafe", None)
    elif view == "deep" and "deep" in config and not _enabled(config["deep"]):
        raise ValueError("DeepSafe report conflicts with explicitly disabled report scope")
    return result


def aggregate(sample: dict, config: dict, paths: list[Path], *,
              view: str = "combined") -> tuple[dict, list[dict], list[dict]]:
    """Validate identity and completion records, retaining interpretation limits."""
    config = _view_config(config, view)
    sid = sample.get("sample_id")
    if not isinstance(sid, str) or not sid:
        raise ValueError("Sample metadata lacks sample_id")
    records, deep_records, seen, sources, decisions = [], [], set(), [], {}
    for path in paths:
        record, source_sha256 = _read_snapshot(path)
        module = record.get("module")
        if record.get("sample_id") != sid:
            raise ValueError(f"Evidence sample identity mismatch: {path}")
        if not isinstance(module, str) or not module or module in seen:
            raise ValueError(f"Missing or duplicate evidence module: {module}")
        if module == "deepsafe" and "deep" in config and not _enabled(config["deep"]):
            raise ValueError("DeepSafe evidence conflicts with explicitly disabled report scope")
        seen.add(module)
        status = record.get("execution_status")
        if status not in STATUSES:
            raise ValueError(f"Unrecognized execution status for {module}: {status}")
        provisional_parent = view == "deep" and module == "deepsafe"
        if status in {"failed", "blocked", "not_started"} and not provisional_parent:
            raise ValueError(f"Cannot finalize report: {module} is {status}; repair the failed Snakemake job")
        if not isinstance(record.get("findings", []), list):
            raise ValueError(f"Invalid findings for {module}")
        record.setdefault("findings", [])
        record.setdefault("metrics", {})
        record.setdefault("artifacts", [])
        decision = _decision(module, record, config, sid)
        if view == "deep" and module == "deepsafe" and not decision.get("requested"):
            raise ValueError("DeepSafe follow-up report conflicts with unrequested saved scope")
        # A completed process may explicitly leave part of its input unassessed.
        # Planning metadata cannot clear that observed limitation, nor can an
        # evidence record clear an existing planning hold.
        if "scope_incomplete" in record:
            if not isinstance(record["scope_incomplete"], bool):
                raise ValueError(f"Invalid evidence scope_incomplete for {module}")
            if record["scope_incomplete"]:
                if not isinstance(record.get("reason"), str) or not record["reason"].strip():
                    raise ValueError(f"Incomplete evidence requires an explicit reason: {module}")
                decision["scope_incomplete"] = True
                if decision.get("reason") != record["reason"]:
                    decision.setdefault("declared_reason", decision.get("reason"))
                    decision["reason"] = record["reason"]
        if status in {"not_assessed", "not_requested"} and not (record.get("reason") or decision.get("reason")):
            raise ValueError(f"Unassessed record requires an explicit reason: {module}")
        if status == "completed" and decision.get("action") == "disabled":
            raise ValueError(f"Completed evidence conflicts with disabled module: {module}")
        decisions[module] = decision
        record["module_decision"] = decision
        if module == "deepsafe":
            from basis.deep_reporting import completion_summary
            if (not isinstance(record.get("candidates", []), list)
                    or any(not isinstance(c, dict) for c in record.get("candidates", []))):
                raise ValueError("Invalid DeepSafe candidate list")
            if any(c.get("sample_id") not in (None, "", sid) for c in record.get("candidates", [])):
                raise ValueError("DeepSafe candidate sample identity mismatch")
            record["completion_summary"] = completion_summary(record)
            # A completed module with a failed selected candidate is inconsistent.
            if status == "completed" and any(c.get("selected") and c.get("execution") != "completed"
                                             for c in record.get("candidates", [])):
                raise ValueError("Completed DeepSafe record contains unfinished selected candidates")
            deep_records.append(record)
        else:
            records.append(record)
        sources.append({"module": module, "path": str(path.resolve()), "sha256": source_sha256})

    configured = config.get("modules") or {}
    requested = set(config.get("requested_modules") or [])
    requested.update(module for module, value in configured.items() if _enabled(value))
    if _enabled(config.get("deep", {})) or view == "deep":
        requested.add("deepsafe")
    if missing := requested - seen:
        raise ValueError("Missing requested evidence: " + ", ".join(sorted(missing)))
    for module, value in configured.items():
        if module in seen or _enabled(value):
            continue
        omitted = {"sample_id": sid, "module": module, "execution_status": "not_assessed",
                   "interpretation": "unavailable", "reason": "module_disabled", "findings": [],
                   "metrics": {}, "artifacts": [], "effective_config": {"enabled": False}}
        decision = _decision(module, omitted, config, sid)
        omitted["module_decision"] = decision
        decisions[module] = decision
        records.append(omitted)

    incomplete = [f"{sid}:{r['module']}" for r in records + deep_records
                  if decisions[r["module"]].get("requested") and
                  (r["execution_status"] != "completed" or decisions[r["module"]].get("scope_incomplete"))]
    for record in deep_records:
        summary = record["completion_summary"]
        if (record.get("complete") is False or record.get("upstream_incomplete")
                or summary["deferred_candidates"] or summary["selected_execution"] == "incomplete"):
            incomplete.append(f"{sid}:deepsafe")
    serialized = json.dumps({"sample": sample, "config": config, "sources": sources}, sort_keys=True).encode()
    run = {
        "schema": "basis-snakemake-run-v1", "run_id": hashlib.sha256(serialized).hexdigest()[:20],
        "report_view": view,
        "version": config.get("version", "0.4.0a1"), "engine": "snakemake",
        "options": {"deep": bool(deep_records), "extend": "pathogenfinder2" in seen},
        "validation_mode": config.get("validation_mode", "research_screening"),
        "enabled_modules": sorted(m for m, d in decisions.items() if d.get("requested") and m != "deepsafe"),
        "module_decisions": {sid: decisions},
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "completeness": "incomplete" if incomplete else "completed_requested_scope",
        "incomplete_modules": sorted(set(incomplete)),
        "evidence_sources": sources,
        "reporting_scope": "Saved evidence aggregation only; no native analyses or model inference performed by reporting.",
        "input_validations": {sid: sample.get("input_validation", {})},
    }
    return run, records, deep_records


def _deep_file(relative: Path) -> bool:
    """Identify historical DeepSafe files without opening or changing them."""
    parts = relative.parts
    return (parts[0] == "deepsafe"
            or len(parts) > 1 and parts[0] in {"native", "logs", "benchmarks"} and parts[1] == "deepsafe"
            or relative.as_posix() in {"evidence/deepsafe.json", "logs/configs/deepsafe.log",
                                       "logs/deepsafe-report.log", "benchmarks/deepsafe-report.tsv"})


def _file_index(root: Path, *, include_deep=True, evidence_root: Path | None = None,
                deep_only=False) -> None:
    """Expose isolate-local files without following symlinks outside the bundle."""
    files = []
    source_root = evidence_root or root
    for folder in ("native", "deepsafe", "evidence", "logs", "benchmarks"):
        directory = source_root / folder
        if (not directory.exists() or directory.is_symlink()
                or not include_deep and _deep_file(Path(folder))):
            continue
        for current, directories, names in os.walk(directory, followlinks=False):
            current = Path(current)
            directories[:] = sorted(name for name in directories
                if include_deep or not _deep_file((current / name).relative_to(source_root)))
            for name in sorted(names):
                path = current / name
                relative = path.relative_to(source_root)
                if ((not include_deep and _deep_file(relative)) or not path.is_file()
                        or path.is_symlink() or not path.resolve().is_relative_to(source_root)
                        or deep_only and not _deep_file(relative)):
                    continue
                files.append({"path": relative.as_posix(), "bytes": path.stat().st_size,
                              "href": quote(Path(os.path.relpath(path, root / "metadata")).as_posix(), safe="/")})
    files.sort(key=lambda item: item["path"])
    _write_json(root / "metadata/files.json", {"schema": "basis-local-files-v1", "files": files,
                "scope": "Local file navigation; availability does not independently verify file contents."})
    rows = "".join('<tr><td><a href="' + f["href"] + '">' + html.escape(f["path"])
                   + '</a></td><td>' + f'{f["bytes"]:,}' + '</td></tr>' for f in files)
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>BaSIS · Run files</title><style>body{font:15px/1.6 system-ui;background:#f3f7f9;color:#173a4a;margin:0}'
            'main{max-width:1100px;margin:auto;padding:32px}a{color:#087d79}table{width:100%;border-collapse:collapse;background:white}'
            'th,td{text-align:left;padding:10px;border-bottom:1px solid #dce7ec}td:first-child{overflow-wrap:anywhere}</style>'
            '<main><a href="../report.html">← Back to report</a><h1>Run files</h1>'
            '<p>Outputs, logs and benchmarks within this report scope. Files are linked in place.</p>'
            '<p><a href="files.json" download>Download index · JSON</a></p><table><thead><tr><th>File</th><th>Bytes</th></tr></thead>'
            '<tbody>' + rows + '</tbody></table></main></html>')
    (root / "metadata/files.html").write_text(page, encoding="utf-8")


def export_workflow_coverage(deep_records, root):
    """Export every candidate; no truncation and no reconstruction of old routing."""
    from basis.deep_workflow import SCHEMA, SCOPE, candidate_workflow, summarize
    rows, inventories = [], []
    for record in deep_records:
        inventory = (record.get("workflow_coverage") or {}).get("family_inventory")
        if inventory is not None:
            inventories.append({"sample_id": record["sample_id"], "inventory": inventory})
        for candidate in record.get("candidates", []):
            saved = candidate.get("workflow_coverage") or {}
            valid = (saved.get("schema") == SCHEMA
                     and saved.get("candidate_id") == candidate.get("candidate_id")
                     and saved.get("protein_id") == candidate.get("protein_id")
                     and isinstance(saved.get("selected"), bool)
                     and saved.get("selected") == candidate.get("selected")
                     and saved.get("sample_id") in {None, record["sample_id"]})
            row = copy.deepcopy(saved) if valid else candidate_workflow({
                **candidate, "family_routing": {}})
            row["sample_id"] = record["sample_id"]
            rows.append(row)
    json_path, tsv_path = root / "metadata/deep-workflow.json", root / "metadata/deep-workflow.tsv"
    _write_json(json_path, {"schema": "basis-deep-workflow-bundle-v1", "scope": SCOPE,
                           "summary": summarize(rows), "inventories": inventories, "candidates": rows})
    fields = ["sample_id", "candidate_id", "protein_id", "selected", "selection_queue",
              "execution", "routing_status", "profile_id", "profile_status", "matched_panels",
              "placement_status", "placement_reason", "adequacy_reasons", "interpretation", "followup", "stages"]
    with tsv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            item = {key: row.get(key) for key in fields}
            profile, placement = row.get("profile") or {}, row.get("placement") or {}
            item.update(profile_id=profile.get("id"), profile_status=profile.get("status"),
                        matched_panels=json.dumps(placement.get("matched_panels", [])),
                        placement_status=placement.get("status"), placement_reason=placement.get("reason"),
                        adequacy_reasons=json.dumps(placement.get("adequacy_reasons", [])),
                        stages=json.dumps(row.get("stages", []), sort_keys=True))
            # TSV is commonly opened in a spreadsheet. JSON preserves exact text.
            writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v
                             for k, v in item.items()})
    return {"json": str(json_path), "tsv": str(tsv_path)}


def render(sample_path: Path, config_path: Path, evidence_paths: list[Path], outdir: Path, *,
           view: str = "combined", evidence_root: Path | None = None) -> dict:
    """Write the polished offline bundle into the isolate's safety directory."""
    if view == "deep" and Path(outdir).is_symlink():
        raise ValueError("DeepSafe report output must not be a symlink")
    sample, sample_sha256 = _read_snapshot(sample_path)
    config, config_sha256 = _read_snapshot(config_path)
    run, records, deep_records = aggregate(sample, config, evidence_paths, view=view)
    from basis.report_scope import deep_requested, validate_report_output_paths
    include_deep = deep_requested(run, deep_records)
    root = outdir.resolve()
    source_root = Path(evidence_root).resolve() if evidence_root is not None else root
    if evidence_root is not None and root != (source_root / "deepsafe-report" if view == "deep" else source_root):
        raise ValueError("External evidence root is allowed only for its deepsafe-report subdirectory")
    validate_report_output_paths(root)
    root.mkdir(parents=True, exist_ok=True)
    folders = ("tables", "metadata") if view == "deep" else ("tables", "metadata", "native", "logs", "benchmarks")
    for name in folders + (("deepsafe",) if include_deep and view != "deep" else ()):
        (root / name).mkdir(exist_ok=True)
    run["report_inputs"] = {"sample": {"path": str(sample_path.resolve()), "sha256": sample_sha256},
                            "config": {"path": str(config_path.resolve()), "sha256": config_sha256}}
    run["config_hash"] = run["report_inputs"]["config"]["sha256"]
    coverage_artifacts = export_workflow_coverage(deep_records, root) if include_deep else {}
    if include_deep:
        run["deep_workflow_artifacts"] = {key: Path(path).relative_to(root).as_posix()
                                         for key, path in coverage_artifacts.items()}
    from basis.report import write_report
    from basis.run_summary import build_run_summary, write_run_summary
    # The vendored renderer is presentation only. Disable its legacy heartbeat;
    # Snakemake owns job-level progress and writes the render log itself.
    previous = os.environ.pop("BASIS_VERBOSE", None)
    try:
        artifacts = write_report(run, [sample], records, deep_records, root,
                                 evidence_root=source_root if evidence_root is not None else None)
        summary = build_run_summary(run, [sample], records, deep_records)
        artifacts.update(write_run_summary(summary, root))
        artifacts.update({"deep_workflow_" + key: path for key, path in coverage_artifacts.items()})
    finally:
        if previous is not None:
            os.environ["BASIS_VERBOSE"] = previous
    text = (root / "report.html").read_text(encoding="utf-8")
    if not include_deep:
        # Only renderer-owned exports are removed; saved analysis evidence,
        # logs, and all native files remain available on disk.
        for relative in ("deferred-candidates.tsv", "tables/deferred-candidates.tsv",
                         "metadata/deep-workflow.json", "metadata/deep-workflow.tsv"):
            (root / relative).unlink(missing_ok=True)
    for original, new in RELOCATIONS.items():
        source, target = root / original, root / new
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            source.replace(target)
        text = text.replace('href="' + original + '"', 'href="' + new + '"')
        for key, value in list(artifacts.items()):
            if Path(value) == source:
                artifacts[key] = str(target)
    from .report_links import matching_report_link
    counterpart = matching_report_link(run, source_root)
    related = ('<a class="button" href="' + html.escape(counterpart[0], quote=True) + '">'
               + html.escape(counterpart[1]) + '</a>') if counterpart else ""
    text = text.replace('<div class="topbar-actions">', '<div class="topbar-actions">' + related + '<a class="button" href="metadata/files.html">Run files</a>')
    (root / "report.html").write_text(text, encoding="utf-8")
    navigation_path = root / "metadata/report-navigation.json"
    navigation = _read(navigation_path)
    navigation["report_sha256"] = _sha(root / "report.html")
    navigation["evidence_sha256"] = _sha(root / "evidence.json")
    # Navigation hrefs are root-report-relative, explicitly recorded after moving
    # the machine-readable navigation index below metadata/.
    navigation["href_base"] = "../report.html"
    _write_json(navigation_path, navigation)
    _file_index(root, include_deep=include_deep, evidence_root=source_root,
                deep_only=view == "deep")
    artifacts.update(files_html=str(root / "metadata/files.html"), files_json=str(root / "metadata/files.json"))
    _write_json(root / "metadata/report-artifacts.json", {"schema": "basis-report-artifacts-v1", "artifacts": artifacts})
    return artifacts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--evidence", required=True, nargs="*", type=Path)
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--view", choices=("combined", "screening", "deep"), default="combined")
    parser.add_argument("--evidence-root", type=Path)
    args = parser.parse_args(argv)
    render(args.sample, args.config, args.evidence, args.outdir,
           view=args.view, evidence_root=args.evidence_root)
    print(f"Report: {args.outdir.resolve() / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
