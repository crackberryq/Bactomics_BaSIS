#!/usr/bin/env python3
"""Check installed rc12 reporting with synthetic saved evidence and local files.

Only verify-install, report, and control-panel are invoked. No biological tools,
GPU inference, downloads, or original studies are used.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import traceback
from urllib.parse import unquote, urlsplit
import zipfile

RELEASE = "0.2.0rc13"
ALLOWED_COMMANDS = frozenset({"verify-install", "report", "control-panel"})


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def now():
    return datetime.now(timezone.utc).isoformat()


class HTMLInventory(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.ids, self.hrefs = Counter(), []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids[attrs["id"]] += 1
        if tag == "a" and attrs.get("href"):
            self.hrefs.append(attrs["href"])


def local_target(href, page, root):
    parsed = urlsplit(href)
    require(not parsed.scheme and not parsed.netloc and not parsed.query, "Expected an offline local link")
    require(not unquote(parsed.path).startswith("/"), "Absolute artifact link is not portable")
    path = (page.parent / unquote(parsed.path)).resolve() if parsed.path else page.resolve()
    require(path.is_relative_to(root.resolve()) and path.is_file(), "Local report link is missing or escapes the acceptance fixture")
    if parsed.fragment:
        ids = HTMLInventory(path.read_text(encoding="utf-8")).ids
        require(ids[unquote(parsed.fragment)] == 1, "Report anchor is missing or duplicated")
    return path


def final_json(output):
    found = []
    for start in re.finditer(r"(?m)^\{", output):
        try:
            obj = json.loads(output[start.start():])
        except ValueError:
            continue
        if isinstance(obj, dict):
            found.append(obj)
    require(len(found) == 1, "Expected one final JSON command result")
    return found[0]


def command(cli, args, outdir, tag, expect_success=True):
    require(args and args[0] in ALLOWED_COMMANDS, "Only installed offline reporting commands may be run")
    print(f"Checking {tag}...", flush=True)
    completed = subprocess.run([*cli, *args], text=True, capture_output=True, check=False)
    record = {"argv": [*cli, *args], "exit_code": completed.returncode,
              "stdout": completed.stdout, "stderr": completed.stderr,
              "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
              "expected_success": expect_success}
    write_json(outdir / f"{tag}.command.json", record)
    if expect_success:
        require(completed.returncode == 0, f"{tag} exited {completed.returncode}: {completed.stderr[-2000:]}")
        return final_json(completed.stdout)
    require(completed.returncode != 0, f"{tag} unexpectedly accepted an invalid request")
    return record


def installed_runtime():
    import basis
    require(basis.__version__ == RELEASE, f"Expected installed BaSIS {RELEASE}; found {basis.__version__}")
    package, prefix = Path(basis.__file__).resolve(), Path(sys.prefix).resolve()
    require(package.is_relative_to(prefix), "BaSIS must import from the active release environment")
    cli = prefix / "bin/basis"
    require(cli.is_file(), f"Installed command is absent: {cli}")
    return [str(cli)], {"basis_version": basis.__version__, "basis_file": str(package),
        "python": sys.version, "python_executable": sys.executable,
        "environment_prefix": str(prefix),
        "release_manifest_sha256": sha(package.parent / "resources/release-manifest.json")}


def make_fixture(root):
    source, allowed, blocked = (root / name for name in ("saved-source", "allowed-native", "blocked-native"))
    for folder in (source / "native", allowed, blocked):
        folder.mkdir(parents=True, exist_ok=False)
    native = source / "native/commands.log"
    external, forbidden = allowed / "external.txt", blocked / "private.txt"
    for path in (native, external, forbidden):
        path.write_text("Synthetic reporting acceptance fixture. No biological data.\n", encoding="utf-8")
    symlink = source / "native/escape.txt"
    symlink.symlink_to(forbidden)
    paths = {"directory": source / "native", "native": native, "external": external,
             "missing": source / "native/absent.txt", "blocked": forbidden, "symlink": symlink}
    candidates = []
    for name, selected, execution in (("SELECTED", True, "completed"), ("DEFERRED", False, "deferred")):
        candidates.append({"sample_id": "SYNTHETIC", "candidate_id": "fixture-" + name.lower(),
            "protein_id": name, "gene": "Synthetic display fixture", "selected": selected,
            "selection_queue": "native_screen" if selected else "exploratory", "execution": execution,
            "source_evidence": [], "references": [],
            "assessment": {"interpretation": "unresolved", "followup": "hold_for_comparator_review"},
            "analyses": {"domains": {"status": "completed", "hits": []},
                         "motifs": {"status": "no_applicable_rule", "rules": []},
                         "phylogeny": {"status": "not_assessed", "reason": "missing_reviewed_comparison_groups"}}})
    evidence = {"schema_version": "basis-report-1", "run": {
        "run_id": "synthetic-rc12-reporting", "version": RELEASE, "validation_mode": "synthetic_fixture",
        "completeness": "incomplete", "enabled_modules": ["amrfinder"],
        "options": {"deep": True, "extend": False}}, "samples": [{"sample_id": "SYNTHETIC"}],
        "records": [{"sample_id": "SYNTHETIC", "module": "amrfinder", "execution_status": "completed",
                     "findings": [], "artifacts": [str(p) for p in paths.values()], "provenance": {"commands": []}}],
        "deep_records": [{"sample_id": "SYNTHETIC", "module": "deepsafe", "execution_status": "completed",
                          "candidates": candidates, "findings": [], "upstream_incomplete": [],
                          "provenance": {"commands": []}}]}
    path = source / "evidence.json"
    write_json(path, evidence)
    return path, evidence, paths, allowed, blocked


def unchanged(observed):
    return all(path.is_file() and not path.is_symlink() and sha(path) == digest for path, digest in observed.items())


def check_preserved(source, rendered):
    for key in ("samples", "records", "deep_records"):
        require(source[key] == rendered.get(key), f"Saved {key} changed during rendering")
    for key, value in source["run"].items():
        require(rendered.get("run", {}).get(key) == value, f"Saved run field changed: {key}")


def inspect_links(page, root, blocked):
    parsed = HTMLInventory(page.read_text(encoding="utf-8"))
    require(all(n == 1 for n in parsed.ids.values()), "Duplicate IDs in generated HTML")
    targets = []
    for link in parsed.hrefs:
        url = urlsplit(link)
        if url.scheme or url.netloc:
            continue
        target = local_target(link, page, root)
        require(not target.is_relative_to(blocked.resolve()), "Generated page links an unauthorized artifact")
        targets.append(target)
    return targets


def inspect_panel_artifacts(run, paths, expected_statuses):
    details = run.get("source_artifacts", {})
    require(details.get("schema") == "basis-panel-source-artifacts-v1", "Panel did not verify source artifact provenance")
    entries = {row["declared_path"]: row for row in details.get("entries", [])}
    for name, status in expected_statuses.items():
        entry = entries.get(str(paths[name]), {})
        require(entry.get("status") == status, f"Panel artifact {name}: expected {status}, got {entry.get('status')}")
        require(bool(entry.get("href")) == (status == "file"), f"Panel artifact {name}: inappropriate link availability")


def verify(cli, outdir, audit, observed):
    installed = command(cli, ["verify-install"], outdir, "verify-install")
    require(installed.get("status") == "passed", "Installed package integrity failed")
    audit["installation"] = installed
    source_path, source, paths, allowed, blocked = make_fixture(outdir / "fixture")
    observed.update({p: sha(p) for p in source_path.parent.parent.rglob("*") if p.is_file() and not p.is_symlink()})
    expected = sha(source_path)
    rejected = outdir / "rejected-pin-report"
    command(cli, ["report", "--evidence", str(source_path), "--outdir", str(rejected),
                  "--expected-sha256", "0" * 64], outdir, "wrong-evidence-pin", expect_success=False)
    require(not rejected.exists(), "Rejected evidence pin created report output")
    rendered = outdir / "rendered"
    result = command(cli, ["report", "--evidence", str(source_path), "--outdir", str(rendered),
        "--expected-sha256", expected, "--artifact-root", str(allowed)], outdir, "saved-report")
    require(result.get("native_analyses_run") is False, "Report command omitted its offline replay declaration")
    check_preserved(source, read_json(rendered / "evidence.json"))
    replay = read_json(rendered / "replay-audit.json")
    require(replay.get("source_unchanged") is True and replay.get("finalized") is True
            and replay.get("native_analyses_run") is False and replay.get("gpu_inference_run") is False,
            "Replay did not finalize evidence-preservation checks")
    require(replay.get("source_evidence", {}).get("sha256") == expected, "Replay evidence pin changed")
    catalogue = read_json(rendered / "source-artifacts.json")
    require(catalogue.get("schema") == "basis-source-artifacts-v1", "Unexpected artifact catalogue schema")
    require(catalogue.get("source_evidence", {}).get("sha256") == expected
            and catalogue.get("evidence_sha256") == sha(rendered / "evidence.json")
            and replay.get("output_file_sha256", {}).get("source-artifacts.json") == sha(rendered / "source-artifacts.json"),
            "Source artifact catalogue is not bound to the exact source and rendered evidence")
    entries = {entry["declared_path"]: entry for entry in catalogue.get("entries", [])}
    expected_statuses = {"directory": "directory", "native": "file", "external": "file", "missing": "missing",
                         "blocked": "outside_allowed_roots", "symlink": "symlink"}
    for name, status in expected_statuses.items():
        row = entries.get(str(paths[name]), {})
        require(row.get("status") == status, f"Artifact {name}: expected {status}, got {row.get('status')}")
        require(bool(row.get("href")) == (status == "file"), f"Artifact {name}: inappropriate link availability")
    page = rendered / "report.html"
    links = inspect_links(page, outdir, blocked)
    require((rendered / "source-artifacts.html").resolve() in links, "Main report does not link the source artifact browser")
    artifact_links = inspect_links(rendered / "source-artifacts.html", outdir, blocked)
    require(paths["native"].resolve() in artifact_links and paths["external"].resolve() in artifact_links,
            "Available declared native files are not accessible")
    navigation = read_json(rendered / "report-navigation.json")
    require(navigation.get("report_sha256") == sha(page), "Navigation report hash is stale")
    require([row.get("protein_id") for row in navigation.get("candidates", [])] == ["SELECTED"],
            "Navigation changed selected/deferred candidate scope")
    protected = {p: sha(p) for p in rendered.rglob("*") if p.is_file() and not p.is_symlink()}
    panel = outdir / "control-panel"
    command(cli, ["control-panel", "--results", str(rendered), "--outdir", str(panel),
                  "--artifact-root", str(source_path.parent), "--artifact-root", str(allowed)], outdir, "control-panel")
    inventory = read_json(panel / "inventory.json")
    require(len(inventory.get("runs", [])) == 1, "Panel changed selected run count")
    run = inventory["runs"][0]
    require(run.get("status") == "incomplete" and run.get("source_snapshot", {}).get("sha256") == expected,
            "Panel changed the saved scope or source identity")
    candidates = run.get("candidates", [])
    require(len(candidates) == 1 and candidates[0].get("protein") == "SELECTED", "Panel changed candidate scope")
    require(candidates[0].get("execution") == "completed" and candidates[0].get("interpretation") == "unresolved",
            "Panel changed execution or interpretation")
    inspect_panel_artifacts(run, paths, expected_statuses)
    panel_links = inspect_links(panel / "index.html", outdir, blocked)
    for c in candidates:
        for link in [c.get("href"), c.get("evidence"), *c.get("sections", {}).values()]:
            if link:
                local_target(link, panel / "index.html", outdir)
    restricted_panel = outdir / "control-panel-without-external-roots"
    command(cli, ["control-panel", "--results", str(rendered), "--outdir", str(restricted_panel)],
            outdir, "panel-root-authorization")
    restricted = read_json(restricted_panel / "inventory.json")["runs"][0]
    restricted_statuses = {name: "symlink" if name == "symlink" else "outside_allowed_roots" for name in paths}
    inspect_panel_artifacts(restricted, paths, restricted_statuses)
    require(unchanged(protected), "Panel construction modified rendered evidence or reports")
    second_path, second, _, second_allowed, _ = make_fixture(outdir / "second-fixture")
    second["run"]["run_id"] = "synthetic-rc12-reporting-second"
    write_json(second_path, second)
    sources = [{"sample_id": "SYNTHETIC", "evidence": str(path.relative_to(outdir)),
                "evidence_sha256": sha(path), "origin": "synthetic_acceptance_fixture"}
               for path in (source_path, second_path)]
    manifest = outdir / "synthetic-sources.json"
    write_json(manifest, sources)
    invalid = outdir / "synthetic-sources-invalid.json"
    write_json(invalid, [sources[0], {**sources[1], "evidence_sha256": "0" * 64}])
    observed.update({p: sha(p) for p in second_path.parent.parent.rglob("*") if p.is_file() and not p.is_symlink()})
    observed.update({p: sha(p) for p in (manifest, invalid)})
    rejected_batch = outdir / "rejected-batch"
    command(cli, ["report", "--sources", str(invalid), "--outdir", str(rejected_batch)],
            outdir, "batch-later-pin-failure", expect_success=False)
    require(not rejected_batch.exists(), "Invalid later batch source created partial report output")
    batch = outdir / "batch"
    result = command(cli, ["report", "--sources", str(manifest), "--outdir", str(batch),
        "--artifact-root", str(allowed), "--artifact-root", str(second_allowed)], outdir, "batch-reports")
    require(result.get("run_count") == 2 and result.get("native_analyses_run") is False, "Batch scope differs from explicit manifest")
    batch_audit = read_json(batch / "refresh-audit.json")
    require(batch_audit.get("status") == "completed_reporting_refresh"
            and batch_audit.get("original_files_unchanged") is True
            and batch_audit.get("gpu_inference_run") is False, "Batch did not finalize source-preservation audit")
    require((batch / "sources.json").read_bytes() == manifest.read_bytes(), "Batch changed its saved source manifest")
    for number, original in enumerate((source, second), 1):
        check_preserved(original, read_json(batch / f"reports/run{number:03d}/evidence.json"))
    batch_runs = read_json(batch / "control-panel/inventory.json")["runs"]
    require(len(batch_runs) == 2 and {r.get("source_snapshot", {}).get("run_id") for r in batch_runs}
            == {source["run"]["run_id"], second["run"]["run_id"]}, "Batch panel changed source run identities")
    audit["checks"] = {"incorrect_evidence_pin_rejected": True, "source_scientific_fields_unchanged": True,
        "selected_completed": 1, "deferred": 1, "recorded_scope": "incomplete", "artifact_statuses": expected_statuses,
        "report_local_links": len(links), "artifact_local_links": len(artifact_links),
        "panel_static_local_links": len(panel_links), "rendered_files_preserved": len(protected),
        "panel_artifact_roots_reauthorized": True, "batch_source_count": 2,
        "all_batch_sources_checked_before_output": True, "batch_source_fields_unchanged": True}


def make_archive(outdir):
    paths = [outdir / "reporting-verification.json", *outdir.glob("*.command.json")]
    paths.extend(outdir / path for path in ("error.txt", "fixture/saved-source/evidence.json",
        "rendered/replay-audit.json", "rendered/report-navigation.json", "rendered/source-artifacts.json",
        "control-panel/inventory.json", "control-panel-without-external-roots/inventory.json",
        "synthetic-sources.json", "synthetic-sources-invalid.json", "second-fixture/saved-source/evidence.json",
        "batch/refresh-audit.json", "batch/control-panel/inventory.json"))
    for number in (1, 2):
        paths.extend(outdir / f"batch/reports/run{number:03d}/{name}" for name in
                     ("replay-audit.json", "report-navigation.json", "source-artifacts.json"))
    archive = outdir / "reporting-check-summary.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in paths:
            if path.is_file() and not path.is_symlink():
                zipped.write(path, path.relative_to(outdir))
    return archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, help="New directory; defaults to reporting-check-* in this directory")
    args = parser.parse_args(argv)
    if args.outdir:
        outdir = args.outdir.expanduser().absolute()
        require(not outdir.exists() and not outdir.is_symlink(), "Output path already exists")
        outdir.mkdir(parents=True, exist_ok=False)
    else:
        outdir = Path(tempfile.mkdtemp(prefix="reporting-check-", dir=Path.cwd()))
    audit = {"schema": "basis-reporting-acceptance-v1", "release_version": RELEASE, "status": "failed",
        "started_at_utc": now(), "outdir": str(outdir), "checker_sha256": sha(__file__),
        "synthetic_inputs_only": True, "native_analyses_performed": False, "gpu_inference_performed": False,
        "biological_validation_performed": False,
        "scope": "Installed CLI reporting, pinned evidence, preserved scope, and explicit artifact links",
        "limitations": ["Synthetic software acceptance only; no fresh native analyses or biological validation.",
                        "Local links and HTML anchors checked; graphical browser interactions not tested."]}
    observed = {}
    try:
        cli, runtime = installed_runtime()
        audit["runtime"] = runtime
        verify(cli, outdir, audit, observed)
        audit["status"] = "passed_reporting_checks"
    except Exception as exc:
        audit["error"] = str(exc)
        (outdir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        try:
            audit["synthetic_inputs_unchanged"] = unchanged(observed)
        except OSError:
            audit["synthetic_inputs_unchanged"] = False
        if not audit["synthetic_inputs_unchanged"]:
            audit["status"], audit["error"] = "failed", "A preserved synthetic input changed during checking"
        audit["observed_synthetic_files"] = len(observed)
        audit["finished_at_utc"] = now()
        write_json(outdir / "reporting-verification.json", audit)
        archive = make_archive(outdir)
    print("Status:", audit["status"], flush=True)
    if audit.get("error"):
        print("ERROR:", audit["error"], flush=True)
    print("Audit:", outdir / "reporting-verification.json")
    print("Upload summary:", archive)
    print("Synthetic software checks only; no native analyses, downloads, or GPU inference.")
    return 0 if audit["status"] == "passed_reporting_checks" else 1


if __name__ == "__main__":
    raise SystemExit(main())
