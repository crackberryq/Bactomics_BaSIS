#!/usr/bin/env python3
"""Build a read-only navigation hub for saved BaSIS reports and run artifacts.

Use explicit saved result directories or a saved pilot manifest. This renderer
runs no analyses or network requests and preserves recorded interpretations.
Keep the panel beside its linked study folders; it is a navigation snapshot,
not a portable copy of the data.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote, unquote, urlsplit
import uuid

from .report_links import candidate_anchor


def say(message):
    print(message, flush=True)


def load_json(path, warnings):
    try:
        if path.stat().st_size > 256 * 1024 * 1024:
            raise ValueError("JSON exceeds the 256 MiB review limit")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected an object")
        return data
    except (OSError, ValueError) as exc:
        warnings.append(f"Could not read {path}: {exc}")
        return {}


def safe_file(path, root):
    try:
        return path.resolve(strict=True).is_relative_to(root.resolve(strict=True)) and path.is_file()
    except OSError:
        return False


def href(path, output, root):
    if not safe_file(path, root):
        return None
    return quote(Path(os.path.relpath(path.resolve(), output.resolve())).as_posix(), safe="/")


def anchor(sample, protein):
    return candidate_anchor(sample, protein)


class _ReportAnchors(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.counts = {}
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "id" and value:
                self.counts[value] = self.counts.get(value, 0) + 1

    handle_startendtag = handle_starttag


def _navigation_file(value, root):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("invalid candidate evidence path")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("candidate evidence link is not a local file")
    relative = Path(unquote(parsed.path))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("candidate evidence link escapes the report directory")
    path = root / relative
    if not safe_file(path, root):
        raise ValueError("candidate evidence file is missing or outside the report directory")
    return path


def _report_navigation(root, output, warnings, pilot_item=None):
    """Use a navigation snapshot only when its report and evidence agree.

    File hashes bind navigation to a saved report. They do not revalidate the
    biological analyses or upgrade any recorded interpretation.
    """
    path = root / "report-navigation.json"
    if not path.exists() and not path.is_symlink():
        return None
    try:
        if not safe_file(path, root):
            raise ValueError("navigation index is outside the report directory")
        index = load_json(path, warnings)
        if index.get("schema") != "basis-report-navigation-v1":
            raise ValueError("unrecognized navigation index schema")
        report, evidence = root / "report.html", root / "evidence.json"
        contents = {}
        for label, source in (("report", report), ("evidence", evidence)):
            if not safe_file(source, root):
                raise ValueError(label + " file is missing or outside the report directory")
            if source.stat().st_size > 256 * 1024 * 1024:
                raise ValueError(label + " exceeds the 256 MiB review limit")
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != index.get(label + "_sha256"):
                raise ValueError(label + " checksum differs from the navigation snapshot")
            contents[label] = content
        bundle = json.loads(contents["evidence"])
        if (not isinstance(bundle, dict) or bundle.get("schema_version") != "basis-report-1"
                or not isinstance(bundle.get("run"), dict)
                or index.get("run_id") != bundle["run"].get("run_id")):
            raise ValueError("navigation run identity differs from saved evidence")
        from .report_navigation import candidate_fingerprint
        expected = {}
        records = bundle.get("deep_records", [])
        if not isinstance(records, list):
            raise ValueError("invalid saved DeepSafe record inventory")
        for rec in records:
            if not isinstance(rec, dict):
                raise ValueError("invalid saved DeepSafe record")
            sid = str(rec.get("sample_id") or "unknown")
            rows = rec.get("candidates", []) or []
            if not isinstance(rows, list):
                raise ValueError("invalid saved candidate inventory")
            for c in rows:
                if (not isinstance(c, dict) or c.get("sample_id") not in (None, "", sid)
                        or not c.get("selected", "selected" not in c)):
                    continue
                protein = str(c.get("protein_id") or c.get("candidate_id") or "Unidentified candidate")
                key = (sid, protein, str(c.get("candidate_id") or ""))
                if key in expected:
                    raise ValueError("duplicate displayed candidate identity")
                expected[key] = candidate_fingerprint(c)
        anchors = _ReportAnchors(contents["report"].decode("utf-8")).counts
        rows = index.get("candidates")
        if not isinstance(rows, list):
            raise ValueError("invalid navigation candidate inventory")
        candidates, seen = [], set()
        report_href = href(report, output, root)
        for item in rows:
            if not isinstance(item, dict):
                raise ValueError("invalid navigation candidate entry")
            sid, protein = item.get("sample_id"), item.get("protein_id")
            if not isinstance(sid, str) or not isinstance(protein, str) or not sid or not protein:
                raise ValueError("navigation candidate identity is missing")
            key = (sid, protein, str(item.get("candidate_id") or ""))
            if key in seen or key not in expected or item.get("candidate_sha256") != expected[key]:
                raise ValueError("navigation candidate differs from saved evidence")
            seen.add(key)
            outer = item.get("anchor")
            if outer != candidate_anchor(sid, protein) or anchors.get(outer) != 1:
                raise ValueError("candidate anchor is missing, ambiguous or inconsistent")
            sections = item.get("sections")
            allowed = {"overview", "domains", "motifs", "trees", "placement", "references", "files"}
            if not isinstance(sections, dict) or set(sections) - allowed:
                raise ValueError("invalid candidate section inventory")
            links = {}
            for name, ident in sections.items():
                if (not isinstance(ident, str) or not (ident == outer or ident.startswith(outer + "-"))
                        or anchors.get(ident) != 1):
                    raise ValueError("candidate section anchor is missing, ambiguous or inconsistent")
                links[name] = report_href + "#" + quote(ident, safe="-")
            evidence_path = _navigation_file(item.get("evidence_href"), root)
            if evidence_path.stat().st_size > 256 * 1024 * 1024:
                raise ValueError("candidate evidence exceeds the 256 MiB review limit")
            if candidate_fingerprint(json.loads(evidence_path.read_bytes())) != expected[key]:
                raise ValueError("candidate evidence export differs from saved evidence")
            if not isinstance(item.get("layer_statuses"), dict):
                raise ValueError("invalid candidate layer status inventory")
            missing = item.get("missing_comparison_groups", [])
            if not isinstance(missing, list) or any(not isinstance(v, str) for v in missing):
                raise ValueError("invalid comparator gap inventory")
            if pilot_item and sid != pilot_item.get("sample_id"):
                continue
            candidates.append({
                "sample": sid, "protein": protein, "gene": str(item.get("label") or ""),
                "execution": item.get("execution", "not recorded"),
                "interpretation": item.get("interpretation", "not recorded"),
                "followup": item.get("followup", "not recorded"),
                "reason": str(item.get("reason") or ""), "missing_comparison_groups": missing,
                "layers": item["layer_statuses"], "href": report_href + "#" + outer,
                "evidence": href(evidence_path, output, root), "sections": links,
                "search_text": str(item.get("search_text") or ""),
                "navigation_source": "verified_report_snapshot"})
        if seen != set(expected):
            raise ValueError("navigation candidate inventory differs from saved evidence")
        return {"candidates": candidates, "run": bundle["run"]}
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        warnings.append("Detailed report navigation unavailable: " + str(exc)
                        + ". Falling back to basic saved-result links.")
        return None


def classify(path):
    suffix = path.suffix.lower()
    name = path.name.lower()
    if "citation" in name or suffix in {".bib", ".ris", ".cff"}:
        return "Citations"
    if ("tree-view" in name or "placement-tree" in name) and suffix in {".svg", ".html"}:
        return "Trees & placement"
    if "alignment" in name or suffix in {".aln", ".afa", ".a3m"}:
        return "Alignments"
    if suffix in {".html", ".htm"}:
        return "Reports"
    if suffix in {".treefile", ".contree", ".nwk", ".newick", ".jplace", ".nex", ".ufboot", ".iqtree"}:
        return "Trees & placement"
    if suffix in {".fasta", ".fa", ".faa", ".fna", ".gbk", ".gbff", ".gff", ".gff3"}:
        return "Sequences & annotation"
    if suffix in {".svg", ".png", ".jpg", ".jpeg", ".pdf"}:
        return "Figures & documents"
    if suffix == ".log" or "stderr" in name or "stdout" in name:
        return "Logs"
    if suffix in {".json", ".tsv", ".csv", ".yaml", ".yml", ".txt", ".domtblout", ".tblout"}:
        return "Evidence & tables"
    return "Other files"


def report_role(relative):
    """Identify report entrypoints by their saved location, never HTML content.

    Keep the allowlist narrow: supporting pages and unrecognized HTML remain
    available in the full file inventory, without being promoted to reports.
    A role describes navigation only; it does not claim execution success or
    current evidence. Those labels still come from the recorded result files.
    """
    parts = Path(relative).parts
    if parts == ("report.html",):
        return "Consolidated run report"
    if not parts or parts[-1].lower() not in {"index.html", "index.htm", "report.html", "report.htm"}:
        return None
    direct_native = len(parts) == 5 and parts[0] == "results" and parts[3] == "native"
    attempt_native = (len(parts) == 7 and parts[0] == "results"
                      and parts[3] == "attempts" and parts[5] == "native")
    if direct_native or attempt_native:
        return "Native tool report"
    return None


def collect_run(root, run_id, output, pilot_item=None, artifact_roots=None):
    root = root.resolve()
    warnings = []
    if not root.is_dir():
        return {"id": run_id, "label": (pilot_item or {}).get("sample_id", root.name),
                "root": str(root), "scope": (pilot_item or {}).get("scope", "not recorded"),
                "status": "output not yet available", "report": None,
                "modules": [], "candidates": [], "files": [], "warnings": ["Run output directory not yet present"]}
    run_path = root / "run.json"
    run = load_json(run_path, warnings) if safe_file(run_path, root) else {}
    evidence_path = root / "evidence.json"
    bundle = {}
    if not run and safe_file(evidence_path, root):
        bundle = load_json(evidence_path, warnings)
        if bundle.get("schema_version") == "basis-report-1" and isinstance(bundle.get("run"), dict):
            run = bundle["run"]
    main_report = href(root / "report.html", output, root)
    modules, candidates, current_attempts = [], [], set()
    result_root = root / "results"
    paths = sorted(result_root.glob("*/*/result.json")) if result_root.is_dir() else []
    entries = []
    for path in paths:
        if not safe_file(path, root):
            warnings.append(f"Excluded result outside run directory: {path}")
            continue
        entries.append((path, load_json(path, warnings), path.parent.parent.name, path.parent.name))
    if not paths and safe_file(evidence_path, root):
        bundle = bundle or load_json(evidence_path, warnings)
        if bundle.get("schema_version") == "basis-report-1":
            seen = set()
            for key in ("records", "deep_records"):
                for rec in bundle.get(key, []) if isinstance(bundle.get(key), list) else []:
                    if not isinstance(rec, dict):
                        warnings.append("Excluded non-object saved module record")
                        continue
                    sid, module = rec.get("sample_id"), rec.get("module")
                    if not isinstance(sid, str) or not isinstance(module, str) or not sid or not module:
                        warnings.append("Excluded saved module record with missing identity")
                        continue
                    if (sid, module) in seen:
                        warnings.append(f"Repeated saved module identity: {sid}/{module}")
                    seen.add((sid, module))
                    entries.append((evidence_path, rec, sid, module))
    for path, rec, sid, module in entries:
        if pilot_item and sid != pilot_item.get("sample_id"):
            warnings.append(f"Result sample differs from pilot manifest: {path}")
            continue
        if rec and (rec.get("sample_id") != sid or rec.get("module") != module):
            warnings.append(f"Result identity mismatch: {path}")
            continue
        recorded_artifacts = rec.get("artifacts", [])
        if not isinstance(recorded_artifacts, list):
            recorded_artifacts = []
        for artifact in recorded_artifacts:
            if not isinstance(artifact, str):
                continue
            candidate_path = Path(artifact)
            if not candidate_path.is_absolute():
                candidate_path = root / candidate_path
            try:
                rel = candidate_path.resolve().relative_to(root)
            except (OSError, ValueError):
                continue
            parts = rel.parts
            if "attempts" in parts:
                i = parts.index("attempts")
                if len(parts) > i + 1:
                    current_attempts.add(Path(*parts[:i+2]).as_posix())
        modules.append({"sample": sid, "name": module,
                        "status": rec.get("execution_status", "not recorded"),
                        "reason": rec.get("error") or rec.get("reason") or "",
                        "href": href(path, output, root)})
        if module == "deepsafe":
            rows = rec.get("candidates", [])
            if not isinstance(rows, list):
                warnings.append(f"Unexpected candidate inventory: {path}")
                continue
            for c in rows:
                if not isinstance(c, dict) or not c.get("selected"):
                    continue
                if c.get("sample_id") not in (None, "", sid):
                    warnings.append(f"Excluded candidate with conflicting sample identity: {path}")
                    continue
                protein = c.get("protein_id") or c.get("candidate_id") or "unrecorded"
                assessment = c.get("assessment") or {}
                layers = c.get("analyses") or {}
                candidates.append({"sample": sid, "protein": str(protein), "gene": str(c.get("gene") or ""),
                    "execution": c.get("execution", "not recorded"),
                    "interpretation": assessment.get("interpretation", "not recorded"),
                    "followup": assessment.get("followup", "not recorded"),
                    "layers": {name: value.get("status", "not recorded")
                               for name, value in layers.items() if isinstance(value, dict)},
                    "href": main_report + "#" + anchor(sid, protein) if main_report else None,
                    "evidence": href(path, output, root)})
    navigation = _report_navigation(root, output, warnings, pilot_item)
    navigation_source = "legacy_result_records"
    if navigation is not None:
        candidates = navigation["candidates"]
        run = navigation["run"]
        navigation_source = "verified_report_snapshot"
    elif candidates and main_report:
        # Legacy results remain browsable, but never fabricate a card link to
        # a newer or otherwise different report that does not contain it.
        try:
            report_path = root / "report.html"
            if report_path.stat().st_size > 256 * 1024 * 1024:
                raise ValueError("report exceeds the 256 MiB review limit")
            card_ids = _ReportAnchors(report_path.read_text(encoding="utf-8")).counts
            missing_cards = 0
            for candidate in candidates:
                target = anchor(candidate["sample"], candidate["protein"])
                if card_ids.get(target) != 1:
                    candidate["href"] = None
                    missing_cards += 1
            if missing_cards:
                warnings.append(f"{missing_cards} saved candidate card links unavailable in this report; source JSON remains accessible.")
        except (OSError, ValueError) as exc:
            for candidate in candidates:
                candidate["href"] = None
            warnings.append("Could not verify legacy candidate card links: " + str(exc))
    inventory = []
    excluded = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            if name.startswith("."):
                continue
            path = Path(directory) / name
            if not safe_file(path, root):
                excluded += 1
                continue
            relative = path.relative_to(root)
            parts = relative.parts
            status = "run artifact"
            if "history" in parts:
                status = "historical snapshot"
            elif "attempts" in parts:
                i = parts.index("attempts")
                prefix = Path(*parts[:i+2]).as_posix()
                status = "referenced by current result" if prefix in current_attempts else "other attempt; not current evidence"
            sample, module = "", ""
            if len(parts) >= 3 and parts[0] == "results":
                sample, module = parts[1], parts[2]
            try:
                size = path.stat().st_size
            except OSError:
                warnings.append(f"File disappeared while indexing: {path}")
                continue
            inventory.append({"path": relative.as_posix(), "href": href(path, output, root),
                              "sample": sample, "module": module, "kind": classify(path),
                              "report_role": report_role(relative),
                              "status": status, "bytes": size})
            if len(inventory) > 200000:
                raise ValueError(f"More than 200,000 files in {root}; select a narrower output directory")
    if excluded:
        warnings.append(f"Excluded {excluded} broken or out-of-root file links")
    from .report_artifacts import (allowed_roots, build_index, read_bound_index,
                                  inspect_declaration, INDEX)
    source_artifacts, source_identity = None, None
    if safe_file(evidence_path, root):
        bundle = bundle or load_json(evidence_path, warnings)
        if bundle.get("schema_version") == "basis-report-1":
            roots = allowed_roots(root, artifact_roots)
            try:
                if (root / INDEX).exists() or (root / INDEX).is_symlink():
                    index, source = read_bound_index(root, bundle)
                    source_identity = index["source_run"]
                    index_sha = hashlib.sha256((root / INDEX).read_bytes()).hexdigest()
                    declared = index["entries"]
                else:
                    source = evidence_path
                    index = build_index(bundle, source, hashlib.sha256(source.read_bytes()).hexdigest(), output, roots)
                    index_sha = None
                    declared = index["entries"]
                # Recheck locations under THIS invocation's allowed roots. The
                # saved index is provenance, never permission to widen access.
                source_artifacts = {"schema": "basis-panel-source-artifacts-v1", "index_sha256": index_sha,
                                    "entries": [inspect_declaration({k: item[k] for k in
                                                 ("declared_path", "sample", "module", "role")},
                                                 source, output, roots) for item in declared]}
                from collections import Counter
                source_artifacts["counts"] = dict(Counter(e["status"] for e in source_artifacts["entries"]))
                existing = {item["href"] for item in inventory}
                for item in source_artifacts["entries"]:
                    if item["status"] != "file" or item["href"] in existing:
                        continue
                    if pilot_item and item["sample"] not in ("", pilot_item.get("sample_id")):
                        continue
                    path = Path(item["path"])
                    role = None
                    if item["role"] == "Original saved report":
                        role = "Original saved report"
                    elif (path.name.lower() in {"index.html", "index.htm", "report.html", "report.htm"}
                          and "native" in path.parts):
                        role = "Native tool report"
                    inventory.append({"path": "original/" + path.as_posix().lstrip("/"),
                                      "href": item["href"], "sample": item["sample"], "module": item["module"],
                                      "kind": classify(path), "report_role": role,
                                      "status": "source-declared artifact; availability checked, contents not revalidated",
                                      "bytes": item["bytes"]})
                    existing.add(item["href"])
                unavailable = {k: v for k, v in source_artifacts["counts"].items() if k not in {"file", "directory"}}
                if unavailable:
                    warnings.append("Source artifact availability: " + ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in sorted(unavailable.items()))
                                    + ". Directory entries are recorded separately; see inventory.")
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                warnings.append("Source artifact navigation unavailable: " + str(exc)
                                + ". No source index links were added.")
                source_artifacts = {"status": "unavailable", "reason": str(exc), "entries": []}
                source_identity = None
    label = (pilot_item or {}).get("sample_id", root.name)
    if source_identity is not None and not pilot_item:
        sample_names = [str(s.get("sample_id")) for s in bundle.get("samples", [])
                        if isinstance(s, dict) and s.get("sample_id")]
        label = ", ".join(dict.fromkeys(sample_names)) or root.name
    scope = (pilot_item or {}).get("scope", "scope recorded in source report")
    if source_identity is not None and not pilot_item:
        scope = ("Saved run " + str(source_identity.get("run_id") or "not recorded")
                 + " · source version " + str(source_identity.get("version") or "not recorded"))
    return {"id": run_id, "label": label, "root": str(root),
            "scope": scope,
            "status": run.get("completeness") or (pilot_item or {}).get("status", "not recorded"),
            "report": main_report, "modules": modules, "candidates": candidates,
            "navigation_source": navigation_source,
            "source_snapshot": source_identity, "source_artifacts": source_artifacts,
            "files": inventory, "warnings": warnings}


HTML = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>BaSIS · Study control panel</title><style>
:root{--ink:#173347;--muted:#597083;--line:#dae4ec;--teal:#097f83;--nav:#102b3f;--bg:#f2f6f9}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
button,input,select{font:inherit}button,a{touch-action:manipulation}a{color:#086e83;text-decoration:none}a:hover{text-decoration:underline}
button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #ce9a30;outline-offset:3px}
.rail{position:fixed;inset:0 auto 0 0;width:234px;background:var(--nav);color:#e6f1f4;padding:30px 20px;overflow:auto}
.brand{font-size:31px;font-weight:780;letter-spacing:-1px}.rail small{color:#9db8c7}.rail nav{display:grid;gap:8px;margin:35px 0}
.nav{background:none;border:0;color:#c6d7e0;text-align:left;border-radius:8px;padding:12px;cursor:pointer}.nav.active{background:#20495c;color:#fff;font-weight:700}
.candidate-shortcuts{display:flex;flex-wrap:wrap;gap:6px;margin:9px 0}.candidate-shortcuts .button{font-size:12px;padding:5px 8px}
.rail footer{font-size:12px;color:#a5becc;margin-top:40px}.main{margin-left:234px;padding:32px;max-width:1800px}
.top{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:25px}.eyebrow{color:var(--teal);text-transform:uppercase;letter-spacing:1.7px;font-size:11px;font-weight:800}
h1{font-size:32px;line-height:1.15;letter-spacing:-1px;margin:7px 0 12px}h2{font-size:22px;margin:0 0 10px}h3{margin:0 0 8px}.muted{color:var(--muted)}
.tag{display:inline-block;padding:5px 10px;background:#e4eef3;border-radius:7px;font-size:12px;font-weight:650}.tag.completed{background:#dcefeb;color:#195d52}.tag.failed{background:#fbe2e2;color:#922e36}
.note{padding:16px 20px;border-left:4px solid #c69a40;background:#fff7e8;border-radius:6px;margin:16px 0 24px;font-size:14px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:18px}.card,.panel{background:white;border:1px solid var(--line);border-radius:12px;padding:22px;margin-bottom:18px;min-width:0}
.card p{margin:8px 0}.metrics{display:flex;gap:24px;margin:22px 0}.metric strong{display:block;font-size:28px;line-height:1.1}.metric span{font-size:12px;color:var(--muted)}
.button{display:inline-block;background:var(--teal);color:white;padding:10px 15px;border-radius:7px;font-weight:650;border:0;cursor:pointer;margin:8px 8px 0 0}.button:hover{background:#066a6c;text-decoration:none}.button.secondary{background:#eaf1f5;color:var(--ink)}
.filters{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0}.filters label{display:grid;gap:6px;font-size:12px;color:var(--muted);min-width:170px}.filters .search{flex:1;min-width:240px}
input,select{padding:10px 12px;border:1px solid #bacbd8;border-radius:7px;background:white;color:var(--ink);max-width:100%}
.scroll{overflow:auto;border:1px solid var(--line);border-radius:9px}table{width:100%;border-collapse:collapse;background:#fff;font-size:13px}th{text-align:left;background:#eaf1f6;color:#416074;font-size:11px;text-transform:uppercase;letter-spacing:.6px}td,th{padding:13px 15px;border-bottom:1px solid var(--line);vertical-align:top}td.path{min-width:260px;max-width:620px;overflow-wrap:anywhere}tr:last-child td{border:0}.small{font-size:12px}.pager{display:flex;gap:12px;align-items:center;justify-content:flex-end;margin:16px 0}
details{margin-top:12px;font-size:12px}summary{cursor:pointer;color:var(--teal);font-weight:650}code{overflow-wrap:anywhere}ul{padding-left:20px}.empty{padding:26px;color:var(--muted);text-align:center}
@media(max-width:950px){.rail{position:static;width:auto;padding:18px 22px}.rail nav{display:flex;flex-wrap:wrap;margin:14px 0 0;gap:4px}.rail footer{display:none}.main{margin:0;padding:22px}.top{align-items:flex-start;flex-direction:column}h1{font-size:28px}.nav{padding:9px}}
@media print{.rail,.filters,.pager{display:none}.main{margin:0;padding:0}.card,.panel{break-inside:avoid}a{color:var(--ink)}}
</style></head><body><aside class="rail"><div class="brand">BaSIS<span style="color:#5dc7c0">.</span></div><small>Study control panel</small><nav id="nav" aria-label="Report sections"></nav><footer>Saved evidence, connected.<br><br>This panel navigates existing outputs. It does not run analyses or issue a safety verdict.</footer></aside>
<main class="main"><header class="top"><div><div class="eyebrow">Bactromics environment</div><h1 id="title">Study overview</h1><div id="timestamp" class="small muted"></div></div><a class="button secondary" href="inventory.json" target="_blank" rel="noopener">Download inventory</a></header>
<div class="note">Execution status and scientific interpretation are separate. AMR evidence does not establish pathogenicity. Unresolved findings and limited-input samples retain their recorded assessment limits.</div>
<section id="content" aria-live="polite"></section></main><script id="data" type="application/json">__DATA__</script><script>
'use strict';
const data=JSON.parse(document.getElementById('data').textContent);
const state={view:'Overview',run:'all',kind:'all',attempt:'all',query:'',page:0};
const views=['Overview','Sample reports','Candidate evidence','Trees & alignments','All run files','Execution & provenance'];
const el=(tag,text,cls)=>{const x=document.createElement(tag);if(text!==undefined)x.textContent=String(text);if(cls)x.className=cls;return x};
const human=x=>String(x??'not recorded').replaceAll('_',' ');
const link=(url,label,cls)=>{if(!url)return el('span','Not yet available','muted');const a=el('a',label,cls);a.href=url;a.target='_blank';a.rel='noopener';return a};
const button=(label,action,cls='button secondary')=>{const b=el('button',label,cls);b.type='button';b.onclick=action;return b};
const selected=()=>data.runs.filter(r=>state.run==='all'||r.id===state.run);
const badge=s=>el('span',human(s),'tag '+(s==='completed'?'completed':s==='failed'?'failed':''));
const bytes=n=>n<1024?n+' B':n<1048576?(n/1024).toFixed(1)+' KiB':(n/1048576).toFixed(1)+' MiB';
document.getElementById('timestamp').textContent='Snapshot: '+data.generated_at_utc+' · Local report navigation';
function setView(view){state.view=view;state.kind='all';state.query='';state.page=0;if(view==='Overview'){state.run='all';state.attempt='all'}render()}
views.forEach(v=>document.getElementById('nav').append(button(v,()=>setView(v),'nav')));
function filters(host,files=false){const row=el('div',undefined,'filters');function select(label,values,key){const lab=el('label',label);const s=el('select');s.setAttribute('aria-label',label);values.forEach(([v,t])=>{const o=el('option',t);o.value=v;s.append(o)});s.value=state[key];s.onchange=()=>{state[key]=s.value;state.page=0;render()};lab.append(s);row.append(lab)}
 select('Run',[['all','All runs'],...data.runs.map(r=>[r.id,r.label])],'run');
 if(files){select('File category',[['all','All categories'],...Array.from(new Set(data.runs.flatMap(r=>r.files.map(f=>f.kind)))).sort().map(x=>[x,x])],'kind');select('Evidence status',[['all','All recorded files'],['current','Exclude historical / other attempts'],['history','Historical / other attempts']],'attempt')}
 const lab=el('label','Search','search');const input=el('input');input.type='search';input.placeholder=files?'Filename, module, sample…':'Protein ID, gene, interpretation…';input.value=state.query;input.oninput=()=>{state.query=input.value;state.page=0;drawRows()};lab.append(input);row.append(lab);host.append(row);
}
let drawRows=()=>{};
function table(host,heads,rows){if(!rows.length){host.append(el('p','No matching records in this snapshot.','empty'));return}const wrap=el('div',undefined,'scroll'),t=el('table'),head=el('thead'),tr=el('tr');heads.forEach(h=>tr.append(el('th',h)));head.append(tr);t.append(head);const body=el('tbody');rows.forEach(cells=>{const r=el('tr');cells.forEach(cell=>{const td=el('td',undefined,'path');if(cell instanceof Node)td.append(cell);else td.textContent=String(cell??'');r.append(td)});body.append(r)});t.append(body);wrap.append(t);host.append(wrap)}
function cards(host){const grid=el('div',undefined,'cards');data.runs.forEach(r=>{const card=el('article',undefined,'card');card.append(el('div',r.scope,'eyebrow'),el('h2',r.label),badge(r.status));const metrics=el('div',undefined,'metrics');[[r.modules.length,'module records'],[r.candidates.length,'selected candidates'],[r.files.length,'run files']].forEach(([n,l])=>{const m=el('div',undefined,'metric');m.append(el('strong',n),el('span',l));metrics.append(m)});card.append(metrics,link(r.report,'Open full report','button'),button('Browse run files',()=>{state.run=r.id;setView('All run files')}));const d=el('details');d.append(el('summary','Recorded location'),el('code',r.root));card.append(d);r.warnings.forEach(w=>card.append(el('p',w,'small muted')));grid.append(card)});host.append(grid)}
function filesView(host){filters(host,true);const target=el('div');host.append(target);drawRows=()=>{target.replaceChildren();const q=state.query.toLowerCase();let rows=selected().flatMap(r=>r.files.map(f=>({...f,run:r.label}))).filter(f=>(state.kind==='all'||f.kind===state.kind)&&[f.path,f.sample,f.module,f.run].join(' ').toLowerCase().includes(q));
 if(state.view==='Trees & alignments')rows=rows.filter(f=>['Trees & placement','Alignments'].includes(f.kind));
 if(state.view==='Sample reports'){const rank={'Consolidated run report':0,'Original saved report':1,'Native tool report':2};rows=rows.filter(f=>Object.hasOwn(rank,f.report_role)).sort((a,b)=>rank[a.report_role]-rank[b.report_role]||a.run.localeCompare(b.run)||a.path.localeCompare(b.path));target.append(el('p','Consolidated run reports, original saved reports and recognized native tool report entrypoints. Alignment viewers are in Trees & alignments; all other HTML remains in All run files. Report availability does not establish execution success.','small muted'))}
 rows=rows.filter(f=>state.attempt==='all'||(state.attempt==='history')===['historical snapshot','other attempt; not current evidence'].includes(f.status));
 target.append(el('p',rows.length.toLocaleString()+' matching files · Open preserves native report assets; download saves the individual file.','small muted'));const start=state.page*100;
 table(target,['Run / module','File','Category / status','Size','Actions'],rows.slice(start,start+100).map(f=>{const actions=el('div');actions.append(link(f.href,'Open'));const a=link(f.href,'Download');a.download=f.path.split('/').pop();actions.append(document.createTextNode(' · '),a);return [f.run+' / '+(f.module||'run'),f.path,(state.view==='Sample reports'?f.report_role:f.kind)+' · '+f.status,bytes(f.bytes),actions]}));
 const pager=el('div',undefined,'pager');const prev=button('Previous',()=>{state.page--;drawRows()});prev.disabled=state.page===0;const next=button('Next',()=>{state.page++;drawRows()});next.disabled=start+100>=rows.length;pager.append(prev,el('span','Page '+(state.page+1)+' / '+Math.max(1,Math.ceil(rows.length/100))),next);target.append(pager)};drawRows()}
function candidatesView(host){
 filters(host);const target=el('div');host.append(target);
 drawRows=()=>{
  target.replaceChildren();const q=state.query.toLowerCase();
  const rows=selected().flatMap(r=>r.candidates.map(c=>({...c,run:r.label}))).filter(c=>[c.sample,c.protein,c.gene,c.interpretation,c.followup,c.reason,c.search_text,...(c.missing_comparison_groups||[])].join(' ').toLowerCase().includes(q));
  target.append(el('p','Recorded selected candidates only. Interpretations and review reasons are copied without reassessment. The full report retains deferred scope.','small muted'));
  if(selected().some(r=>r.navigation_source==='verified_report_snapshot'))target.append(el('p','Detailed links follow the saved report snapshot; its report, evidence and candidate exports matched the navigation index. This checks links, not biological interpretation.','small muted'));
  selected().forEach(r=>r.warnings.filter(w=>w.startsWith('Detailed report navigation unavailable:')).forEach(w=>target.append(el('p',r.label+': '+w,'note'))));
  table(target,['Sample / protein','Recorded label','Execution / interpretation','Recorded review','Evidence'],rows.map(c=>{
   const box=el('div');box.append(link(c.href,'Open candidate'),document.createTextNode(' · '),link(c.evidence,c.navigation_source==='verified_report_snapshot'?'Candidate JSON':'Source JSON'));
   const labels={domains:'Domains',motifs:'Motifs & profiles',trees:'Trees & alignments',placement:'Reference placement',references:'Comparators',files:'Supporting files'};
   const shortcuts=el('div',undefined,'candidate-shortcuts');
   Object.entries(labels).forEach(([key,label])=>{if(c.sections&&c.sections[key])shortcuts.append(link(c.sections[key],label,'button secondary'))});
   if(shortcuts.children.length)box.append(shortcuts);
   const d=el('details');d.append(el('summary','Recorded analysis layers'));Object.entries(c.layers||{}).forEach(([k,v])=>d.append(el('p',human(k)+': '+human(v))));box.append(d);
   const review=el('div');review.append(el('p',human(c.followup)));
   if(c.reason)review.append(el('p',human(c.reason),'small'));
   if((c.missing_comparison_groups||[]).length)review.append(el('p','Comparator gaps: '+c.missing_comparison_groups.join(', '),'small'));
   return [c.sample+' / '+c.protein,c.gene,human(c.execution)+' / '+human(c.interpretation),review,box];
  }));
 };drawRows();
}
function render(){document.getElementById('title').textContent=state.view;document.querySelectorAll('.nav').forEach((b,i)=>b.classList.toggle('active',views[i]===state.view));const host=document.getElementById('content');host.replaceChildren();drawRows=()=>{};
 if(state.view==='Overview'){cards(host);const p=el('div',undefined,'panel');p.append(el('h2','One entry point to the study'),el('p','Open a sample report, inspect a selected candidate, or browse trees, alignments, tables, sequences and command logs. Native reports open in a new tab so their existing interactive assets remain available.'));p.append(el('p','This is a saved snapshot. Rebuild after analyses finish to include new artifacts. Keep this panel beside its linked study folders; the HTML alone is not a portable report bundle.','muted'));host.append(p)}
 else if(state.view==='Candidate evidence')candidatesView(host);
 else if(state.view==='Execution & provenance'){filters(host);const target=el('div');host.append(target);drawRows=()=>{target.replaceChildren();const q=state.query.toLowerCase();table(target,['Sample','Module','Recorded execution','Reason','Evidence'],selected().flatMap(r=>r.modules).filter(m=>[m.sample,m.name,m.status,m.reason].join(' ').toLowerCase().includes(q)).map(m=>[m.sample,m.name,human(m.status),m.reason,link(m.href,'Open JSON')]));data.warnings.forEach(w=>target.append(el('p',w,'note')))};drawRows()}
 else filesView(host);
}
render();
</script></body></html>'''


def render(data):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return HTML.replace("__DATA__", payload)


def build_control_panel(results=(), pilot_dir=None, outdir=None, project=None, artifact_roots=None):
    """Index explicitly selected saved outputs and write a fresh local panel.

    The returned paths are absolute. An existing output directory is never
    overwritten. No input checksums, native outputs or interpretations are
    silently treated as newly validated by the navigation index.
    """
    project = Path(project or Path.cwd()).resolve()
    pilot_dir = Path(pilot_dir).resolve() if pilot_dir is not None else None
    warnings, sources = [], []
    if pilot_dir:
        manifest_path = pilot_dir / "pilot.json"
        if not safe_file(manifest_path, pilot_dir):
            raise ValueError("Pilot manifest missing or outside the selected pilot")
        pilot = load_json(manifest_path, warnings)
        if pilot.get("schema") != "basis-pilot456-runner-v1":
            raise ValueError("Unrecognized pilot manifest schema")
        for item in pilot.get("samples", []):
            root = Path(item["outdir"]).resolve()
            if not root.is_relative_to(pilot_dir):
                raise ValueError(f"Pilot output escapes selected study: {root}")
            sources.append((root, item))
        say(f"Selected pilot: {pilot_dir}")
    for root in results:
        root = Path(root).resolve(strict=True)
        if not root.is_dir() or not ((root / "results").is_dir() or (root / "report.html").is_file()):
            raise ValueError(f"Not a BaSIS output directory: {root}")
        sources.append((root, None))
    if not sources:
        raise ValueError("No run outputs selected")
    from .report_artifacts import allowed_roots, INDEX
    # Resolve user-selected roots before any output mutation; saved metadata is
    # never consulted to authorize additional directories.
    explicit_roots = list(dict.fromkeys(allowed_roots(value)[0] for value in (artifact_roots or ())))
    base = pilot_dir if pilot_dir else project
    output_path = Path(outdir).expanduser() if outdir is not None else base / "control-panels" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:6])
    if output_path.is_symlink():
        raise FileExistsError(f"Output already exists as a symlink: {output_path}")
    output = output_path.resolve()
    for root, _ in sources:
        if output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError("Run the helper from outside the selected output directories")
    source_hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for root, _ in sources
                     for p in (root / "evidence.json", root / "run.json", root / INDEX)
                     if safe_file(p, root)}
    output.mkdir(parents=True, exist_ok=False)
    data = {"schema": "basis-report-control-panel-v1", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "runs": [], "warnings": warnings,
            "scope": "Existing in-run files and exact source-declared files within explicitly selected artifact roots; external directories are never recursively inventoried.",
            "limitations": "No analyses, biological reassessment or independent scientific validation performed. Navigation snapshot hashes verify report-link consistency only; native analysis artifacts are not revalidated."}
    seen = set()
    for root, item in sources:
        if root in seen:
            continue
        seen.add(root)
        say(f"Indexing saved outputs: {root}")
        data["runs"].append(collect_run(root, f"run{len(data['runs'])+1}", output, item, explicit_roots))
    for path, digest in source_hashes.items():
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Saved report source changed while indexing: {path}")
    (output / "inventory.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "index.html").write_text(render(data), encoding="utf-8")
    say(f"CONTROL PANEL: {output / 'index.html'}")
    say(f"Runs: {len(data['runs'])} | files linked: {sum(len(r['files']) for r in data['runs']):,}")
    say("Report navigation only. Original results and scientific interpretations unchanged.")
    return {"html": str(output / "index.html"), "inventory": str(output / "inventory.json"),
            "run_count": len(data["runs"]), "file_count": sum(len(r["files"]) for r in data["runs"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-dir", type=Path)
    parser.add_argument("--results", type=Path, action="append", default=[])
    parser.add_argument("--outdir", type=Path)
    parser.add_argument("--artifact-root", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    if not args.pilot_dir and not args.results:
        parser.error("select saved results with --results PATH or --pilot-dir PATH")
    build_control_panel(results=args.results, pilot_dir=args.pilot_dir, outdir=args.outdir,
                        artifact_roots=args.artifact_root)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(f"Control-panel error: {exc}", file=sys.stderr)
        raise SystemExit(1)
