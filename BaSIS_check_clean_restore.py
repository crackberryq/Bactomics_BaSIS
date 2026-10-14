#!/usr/bin/env python3
"""Restore the BaSIS Python launcher into a fresh isolated venv.

Default: stage exact pinned dependency wheels from PyPI, then install offline.
Repeat offline with --wheelhouse PATH --expected-lock-sha256 SHA256.
This checks a clean Python launcher on this host, not native tools, databases,
GPU inference, clean-machine restoration, or independent biological validation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

RELEASE = "0.2.0rc13"
DEPENDENCIES = {"pyyaml": "6.0.3", "openpyxl": "3.1.5", "et-xmlfile": "2.0.0"}
EXPECTED_PACKAGES = {"bactromics-basis": RELEASE, **DEPENDENCIES}
LOCK_NAME = "wheelhouse-lock.json"
LOCK_SCHEMA = "basis-launcher-wheelhouse-v1"
RUNTIME_PROBE = r'''
import importlib.metadata as m, json, site, sys
from pathlib import Path
import yaml, openpyxl, et_xmlfile, basis
prefix = Path(sys.prefix).resolve()
assert sys.prefix != sys.base_prefix, "Expected a virtual environment"
assert site.ENABLE_USER_SITE is False, "User site packages must be disabled"
expected = json.loads(sys.argv[1])
modules = {"bactromics-basis": basis, "pyyaml": yaml,
           "openpyxl": openpyxl, "et-xmlfile": et_xmlfile}
packages = {}
for name, module in modules.items():
    path = Path(module.__file__).resolve()
    assert path.is_relative_to(prefix), "Package imported outside clean venv: " + name
    version = m.version(name)
    assert version == expected[name], "Unexpected installed version: " + name
    packages[name] = {"version": version, "module_file": str(path)}
assert "include-system-site-packages = false" in (prefix / "pyvenv.cfg").read_text().lower()
print(json.dumps({"python": sys.version, "prefix": str(prefix),
                  "base_prefix": sys.base_prefix, "user_site_enabled": site.ENABLE_USER_SITE,
                  "packages": packages, "pip_version": m.version("pip"),
                  "system_site_packages": False}))
'''


def require(condition, message):
    if not condition:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def checked_file(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), "Symlink paths are not accepted: " + str(path))
    require(path.is_file(), "Expected a regular file: " + str(path))
    return path


def checked_relative(name):
    require(isinstance(name, str) and name and "\\" not in name, "Invalid relative filename")
    path = Path(name)
    require(not path.is_absolute() and ".." not in path.parts and "." not in path.parts,
            "Unsafe relative filename: " + name)
    return path


def normalized_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_identity(path):
    checked_file(path)
    require(path.suffix == ".whl", "Only wheel archives are accepted")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate wheel archive members")
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        require(len(metadata) == 1, "Expected one wheel package metadata record")
        info = archive.getinfo(metadata[0])
        require(info.file_size < 1024 * 1024, "Wheel metadata is unexpectedly large")
        parsed = BytesParser().parsebytes(archive.read(metadata[0]))
    require(len(parsed.get_all("Name", [])) == 1 and len(parsed.get_all("Version", [])) == 1,
            "Wheel identity is absent or ambiguous")
    return normalized_name(parsed["Name"]), parsed["Version"]


def verify_bundle(root):
    manifest_path = checked_file(root / "BUNDLE_MANIFEST.json")
    manifest = read_json(manifest_path)
    require(manifest.get("schema") == "basis-bundle-manifest-v1" and manifest.get("version") == RELEASE,
            "Expected the sealed BaSIS " + RELEASE + " release")
    hashes = manifest.get("sha256")
    require(isinstance(hashes, dict) and bool(hashes), "Bundle checksum manifest is empty")
    for name, digest in hashes.items():
        path = checked_file(root / checked_relative(name))
        require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest), "Invalid bundle checksum")
        require(sha(path) == digest, "Bundle checksum mismatch: " + name)
    wheels = sorted((root / "dist").glob("bactromics_basis-*.whl"))
    require(len(wheels) == 1, "Expected exactly one local BaSIS wheel")
    wheel = wheels[0]
    require("dist/" + wheel.name in hashes, "BaSIS wheel is not covered by the release manifest")
    require(wheel_identity(wheel) == ("bactromics-basis", RELEASE), "Unexpected BaSIS wheel identity")
    return manifest, wheel


def make_lock(wheelhouse, bundle_manifest_sha256, basis_wheel_sha256):
    records, observed = [], set()
    for path in sorted(wheelhouse.iterdir()):
        require(path.suffix == ".whl", "Unexpected file in new wheelhouse: " + path.name)
        name, version = wheel_identity(path)
        require(name not in observed and EXPECTED_PACKAGES.get(name) == version,
                "Unexpected or duplicate wheel identity: " + name + " " + version)
        observed.add(name)
        records.append({"filename": path.name, "name": name, "version": version,
                        "size": path.stat().st_size, "sha256": sha(path)})
    require(observed == set(EXPECTED_PACKAGES), "Wheelhouse does not contain exactly the four pinned packages")
    require(next(r for r in records if r["name"] == "bactromics-basis")["sha256"] == basis_wheel_sha256,
            "Staged BaSIS wheel differs from sealed release")
    lock = {"schema": LOCK_SCHEMA, "version": RELEASE, "created_at_utc": now(),
            "bundle_manifest_sha256": bundle_manifest_sha256, "artifacts": records,
            "scope": "Python launcher and Excel dependencies only; platform-specific wheel set"}
    write_json(wheelhouse / LOCK_NAME, lock)
    return lock


def verify_wheelhouse(wheelhouse, expected_lock_sha256, basis_wheel_sha256):
    require(isinstance(expected_lock_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", expected_lock_sha256),
            "A recorded --expected-lock-sha256 is required for offline restoration")
    lock_path = checked_file(wheelhouse / LOCK_NAME)
    require(sha(lock_path) == expected_lock_sha256, "Wheelhouse lock checksum mismatch")
    lock = read_json(lock_path)
    require(lock.get("schema") == LOCK_SCHEMA and lock.get("version") == RELEASE, "Unexpected wheelhouse lock schema/version")
    records = lock.get("artifacts")
    require(isinstance(records, list) and len(records) == len(EXPECTED_PACKAGES), "Expected four locked wheel artifacts")
    observed, filenames = set(), set()
    paths = []
    for record in records:
        filename = record.get("filename")
        rel = checked_relative(filename)
        require(len(rel.parts) == 1 and rel.suffix == ".whl", "Wheel lock must name a direct wheel file")
        path = checked_file(wheelhouse / rel)
        name, version = wheel_identity(path)
        require(name not in observed and filename not in filenames, "Duplicate wheel in lock")
        require((name, version) == (record.get("name"), record.get("version")) and EXPECTED_PACKAGES.get(name) == version,
                "Wheel metadata does not match the pinned lock identity")
        require(isinstance(record.get("size"), int) and path.stat().st_size == record["size"], "Wheel size mismatch: " + filename)
        require(sha(path) == record.get("sha256"), "Wheel checksum mismatch: " + filename)
        if name == "bactromics-basis":
            require(record["sha256"] == basis_wheel_sha256, "Locked BaSIS wheel differs from sealed release")
        observed.add(name)
        filenames.add(filename)
        paths.append(path)
    require(observed == set(EXPECTED_PACKAGES), "Wheelhouse packages are incomplete")
    require({p.name for p in wheelhouse.iterdir()} == filenames | {LOCK_NAME}, "Unrecorded wheelhouse contents")
    return lock, paths


def clean_environment(prefix=None):
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith("PIP_") or key in {"PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "VIRTUAL_ENV"}:
            environment.pop(key, None)
    environment.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PIP_CONFIG_FILE=os.devnull,
                       PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INDEX="1", PIP_NO_CACHE_DIR="1")
    if prefix:
        environment["PATH"] = str(prefix / "bin") + os.pathsep + environment.get("PATH", "")
        environment["VIRTUAL_ENV"] = str(prefix)
    return environment


def download_command(python, wheelhouse):
    return [python, "-I", "-m", "pip", "download", "--disable-pip-version-check", "--no-cache-dir",
            "--no-deps", "--only-binary=:all:", "--index-url", "https://pypi.org/simple", "--dest", wheelhouse,
            *[name + "==" + version for name, version in DEPENDENCIES.items()]]


def install_command(python, wheels):
    return [python, "-I", "-m", "pip", "install", "--disable-pip-version-check", "--no-index", "--no-deps",
            "--no-cache-dir", *wheels]


def run(argv, name, output, audit, environment, *, timeout=600, allowed=(0,)):
    args = list(map(str, argv))
    log = output / "logs" / (name + ".log")
    print("CHECK " + name + ": " + shlex.join(args), flush=True)
    start = time.monotonic()
    record = {"name": name, "argv": args, "started_at_utc": now(), "log": str(log), "exit_code": None}
    audit["commands"].append(record)
    try:
        with log.open("x", encoding="utf-8") as stream:
            process = subprocess.Popen(args, cwd=output, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=stream, stderr=subprocess.STDOUT)
            try:
                while True:
                    remaining = timeout - (time.monotonic() - start)
                    if remaining <= 0:
                        raise TimeoutError(name + " exceeded its time limit")
                    try:
                        record["exit_code"] = process.wait(timeout=min(30, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        print(f"{name}: still running ({time.monotonic() - start:.0f}s); log: {log}", flush=True)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        require(record["exit_code"] in allowed, name + " failed; inspect " + str(log))
    finally:
        record.update(seconds=round(time.monotonic() - start, 3), log_sha256=sha(log) if log.is_file() else None)
    return log


def fresh_output(requested, root):
    if requested is None:
        for parent in (root, *root.parents):
            require(not (parent / "pyvenv.cfg").exists() and not (parent / "conda-meta").is_dir(),
                    "Restore output must be outside existing environment prefixes")
        return Path(tempfile.mkdtemp(prefix="clean-restore-", dir=root))
    raw = requested.expanduser().absolute()
    require(not os.path.lexists(raw), "Existing output preserved; choose a new --outdir")
    require(not any(p.is_symlink() for p in raw.parents), "Output parent symlinks are not accepted")
    resolved = raw.resolve()
    for parent in resolved.parents:
        require(not (parent / "pyvenv.cfg").exists() and not (parent / "conda-meta").is_dir(),
                "Restore output must be outside existing environment prefixes")
    resolved.mkdir(parents=True, exist_ok=False)
    return resolved


def write_summary(output, audit):
    audit["finished_at_utc"] = now()
    write_json(output / "restore-summary.json", audit)
    members = [output / "restore-summary.json", *sorted((output / "logs").glob("*.log"))]
    for rel in (LOCK_NAME, "synthetic-demo/results/evidence.json", "saved-report/replay-audit.json",
                "control-panel/inventory.json"):
        path = output / rel
        if path.is_file():
            members.append(path)
    with zipfile.ZipFile(output / "restore-summary.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in members:
            archive.write(path, path.relative_to(output))
    print("UPLOAD SUMMARY: " + str(output / "restore-summary.zip"), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--outdir", type=Path, help="New output directory (default: unique directory inside release)")
    parser.add_argument("--wheelhouse", type=Path, help="Existing locked wheelhouse; disables all dependency downloads")
    parser.add_argument("--expected-lock-sha256", help="Previously recorded lock hash, required with --wheelhouse")
    args = parser.parse_args(argv)
    require(sys.version_info >= (3, 11), "Use trusted Python 3.11 or newer")
    require(os.name == "posix", "This acceptance helper currently targets Linux/WSL")
    require(bool(args.wheelhouse) == bool(args.expected_lock_sha256),
            "Use --wheelhouse and --expected-lock-sha256 together")
    root = args.release_dir.expanduser().absolute()
    print("Verifying sealed release files...", flush=True)
    manifest, basis_wheel = verify_bundle(root)
    source_manifest_sha, basis_sha = sha(root / "BUNDLE_MANIFEST.json"), sha(basis_wheel)
    source_wheelhouse = args.wheelhouse.expanduser().absolute() if args.wheelhouse else None
    offline_wheels = []
    if source_wheelhouse:
        _, offline_wheels = verify_wheelhouse(source_wheelhouse, args.expected_lock_sha256, basis_sha)
    output = fresh_output(args.outdir, root)
    print("CLEAN RESTORE DIRECTORY: " + str(output), flush=True)
    (output / "logs").mkdir()
    prefix, wheelhouse = output / "venv", output / "wheelhouse"
    audit = {"schema": "basis-clean-launcher-restore-v1", "version": RELEASE, "status": "started",
             "started_at_utc": now(), "checker_sha256": sha(Path(__file__)), "output_directory": str(output),
             "environment_prefix": str(prefix), "bootstrap_python": sys.executable, "bootstrap_python_version": sys.version,
             "bootstrap_python_resolved": str(Path(sys.executable).resolve()),
             "bootstrap_python_sha256": sha(Path(sys.executable).resolve()),
             "bootstrap_base_prefix": sys.base_prefix, "bundle_manifest_sha256": source_manifest_sha,
             "basis_wheel_sha256": basis_sha, "system_site_packages": False, "commands": [],
             "network_dependency_staging": source_wheelhouse is None, "offline_install": True,
             "restoration_scope": "fresh isolated Python launcher on current host",
             "native_tools_restored": False, "databases_restored": False, "gpu_inference_run": False,
             "biological_validation_performed": False, "fresh_machine_restoration_tested": False,
             "limitations": ["Reuses this host's Python interpreter and OS; neither is restored or archived.",
                             "The wheelhouse is platform/interpreter specific; another platform may need different wheels.",
                             "Recorded wheel hashes establish repeatability of staged artifacts, not publisher authentication.",
                             "Native tool environments, models and reference databases are outside this check."]}
    environment = clean_environment()
    try:
        run([sys.executable, "-I", "-m", "venv", prefix], "create-isolated-venv", output, audit, environment)
        require("include-system-site-packages = false" in (prefix / "pyvenv.cfg").read_text().lower(),
                "New venv unexpectedly enables system site packages")
        audit["pyvenv_config_sha256"] = sha(prefix / "pyvenv.cfg")
        python, basis = prefix / "bin/python", prefix / "bin/basis"
        environment = clean_environment(prefix)
        wheelhouse.mkdir()
        if source_wheelhouse:
            for wheel in offline_wheels:
                shutil.copyfile(wheel, wheelhouse / wheel.name)
            shutil.copyfile(source_wheelhouse / LOCK_NAME, wheelhouse / LOCK_NAME)
            lock_sha = args.expected_lock_sha256
        else:
            shutil.copyfile(basis_wheel, wheelhouse / basis_wheel.name)
            download_env = dict(environment)
            download_env.pop("PIP_NO_INDEX")
            run(download_command(python, wheelhouse), "download-pinned-wheels", output, audit, download_env)
            make_lock(wheelhouse, source_manifest_sha, basis_sha)
            lock_sha = sha(wheelhouse / LOCK_NAME)
        lock, wheels = verify_wheelhouse(wheelhouse, lock_sha, basis_sha)
        shutil.copyfile(wheelhouse / LOCK_NAME, output / LOCK_NAME)
        audit.update(wheelhouse=str(wheelhouse), wheelhouse_lock_sha256=lock_sha, locked_artifacts=lock["artifacts"])
        print("WHEELHOUSE LOCK SHA256: " + lock_sha, flush=True)
        run(install_command(python, wheels), "offline-install", output, audit, environment)
        runtime_log = run([python, "-I", "-c", RUNTIME_PROBE, json.dumps(EXPECTED_PACKAGES)],
                          "runtime-probe", output, audit, environment)
        audit["runtime"] = read_json(runtime_log)
        run([python, "-I", "-m", "pip", "check"], "pip-check", output, audit, environment)
        verified_log = run([basis, "verify-install"], "verify-install", output, audit, environment)
        verified = read_json(verified_log)
        require(verified.get("status") == "passed" and verified.get("version") == RELEASE,
                "Installed package integrity did not pass")
        audit["package_integrity"] = verified
        run([basis, "demo", "--outdir", output / "synthetic-demo", "-d", "-e"], "synthetic-demo",
            output, audit, environment, allowed=(0, 2))
        evidence = output / "synthetic-demo/results/evidence.json"
        demo = read_json(evidence)
        require(demo.get("run", {}).get("validation_mode") == "synthetic_fixture" and
                demo["run"].get("completeness") == "completed_requested_scope", "Synthetic demo did not complete")
        require({s.get("sample_id") for s in demo.get("samples", [])} == {"DEMO_A", "DEMO_B"},
                "Synthetic sample identities do not match")
        original_hash = sha(evidence)
        run([basis, "report", "--evidence", evidence, "--expected-sha256", original_hash,
             "--outdir", output / "saved-report"], "saved-report", output, audit, environment)
        run([basis, "control-panel", "--results", output / "synthetic-demo/results", "--results",
             output / "saved-report", "--outdir", output / "control-panel"], "control-panel", output, audit, environment)
        require(sha(evidence) == original_hash and (output / "control-panel/index.html").is_file(),
                "Saved evidence changed or control panel is missing")
        verify_wheelhouse(wheelhouse, lock_sha, basis_sha)
        verify_bundle(root)
        require(sha(root / "BUNDLE_MANIFEST.json") == source_manifest_sha, "Release manifest changed during check")
        if source_wheelhouse:
            verify_wheelhouse(source_wheelhouse, args.expected_lock_sha256, basis_sha)
        audit.update(status="passed_clean_launcher_restore", source_release_unchanged=True,
                     synthetic_evidence_sha256=original_hash, synthetic_report=str(output / "control-panel/index.html"))
    except Exception as exc:
        audit.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        print("CLEAN RESTORE FAILED: " + str(exc), file=sys.stderr, flush=True)
    finally:
        write_summary(output, audit)
    if audit["status"] != "passed_clean_launcher_restore":
        return 2
    print("CLEAN PYTHON LAUNCHER RESTORE: PASSED", flush=True)
    print("Activate: source " + shlex.quote(str(prefix / "bin/activate")), flush=True)
    print("Offline repeat requires the wheelhouse and its recorded lock hash above.", flush=True)
    print("Native tools, databases, GPU and independent biological validation were not run.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print("CLEAN RESTORE STOPPED: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
