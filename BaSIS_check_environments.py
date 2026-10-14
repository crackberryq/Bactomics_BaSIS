#!/usr/bin/env python3
"""Check rc12 virtual-environment metadata and saved synthetic cache behavior.

Run using the Python in the successful clean-restore directory. No installation,
download, native analysis, GPU inference or biological input scan is performed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import uuid
import zipfile

RELEASE = "0.2.0rc13"
LIMIT = 32 * 1024 * 1024


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= LIMIT, "Missing or oversized JSON: " + str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def record(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path)}


def protected_file(path, root):
    path = Path(path).resolve(strict=True)
    require(path.is_relative_to(root.resolve()) and path.is_file(), "File escapes expected directory: " + str(path))
    return path


def load_release_checker(root):
    manifest = read_json(root / "BUNDLE_MANIFEST.json")
    require(manifest.get("schema") == "basis-bundle-manifest-v1" and manifest.get("version") == RELEASE,
            "Expected the rc12 sealed release")
    hashes = manifest.get("sha256", {})
    for name in ("BaSIS_check_environments.py", "BaSIS_check_clean_restore.py"):
        require(hashes.get(name) == sha(root / name), "Helper differs from release manifest: " + name)
    require(sha(Path(__file__)) == hashes["BaSIS_check_environments.py"], "Run the delivered environment checker")
    spec = importlib.util.spec_from_file_location("basis_sealed_clean_restore", root / "BaSIS_check_clean_restore.py")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    require(checker.RELEASE == RELEASE, "Unexpected clean-restore checker version")
    _, wheel = checker.verify_bundle(root)
    return checker, wheel


def verify_restore(root, restore, prefix, checker, wheel):
    summary_path = restore / "restore-summary.json"
    result = read_json(summary_path)
    require(result.get("schema") == "basis-clean-launcher-restore-v1" and
            result.get("version") == RELEASE and result.get("status") == "passed_clean_launcher_restore",
            "A successful rc12 clean restore is required")
    require(Path(result["output_directory"]).resolve() == restore and
            Path(result["environment_prefix"]).resolve() == prefix and prefix == restore / "venv",
            "Activate the venv belonging to this restore record")
    bindings = {
        "checker_sha256": root / "BaSIS_check_clean_restore.py",
        "bundle_manifest_sha256": root / "BUNDLE_MANIFEST.json",
        "basis_wheel_sha256": wheel,
        "pyvenv_config_sha256": prefix / "pyvenv.cfg",
        "synthetic_evidence_sha256": restore / "synthetic-demo/results/evidence.json",
        "wheelhouse_lock_sha256": restore / "wheelhouse-lock.json",
    }
    protected = [record(summary_path)]
    for key, path in bindings.items():
        require(result.get(key) == sha(path), "Restore identity mismatch: " + key)
        protected.append(record(path))
    require(result.get("system_site_packages") is False and result.get("runtime", {}).get("system_site_packages") is False,
            "The restored venv must be isolated")
    commands = result.get("commands", [])
    expected = {"create-isolated-venv", "offline-install", "runtime-probe", "pip-check", "verify-install",
                "synthetic-demo", "saved-report", "control-panel"}
    if result.get("network_dependency_staging"):
        expected.add("download-pinned-wheels")
    require(len(commands) == len(expected) and {c.get("name") for c in commands} == expected,
            "Restore command checks are incomplete or duplicated")
    for item in commands:
        require(item.get("exit_code") == 0, "Restore command failed: " + str(item.get("name")))
        log = protected_file(item["log"], restore / "logs")
        require(sha(log) == item.get("log_sha256"), "Restore command log checksum mismatch: " + str(log))
        protected.append(record(log))
    require(Path(result["wheelhouse"]).resolve() == restore / "wheelhouse", "Unexpected restored wheelhouse location")
    lock, wheels = checker.verify_wheelhouse(restore / "wheelhouse", result["wheelhouse_lock_sha256"], sha(wheel))
    require(lock["artifacts"] == result.get("locked_artifacts"), "Restore wheel identities differ from lock")
    protected.extend(record(p) for p in [*wheels, restore / "wheelhouse/wheelhouse-lock.json"])
    return result, protected


def verify_synthetic(restore):
    evidence = read_json(restore / "synthetic-demo/results/evidence.json")
    require(evidence.get("schema_version") == "basis-report-1" and
            evidence.get("run", {}).get("version") == RELEASE and
            evidence["run"].get("validation_mode") == "synthetic_fixture" and
            evidence["run"].get("completeness") == "completed_requested_scope",
            "Saved evidence is not a completed rc12 synthetic demo")
    deep = evidence.get("deep_records", [])
    require(len(deep) == 2 and {r.get("sample_id") for r in deep} == {"DEMO_A", "DEMO_B"},
            "Expected both synthetic DeepSafe records")
    rows, protected = [], []
    for item in deep:
        require(item.get("execution_status") == "completed" and item.get("validation_mode") == "synthetic_fixture",
                "Synthetic DeepSafe execution is incomplete")
        require(item.get("cache_status") == {"status": "not_applicable", "reason": "synthetic_fixture"},
                "Synthetic DeepSafe did not explicitly skip native cache fingerprinting")
        require(item.get("cache_hit") is False and not item.get("deep_cache"), "Synthetic evidence carries a native cache seal")
        provenance = item.get("provenance", {})
        require(provenance.get("commands") == [] and provenance.get("engine_identity") is None,
                "Synthetic DeepSafe recorded a native command or engine identity")
        logs = [Path(p) for p in item.get("artifacts", []) if Path(p).name == "commands.log"]
        require(len(logs) == 1, "Expected one native command log per synthetic DeepSafe record")
        log = protected_file(logs[0], restore / "synthetic-demo/results")
        require(log.stat().st_size == 0, "Synthetic native command log is not empty: " + str(log))
        protected.append(record(log))
        rows.append({"sample_id": item["sample_id"], "execution_status": item["execution_status"],
                     "cache_status": item["cache_status"], "native_command_count": 0,
                     "native_command_log": record(log), "engine_identity": None})
    require(not list((restore / "synthetic-demo/results").rglob("deep_cache.json")),
            "Unexpected synthetic native cache file")
    return rows, protected


def verify_audit(audit, prefix):
    require(audit.get("schema_version") == "basis-reproducibility-audit-1" and
            audit.get("basis_version") == RELEASE and audit.get("status") == "completed_with_open_gaps",
            "Environment metadata audit did not complete")
    require(audit.get("installed_package", {}).get("status") == "passed", "Installed package audit failed")
    for key in ("native_analyses_run", "gpu_inference_run", "network_access_used", "installations_performed"):
        require(audit.get(key) is False, "Unexpected audit action: " + key)
    require(not audit.get("sources") and not audit.get("inputs") and not audit.get("resources"),
            "Environment-only audit unexpectedly included study data")
    rows = audit.get("environments", [])
    require(len(rows) == 1 and Path(rows[0]["prefix"]).resolve() == prefix, "Unexpected environment inventory scope")
    row = rows[0]
    require(row.get("status") == "inventoried" and row.get("environment_type") == "python_venv",
            "Active venv was not correctly classified")
    require(row.get("conda_metadata", {}).get("status") == "not_applicable", "Ordinary venv incorrectly requires Conda metadata")
    require(row.get("pyvenv_cfg", {}).get("status") == "valid_metadata", "Venv configuration metadata was not valid")
    require(not any("Environment " in g for g in audit.get("open_gaps", [])), "Environment inventory retained an unexpected gap")
    return row


def summary_zip(output, result, paths):
    result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_json(output / "environment-check-summary.json", result)
    # Include a child command's log even when the command raised before returning.
    members = [output / "environment-check-summary.json", *paths, *sorted((output / "logs").glob("*.log"))]
    sums = {str(p.relative_to(output)): sha(p) for p in members if p.is_file()}
    write_json(output / "summary-checksums.json", sums)
    with zipfile.ZipFile(output / "environment-check-summary.zip", "w", compression=zipfile.ZIP_DEFLATED) as z:
        for rel in [*sums, "summary-checksums.json"]:
            z.write(output / rel, rel)
    print("UPLOAD SUMMARY: " + str(output / "environment-check-summary.zip"), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore-dir", type=Path, default=Path("clean-restore-rc13"))
    parser.add_argument("--outdir", type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    prefix, restore = Path(sys.prefix).resolve(), args.restore_dir.expanduser().resolve(strict=True)
    require(sys.flags.isolated, "Run this helper with python -I")
    require(sys.prefix != sys.base_prefix, "Activate the successful clean-restore venv first")
    checker, wheel = load_release_checker(root)
    import basis
    from basis.release import verify_install
    package = Path(basis.__file__).resolve().parent
    require(basis.__version__ == RELEASE and package.is_relative_to(prefix), "Use the installed rc12 package in the active venv")
    installed = verify_install()
    require(installed.get("status") == "passed", "Installed rc12 integrity check failed")
    runtime_seal = package / "resources/release-manifest.json"
    with zipfile.ZipFile(wheel) as z:
        require(hashlib.sha256(z.read("basis/resources/release-manifest.json")).hexdigest() == sha(runtime_seal),
                "Installed runtime manifest differs from delivered wheel")
    print("Checking saved clean-restore identities and logs...", flush=True)
    restored, protected = verify_restore(root, restore, prefix, checker, wheel)
    synthetic, native_logs = verify_synthetic(restore)
    protected += native_logs + [record(runtime_seal), record(root / "BaSIS_check_environments.py")]
    requested = args.outdir.expanduser().absolute() if args.outdir else root / ("environment-checks-" + uuid.uuid4().hex[:12])
    require(not requested.resolve().is_relative_to(restore), "Output must be outside the existing restore directory")
    output = checker.fresh_output(requested, root)
    (output / "logs").mkdir()
    result = {"schema": "basis-environment-acceptance-v1", "version": RELEASE, "status": "started",
              "started_at_utc": datetime.now(timezone.utc).isoformat(), "commands": [],
              "installed_package": installed, "installed_runtime_manifest": record(runtime_seal),
              "helper": record(root / "BaSIS_check_environments.py"), "bundle_manifest": record(root / "BUNDLE_MANIFEST.json"),
              "basis_wheel": record(wheel), "restore_summary": record(restore / "restore-summary.json"),
              "restore_command_count": len(restored["commands"]), "verified_prior_files": protected,
              "current_python": sys.executable, "current_prefix": str(prefix), "base_prefix": sys.base_prefix,
              "base_prefix_inventoried": False, "synthetic_cache_checks": synthetic,
              "native_analyses_run": False, "gpu_inference_run": False, "downloads_performed": False,
              "independent_biological_validation": "not_performed",
              "limitations": ["Checks launcher metadata and previously generated synthetic fixtures only.",
                              "The existing biological baseline and reports are preserved unchanged.",
                              "Native environments, databases and fresh-machine restoration are outside this check."]}
    compact = []
    try:
        print("Auditing the active virtual environment...", flush=True)
        log = checker.run([sys.executable, "-I", "-m", "basis", "audit-reproducibility", "--environment", prefix,
                           "--outdir", output / "audit", "-v"], "environment-audit", output, result,
                          checker.clean_environment(prefix))
        compact.append(log)
        audit_path = output / "audit/manifest.json"
        audit = read_json(audit_path)
        row = verify_audit(audit, prefix)
        write_json(output / "environment-row.json", row)
        compact.append(output / "environment-row.json")
        result.update(audit_manifest=record(audit_path), audit_open_gaps=audit.get("open_gaps", []))
        checker.verify_bundle(root)
        for item in protected:
            require(sha(item["path"]) == item["sha256"], "Verified source changed during check: " + item["path"])
        require(verify_install().get("status") == "passed", "Installed runtime changed during check")
        result.update(status="passed_environment_checks", environment_type=row["environment_type"],
                      conda_metadata=row["conda_metadata"], sources_unchanged=True)
    except Exception as exc:
        result.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        print("ENVIRONMENT CHECK FAILED: " + str(exc), file=sys.stderr, flush=True)
    finally:
        summary_zip(output, result, compact)
    if result["status"] != "passed_environment_checks":
        return 2
    print("ENVIRONMENT AND SYNTHETIC CACHE CHECKS: PASSED", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print("ENVIRONMENT CHECK STOPPED: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
