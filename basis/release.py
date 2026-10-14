"""Inspect installed package identity without running analysis tools."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import __version__

MANIFEST = "resources/release-manifest.json"


def runtime_hashes(root=None):
    root = Path(root or Path(__file__).parent).resolve()
    paths = set(root.rglob("*.py"))
    resources = root / "resources"
    if resources.is_dir():
        paths.update(p for p in resources.rglob("*") if p.is_file())
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths) if "__pycache__" not in p.parts
            and p.suffix not in {".pyc", ".pyo"}
            and p.relative_to(root).as_posix() != MANIFEST}


def verify_install(root=None):
    root = Path(root or Path(__file__).parent).resolve()
    path = root / MANIFEST
    result = {"schema": "basis-install-verification-v1", "version": __version__,
              "package_path": str(root), "native_analyses_run": False,
              "scope": "Installed Python source and bundled runtime assets only; external tools/data are not verified."}
    if not path.is_file():
        return {**result, "status": "unsealed_source_checkout", "manifest": str(path)}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected = manifest.get("sha256")
    if manifest.get("schema") != "basis-package-manifest-v1" or not isinstance(expected, dict):
        raise ValueError("Invalid installed release manifest")
    actual = runtime_hashes(root)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(k for k in expected.keys() & actual.keys() if expected[k] != actual[k])
    version_matches = manifest.get("version") == __version__
    return {**result, "status": "passed" if not (missing or extra or changed) and version_matches else "failed",
            "manifest": str(path), "checked_files": len(expected), "missing": missing,
            "unexpected": extra, "changed": changed, "version_matches": version_matches}
