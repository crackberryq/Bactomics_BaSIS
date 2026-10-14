"""Refresh explicit saved snapshots and build a control panel without analyses."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from .progress import stage
from .util import atomic_json, sha256_file


def _source_path(value, base):
    if not isinstance(value, str) or not value:
        raise ValueError("Each source needs a nonempty evidence path")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve(strict=True)


def inspect_sources(manifest, outdir):
    """Validate every explicit source before creating any output directory."""
    manifest = Path(manifest).expanduser().resolve(strict=True)
    outdir = Path(outdir).expanduser()
    if os.path.lexists(outdir):
        raise FileExistsError(f"Existing output preserved: {outdir}")
    outdir = outdir.resolve()
    raw = manifest.read_bytes()
    specs = json.loads(raw)
    if not isinstance(specs, list) or not specs:
        raise ValueError("Source manifest must be a nonempty JSON list")
    selected, seen = [], set()
    protected = {str(manifest): hashlib.sha256(raw).hexdigest()}
    for index, spec in enumerate(specs, 1):
        if not isinstance(spec, dict):
            raise ValueError("Every source entry must be an object")
        source = _source_path(spec.get("evidence"), manifest.parent)
        if not source.is_file() or source in seen:
            raise ValueError(f"Missing or duplicate evidence source: {source}")
        seen.add(source)
        if outdir.is_relative_to(source.parent) or source.is_relative_to(outdir):
            raise ValueError("Output must be outside every selected source report directory")
        digest = sha256_file(source)
        expected = spec.get("evidence_sha256")
        if expected is not None and (not isinstance(expected, str) or
                not re.fullmatch(r"[a-fA-F0-9]{64}", expected) or expected.lower() != digest):
            raise ValueError(f"Evidence checksum mismatch or invalid pin: {source}")
        evidence = json.loads(source.read_bytes())
        if not isinstance(evidence, dict) or evidence.get("schema_version") != "basis-report-1":
            raise ValueError(f"Unrecognized evidence schema: {source}")
        if not isinstance(evidence.get("run"), dict):
            raise ValueError(f"Missing saved run object: {source}")
        for key in ("samples", "records", "deep_records"):
            if not isinstance(evidence.get(key), list) or any(not isinstance(v, dict) for v in evidence[key]):
                raise ValueError(f"Invalid {key}: {source}")
        sid = spec.get("sample_id")
        if sid is not None and (not isinstance(sid, str) or not sid or
                {v.get("sample_id") for v in evidence["samples"]} != {sid} or
                any(v.get("sample_id") != sid for key in ("records", "deep_records") for v in evidence[key])):
            raise ValueError(f"Source sample identity differs from manifest: {source}")
        for rec in evidence["deep_records"]:
            candidates = rec.get("candidates", [])
            if not isinstance(candidates, list) or any(not isinstance(v, dict) for v in candidates):
                raise ValueError(f"Invalid saved candidate inventory: {source}")
        replays = evidence["run"].get("report_replays", [])
        if not isinstance(replays, list) or any(not isinstance(v, dict) for v in replays):
            raise ValueError(f"Invalid reporting history: {source}")
        # Optional historical configuration seal is checked only when explicit.
        if spec.get("configuration"):
            import yaml
            config = _source_path(spec["configuration"], manifest.parent)
            pin = spec.get("configuration_sha256")
            if not isinstance(pin, str) or sha256_file(config) != pin.lower():
                raise ValueError(f"Configuration checksum mismatch: {config}")
            cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
            declared = Path(cfg["outdir"]).expanduser()
            declared = (declared if declared.is_absolute() else config.parent / declared).resolve()
            if declared != source.parent:
                raise ValueError(f"Evidence differs from configured outdir: {source}")
            protected[str(config)] = pin.lower()
        protected[str(source)] = digest
        for name in ("report.html", "run.json", "report-navigation.json"):
            p = source.parent / name
            if p.is_file() and not p.is_symlink():
                protected[str(p)] = sha256_file(p)
        selected.append({"evidence": str(source), "sha256": digest,
                         "checksum_basis": "supplied_pin" if expected else "captured_at_refresh",
                         "sample_id": sid, "origin": spec.get("origin", "explicit_saved_source"),
                         "source_run_id": evidence["run"].get("run_id"),
                         "source_version": evidence["run"].get("version"),
                         "source_scope": evidence["run"].get("completeness"),
                         "report_directory": f"reports/run{index:03d}"})
    return manifest, raw, selected, protected, outdir


def refresh_saved_reports(manifest, outdir, artifact_roots=None):
    """Render all chosen snapshots, keeping their run and scientific records intact."""
    from .report_refresh import refresh_saved_report
    from .control_panel import build_control_panel
    manifest, raw, sources, protected, output = inspect_sources(manifest, outdir)
    roots = []
    for value in artifact_roots or []:
        root = Path(value).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"Artifact root is not a directory: {root}")
        roots.append(root)
    output.mkdir(parents=True, exist_ok=False)
    (output / "sources.json").write_bytes(raw)
    audit = {"schema": "basis-report-batch-v1", "status": "in_progress",
             "started_at_utc": datetime.now(timezone.utc).isoformat(),
             "source_manifest": {"path": str(manifest), "sha256": hashlib.sha256(raw).hexdigest()},
             "sources": sources, "reports": [], "native_analyses_run": False,
             "gpu_inference_run": False, "independent_biological_validation": False,
             "original_files_unchanged": False, "protected_source_sha256": protected}
    atomic_json(output / "refresh-audit.json", audit)
    try:
        for source in sources:
            with stage("Refresh saved run " + str(source["source_run_id"])):
                _, artifacts = refresh_saved_report(source["evidence"], output / source["report_directory"],
                    artifact_roots=roots, expected_sha256=source["sha256"])
            audit["reports"].append(artifacts)
        # Source report directories are explicitly selected by the manifest;
        # metadata itself never grants access to any additional location.
        panel_roots = [*roots, *(Path(s["evidence"]).parent for s in sources)]
        with stage("Build saved-run control panel"):
            audit["control_panel"] = build_control_panel(
                results=[output / s["report_directory"] for s in sources],
                outdir=output / "control-panel", artifact_roots=panel_roots)
        for path, digest in protected.items():
            if sha256_file(path) != digest:
                raise RuntimeError(f"Selected source changed during refresh: {path}")
        audit.update(status="completed_reporting_refresh", original_files_unchanged=True)
    except Exception as exc:
        audit.update(status="failed_reporting_refresh", error=str(exc))
        raise
    finally:
        audit["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        atomic_json(output / "refresh-audit.json", audit)
    return {"status": audit["status"], "run_count": len(sources), "native_analyses_run": False,
            "artifacts": {"html": audit["control_panel"]["html"],
                          "audit": str(output / "refresh-audit.json")}}
