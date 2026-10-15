"""Explicit reuse of a previously recorded local VFDB Set A snapshot."""
from pathlib import Path
import re

from .inputs import read_fasta
from .util import read_json, sha256_file


def verify_legacy_vfdb_snapshot(directory):
    root = Path(directory).expanduser().resolve(strict=True)
    manifest_path = root / "provenance.json"
    before = sha256_file(manifest_path)
    manifest = read_json(manifest_path)
    if manifest.get("database") != "VFDB" or manifest.get("dataset") != "Set A core proteins":
        raise ValueError("Expected a recorded VFDB Set A core protein snapshot")
    if manifest.get("source_url") not in {
        "https://www.mgc.ac.cn/VFs/Down/VFDB_setA_pro.fas.gz",
        "https://mgc.ac.cn/VFs/Down/VFDB_setA_pro.fas.gz",
    }:
        raise ValueError("VFDB manifest does not identify the supported official Set A source")
    version = manifest.get("database_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("VFDB manifest has no database version")
    checks = {}
    for name in ("vfdb_setA.dmnd", "VFDB_setA_pro.utf8.fas"):
        path = root / name
        expected = manifest.get("files", {}).get(name, {}).get("sha256")
        if (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected)
                or not path.is_file() or not path.stat().st_size or not path.resolve().is_relative_to(root)):
            raise ValueError("VFDB snapshot file or recorded checksum unavailable: " + name)
        observed = sha256_file(path)
        if observed != expected.lower():
            raise ValueError("VFDB snapshot checksum mismatch: " + name)
        checks[name] = {"path": str(path), "sha256": observed, "bytes": path.stat().st_size}
    fasta = root / "VFDB_setA_pro.utf8.fas"
    count = len(read_fasta(fasta))
    if not count or count != manifest.get("protein_count"):
        raise ValueError("VFDB FASTA record count does not match its manifest")
    if sha256_file(manifest_path) != before:
        raise ValueError("VFDB manifest changed during verification")
    return ({"database": str(root / "vfdb_setA.dmnd"), "reference_fasta": str(fasta),
             "database_manifest": str(manifest_path), "database_version": version},
            {"source": "explicit_local_vfdb_snapshot", "manifest": str(manifest_path),
             "manifest_sha256": before, "database_version": version,
             "verified_files": checks, "protein_count": count,
             "integrity_basis": "matches previously recorded local provenance; not independent vendor authentication",
             "download_performed": False})
