#!/usr/bin/env python3
"""Freeze four pinned historical pilot snapshots and current dependency identities.

Run with the installed rc12 Python in isolated mode. No installation, download,
native analysis or GPU inference is performed. This is not a restore test.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import zipfile

RELEASE = "0.2.0rc13"
SOURCE_MANIFEST = "examples/reporting/pilot-pc-verified-sources.json"
CONTEXT_EXPORT = "reproducibility/basis-context/conda-explicit.incomplete.txt"
EXPECTED_SAMPLES = {"isolate1", "isolate4", "isolate5", "isolate6"}
LIMIT = 512 * 1024 * 1024


def require(value, message):
    if not value:
        raise RuntimeError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= LIMIT, f"Missing or oversized JSON: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def file_record(path):
    path = Path(path).resolve(strict=True)
    before = path.stat()
    digest = sha(path)
    after = path.stat()
    require((before.st_ino, before.st_mtime_ns, before.st_size) ==
            (after.st_ino, after.st_mtime_ns, after.st_size), f"File changed while hashing: {path}")
    return {"path": str(path), "bytes": after.st_size, "sha256": digest}


def assert_unchanged(records):
    for item in records:
        require(sha(item["path"]) == item["sha256"], f"Source changed: {item['path']}")


def inspect_sources(manifest, output):
    """Require all four pins and identities before creating an output folder."""
    manifest = Path(manifest).resolve(strict=True)
    specs = read_json(manifest)
    require(isinstance(specs, list) and len(specs) == 4, "Expected the four-source pilot manifest")
    require(all(isinstance(s, dict) and isinstance(s.get("sample_id"), str) for s in specs),
            "Malformed source manifest entries")
    require({s["sample_id"] for s in specs} == EXPECTED_SAMPLES, "Pilot sample identities differ")
    sources, envs, seen = [], set(), set()
    for spec in specs:
        require(isinstance(spec.get("evidence"), str) and spec["evidence"], "Evidence path is missing")
        path = Path(spec["evidence"]).expanduser()
        path = (path if path.is_absolute() else manifest.parent / path).resolve(strict=True)
        require(path not in seen, "Duplicate evidence source")
        seen.add(path)
        require(not output.is_relative_to(path.parent) and not path.is_relative_to(output),
                "Output must be outside every selected source report directory")
        item = file_record(path)
        require(isinstance(spec.get("evidence_sha256"), str) and
                item["sha256"] == spec["evidence_sha256"].lower(), f"Evidence checksum mismatch: {path}")
        print(f"Checking pinned historical evidence: {spec['sample_id']}", flush=True)
        evidence = read_json(path)
        require(isinstance(evidence, dict) and evidence.get("schema_version") == "basis-report-1",
                f"Unrecognized evidence schema: {path}")
        require(isinstance(evidence.get("run"), dict), f"Missing run identity: {path}")
        for key in ("samples", "records", "deep_records"):
            require(isinstance(evidence.get(key), list) and
                    all(isinstance(row, dict) for row in evidence[key]), f"Invalid {key}: {path}")
        sid = spec["sample_id"]
        require({s.get("sample_id") for s in evidence["samples"]} == {sid}, f"Sample identity mismatch: {path}")
        for row in evidence["records"] + evidence["deep_records"]:
            require(row.get("sample_id") == sid, f"Record sample identity mismatch: {path}")
            section = row.get("effective_config", {})
            if isinstance(section, dict):
                argv = section.get("command_prefix", [])
                if isinstance(argv, list) and argv and all(isinstance(v, str) for v in argv):
                    if Path(argv[0]).name in {"conda", "mamba", "micromamba"} and "run" in argv:
                        for i, part in enumerate(argv):
                            value = (argv[i + 1] if part in {"-p", "--prefix"} and i + 1 < len(argv)
                                     else part.split("=", 1)[1] if part.startswith("--prefix=") else None)
                            if value:
                                prefix = Path(value).expanduser()
                                envs.add(str((prefix if prefix.is_absolute() else path.parent / prefix).resolve()))
        for row in evidence["deep_records"]:
            require(isinstance(row.get("candidates", []), list) and
                    all(isinstance(c, dict) for c in row.get("candidates", [])), f"Invalid candidate list: {path}")
        item.update(sample_id=sid, source_run_id=evidence["run"].get("run_id"),
                    source_scope=evidence["run"].get("completeness"),
                    origin=spec.get("origin"), role="pinned_historical_evidence")
        require(sha(path) == item["sha256"], f"Evidence changed during inspection: {path}")
        sources.append(item)
    return sources, envs


def installed_runtime():
    import basis
    from basis.release import verify_install
    package = Path(basis.__file__).resolve().parent
    require(basis.__version__ == RELEASE, f"Activate installed BaSIS {RELEASE} first")
    require(package.is_relative_to(Path(sys.prefix).resolve()), "BaSIS was imported outside the active environment")
    verification = verify_install()
    require(verification["status"] == "passed", "Installed BaSIS integrity check failed")
    return package, verification


def verify_release(root, archive, package):
    """Bind selected delivery files to the ZIP and installed runtime seal."""
    root, archive = Path(root).resolve(), Path(archive).resolve(strict=True)
    seal_path = root / "BUNDLE_MANIFEST.json"
    seal = read_json(seal_path)
    require(seal.get("version") == RELEASE and isinstance(seal.get("sha256"), dict), "Unexpected bundle manifest")
    wheel_name = f"dist/bactromics_basis-{RELEASE}-py3-none-any.whl"
    names = ["BaSIS_freeze_validation.py", SOURCE_MANIFEST, CONTEXT_EXPORT, wheel_name]
    identities = [file_record(root / name) for name in names]
    for name, item in zip(names, identities):
        require(item["sha256"] == seal["sha256"].get(name), f"Delivery file differs from bundle manifest: {name}")
    archive_identity = file_record(archive)
    with zipfile.ZipFile(archive) as z:
        members = z.namelist()
        require(len(members) == len(set(members)), "Release archive has duplicate entries")
        for name in ["BUNDLE_MANIFEST.json", *names]:
            member = f"BaSIS_v{RELEASE}/{name}"
            require(member in members and z.getinfo(member).file_size <= LIMIT, f"Release member missing or too large: {member}")
            require(hashlib.sha256(z.read(member)).hexdigest() == sha(root / name), f"Archive member differs: {name}")
    wheel = root / wheel_name
    with zipfile.ZipFile(wheel) as z:
        runtime = z.read("basis/resources/release-manifest.json")
    require(hashlib.sha256(runtime).hexdigest() == sha(package / "resources/release-manifest.json"),
            "Installed runtime seal differs from the delivered wheel")
    return {"release_archive": archive_identity, "wheel": file_record(wheel),
            "bundle_manifest": file_record(seal_path), "verified_delivery_files": identities,
            "installed_runtime_manifest": file_record(package / "resources/release-manifest.json"),
            "archive_members_verified": ["BUNDLE_MANIFEST.json", *names],
            "scope": "Selected delivery members and installed runtime seal; archive hash recorded, not a cryptographic signature."}


def command(argv, output):
    """Keep hash-bound command evidence and stream the audit's progress."""
    log = output / "audit-command.log"
    with log.open("w", encoding="utf-8") as handle:
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace") as proc:
            for line in proc.stdout:
                handle.write(line)
                handle.flush()
                print(line, end="", flush=True)
            code = proc.wait()
    record = {"argv": argv, "exit_code": code, "log": file_record(log),
              "native_analyses_run": False, "gpu_inference_run": False}
    write_json(output / "audit-command.json", record)
    require(code == 0, f"Metadata audit exited {code}; see {log}")
    return record


def verify_audit(audit, audit_dir, expected_sources):
    require(audit.get("schema_version") == "basis-reproducibility-audit-1" and
            audit.get("basis_version") == RELEASE, "Unexpected audit schema or release")
    require(audit.get("status") == "completed_with_open_gaps", "Audit capture did not complete")
    require(audit.get("sources_unchanged") is True and
            audit.get("installed_package", {}).get("status") == "passed", "Audit integrity failed")
    for key in ("native_analyses_run", "gpu_inference_run", "network_access_used", "installations_performed", "portable_restore_verified"):
        require(audit.get(key) is False, f"Unexpected audit claim: {key}")
    require(audit.get("resource_hashing_requested") is True, "Resource hashing was not requested")
    rows = audit.get("sources", [])
    indexed = {row["path"]: row for row in rows}
    require(len(indexed) == len(rows), "Duplicate audit source paths")
    for expected in expected_sources:
        require(expected["path"] in indexed and indexed[expected["path"]]["sha256"] == expected["sha256"],
                f"Audit source pin differs: {expected['path']}")
    checked = []
    for row in rows:
        frozen = (audit_dir / row["frozen_path"]).resolve(strict=True)
        require(frozen.is_relative_to(audit_dir.resolve()), "Frozen source escapes audit directory")
        require(sha(frozen) == row["sha256"] == sha(row["path"]), f"Frozen/current source mismatch: {row['path']}")
        checked.append({"path": row["path"], "sha256": row["sha256"], "frozen_path": row["frozen_path"], "verified": True})
    return checked


def run(args, root=None):
    root = Path(root or Path(__file__).resolve().parent).resolve()
    output = Path(args.outdir).expanduser() if args.outdir else root / (
        "validation-baseline-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    require(not os.path.lexists(output), f"Existing output preserved: {output}")
    output = output.resolve()
    package, installed = installed_runtime()
    archive = args.release_zip or root.parent / f"BaSIS_v{RELEASE}_release.zip"
    release = verify_release(root, archive, package)
    manifest = root / SOURCE_MANIFEST
    sources, declared_envs = inspect_sources(manifest, output)
    protected = [file_record(manifest), *sources, *release["verified_delivery_files"],
                 release["release_archive"], release["bundle_manifest"], release["installed_runtime_manifest"]]
    protected = list({item["path"]: item for item in protected}.values())
    provider = Path(args.provider_prefix or sys.base_prefix).expanduser().resolve(strict=True)
    require(provider.is_dir(), "Base-Python prefix selected for inventory is missing")
    if args.context_prefix:
        context = Path(args.context_prefix).expanduser().resolve(strict=True)
    else:
        contexts = [Path(p) for p in declared_envs if Path(p).name == "basis-context"]
        require(len(contexts) == 1, "Cannot identify one declared basis-context prefix; supply --context-prefix")
        context = contexts[0].resolve(strict=True)
    require(context.is_dir(), "Context environment prefix is missing")
    explicit = root / CONTEXT_EXPORT
    configs = [file_record(Path(p).expanduser()) for p in args.config]
    protected.extend(configs)
    # Stop before output creation for malformed explicit configs/sample sheets.
    import yaml
    for item in configs:
        config = yaml.safe_load(Path(item["path"]).read_text(encoding="utf-8"))
        require(isinstance(config, dict) and isinstance(config.get("samples"), str), "Configuration lacks a TSV sample sheet")
        sheet = Path(config["samples"]).expanduser()
        sheet = (sheet if sheet.is_absolute() else Path(item["path"]).parent / sheet).resolve(strict=True)
        require(sheet.suffix.lower() == ".tsv", "Freeze an imported execution TSV, not an Excel/CSV source")
        protected.append(file_record(sheet))
    for prefix in [provider, context, Path(sys.prefix), *(Path(p) for p in declared_envs)]:
        require(not output.is_relative_to(prefix), "Output must be outside audited environment prefixes")
    assert_unchanged(protected)
    output.mkdir(parents=True, exist_ok=False)
    for name, dest in [(SOURCE_MANIFEST, "sources.json"), (CONTEXT_EXPORT, "context-explicit.txt"),
                       ("BUNDLE_MANIFEST.json", "BUNDLE_MANIFEST.json")]:
        shutil.copyfile(root / name, output / dest)
        require(sha(root / name) == sha(output / dest), "Delivery source changed while copying")
    baseline = {"schema": "basis-validation-freeze-v1", "release": RELEASE, "status": "in_progress",
        "started_at_utc": now(), "release_identity": release, "installed_package_before": installed,
        "source_manifest": file_record(manifest), "historical_source_snapshots": sources,
        "explicit_additional_configurations": configs, "launcher_base_python_prefix": str(provider),
        "runtime_base_prefix": str(Path(sys.base_prefix).resolve()),
        "base_python_prefix_selection": "explicit_override" if args.provider_prefix else "runtime_base_prefix",
        "context_export_prefix": str(context), "current_runtime_prefix": str(Path(sys.prefix).resolve()),
        "helper_sha256": sha(root / "BaSIS_freeze_validation.py"),
        "native_analyses_run": False, "gpu_inference_run": False,
        "independent_biological_validation": "not_performed", "portable_restore_verified": False,
        "scope": "Four pinned historical pilot snapshots plus current dependency inventory. Optional explicit configurations are captured separately.",
        "limitations": ["Historical evidence is preserved as recorded, including incomplete scope and deferred candidates.",
            "Current input/resource/environment hashes are new observations; they do not make historical results current or prove historical tool identity.",
            "Package archives, wheels, biological inputs, databases and model weights are not copied into this baseline audit.",
            "This is not a successful fresh-machine restore or independent biological validation.",
            "The default capture does not select or certify an exact future scientific execution configuration."]}
    write_json(output / "baseline-manifest.json", baseline)
    print("BASELINE DIRECTORY:", output, flush=True)
    try:
        audit_dir = output / "audit"
        argv = [sys.executable, "-I", "-m", "basis", "audit-reproducibility"]
        for source in sources:
            argv += ["--evidence", source["path"]]
        for item in configs:
            argv += ["--config", item["path"]]
        for prefix in sorted({str(provider), str(Path(sys.prefix).resolve())}):
            argv += ["--environment", prefix]
        argv += ["--conda-explicit", str(context), str(explicit), "--hash-resources", "--outdir", str(audit_dir), "-v"]
        print("Freezing historical evidence and hashing current resources; progress is recorded in audit-command.log.", flush=True)
        command_record = command(argv, output)
        audit_path = audit_dir / "manifest.json"
        audit = read_json(audit_path)
        frozen = verify_audit(audit, audit_dir, [*sources, *configs])
        assert_unchanged(protected)
        _, installed_after = installed_runtime()
        verification = {"schema": "basis-validation-freeze-verification-v1", "status": "passed_capture_checks",
            "source_before_after": [{**item, "after_sha256": sha(item["path"]), "unchanged": True} for item in protected],
            "frozen_source_checks": frozen, "installed_package_after": installed_after,
            "current_dependency_audit": file_record(audit_path), "audit_command": command_record,
            "portable_restore_verified": False, "independent_biological_validation": "not_performed"}
        write_json(output / "verification.json", verification)
        baseline.update(status="frozen_with_open_gaps", finished_at_utc=now(), source_files_unchanged=True,
            audit=file_record(audit_path), current_inventory_counts={key: len(audit.get(key, [])) for key in
                ("sources", "inputs", "resources", "environments")}, open_gaps=audit.get("open_gaps", []))
    except Exception as exc:
        baseline.update(status="failed_capture", error=str(exc), finished_at_utc=now())
        raise
    finally:
        write_json(output / "baseline-manifest.json", baseline)
    compact = output / "validation-freeze-summary.zip"
    paths = ["baseline-manifest.json", "verification.json", "sources.json", "context-explicit.txt",
             "BUNDLE_MANIFEST.json", "audit-command.json", "audit-command.log", "audit/manifest.json", "audit/SHA256SUMS"]
    checksums = {name: sha(output / name) for name in paths}
    write_json(output / "summary-checksums.json", checksums)
    with zipfile.ZipFile(compact, "x", zipfile.ZIP_DEFLATED) as z:
        for name in [*paths, "summary-checksums.json"]:
            z.write(output / name, name)
    print("BASELINE:", output, flush=True)
    print("Status: frozen_with_open_gaps | historical sources: 4 | native/GPU analyses: none", flush=True)
    print("UPLOAD SUMMARY:", compact, flush=True)
    return baseline


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--outdir", help="New output directory; default is a fresh timestamped release-local folder")
    p.add_argument("--release-zip", help="Exact rc12 release ZIP; default is the adjacent delivered archive")
    p.add_argument("--base-python-prefix", "--provider-prefix", dest="provider_prefix",
                   help="Base-Python prefix additionally inventoried; default is this Python's base prefix; no dependency bridge is assumed")
    p.add_argument("--context-prefix", help="Explicit context prefix; otherwise require one from saved command declarations")
    p.add_argument("--config", action="append", default=[], help="Explicit additional execution YAML to freeze; repeatable; no future config is guessed")
    return p


def main():
    try:
        run(parser().parse_args())
        return 0
    except Exception as exc:
        print(f"FREEZE STOPPED: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
