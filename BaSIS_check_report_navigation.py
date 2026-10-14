#!/usr/bin/env python3
"""Check installed rc9 report navigation using small, synthetic saved JSON only.

This checker invokes verify-install, report and control-panel. It does not invoke
a pipeline run, download references, inspect original studies, or run analyses.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from urllib.parse import unquote, urlsplit
import zipfile

RELEASE = "0.2.0rc9"
SECTIONS = {"overview", "domains", "motifs", "trees", "placement", "references", "files"}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def object_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


class HTMLInventory(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.ids, self.hrefs, self.text = Counter(), [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids[attrs["id"]] += 1
        if tag == "a" and attrs.get("href"):
            self.hrefs.append(attrs["href"])

    def handle_data(self, text):
        self.text.append(text)


def local_target(href, page, allowed_root):
    """Resolve a local offline link and, when present, verify its HTML anchor."""
    require(isinstance(href, str) and bool(href), "Missing local link")
    url = urlsplit(href)
    require(not url.scheme and not url.netloc and not url.query, "Unexpected non-local link")
    require(not unquote(url.path).startswith("/"), "Unexpected absolute link")
    target = (page.parent / unquote(url.path)).resolve() if url.path else page.resolve()
    require(target.is_relative_to(allowed_root.resolve()) and target.is_file(), "Link is missing or escapes checker outputs")
    if url.fragment:
        ids = HTMLInventory(target.read_text(encoding="utf-8")).ids
        require(ids.get(unquote(url.fragment)) == 1, "Link anchor is missing or duplicated")
    return target


def trailing_json(stdout):
    """Reporting commands retain human preambles; their final payload is JSON."""
    found = []
    for match in re.finditer(r"(?m)^\{", stdout):
        try:
            value = json.loads(stdout[match.start():])
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    require(len(found) == 1, "Expected exactly one final reporting JSON object")
    return found[0]


def command(argv, outdir, tag):
    require(len(argv) > 1 and argv[1] in {"verify-install", "report", "control-panel"}, "Only offline reporting commands are permitted")
    print(f"Checking {tag}...", flush=True)
    stdout_path = outdir / f"{tag}.stdout.txt"
    errors = []
    with stdout_path.open("w", encoding="utf-8") as stream:
        with subprocess.Popen(argv, stdout=stream, stderr=subprocess.PIPE, text=True) as process:
            for line in process.stderr:
                errors.append(line)
                sys.stderr.write(line)
                sys.stderr.flush()
            code = process.wait()
    stdout = stdout_path.read_text(encoding="utf-8")
    record = {"argv": argv, "exit_code": code, "stdout_sha256": sha(stdout_path),
              "stdout_bytes": stdout_path.stat().st_size, "stdout": stdout, "stderr": "".join(errors)}
    try:
        record["result"] = trailing_json(stdout)
    except Exception as exc:
        record["parse_error"] = str(exc)
        write_json(outdir / f"{tag}.command.json", record)
        raise
    write_json(outdir / f"{tag}.command.json", record)
    require(code == 0, f"{tag}: reporting command returned {code}")
    return record["result"]


def installed_runtime():
    import basis
    require(basis.__version__ == RELEASE, f"Expected installed BaSIS {RELEASE}; found {basis.__version__}")
    package, prefix = Path(basis.__file__).resolve(), Path(sys.prefix).resolve()
    require(package.is_relative_to(prefix), "BaSIS must import from the active release environment")
    cli = prefix / "bin/basis"
    require(cli.is_file(), f"Installed command is absent: {cli}")
    names = {"cli.py", "report.py", "report_refresh.py", "report_navigation.py", "control_panel.py", "report_motif_views.py"}
    return [str(cli)], {"basis_version": basis.__version__, "basis_file": str(package),
        "python": sys.version, "python_executable": sys.executable, "environment_prefix": str(prefix),
        "package_code_sha256": {name: sha(package.parent / name) for name in sorted(names)},
        "release_manifest_sha256": sha(package.parent / "resources/release-manifest.json")}


def candidate(sample, identifier, protein, selected=True, execution="completed"):
    return {"candidate_id": identifier, "protein_id": protein, "sample_id": sample,
        "gene": "Synthetic display label", "family_labels": ["Synthetic display alias"],
        "selected": selected, "selection_queue": "native_screen" if selected else "exploratory",
        "execution": execution if selected else "deferred", "source_evidence": [], "references": [],
        "assessment": {"interpretation": "unresolved", "followup": "review_existing_evidence"},
        "analyses": {"domains": {"status": "completed", "hits": []},
            "motifs": {"status": "no_applicable_rule", "rules": []},
            "prosite_patterns": {"status": "completed", "hits": []},
            "prosite_profiles": {"status": "completed", "hits": []},
            "phylogeny": {"status": "not_assessed", "reason": "fixture_tree_artifact_not_available"}}}


def make_fixture(path):
    a = candidate("DEMO_A", "synthetic-a-shared", "SAME_PROTEIN")
    a["analyses"]["motifs"] = {"status": "completed", "rules": [{
        "name": "Synthetic navigation motif", "rule_id": "navigation_fixture_rule",
        "pattern": "synthetic-display-only", "status": "present",
        "matches": [{"start": 2, "end": 4, "sequence": "XXX"}],
        "limitations": ["Fabricated display fixture; no biological inference."]}]}
    hold = candidate("DEMO_A", "synthetic-hold", "HOLD_PROTEIN")
    hold["assessment"].update(followup="hold_for_comparator_review",
                              reason="missing_reviewed_comparison_groups",
                              missing_comparison_groups=["synthetic_missing_group"])
    hold["notes"] = ["missing_reviewed_comparison_group:synthetic_missing_group"]
    hold["analyses"]["phylogeny"]["reason"] = "missing_reviewed_comparison_groups"
    deferred = candidate("DEMO_A", "synthetic-deferred", "DEFERRED_PROTEIN", selected=False)
    b = candidate("DEMO_B", "synthetic-b-shared", "SAME_PROTEIN")
    failed = candidate("DEMO_B", "synthetic-failure", "FAILED_PROTEIN", execution="failed")
    failed["reason"] = "Deliberate saved-candidate software failure"
    # A failed record intentionally retains a motif match, which must not be
    # promoted to completed evidence by the report's search/card presentation.
    failed["analyses"]["motifs"] = copy.deepcopy(a["analyses"]["motifs"])
    failed["analyses"]["motifs"]["rules"][0]["name"] = "Untrusted retained motif fixture"
    rows = {"DEMO_A": [a, hold, deferred], "DEMO_B": [b, failed]}
    evidence = {"schema_version": "basis-report-1", "run": {
        "run_id": "synthetic-report-navigation-fixture", "version": RELEASE,
        "validation_mode": "synthetic_fixture", "completeness": "incomplete",
        "enabled_modules": ["amrfinder"], "options": {"deep": True, "extend": False}},
        "samples": [{"sample_id": sample} for sample in rows],
        "records": [{"sample_id": sample, "module": "amrfinder", "execution_status": "completed",
            "findings": [], "provenance": {"commands": []}} for sample in rows],
        "deep_records": [{"sample_id": sample, "module": "deepsafe", "execution_status": "completed",
            "candidates": candidates, "findings": [], "upstream_incomplete": [], "provenance": {"commands": []}}
            for sample, candidates in rows.items()]}
    path.parent.mkdir(parents=True, exist_ok=False)
    write_json(path, evidence)
    return evidence


def selected_map(evidence):
    return {(record["sample_id"], c["candidate_id"]): c for record in evidence["deep_records"]
            for c in record.get("candidates", []) if c.get("selected") is True}


def check_preserved(source, rendered):
    for key in ("samples", "records", "deep_records"):
        require(source.get(key) == rendered.get(key), f"Saved {key} changed during rendering")
    for key, value in source["run"].items():
        require(rendered.get("run", {}).get(key) == value, "A recorded run field changed during rendering")


def inspect_report(directory, source):
    page, evidence_path, index_path = (directory / name for name in ("report.html", "evidence.json", "report-navigation.json"))
    rendered, index = read_json(evidence_path), read_json(index_path)
    check_preserved(source, rendered)
    require(index.get("schema") == "basis-report-navigation-v1", "Unexpected report navigation schema")
    require(index.get("report_sha256") == sha(page), "Navigation index report hash is stale")
    require(index.get("evidence_sha256") == sha(evidence_path), "Navigation index evidence hash is stale")
    parsed = HTMLInventory(page.read_text(encoding="utf-8"))
    require(all(n == 1 for n in parsed.ids.values()), "Report contains duplicate HTML IDs")
    expected, seen, anchors, section_links = selected_map(source), set(), set(), 0
    for row in index.get("candidates", []):
        key = (row.get("sample_id"), row.get("candidate_id"))
        require(key in expected and key not in seen, "Navigation candidate identity missing or duplicated")
        seen.add(key)
        c = expected[key]
        require(row.get("protein_id") == c["protein_id"], "Navigation changed a protein identity")
        require(row.get("candidate_sha256") == object_sha(c), "Navigation candidate fingerprint differs from saved evidence")
        anchor = row.get("anchor")
        require(isinstance(anchor, str) and parsed.ids.get(anchor) == 1 and anchor not in anchors,
                "Candidate anchor missing or reused across samples")
        anchors.add(anchor)
        require(set(row.get("sections", {})) == SECTIONS, "Candidate section inventory is incomplete")
        for target in row["sections"].values():
            require(parsed.ids.get(target) == 1, "Candidate section anchor missing or duplicated")
            local_target("#" + target, page, directory)
            section_links += 1
        payload = local_target(row.get("evidence_href"), page, directory)
        require(read_json(payload) == c, "Candidate download differs from saved candidate evidence")
        require(row.get("execution") == c["execution"], "Candidate execution label changed")
        require(row.get("interpretation") == c["assessment"]["interpretation"], "Candidate interpretation changed")
        require(row.get("followup") == c["assessment"]["followup"], "Candidate follow-up changed")
        require(isinstance(row.get("search_text"), str) and row["search_text"].strip(), "Candidate search text missing")
        require(row.get("layer_statuses") == {name: layer.get("status", "not recorded")
                for name, layer in c.get("analyses", {}).items() if isinstance(layer, dict)},
                "Navigation changed recorded analysis-layer statuses")
        if key == ("DEMO_A", "synthetic-a-shared"):
            require("synthetic navigation motif" in row["search_text"].lower(), "Recorded motif is not searchable")
    require(seen == set(expected), "Report omitted a selected candidate or included a deferred candidate")
    text = " ".join(parsed.text)
    require("Synthetic navigation motif" in text, "Recorded motif is not visible in report details")
    require("fixture tree artifact not available" in text.replace("_", " "), "Unavailable tree reason is not visible")
    require("synthetic missing group" in text.replace("_", " "), "Comparator hold detail is not visible")
    checked_local = 0
    for target in parsed.hrefs:
        parts = urlsplit(target)
        if not parts.scheme and not parts.netloc:
            local_target(target, page, directory)
            checked_local += 1
    return {"candidate_count": len(seen), "candidate_anchor_count": len(anchors), "section_link_count": section_links,
        "checked_local_report_links": checked_local,
        "report_sha256": sha(page), "evidence_sha256": sha(evidence_path), "navigation_sha256": sha(index_path),
        "source_record_fields_unchanged": True}, index


def inspect_panel(directory, expected_source, outdir, index=None):
    inventory = read_json(directory / "inventory.json")
    runs = inventory.get("runs", [])
    require(len(runs) == 1, "Panel changed the selected run count")
    run = runs[0]
    require(run.get("navigation_source") == expected_source, "Panel navigation source was mislabelled")
    candidates = run.get("candidates", [])
    require(len(candidates) == 4, "Panel changed the selected-candidate inventory")
    page = directory / "index.html"
    checked = 0
    for row in candidates:
        require(row.get("protein") != "DEFERRED_PROTEIN", "Deferred candidate was promoted to selected")
        for key in ("href", "evidence"):
            target = row.get(key)
            if target:
                local_target(target, page, outdir)
                checked += 1
        if expected_source == "verified_report_snapshot":
            require(set(row.get("sections", {})) == SECTIONS, "Panel omitted direct candidate section links")
            for target in row["sections"].values():
                local_target(target, page, outdir)
                checked += 1
    if index is not None:
        expected = {(r["sample_id"], r["protein_id"]): r for r in index["candidates"]}
        for row in candidates:
            original = expected[(row["sample"], row["protein"])]
            for key in ("execution", "interpretation", "followup", "reason", "missing_comparison_groups", "search_text"):
                require(row.get(key) == original.get(key), f"Panel {key} differs from report snapshot")
    return {"navigation_source": expected_source, "candidate_count": len(candidates),
            "checked_candidate_links": checked, "warnings": run.get("warnings", []),
            "inventory_sha256": sha(directory / "inventory.json")}


def verify(cli, outdir, bundle, observed):
    checked = command([*cli, "verify-install"], outdir, "verify-install")
    require(checked.get("status") == "passed", "Installed package integrity failed")
    bundle["installation"] = checked
    source_path = outdir / "synthetic-source/evidence.json"
    source = make_fixture(source_path)
    observed[source_path] = sha(source_path)
    cases = bundle.setdefault("cases", [])
    directory = outdir / "rendered"
    result = command([*cli, "report", "--evidence", str(source_path), "--outdir", str(directory)], outdir, "saved-report")
    require(result.get("native_analyses_run") is False, "Reporting command did not declare offline replay")
    check, index = inspect_report(directory, source)
    audit = read_json(directory / "replay-audit.json")
    require(audit.get("source_unchanged") is True and audit.get("finalized") is True
            and audit.get("native_analyses_run") is False and audit.get("gpu_inference_run") is False,
            "Replay preservation audit is incomplete")
    cases.append({"case": "saved-report", "status": "passed", **check})
    # Copies are expendable test fixtures. The original generated report and its
    # immutable saved source are never altered for the fallback checks.
    protected = {p: sha(p) for p in directory.rglob("*") if p.is_file()}
    for case, nav_source in (("current-index", "verified_report_snapshot"),
                             ("absent-index", "legacy_result_records"),
                             ("stale-index", "legacy_result_records")):
        root = directory
        if case != "current-index":
            root = outdir / case
            shutil.copytree(directory, root)
            if case == "absent-index":
                (root / "report-navigation.json").unlink()
            else:
                stale = read_json(root / "report-navigation.json")
                stale["report_sha256"] = "0" * 64
                write_json(root / "report-navigation.json", stale)
        panel = outdir / f"{case}-panel"
        command([*cli, "control-panel", "--results", str(root), "--outdir", str(panel)], outdir, case)
        inspected = inspect_panel(panel, nav_source, outdir, index if case == "current-index" else None)
        if case == "stale-index":
            require(inspected["warnings"], "Stale navigation index was silently accepted")
        cases.append({"case": case, "status": "passed", **inspected})
    require(unchanged(protected), "Control-panel construction changed the original rendered report")
    bundle["rendered_result_files_preserved"] = len(protected)


def unchanged(observed):
    try:
        return all(path.is_file() and sha(path) == digest for path, digest in observed.items())
    except OSError:
        return False


def make_archive(outdir):
    paths = [outdir / "report-navigation-verification.json", *outdir.glob("*.command.json")]
    if (outdir / "error.txt").is_file():
        paths.append(outdir / "error.txt")
    for name in ("synthetic-source", "rendered", "absent-index", "stale-index",
                 "current-index-panel", "absent-index-panel", "stale-index-panel"):
        directory = outdir / name
        if directory.is_dir():
            paths.extend(p for p in directory.rglob("*") if p.is_file()
                         and p.suffix in {".json", ".html", ".tsv", ".txt", ".cff", ".bib", ".ris"})
    archive = outdir / "report-navigation-check-summary.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(set(paths)):
            zipped.write(path, path.relative_to(outdir))
    return archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, help="New directory; default creates report-navigation-check-* here")
    args = parser.parse_args(argv)
    if args.outdir:
        outdir = args.outdir.expanduser().absolute()
        require(not outdir.exists() and not outdir.is_symlink(), "Output path already exists")
        outdir.mkdir(parents=True, exist_ok=False)
    else:
        outdir = Path(tempfile.mkdtemp(prefix="report-navigation-check-", dir=Path.cwd()))
    observed = {}
    bundle = {"schema": "basis-report-navigation-acceptance-v1", "release_version": RELEASE,
        "status": "failed", "started_at_utc": now(), "outdir": str(outdir), "checker_sha256": sha(__file__),
        "scope": "offline saved-report rendering, candidate navigation, evidence preservation and control-panel fallback",
        "synthetic_inputs_only": True, "external_original_inputs_read": False,
        "native_analyses_performed": False, "gpu_inference_performed": False, "biological_validation_performed": False,
        "limitations": ["Synthetic saved labels and statuses test software navigation only.",
                        "No native analyses, downloads, scientific interpretation or browser interaction are validated."]}
    try:
        cli, runtime = installed_runtime()
        bundle["runtime"] = runtime
        verify(cli, outdir, bundle, observed)
        bundle["status"] = "passed_report_navigation_checks"
    except Exception as exc:
        bundle["error"] = str(exc)
        (outdir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        bundle["synthetic_inputs_unchanged"] = unchanged(observed)
        bundle["observed_synthetic_input_file_count"] = len(observed)
        if not bundle["synthetic_inputs_unchanged"]:
            bundle["status"], bundle["error"] = "failed", "A preserved synthetic source changed during checking"
        bundle["finished_at_utc"] = now()
        write_json(outdir / "report-navigation-verification.json", bundle)
        archive = make_archive(outdir)
    print("Status:", bundle["status"])
    if bundle.get("error"):
        print("ERROR:", bundle["error"])
    print("Evidence:", outdir / "report-navigation-verification.json")
    print("Upload summary:", archive)
    print("Synthetic saved evidence only. No native biological tools or GPU inference used.")
    return 0 if bundle["status"] == "passed_report_navigation_checks" else 1


if __name__ == "__main__":
    raise SystemExit(main())
