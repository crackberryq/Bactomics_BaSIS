"""Exact, source-declared artifact navigation for saved reports.

An artifact root is an explicit caller permission, never a path inferred from
saved metadata. Directories are recorded without recursively discovering files.
Availability and snapshot integrity are not biological revalidation.
"""
from __future__ import annotations

from collections import Counter
from html import escape
import json
import os
from pathlib import Path
from urllib.parse import quote

from .util import atomic_json, hash_object, sha256_file


SCHEMA = "basis-source-artifacts-v1"
INDEX = "source-artifacts.json"
PAGE = "source-artifacts.html"


def allowed_roots(default_root, artifact_roots=None):
    roots = []
    for value in [default_root, *(artifact_roots or ())]:
        root = Path(value).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"Artifact root is not a directory: {root}")
        if root not in roots:
            roots.append(root)
    return roots


def declarations(bundle, source):
    """Only module-level declared artifacts and the original report entrypoint."""
    source = Path(source)
    entries = [{"declared_path": str(source.parent / "report.html"), "sample": "",
                "module": "", "role": "Original saved report"}]
    seen = {(entries[0]["declared_path"], "", "")}
    for key in ("records", "deep_records"):
        for record in bundle.get(key, []):
            if not isinstance(record, dict):
                continue
            values = record.get("artifacts") or []
            if not isinstance(values, list):
                continue
            sample, module = str(record.get("sample_id") or ""), str(record.get("module") or "")
            for value in values:
                if not isinstance(value, str) or not value:
                    continue
                identity = (value, sample, module)
                if identity in seen:
                    continue
                seen.add(identity)
                entries.append({"declared_path": value, "sample": sample, "module": module,
                                "role": "Recorded source artifact"})
    return entries


def _symlink_component(path):
    # Inspect lexical components before resolving: an escaping link must not
    # become an ordinary out-of-root path and lose its diagnostic identity.
    return any(part.is_symlink() for part in [path, *path.parents])


def inspect_declaration(entry, source, output, roots):
    result = dict(entry)
    result["href"] = None
    try:
        path = Path(entry["declared_path"])
        path = path if path.is_absolute() else Path(source).parent / path
        # abspath removes lexical '..' without following symlinks.
        lexical = Path(os.path.abspath(path))
        result["path"] = str(lexical)
        if _symlink_component(path.absolute()):
            result["status"] = "symlink"
            return result
        resolved = lexical.resolve(strict=False)
        result["path"] = str(resolved)
        if not any(resolved.is_relative_to(root) for root in roots):
            result["status"] = "outside_allowed_roots"
            return result
        if not resolved.exists():
            result["status"] = "missing"
        elif resolved.is_dir():
            result["status"] = "directory"
        elif resolved.is_file():
            result.update(status="file", bytes=resolved.stat().st_size,
                          href=quote(Path(os.path.relpath(resolved, output)).as_posix(), safe="/"))
        else:
            result.update(status="unsupported_file_type")
    except (OSError, ValueError, RuntimeError) as exc:
        result.update(status="error", error=str(exc))
    return result


def source_snapshot(bundle, source, source_hash):
    run = bundle.get("run", {})
    return {"path": str(source), "sha256": source_hash,
            "run_id": run.get("run_id"), "version": run.get("version"),
            "completeness": run.get("completeness"), "finished_at": run.get("finished_at")}


def build_index(bundle, source, source_hash, output, roots):
    entries = [inspect_declaration(entry, source, output, roots)
               for entry in declarations(bundle, source)]
    return {"schema": SCHEMA, "source_evidence": {"path": str(source), "sha256": source_hash},
            "source_run": source_snapshot(bundle, source, source_hash),
            "allowed_roots": [str(p) for p in roots],
            "entries": entries, "counts": dict(Counter(e["status"] for e in entries)),
            "scope": "Exact module-declared paths plus original report only. Directory contents are not discovered.",
            "verification_scope": "Link availability only; native contents and scientific conclusions are not revalidated."}


def render_index(index):
    e = lambda value: escape(str(value if value is not None else "not recorded"), quote=True)
    rows = []
    for item in index["entries"]:
        action = ('<a href="' + e(item["href"]) + '" target="_blank" rel="noopener">Open file</a>') if item.get("href") else "—"
        rows.append("<tr><td>" + e(item["sample"]) + " / " + e(item["module"]) + "</td><td><code>"
                    + e(item["declared_path"]) + "</code></td><td>" + e(item["status"].replace("_", " "))
                    + "</td><td>" + action + "</td></tr>")
    run = index["source_run"]
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>BaSIS · Source artifacts</title><style>body{font:15px/1.5 system-ui;margin:0;background:#f2f6f9;color:#173347}'
            'main{max-width:1200px;margin:auto;padding:36px}a{color:#087c80}h1{margin-bottom:6px}.panel{padding:22px;background:white;'
            'border:1px solid #dae4ec;border-radius:10px;margin:20px 0}table{border-collapse:collapse;width:100%;font-size:13px}'
            'td,th{text-align:left;padding:12px;border-bottom:1px solid #dae4ec}code{overflow-wrap:anywhere}.scroll{overflow:auto}'
            '.muted{color:#597083}</style><main><a href="report.html">← Full report</a><h1>Source artifacts</h1>'
            '<p class="muted">Saved run ' + e(run["run_id"]) + ' · source version ' + e(run["version"])
            + ' · recorded scope ' + e(run["completeness"]) + '</p><div class="panel"><p>' + e(index["scope"])
            + '</p><p>Folders are recorded as directories and are not missing files. External paths require explicitly selected artifact roots.</p>'
            '<p>' + e(index["verification_scope"]) + '</p><a href="source-artifacts.json">Download artifact index</a></div>'
            '<div class="panel scroll"><table><thead><tr><th>Sample / module</th><th>Recorded path</th><th>Availability</th><th>Action</th>'
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div></main></html>')


def write_index(index, output):
    output = Path(output)
    atomic_json(output / INDEX, index)
    (output / PAGE).write_text(render_index(index), encoding="utf-8")
    return {"source_artifacts_json": str(output / INDEX), "source_artifacts_html": str(output / PAGE)}


def read_bound_index(root, bundle):
    """Verify a replay index, without accepting its recorded roots as permission."""
    root = Path(root)
    for name in (INDEX, "replay-audit.json", "evidence.json"):
        path = root / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Artifact navigation file missing or outside report: {name}")
        if path.stat().st_size > 256 * 1024 * 1024:
            raise ValueError(f"Artifact navigation file exceeds review limit: {name}")
    index = json.loads((root / INDEX).read_bytes())
    audit = json.loads((root / "replay-audit.json").read_bytes())
    if (not isinstance(index, dict) or index.get("schema") != SCHEMA or not isinstance(audit, dict)
            or audit.get("schema_version") != "basis-report-replay-1"):
        raise ValueError("Unrecognized source artifact index or replay audit")
    if not audit.get("finalized") or not audit.get("source_unchanged"):
        raise ValueError("Source artifact replay was not finalized")
    if (audit.get("output_file_sha256", {}).get(INDEX) != sha256_file(root / INDEX)
            or index.get("evidence_sha256") != sha256_file(root / "evidence.json")):
        raise ValueError("Source artifact index checksum differs from its saved replay")
    if (audit.get("source_run_id") != bundle.get("run", {}).get("run_id")
            or any(audit.get("recorded_evidence_sha256", {}).get(k) != hash_object(bundle.get(k))
                   for k in ("samples", "records", "deep_records"))):
        raise ValueError("Source artifact replay evidence differs from its audit")
    source_info = index.get("source_evidence")
    history = bundle.get("run", {}).get("report_replays", [])
    if (not isinstance(source_info, dict) or source_info != audit.get("source_evidence")
            or not history or history[-1].get("source_evidence") != source_info):
        raise ValueError("Source artifact provenance differs from saved reporting history")
    # Validate declarations against the same preserved scientific records in the
    # replay. Do not need to authorize/read the historical source to use this.
    source = Path(source_info["path"])
    if not source.is_absolute():
        raise ValueError("Source artifact provenance needs an absolute evidence path")
    expected_run = source_snapshot(bundle, source, source_info["sha256"])
    if index.get("source_run") != expected_run:
        raise ValueError("Source artifact run identity differs from saved evidence")
    expected = declarations(bundle, source)
    entries = index.get("entries")
    if not isinstance(entries, list) or len(entries) != len(expected):
        raise ValueError("Source artifact declarations differ from saved evidence")
    for item, declared in zip(entries, expected):
        if not isinstance(item, dict) or any(item.get(k) != v for k, v in declared.items()):
            raise ValueError("Source artifact declarations differ from saved evidence")
    return index, source
