"""Read-only provenance capture; never executes a tool or restores an environment.

Frozen configurations and evidence retain their exact bytes. Resource hashes are
an opt-in inventory, not an archive, and recorded results are never reinterpreted.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
from html import escape
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
from urllib.parse import urlsplit, urlunsplit

import yaml

from .config import PATH_KEYS, default_config
from .inputs import SAMPLE_ID
from .progress import tracked
from .util import hash_object, resolve_path, sha256_file


CONDA_METADATA_LIMIT = 128 * 1024 * 1024
PYVENV_CONFIG_LIMIT = 64 * 1024
SOURCE_METADATA_LIMIT = 512 * 1024 * 1024
INPUT_KEYS = ("assembly", "proteins", "gff", "genbank", "phenotypes", "phenotype_source")
RESOURCE_KEYS = PATH_KEYS - {"placement_cache_dir"}
LIMITATIONS = [
    "This is a local provenance capture, not a verified portable or offline restore.",
    "Package archives, wheels, biological input files, databases and model weights are not copied into this audit.",
    "Python distribution names/versions and Conda metadata are inventories, not tested installation locks.",
    "No native analyses, GPU inference, installation, download or independent biological validation were performed.",
    "Current input/resource identities are distinct from historical result provenance; this audit does not make saved results current.",
    "Exact frozen configurations, sample sheets and evidence can contain local paths or sensitive content; review before sharing.",
]


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _bounded_bytes(path, limit):
    if not path.is_file():
        raise OSError(f"Not a regular file: {path}")
    size = path.stat().st_size
    if size > limit:
        raise OverflowError(f"{size} bytes exceeds {limit}-byte read limit")
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise OverflowError(f"File grew beyond {limit}-byte read limit")
    return raw


def _safe_url(value):
    """Keep remote identity without credentials, token paths, query or fragment."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http", "git+https", "git+http", "ssh", "git+ssh"} or not parsed.hostname:
            return "<local-or-unrecognized-source>"
        host = parsed.hostname
        if parsed.port:
            host += f":{parsed.port}"
        path = re.sub(r"/t/[^/]+", "/t/<redacted>", parsed.path)
        return urlunsplit((parsed.scheme, host, path, "", ""))
    except ValueError:
        return "<unrecognized-source>"


def _direct_source(raw):
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("direct_url.json must be an object")
    result = {"url": _safe_url(data.get("url"))}
    vcs = data.get("vcs_info")
    if isinstance(vcs, dict):
        result["vcs_info"] = {k: vcs[k] for k in ("vcs", "commit_id")
                              if isinstance(vcs.get(k), str)}
    archive = data.get("archive_info")
    if isinstance(archive, dict) and isinstance(archive.get("hashes"), dict):
        result["archive_hashes"] = {k: v for k, v in archive["hashes"].items()
                                     if k in {"sha256", "sha512", "md5"} and isinstance(v, str)}
    if isinstance(data.get("dir_info"), dict):
        result["editable"] = data["dir_info"].get("editable") is True
    return result


def _physical_metadata_identity(path):
    """Identify a metadata object, without reading or executing its contents."""
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    # Resolve symlink aliases and, where available, hard links/bind aliases too.
    # A zero inode is not a usable identity on some filesystems.
    identity = ("inode", stat.st_dev, stat.st_ino) if stat.st_ino else ("path", str(resolved))
    return resolved, identity


def _python_inventory(prefix):
    paths = sorted(prefix.glob("lib/python*/site-packages"))
    if (prefix / "Lib/site-packages").is_dir():
        paths.append(prefix / "Lib/site-packages")
    result = {"status": "inventoried" if paths else "no_site_packages_found",
              "distribution_inventory_only": True, "startup_code_executed": False,
              "distributions": [], "issues": [], "pth_files": [],
              "site_packages": [], "scan_aliases": []}
    seen_sites, seen_distributions, seen_pth = {}, {}, {}
    for site in paths:
        try:
            resolved_site, site_identity = _physical_metadata_identity(site)
        except (OSError, RuntimeError) as exc:
            result["issues"].append({"path": str(site), "kind": type(exc).__name__,
                                     "reason": "site_packages_unreadable"})
            continue
        site_row = {"path": str(site), "resolved_path": str(resolved_site)}
        if site_identity in seen_sites:
            site_row.update(status="alias_of_scanned_root", alias_of=seen_sites[site_identity])
            result["site_packages"].append(site_row)
            result["scan_aliases"].append({"kind": "site_packages", "path": str(site),
                                          "alias_of": seen_sites[site_identity]})
            continue
        seen_sites[site_identity] = str(site)
        site_row["status"] = "scanned"
        result["site_packages"].append(site_row)
        # importlib.metadata examines distribution metadata, not .pth or modules.
        for p in sorted(site.glob("*.pth")):
            try:
                resolved_pth, pth_identity = _physical_metadata_identity(p)
                if pth_identity in seen_pth:
                    result["scan_aliases"].append({"kind": "pth_file", "path": str(p),
                                                  "alias_of": seen_pth[pth_identity]})
                    continue
                seen_pth[pth_identity] = str(p)
                raw = _bounded_bytes(p, 1024 * 1024)
                result["pth_files"].append({"name": p.name, "path": str(p),
                    "resolved_path": str(resolved_pth), "sha256": hashlib.sha256(raw).hexdigest(),
                    "contains_startup_code": any(line.lstrip().startswith((b"import ", b"import\t"))
                                                 for line in raw.splitlines()), "executed": False})
            except (OSError, OverflowError, RuntimeError) as exc:
                result["issues"].append({"name": p.name, "kind": type(exc).__name__})
        try:
            distributions = list(importlib.metadata.distributions(path=[str(site)]))
        except Exception as exc:
            result["issues"].append({"name": site.name, "kind": type(exc).__name__})
            continue
        for dist in distributions:
            try:
                # PathDistribution records the actual .dist-info/.egg-info
                # location here. Name/version are deliberately NOT identities:
                # two physical installations (or conflicting versions) must
                # remain visible. If a provider has no path, retain its row
                # with an explicit audit issue rather than guessing a duplicate.
                metadata_path = getattr(dist, "_path", None)
                metadata_source = None
                if isinstance(metadata_path, (str, os.PathLike)):
                    metadata_source, metadata_identity = _physical_metadata_identity(Path(metadata_path))
                    if metadata_identity in seen_distributions:
                        result["scan_aliases"].append({"kind": "distribution_metadata",
                            "path": str(metadata_path), "alias_of": seen_distributions[metadata_identity]})
                        continue
                    seen_distributions[metadata_identity] = str(metadata_path)
                else:
                    result["issues"].append({"kind": "metadata_location_unavailable",
                                             "reason": "distribution_identity_not_deduplicated"})
                row = {"name": dist.metadata.get("Name"), "version": dist.version,
                       "installer": (dist.read_text("INSTALLER") or "").strip() or None,
                       "site_packages": str(site),
                       "metadata_path": str(metadata_source) if metadata_source is not None else None}
                direct = dist.read_text("direct_url.json")
                if direct:
                    row["direct_source"] = _direct_source(direct)
                if not row["name"] or not row["version"]:
                    raise ValueError("Missing distribution name or version")
                result["distributions"].append(row)
            except Exception as exc:
                result["issues"].append({"kind": type(exc).__name__, "reason": "distribution_metadata_unreadable"})
    result["distributions"].sort(key=lambda d: (d["name"].lower(), d["version"],
                                              d["metadata_path"] or "", d["site_packages"]))
    result["pip_origin_count"] = sum(d["installer"] == "pip" for d in result["distributions"])
    if result["issues"]:
        result["status"] = "incomplete_metadata_inventory"
    return result


def _read_explicit(path):
    raw = _bounded_bytes(path, 16 * 1024 * 1024)
    entries, saw_header = [], False
    for line in raw.decode("utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "@EXPLICIT":
            saw_header = True
            continue
        parts = urlsplit(line)
        filename = Path(parts.path).name
        if not filename.endswith((".conda", ".tar.bz2")):
            raise ValueError("Unexpected explicit-export package line")
        digest = parts.fragment
        if digest and not re.fullmatch(r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{64})", digest):
            raise ValueError("Unexpected explicit-export digest")
        entries.append({"filename": filename, "url": _safe_url(line),
                        "digest": digest.lower() or None,
                        "digest_kind": "sha256" if len(digest) == 64 else "md5" if digest else None})
    if not saw_header:
        raise ValueError("Missing @EXPLICIT header")
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "entries": entries}


def _venv_metadata(prefix):
    """Read venv markers as evidence; never launch its interpreter or startup code."""
    path = prefix / "pyvenv.cfg"
    result = {"path": str(path), "status": "not_present", "read_limit_bytes": PYVENV_CONFIG_LIMIT,
              "interpreter_executed": False}
    try:
        path.lstat()  # A dangling symlink is invalid evidence, not an absent marker.
    except FileNotFoundError:
        return result
    except OSError:
        return {**result, "status": "read_error"}
    try:
        result["is_symlink"] = path.is_symlink()
        resolved = path.resolve(strict=True)
        result["resolved_path"] = str(resolved)
        before = resolved.stat()
        result["bytes"] = before.st_size
        raw = _bounded_bytes(path, PYVENV_CONFIG_LIMIT)
        result["sha256"] = hashlib.sha256(raw).hexdigest()
        after = path.stat()
        if (path.resolve(strict=True) != resolved or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            return {**result, "status": "changed_during_read"}
        fields = {}
        for line in raw.decode("utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key, value = key.strip().lower(), value.strip()
            if not separator or not key or key in fields or "\x00" in line:
                raise ValueError("Malformed or duplicate pyvenv.cfg field")
            fields[key] = value
        if not fields.get("home") or fields.get("include-system-site-packages", "").lower() not in {"true", "false"}:
            raise ValueError("Missing home or invalid include-system-site-packages")
        # Preserve only fields needed to describe the environment. The free-form
        # creation command is unnecessary and can contain private arguments.
        result["fields"] = {key: fields[key] for key in ("home", "version", "executable") if key in fields}
        result["fields"]["include_system_site_packages"] = fields["include-system-site-packages"].lower() == "true"
        interpreters = [prefix / "bin/python", prefix / "Scripts/python.exe"]
        interpreter = next((p for p in interpreters if p.is_file()), None)
        if interpreter is None:
            return {**result, "status": "interpreter_missing"}
        result["interpreter"] = {"path": str(interpreter), "resolved_path": str(interpreter.resolve(strict=True)),
                                 "is_symlink": interpreter.is_symlink()}
        result["status"] = "valid_metadata"
    except OverflowError:
        result["status"] = "size_limit_exceeded"
    except (OSError, RuntimeError):
        result["status"] = "read_error"
    except (ValueError, UnicodeDecodeError):
        result["status"] = "malformed_metadata"
    return result


@tracked(lambda prefix, explicit_path=None: f"Audit environment metadata: {Path(prefix).name}")
def audit_environment(prefix, explicit_path=None):
    """Inventory Conda or Python venv metadata without asserting a runnable restore."""
    prefix = Path(prefix).expanduser().resolve()
    result = {"prefix": str(prefix), "status": "inventoried", "metadata_read_limit_bytes": CONDA_METADATA_LIMIT,
              "metadata_files": [], "packages": [], "issues": [], "archives_verified": False,
              "environment_type": "unknown", "conda_metadata": {"status": "not_assessed"}}
    meta = prefix / "conda-meta"
    if not prefix.is_dir():
        result.update(status="missing_environment", python={"status": "not_assessed"})
        return result
    venv = _venv_metadata(prefix)
    result["pyvenv_cfg"] = venv
    if meta.is_dir():
        result["environment_type"] = "conda"
        result["conda_metadata"]["status"] = "inventoried"
        if venv["status"] != "not_present":
            result["environment_type"] = "unknown"
            result["issues"].append({"kind": "conflicting_environment_markers"})
    elif venv["status"] == "valid_metadata":
        result["environment_type"] = "python_venv"
        result["conda_metadata"]["status"] = "not_applicable"
    else:
        result["conda_metadata"]["status"] = "missing"
        result["issues"].append({"kind": "conda_meta_missing"})
    if not meta.is_dir() and (meta.exists() or meta.is_symlink()):
        result["environment_type"] = "unknown"
        result["conda_metadata"]["status"] = "invalid_marker"
        result["issues"].append({"kind": "conda_meta_not_directory"})
    if venv["status"] not in {"not_present", "valid_metadata"}:
        result["issues"].append({"kind": "pyvenv_cfg_" + venv["status"]})
    for p in sorted(meta.glob("*.json")):
        identity = {"file": p.name}
        try:
            identity["bytes"] = p.stat().st_size
            raw = _bounded_bytes(p, CONDA_METADATA_LIMIT)
            identity["sha256"] = hashlib.sha256(raw).hexdigest()
            data = json.loads(raw)
            if not isinstance(data, dict) or any(not isinstance(data.get(k), str) or not data[k]
                                                 for k in ("name", "version", "build")):
                raise ValueError("Missing package identity fields")
            if any(data.get(k) is not None and not isinstance(data[k], str) for k in ("url", "channel")):
                raise ValueError("Invalid package URL fields")
            row = {k: data.get(k) for k in ("name", "version", "build", "build_number", "subdir", "fn", "sha256", "md5")}
            if any(row[k] is not None and not isinstance(row[k], str)
                   for k in ("subdir", "fn", "sha256", "md5")):
                raise ValueError("Invalid package filename or digest fields")
            row.update(metadata_file=p.name, metadata_sha256=identity["sha256"],
                       url=_safe_url(data.get("url")), channel=_safe_url(data.get("channel")))
            if not row["fn"]:
                row["fn"] = Path(urlsplit(data.get("url") or "").path).name or None
            result["packages"].append(row)
            identity["status"] = "read"
        except OverflowError:
            identity["status"] = "size_limit_exceeded"
        except OSError:
            identity["status"] = "read_error"
        except (ValueError, UnicodeDecodeError, TypeError):
            identity["status"] = "malformed_metadata"
        if identity["status"] != "read":
            result["issues"].append({"file": p.name, "kind": identity["status"]})
        result["metadata_files"].append(identity)
    result["metadata_file_count"] = len(result["metadata_files"])
    result["package_count"] = len(result["packages"])
    result["python"] = _python_inventory(prefix)
    if explicit_path is not None:
        if result["environment_type"] != "conda":
            result["issues"].append({"kind": "conda_export_for_non_conda_environment"})
        try:
            export = _read_explicit(Path(explicit_path).expanduser().resolve(strict=True))
            by_filename = {p["fn"]: p for p in result["packages"] if p["fn"]}
            names = [r["filename"] for r in export["entries"]]
            mismatches = []
            for entry in export["entries"]:
                package = by_filename.get(entry["filename"])
                if package is None:
                    mismatches.append({"filename": entry["filename"], "kind": "missing_metadata_record"})
                elif entry["digest_kind"] and entry["digest"] != package.get(entry["digest_kind"]):
                    mismatches.append({"filename": entry["filename"], "kind": "digest_mismatch_or_unavailable"})
            for filename in sorted(set(by_filename) - set(names)):
                mismatches.append({"filename": filename, "kind": "metadata_not_in_export"})
            if len(set(names)) != len(names):
                mismatches.append({"kind": "duplicate_export_entries"})
            if len(by_filename) != len(result["packages"]):
                mismatches.append({"kind": "missing_or_duplicate_metadata_filenames"})
            export.update(status="matches_metadata" if not mismatches and not result["issues"] else "mismatch",
                          mismatches=mismatches,
                          comparison_scope="Recorded filenames and available export digests; package archives were not checked.")
            result["explicit_export"] = export
            if export["status"] != "matches_metadata":
                result["issues"].append({"kind": "explicit_export_metadata_mismatch"})
        except (OSError, ValueError, OverflowError) as exc:
            result["explicit_export"] = {"status": "unreadable", "error_type": type(exc).__name__}
            result["issues"].append({"kind": "explicit_export_unreadable"})
    else:
        result["explicit_export"] = {"status": "not_supplied"}
    if result["issues"] or result["python"].get("issues"):
        result["status"] = "incomplete_metadata_inventory"
    if meta.is_dir() and any(row["status"] != "read" for row in result["metadata_files"]):
        result["conda_metadata"]["status"] = "incomplete_metadata_inventory"
    return result


def _file_identity(path, do_hash=True):
    result = {"path": str(path), "status": "present"}
    try:
        resolved = path.resolve(strict=True)
        result["resolved_path"] = str(resolved)
        if not resolved.is_file():
            return {**result, "status": "not_a_regular_file"}
        before = resolved.stat()
        result["bytes"] = before.st_size
        if do_hash:
            result["sha256"] = sha256_file(resolved)
            after = resolved.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                result["status"] = "changed_during_hashing"
            else:
                result["status"] = "hashed"
        return result
    except FileNotFoundError:
        return {**result, "status": "missing"}
    except OSError:
        return {**result, "status": "unreadable"}


@tracked(lambda path, do_hash, outdir: f"{'Hash' if do_hash else 'Inspect'} declared resource: {Path(path).name}")
def _resource_identity(path, do_hash, outdir):
    path = Path(path)
    if not path.is_dir():
        return _file_identity(path, do_hash)
    resolved = path.resolve()
    if outdir.is_relative_to(resolved):
        raise ValueError("Audit output must not be inside an audited resource directory")
    result = {"path": str(path), "resolved_path": str(resolved), "kind": "directory",
              "status": "not_hashed", "files": [], "issues": []}
    if not do_hash:
        return result
    def walk_error(exc):
        result["issues"].append({"kind": "directory_read_error", "path": str(exc.filename)})
    for root, dirs, files in os.walk(resolved, followlinks=False, onerror=walk_error):
        for name in list(dirs):
            p = Path(root) / name
            if p.is_symlink():
                result["issues"].append({"kind": "symlink_directory_not_traversed", "path": str(p.relative_to(resolved))})
                dirs.remove(name)
        for name in sorted(files):
            p = Path(root) / name
            if not p.resolve().is_relative_to(resolved):
                result["issues"].append({"kind": "external_symlink_not_hashed", "path": str(p.relative_to(resolved))})
                continue
            item = _file_identity(p)
            item["path"] = str(p.relative_to(resolved))
            result["files"].append(item)
            if item["status"] != "hashed":
                result["issues"].append({"kind": item["status"], "path": item["path"]})
    result["files"].sort(key=lambda x: x["path"])
    result["inventory_sha256"] = hash_object([
        {key: row.get(key) for key in ("path", "bytes", "sha256", "status")}
        for row in result["files"]])
    result["inventory_hash_scope"] = "Sorted relative file paths, byte counts, file SHA256 values and hashing statuses; absolute locations excluded."
    result["status"] = "hashed" if not result["issues"] else "incomplete_hash_inventory"
    return result


def _declared_environments(section, parent):
    argv = section.get("command_prefix", [])
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        return []
    if Path(argv[0]).name not in {"conda", "mamba", "micromamba"} or "run" not in argv:
        return []
    values = []
    for i, value in enumerate(argv):
        if value in {"-p", "--prefix"} and i + 1 < len(argv):
            values.append(resolve_path(argv[i + 1], parent))
        elif value.startswith("--prefix="):
            values.append(resolve_path(value.split("=", 1)[1], parent))
    return values


def _render_report(manifest):
    gaps = "".join(f"<li>{escape(g)}</li>" for g in manifest["open_gaps"])
    env_rows = []
    for environment in manifest["environments"]:
        conda_status = environment.get("conda_metadata", {}).get("status", "not_assessed")
        conda_inventory = ("Not applicable (Python venv)" if conda_status == "not_applicable" else
                           f"{environment.get('package_count', 0)} · {conda_status}")
        values = [environment["prefix"], environment.get("environment_type", "unknown"),
                  environment["status"], conda_inventory,
                  environment.get("explicit_export", {}).get("status", "not_assessed")]
        env_rows.append("<tr>" + "".join(f"<td>{escape(value)}</td>" for value in values) + "</tr>")
    envs = "".join(env_rows)
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BaSIS reproducibility audit</title><style>body{{font:16px system-ui;background:#eef3f7;color:#183047;max-width:1120px;margin:40px auto;padding:24px}}main{{background:white;padding:30px;border-radius:16px}}h1{{color:#123b50}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #dde4eb;overflow-wrap:anywhere}}table{{width:100%;border-collapse:collapse}}a{{color:#096b83}}li{{margin:8px 0}}code{{overflow-wrap:anywhere}}</style>
<main><h1>BaSIS reproducibility audit</h1><p><strong>{escape(manifest['status'])}</strong> · {escape(manifest['created_at_utc'])}</p>
<p>{len(manifest['sources'])} frozen source files · {len(manifest['inputs'])} input identities · {len(manifest['resources'])} resource declarations · {len(manifest['environments'])} environments.</p>
<p><a href="manifest.json">Download manifest</a> · <a href="SHA256SUMS">Download audit checksums</a></p>
<h2>Environment records</h2><table><thead><tr><th>Prefix</th><th>Environment type</th><th>Inventory</th><th>Conda packages / metadata</th><th>Explicit comparison</th></tr></thead><tbody>{envs}</tbody></table>
<p>Python venvs are identified from their configuration and interpreter file without execution. Conda metadata is not applicable to them; Python distributions are inventoried in the manifest. This classification is not a runtime or restoration test.</p>
<h2>Open gaps and limits</h2><ul>{gaps}</ul><p>Source/result interpretations were not changed. This audit does not establish biological validity or a successful fresh-machine restore.</p></main></html>"""


@tracked("Capture reproducibility records")
def audit_reproducibility(config_paths=(), evidence_paths=(), environment_prefixes=(), outdir=None,
                          hash_resources=False, conda_explicit_paths=None):
    """Freeze source bytes and inventory current inputs/resources/environments.

    ``conda_explicit_paths`` optionally maps environment prefixes to saved
    @EXPLICIT export files. Output must be a new directory. Missing resources or
    metadata are recorded as gaps; malformed explicitly supplied sources fail.
    """
    from . import __version__
    if outdir is None:
        raise ValueError("An output directory is required")
    requested_output = Path(outdir).expanduser()
    if os.path.lexists(requested_output):
        raise FileExistsError(f"Output already exists: {requested_output}")
    output = requested_output.resolve()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    configs = [Path(p).expanduser().resolve(strict=True) for p in config_paths]
    evidences = [Path(p).expanduser().resolve(strict=True) for p in evidence_paths]
    environments = {str(Path(p).expanduser().resolve()) for p in environment_prefixes}
    exports = {}
    for key, value in (conda_explicit_paths or {}).items():
        resolved = str(Path(key).expanduser().resolve())
        if resolved in exports:
            raise ValueError(f"Multiple explicit exports resolve to the same environment: {resolved}")
        exports[resolved] = value
    environments.update(exports)
    if not configs and not evidences and not environments:
        raise ValueError("Supply at least one configuration, evidence file or environment prefix")
    for path in configs + evidences:
        if not path.is_file():
            raise ValueError(f"Expected a source file: {path}")
    manifest = {"schema_version": "basis-reproducibility-audit-1", "basis_version": __version__,
                "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "capture_in_progress",
                "sources": [], "inputs": [], "resources": [], "environments": [], "module_scopes": [],
                "resource_hashing_requested": bool(hash_resources), "native_analyses_run": False,
                "gpu_inference_run": False, "network_access_used": False, "installations_performed": False,
                "independent_biological_validation": "not_performed", "portable_restore_verified": False,
                "open_gaps": list(LIMITATIONS)}
    manifest["runtime"] = {"python_version": platform.python_version(),
                           "implementation": platform.python_implementation(),
                           "executable": sys.executable, "prefix": sys.prefix,
                           "base_prefix": sys.base_prefix, "platform": platform.platform(),
                           "machine": platform.machine()}
    from .release import runtime_hashes, verify_install
    manifest["installed_package"] = verify_install()
    manifest["package_file_sha256"] = runtime_hashes()
    if manifest["installed_package"]["status"] != "passed":
        manifest["open_gaps"].append("Installed BaSIS package seal did not pass; inspect installed_package status and file details.")
    pending_sources, inputs, resources = {}, {}, {}

    def source(path, role):
        raw = _bounded_bytes(path, SOURCE_METADATA_LIMIT)
        digest = hashlib.sha256(raw).hexdigest()
        item = pending_sources.setdefault(path, {"sha256": digest, "bytes": len(raw), "roles": []})
        if item["sha256"] != digest:
            raise RuntimeError(f"Source changed during inspection: {path}")
        item["roles"].append(role)
        return raw

    def sample_inputs(sample, parent, origin):
        for key in INPUT_KEYS:
            value = sample.get(key)
            if isinstance(value, str) and value:
                path = resolve_path(value, parent)
                inputs.setdefault(path, []).append({"source": origin, "sample_id": sample.get("sample_id"), "field": key})

    def section_resources(section, parent, origin, sample_id=None, scope="configured_enabled",
                          expand_sample_template=False):
        if not isinstance(section, dict):
            return
        active = scope not in {"disabled", "not_requested", "sample_scope_exclusion"}
        manifest["module_scopes"].append({"source": origin, "sample_id": sample_id,
                                           "scope": scope, "assessed_dependencies": active})
        if active:
            environments.update(_declared_environments(section, parent))
        argv = section.get("command_prefix", [])
        if active and isinstance(argv, list) and any(v in argv for v in ("-n", "--name")):
            manifest["open_gaps"].append(f"Named environment not resolved automatically: {origin}; supply its explicit prefix.")
        for key in sorted(RESOURCE_KEYS):
            value = section.get(key)
            if isinstance(value, str) and value:
                original_value = value
                if expand_sample_template and sample_id is not None:
                    value = value.replace("{sample_id}", sample_id)
                unresolved_template = "{" in value or "}" in value
                path = resolve_path(value, parent)
                resources.setdefault(path, []).append({"source": origin, "field": key, "sample_id": sample_id,
                    "scope": scope, "assessment_requested": active, "declared_value": original_value,
                    "template_status": "unresolved_template" if unresolved_template else "resolved",
                    "declared_database_version": str(section["database_version"]) if section.get("database_version") is not None else None,
                    "declared_model_source_commit": str(section["pf2_source_commit"]) if section.get("pf2_source_commit") is not None else None})

    for p in configs:
        data = yaml.safe_load(source(p, "configuration"))
        if not isinstance(data, dict):
            raise ValueError(f"Configuration must be a mapping: {p}")
        sheet = data.get("samples")
        if not isinstance(sheet, str) or not sheet:
            raise ValueError(f"Configuration lacks a sample sheet: {p}")
        sheet_path = Path(resolve_path(sheet, p.parent))
        sheet_raw = source(sheet_path, "sample_sheet")
        if sheet_path.suffix.lower() in {".xlsx", ".xls", ".csv"}:
            raise ValueError("Execution sample sheets must be TSV; freeze the imported TSV configuration")
        rows = csv.DictReader(io.StringIO(sheet_raw.decode("utf-8-sig")), delimiter="\t", strict=True)
        if not rows.fieldnames or "sample_id" not in rows.fieldnames or len(rows.fieldnames) != len(set(rows.fieldnames)):
            raise ValueError(f"Malformed sample-sheet headers: {sheet_path}")
        sample_ids, seen = [], set()
        for row in rows:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed sample-sheet row: {sheet_path}")
            if not any(value.strip() for value in row.values()):
                continue
            sid = row["sample_id"].strip()
            if not SAMPLE_ID.fullmatch(sid) or sid.casefold() in seen or sid in {".", ".."}:
                raise ValueError(f"Invalid or duplicate sample ID in {sheet_path}: {sid!r}")
            seen.add(sid.casefold())
            sample_ids.append(sid)
            row["sample_id"] = sid
            sample_inputs(row, sheet_path.parent, str(sheet_path))
        if not sample_ids:
            raise ValueError(f"Sample sheet contains no samples: {sheet_path}")
        modules = data.get("modules", {})
        if not isinstance(modules, dict):
            raise ValueError(f"Configuration modules must be a mapping: {p}")
        from .sample_policy import validate_sample_overrides
        validate_sample_overrides(data, [{"sample_id": sid} for sid in sample_ids])
        defaults = default_config()["modules"]
        for name, section in modules.items():
            if not isinstance(section, dict):
                raise ValueError(f"Configuration module must be a mapping: {name}")
            enabled = section.get("enabled", defaults.get(name, {}).get("enabled", True))
            if not isinstance(enabled, bool):
                raise ValueError(f"Module enabled must be a boolean: {name}")
            for sid in sample_ids:
                excluded = name in data.get("sample_module_overrides", {}).get(sid, {})
                scope = "disabled" if not enabled else "sample_scope_exclusion" if excluded else "configured_enabled"
                section_resources(section, p.parent, f"{p}:{name}", sid, scope, True)
        # DeepSafe resource paths are not sample templates in the execution API.
        section_resources(data.get("deep", {}), p.parent, f"{p}:deep", scope="configured_optional_scope_unknown")
    for p in evidences:
        data = json.loads(source(p, "evidence"))
        if not isinstance(data, dict) or data.get("schema_version") != "basis-report-1":
            raise ValueError(f"Expected basis-report-1 evidence: {p}")
        for key in ("samples", "records", "deep_records"):
            if not isinstance(data.get(key), list) or any(not isinstance(x, dict) for x in data[key]):
                raise ValueError(f"Evidence has invalid {key}: {p}")
        for sample in data["samples"]:
            sample_inputs(sample, p.parent, str(p))
        for record in data["records"] + data["deep_records"]:
            section = record.get("effective_config", {})
            decision = record.get("module_decision") or {}
            scope = "saved_execution_scope"
            if (isinstance(decision, dict) and decision.get("requested") is False
                    or record.get("reason") in {"deepsafe_not_requested", "extension_not_requested", "module_disabled"}):
                scope = "not_requested"
            elif isinstance(section, dict) and section.get("enabled") is False and record.get("execution_status") in {"not_assessed", "disabled", "skipped"}:
                scope = "disabled"
            section_resources(section, p.parent, f"{p}:{record.get('sample_id')}:{record.get('module')}",
                              record.get("sample_id"), scope)
    for prefix in environments:
        if output.is_relative_to(Path(prefix)):
            raise ValueError("Audit output must not be inside an audited environment")
    for path, declarations in resources.items():
        if (any(d["assessment_requested"] and d["template_status"] == "resolved" for d in declarations)
                and Path(path).is_dir() and output.is_relative_to(Path(path).resolve())):
            raise ValueError("Audit output must not be inside an audited resource directory")
    output.mkdir(parents=True, exist_ok=False)
    frozen = output / "frozen"
    frozen.mkdir()
    for index, (path, item) in enumerate(pending_sources.items(), 1):
        digest = item["sha256"]
        target = frozen / f"{index:03d}-{path.name}"
        shutil.copyfile(path, target)
        if sha256_file(target) != digest or sha256_file(path) != digest:
            raise RuntimeError(f"Source changed while freezing: {path}")
        manifest["sources"].append({"path": str(path), "roles": sorted(set(item["roles"])),
                                    "sha256": digest, "frozen_path": str(target.relative_to(output)),
                                    "bytes": item["bytes"], "source_unchanged": True})
    for path, declarations in sorted(inputs.items()):
        row = _file_identity(Path(path))
        row["declarations"] = declarations
        manifest["inputs"].append(row)
        if row["status"] != "hashed":
            manifest["open_gaps"].append(f"Input {row['status']}: {path}")
    for path, declarations in sorted(resources.items()):
        active = [d for d in declarations if d["assessment_requested"]]
        if not active:
            row = {"path": path, "status": "not_assessed", "reason": "all_declarations_inactive"}
        elif any(d["template_status"] == "unresolved_template" for d in active):
            row = {"path": path, "status": "unresolved_template", "reason": "unsupported_or_unresolved_path_placeholder"}
        else:
            row = _resource_identity(path, hash_resources, output)
        row["declarations"] = declarations
        manifest["resources"].append(row)
        if active and row["status"] not in {"hashed", "present", "not_hashed"}:
            manifest["open_gaps"].append(f"Resource {row['status']}: {path}")
    if any(r["status"] in {"present", "not_hashed"} for r in manifest["resources"]) and not hash_resources:
        manifest["open_gaps"].append("Resource contents were not hashed; use --hash-resources for a full current file inventory.")
    for prefix in sorted(environments):
        row = audit_environment(prefix, exports.get(prefix))
        manifest["environments"].append(row)
        if row["status"] != "inventoried":
            manifest["open_gaps"].append(f"Environment {row['status']}: {prefix}")
    if not environments:
        manifest["open_gaps"].append("No environment prefixes were supplied or resolved from declared commands.")
    for row in manifest["sources"]:
        if sha256_file(row["path"]) != row["sha256"]:
            raise RuntimeError(f"Source changed during audit: {row['path']}")
    manifest.update(status="completed_with_open_gaps", sources_unchanged=True,
                    finished_at_utc=datetime.now(timezone.utc).isoformat(),
                    artifacts={"manifest": str(output / "manifest.json"), "report": str(output / "report.html"),
                               "checksums": str(output / "SHA256SUMS")})
    _write_json(output / "manifest.json", manifest)
    (output / "report.html").write_text(_render_report(manifest), encoding="utf-8")
    sums = [(str(p.relative_to(output)), sha256_file(p)) for p in sorted(output.rglob("*")) if p.is_file()]
    (output / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sums), encoding="utf-8")
    return manifest
