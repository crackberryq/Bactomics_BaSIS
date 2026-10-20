"""Track and freeze small, user-configured DeepSafe family resources.

These are file-integrity operations only. No reference labels, comparator
eligibility, retrieval settings or scientific interpretations are changed.
Large sequence/domain databases retain their existing resource receipts.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import yaml


SCHEMA = "basis-snakemake-deep-family-resources-v1"
RESOURCE_KINDS = {"profiles_dir": "directory", "placement_registry": "file",
                  "motif_rules": "file", "tree_review_registry": "file"}
DISCOVERY_SCHEMA = "basis-snakemake-deep-family-inputs-v1"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _files(path, kind):
    if kind == "file":
        if not path.is_file():
            raise ValueError("DeepSafe family resource is not a file: " + str(path))
        return [path]
    if not path.is_dir():
        raise ValueError("DeepSafe family resource is not a directory: " + str(path))
    # Match validate_profiles exactly. A profile folder may also contain large
    # databases or unrelated documentation; neither is a consumed profile.
    entries = sorted(set(path.glob("*.yaml")) | set(path.glob("*.yml")))
    if any(not item.is_file() for item in entries):
        raise ValueError("DeepSafe profile resource is not a file: " + str(path))
    return entries


def config_input_paths(config):
    """Return declared paths and members for Snakemake's DeepSafe-only inputs.

    Including the directory itself detects additions/removals; individual file
    inputs detect same-path edits. Missing paths remain explicit DAG inputs so
    Snakemake reports the absent resource instead of silently using defaults.
    """
    paths = set()
    for key, kind in RESOURCE_KINDS.items():
        if not config.get(key):
            continue
        path = Path(config[key]).expanduser().absolute()
        paths.add(str(path))
        if path.exists():
            for member in _files(path, kind):
                paths.add(str(member))
                if member.is_symlink():
                    paths.add(str(member.resolve(strict=True)))
    paths.update(map(str, _profile_database_files(config)))
    return sorted(paths)


def _profile_database_files(config):
    directory = config.get("profiles_dir")
    if not directory or not Path(directory).expanduser().exists():
        return []
    paths = set()
    for profile in _files(Path(directory).expanduser(), "directory"):
        data = yaml.safe_load(profile.read_text())
        if not isinstance(data, dict):
            continue  # The existing profile validator reports malformed data.
        resources = data.get("resources") or {}
        if not isinstance(resources, dict):
            continue
        resource = resources.get("domains") or {}
        if not isinstance(resource, (str, dict)):
            continue
        database = resource if isinstance(resource, str) else resource.get("database")
        if not database:
            continue
        path = Path(database).expanduser()
        if not path.is_absolute():
            path = profile.resolve().parent / path
        for suffix in ("", ".h3f", ".h3i", ".h3m", ".h3p"):
            member = Path(str(path) + suffix)
            # Missing optional profile databases retain the existing selected-
            # candidate failure semantics; existing bytes are tracked now.
            if member.is_file():
                paths.add(member.resolve())
    return sorted(paths)


def _stat(path):
    stat = path.stat()
    return {key: getattr(stat, key) for key in
            ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}


def resolve_profile_resources(profile, manifest):
    """Keep relative resource lookup anchored to the original profile file."""
    result = deepcopy(profile)
    registry = result.get("registry_path")
    for resource in manifest["resources"]:
        if resource["key"] != "profiles_dir":
            continue
        for row in resource["files"]:
            if registry != row["snapshot_path"]:
                continue
            source = Path(row.get("source_resolved_path") or row["source_path"])
            result["original_registry_path"] = str(source)
            domains = (result.get("resources") or {}).get("domains") or {}
            if isinstance(domains, str):
                domains = {"database": domains}
            database = domains.get("database")
            if database:
                path = Path(database).expanduser()
                if not path.is_absolute():
                    path = source.parent / path
                result.setdefault("resources", {})["domains"] = {
                    **domains, "database": str(path.resolve())}
                result["original_domain_database"] = database
            return result
    return result


def discover_inputs(config_path, output):
    """Publish metadata after setup, for a dynamic Snakemake input function."""
    source = Path(config_path).resolve(strict=True)
    data = source.read_bytes()
    config = json.loads(data)["deep"]
    document = {"schema": DISCOVERY_SCHEMA, "source_config": str(source),
                "source_sha256": hashlib.sha256(data).hexdigest(), "deep": config,
                "declared_inputs": config_input_paths(config)}
    if data != source.read_bytes():
        raise ValueError("DeepSafe configuration changed during input discovery")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    temporary.replace(output)
    return document


def load_discovered_inputs(path):
    """Validate the serialized config, then enumerate current directory members."""
    document = json.loads(Path(path).read_text())
    if document.get("schema") != DISCOVERY_SCHEMA:
        raise ValueError("Unrecognized DeepSafe family input discovery")
    data = Path(document["source_config"]).read_bytes()
    if (hashlib.sha256(data).hexdigest() != document.get("source_sha256")
            or json.loads(data).get("deep") != document.get("deep")):
        raise ValueError("DeepSafe configuration changed after input discovery")
    return document


def snapshot_resources(config, destination):
    """Copy exact bytes into a new caller-owned output and bind their hashes.

    Originals are never modified. A snapshot cannot overwrite an existing
    directory, and is not created inside a source resource directory.
    """
    destination = Path(destination).expanduser().absolute()
    sources = []
    external_files = []
    for path in _profile_database_files(config):
        before = _stat(path)
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if before != _stat(path):
            raise ValueError("DeepSafe profile database changed while hashing: " + str(path))
        external_files.append({"path": str(path), "sha256": digest, "stat": before})
    for key, kind in RESOURCE_KINDS.items():
        if not config.get(key):
            continue
        source = Path(config[key]).expanduser().resolve(strict=True)
        files = _files(source, kind)
        if destination.resolve().is_relative_to(source):
            raise ValueError("DeepSafe snapshot must not be inside a source resource")
        sources.append((key, kind, source, files))
    destination.mkdir(parents=True, exist_ok=False)
    effective = deepcopy(config)
    resources = []
    for key, kind, source, files in sources:
        target = destination / key
        if kind == "directory":
            target.mkdir()
        rows = []
        for path in files:
            relative = str(path.relative_to(source)) if kind == "directory" else "."
            snapshot = target / relative if kind == "directory" else target
            resolved_source = path.resolve(strict=True)
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(data)
            if (path.resolve(strict=True) != resolved_source
                    or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
                raise ValueError("DeepSafe resource changed during snapshot: " + str(path))
            rows.append({"relative_path": relative, "source_path": str(path),
                         "source_resolved_path": str(resolved_source),
                         "snapshot_path": str(snapshot), "sha256": digest, "bytes": len(data)})
        if files != _files(source, kind):
            raise ValueError("DeepSafe resource members changed during snapshot: " + str(source))
        resources.append({"key": key, "kind": kind, "configured_path": str(config[key]),
                          "source_path": str(source), "snapshot_path": str(target), "files": rows})
        effective[key] = str(target)
    content = [{"key": row["key"], "kind": row["kind"],
                "files": [{k: item[k] for k in ("relative_path", "sha256", "bytes")}
                          for item in row["files"]]} for row in resources]
    manifest = {"schema": SCHEMA, "snapshot_root": str(destination),
                "resources": resources, "external_files": external_files,
                "content_sha256": _digest({"snapshots": content,
                    "external": [{"path": row["path"], "sha256": row["sha256"]} for row in external_files]})}
    manifest["manifest_sha256"] = _digest(manifest)
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    effective["family_resource_snapshot"] = {
        "manifest": str(manifest_path), "manifest_sha256": manifest["manifest_sha256"],
        "content_sha256": manifest["content_sha256"]}
    return {"effective_config": effective, "manifest": manifest}


def verify_resources(manifest):
    """Verify only frozen small resources before a resumed DeepSafe stage."""
    if manifest.get("schema") != SCHEMA:
        raise ValueError("Unrecognized DeepSafe family resource snapshot")
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if manifest.get("manifest_sha256") != _digest(unsigned):
        raise ValueError("DeepSafe family resource manifest changed")
    root = Path(manifest["snapshot_root"])
    if root.is_symlink():
        raise ValueError("DeepSafe family resource snapshot root is a symlink")
    root = root.resolve(strict=True)
    stored = root / "manifest.json"
    if stored.is_symlink() or not stored.is_file() or json.loads(stored.read_text()) != manifest:
        raise ValueError("DeepSafe family resource stored manifest changed or is missing")
    for external in manifest.get("external_files", []):
        path = Path(external["path"])
        if not path.is_file() or _stat(path) != external["stat"]:
            raise ValueError("DeepSafe profile database changed after snapshot: " + str(path))
    seen = set()
    for resource in manifest["resources"]:
        key = resource["key"]
        if key in seen or RESOURCE_KINDS.get(key) != resource["kind"]:
            raise ValueError("Invalid DeepSafe family resource snapshot entry")
        seen.add(key)
        target = Path(resource["snapshot_path"])
        if target.is_symlink() or target.resolve() != root / key:
            raise ValueError("DeepSafe family resource snapshot escapes its output")
        expected = set()
        for row in resource["files"]:
            path = Path(row["snapshot_path"])
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("DeepSafe family resource file escapes its output")
            expected.add(str(path))
            if (not path.is_file() or path.stat().st_size != row["bytes"]
                    or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]):
                raise ValueError("DeepSafe family resource checksum mismatch: " + str(path))
        if expected != {str(path) for path in _files(target, resource["kind"])}:
            raise ValueError("DeepSafe family resource snapshot members changed")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    discover_inputs(args.config, args.output)


if __name__ == "__main__":
    main()
