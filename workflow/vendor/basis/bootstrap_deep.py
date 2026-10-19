"""First-run DeepSafe/Extend resources; no query analyses or biological curation.

The bootstrap orchestrator owns locking, staging, immutable promotion and the
outer manifest. These providers return configuration paths rooted in staging;
the orchestrator rewrites those paths when publishing the completed snapshot.
"""
from __future__ import annotations

import csv
from collections import Counter
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import re
import shutil

from .progress import emit
from .references import (PROTEIN_ALPHABET, load_catalog, read_fasta,
                         sequence_hash, validate_profiles, write_fasta)
from .util import atomic_json, now, sha256_file


PROTT5_REPOSITORY = "Rostlab/prot_t5_xl_half_uniref50-enc"
PROTT5_REVISION = "94a6abc029ae13029317b140b7424e012bf8dfbf"
PROSITE_BASE = "https://ftp.expasy.org/databases/prosite/"
PROSITE_FILES = ("prosite.dat", "prosite.aux", "prosite.doc", "ps_reldt.txt",
                 "ps_scan.pl", "prorule.dat")
PROTT5_FILES = ("config.json", "pytorch_model.bin", "special_tokens_map.json",
                "spiece.model", "tokenizer_config.json", "README.md")

# Executed by the managed resource environment, not the controller interpreter.
# TLS verification is retained; partial network reads are retried from scratch.
_DOWNLOAD = r'''
import json, os, sys, time, urllib.request
from pathlib import Path
url, target, maximum = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
partial = target.with_name(target.name + ".part")
for attempt in range(4):
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "BaSIS-resource-setup/1"})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            if not response.geturl().startswith("https://"):
                raise ValueError("Resource redirected away from HTTPS")
            expected = response.headers.get("Content-Length")
            received = 0
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                received += len(block)
                if received > maximum:
                    raise ValueError("Resource exceeds download size limit")
                output.write(block)
            if not received or (expected is not None and received != int(expected)):
                raise ValueError("Empty or truncated resource download")
            output.flush()
            os.fsync(output.fileno())
            effective_url = response.geturl()
        partial.replace(target)
        print(json.dumps({"source_url": url, "effective_url": effective_url, "bytes": received}))
        break
    except Exception:
        partial.unlink(missing_ok=True)
        if attempt == 3:
            raise
        time.sleep(min(2 ** attempt, 8))
'''

_PROTT5 = r'''
import importlib.metadata, importlib.util, json, os, shutil, sys, time
from pathlib import Path
# Hub settings are read at import time. The regular HTTPS transport avoids the
# additional Xet CAS endpoint, which fails on some otherwise working networks.
# This changes transport only: the repository/revision and TLS checks are fixed.
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
from huggingface_hub import snapshot_download
destination, repository, revision, source_commit = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
download_model, copy_weights = sys.argv[5] == "yes", sys.argv[6] == "yes"
download_directory = Path(sys.argv[7])
distribution = importlib.metadata.distribution("PathogenFinder2")
if distribution.version != "0.7.1":
    raise ValueError("Expected PathogenFinder2 0.7.1 for the inspected adapter")
direct_url = distribution.read_text("direct_url.json")
if direct_url:
    direct_url = json.loads(direct_url)
    recorded_commit = direct_url.get("vcs_info", {}).get("commit_id")
    if recorded_commit and recorded_commit != source_commit:
        raise ValueError("PathogenFinder2 source commit differs from the pinned adapter")
package = importlib.util.find_spec("pathogenfinder2")
if package is None or not package.submodule_search_locations:
    raise ValueError("PathogenFinder2 package location unavailable")
package_roots = list(package.submodule_search_locations)
if len(package_roots) != 1:
    raise ValueError("Ambiguous PathogenFinder2 package location")
weights = Path(package_roots[0]) / "data" / "models_weights"
required_weights = ["model.pt"] + ["weights_model%d.pt" % n for n in range(1, 5)]
for name in required_weights:
    if not (weights / name).is_file() or not (weights / name).stat().st_size:
        raise ValueError("Missing bundled classifier weights: " + name)
if download_model:
    model = destination / "model"
    model_files = ["config.json", "pytorch_model.bin", "special_tokens_map.json",
                   "spiece.model", "tokenizer_config.json", "README.md"]
    for attempt in range(1, 5):
        print("ProtT5 HTTPS download attempt %d/4; pinned revision %s; partial downloads retained" %
              (attempt, revision), flush=True)
        try:
            snapshot_download(repo_id=repository, revision=revision, local_dir=str(download_directory),
                allow_patterns=model_files, max_workers=1)
            break
        except Exception as exc:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", getattr(exc, "code", None))
            retryable = not isinstance(exc, (ValueError, PermissionError)) and status not in (400, 401, 403, 404, 422)
            # Signed download URLs can appear in exception text. Do not copy
            # those credentials into the diagnostic message or traceback.
            diagnostic = type(exc).__name__ + (" HTTP " + str(status) if status else "")
            if attempt == 4 or not retryable:
                raise RuntimeError("Pinned ProtT5 HTTPS download failed after %d attempt(s): %s. "
                    "Partial files remain in %s; retry uses the same revision and cache." %
                    (attempt, diagnostic, download_directory)) from None
            print("ProtT5 download interrupted (%s); retrying the same cache" % diagnostic, flush=True)
            time.sleep(min(2 ** attempt, 8))
    # The download cache survives a failed workflow, independently of staging.
    # Publish only complete required files; no HF partial files enter a snapshot.
    if model.resolve() != download_directory.resolve():
        model.mkdir()
        for name in model_files:
            source = download_directory / name
            if not source.is_file() or not source.stat().st_size:
                raise ValueError("Pinned ProtT5 download lacks required file: " + name)
            shutil.copyfile(source, model / name)
if copy_weights:
    copied = destination / "classifier_weights"
    copied.mkdir()
    for name in required_weights:
        shutil.copyfile(weights / name, copied / name)
identity = {"repository": repository, "revision": revision,
    "pathogenfinder2_version": distribution.version, "pf2_source_commit": source_commit,
    "installed_direct_url": direct_url, "classifier_source": str(weights),
    "classifier_files": required_weights, "gpu_computation_performed": False,
    "model_downloaded": download_model, "weights_copied": copy_weights,
    "download_transport": "huggingface_hub_https_xet_disabled",
    "download_cache": str(download_directory) if download_model else None,
    "download_attempts": attempt if download_model else 0}
(destination / "model-identity.json").write_text(json.dumps(identity, indent=2) + "\n")
'''


def _options(spec):
    options = spec.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("Deep resource options must be a mapping")
    return options


def _required_file(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or not path.stat().st_size:
        raise ValueError("Required resource file is missing or empty: " + str(path))
    return path


def _checksums(directory):
    return {str(path.relative_to(directory)): sha256_file(path)
            for path in sorted(Path(directory).rglob("*"))
            if path.is_file() and ".cache" not in path.relative_to(directory).parts}


def _download(url, target, runner):
    emit("Downloading resource: " + target.name)
    runner.run(["python", "-c", _DOWNLOAD, url, str(target), str(256 * 1024 * 1024)],
               cwd=target.parent)
    return _required_file(target)


def _prosite(spec, destination, runner):
    from .prosite import load_snapshot
    from .prosite_profiles import load_profile_snapshot

    options = _options(spec)
    sources = {name: PROSITE_BASE + ("ps_scan/" if name == "ps_scan.pl" else "") + name
               for name in PROSITE_FILES}
    # Read the release marker on both sides of the transfer; a release changing
    # during bootstrap must not silently produce a mixed snapshot.
    _download(sources["ps_reldt.txt"], destination / "ps_reldt.txt", runner)
    initial_marker = (destination / "ps_reldt.txt").read_bytes()
    match = re.search(rb"Release (\d{4}_\d+)", initial_marker)
    if not match:
        raise ValueError("PROSITE release marker is unrecognized")
    release = match.group(1).decode("ascii")
    expected = options.get("release") or spec.get("version")
    if expected and expected not in {"current", "latest"} and expected != release:
        raise ValueError("PROSITE release differs from requested release: " + str(expected))
    for name in PROSITE_FILES:
        if name != "ps_reldt.txt":
            _download(sources[name], destination / name, runner)
    after = destination / "release-after.txt"
    _download(sources["ps_reldt.txt"], after, runner)
    if after.read_bytes() != initial_marker:
        raise ValueError("PROSITE changed release during download; retry a fresh snapshot")
    after.unlink()
    manifest = {"schema": "basis-prosite-snapshot-v1", "release": release,
        "downloaded_at_utc": now(), "source_urls": sources,
        "sha256": {name: sha256_file(destination / name) for name in PROSITE_FILES},
        "license_url": "https://prosite.expasy.org/prosite_license.html",
        "license_notice": "PROSITE: CC BY-NC-ND 4.0; original data retained unmodified. ps_scan has its own GPL notice.",
        "scope": "Patterns and profiles; conservative supported ProRule residue observations, not full functional rule inference."}
    atomic_json(destination / "provenance.json", manifest)
    patterns, pattern_meta = load_snapshot(destination)
    profiles, rules, profile_meta = load_profile_snapshot(destination)
    if not profiles or not rules:
        raise ValueError("PROSITE profile/rule snapshot is empty")
    # Official release 2026_03 itself has profile links to rules absent from its
    # bulk ProRule file. Profile detection remains valid, but the missing rule's
    # annotations cannot be assessed. Do not mix in unversioned web rules.
    coverage = profile_meta["rule_coverage"]
    atomic_json(destination / "rule-coverage.json", coverage)
    if coverage["unavailable_rule_ids"]:
        emit("PROSITE profiles ready with partial ProRule coverage: " +
             str(len(coverage["unavailable_rule_ids"])) +
             " linked rules absent from the release; affected residue annotations will be marked unavailable")
    return {"module_updates": {}, "deep_updates": {
        "prosite_snapshot": str(destination), "prosite_profiles": True,
        "executables": {"pfscan": "pfscanV3"}}, "provenance": {
        "provider": "prosite", "release": release, "source_urls": sources,
        "files": _checksums(destination), "pattern_count": len(patterns),
        "eligible_pattern_count": pattern_meta["eligible_patterns"],
        "profile_count": len(profiles), "prorule_count": len(rules),
        "rule_coverage": coverage,
        "limitation": profile_meta["limitation"], "analyses_run": False}}


def _prott5(spec, destination, runner):
    from .adapters.extend import PF2_SOURCE_COMMIT

    options = _options(spec)
    repository = options.get("repository", PROTT5_REPOSITORY)
    revision = options.get("revision", PROTT5_REVISION)
    if (repository, revision) != (PROTT5_REPOSITORY, PROTT5_REVISION):
        raise ValueError("ProtT5 bootstrap supports the inspected repository and pinned revision only")
    external = options.get("external_modules", {}).get("pathogenfinder2", spec.get("module_config", {}))
    external_model, external_weights = external.get("prot_t5_path"), external.get("weights_model")
    model = Path(external_model).resolve(strict=True) if external_model else destination / "model"
    weights = Path(external_weights).resolve(strict=True) if external_weights else destination / "classifier_weights"
    if external_model and not external.get("prot_t5_identity"):
        raise ValueError("An existing ProtT5 directory requires its recorded prot_t5_identity; bootstrap will not assert the downloaded model identity for external files")
    emit("Preparing pinned ProtT5 and bundled PathogenFinder2 classifier weights; no GPU inference")
    download_directory = (Path(spec["cache_directory"]) / "downloads" / "prott5" / revision
                          if spec.get("cache_directory") else destination / "model")
    runner.run(["python", "-c", _PROTT5, str(destination), repository, revision,
                PF2_SOURCE_COMMIT, "no" if external_model else "yes",
                "no" if external_weights else "yes", str(download_directory)], cwd=destination)
    for name in (PROTT5_FILES if not external_model else ("config.json", "spiece.model")):
        _required_file(model / name)
    if external_model and not any((model / name).is_file() for name in ("pytorch_model.bin", "model.safetensors")):
        raise ValueError("Existing ProtT5 directory lacks model weights")
    config = json.loads((model / "config.json").read_text())
    if config.get("model_type") != "t5" or config.get("d_model") != 1024:
        raise ValueError("Downloaded ProtT5 configuration differs from the expected encoder")
    identity = json.loads(_required_file(destination / "model-identity.json").read_text())
    if (identity.get("repository"), identity.get("revision"), identity.get("pf2_source_commit")) != (
            repository, revision, PF2_SOURCE_COMMIT):
        raise ValueError("ProtT5 preparation identity differs from the requested model")
    for name in ["model.pt"] + [f"weights_model{n}.pt" for n in range(1, 5)]:
        _required_file(weights / name)
    emit("Recording model and classifier file checksums")
    updates = {"pf2_source_commit": PF2_SOURCE_COMMIT, "expected_models": 4}
    if not external_model:
        updates.update(prot_t5_path=str(model), prot_t5_identity=repository + "@" + revision)
    if not external_weights:
        updates["weights_model"] = str(weights)
    external_provenance = {}
    if external_model:
        external_provenance["model"] = {"path": str(model), "identity": external["prot_t5_identity"], "sha256": _checksums(model)}
    if external_weights:
        external_provenance["classifier_weights"] = {"path": str(weights), "sha256": _checksums(weights)}
    return {"module_updates": {"pathogenfinder2": updates},
        "deep_updates": {}, "provenance": {"provider": "prott5", **identity,
        "files": _checksums(destination), "external_resources": external_provenance,
        "model_loaded": False, "analyses_run": False}}


def _dependencies(spec):
    values = []
    for row in spec.get("dependency_resources", []):
        if not isinstance(row, dict):
            raise ValueError("Deep resource dependency manifest must be a mapping")
        # Manifest writers may wrap the provider result as `result`.
        values.append(row.get("result", row))
    dependencies = spec.get("dependencies", {})
    if isinstance(dependencies, dict):
        values.extend(row.get("result", row) for row in dependencies.values() if isinstance(row, dict))
    return values


def _sources_and_pfam(spec):
    options = _options(spec)
    sources = list(options.get("reference_sources", []))
    pfam = options.get("domain_database")
    for module, fields in options.get("external_modules", {}).items():
        if not isinstance(fields, dict) or not fields.get("database"):
            continue
        database = Path(fields["database"]).resolve(strict=True)
        if module == "amrfinder":
            sources.append({"source": "AMRFinder", "path": str(database / "AMRProt.fa"), "format": "fasta"})
        elif module == "rgi":
            sources.append({"source": "CARD", "path": str(database / "card.json" if database.is_dir() else database), "format": "card_json"})
        elif module == "vfdb":
            source = fields.get("reference_fasta")
            if not source:
                names = ("VFDB_setA_pro.utf8.fas", "VFDB_setA_pro.normalized.fas", "VFDB_setA_pro.fas")
                choices = [database.parent / name for name in names if (database.parent / name).is_file()]
                if len(choices) != 1:
                    raise ValueError("Existing VFDB requires an unambiguous source FASTA; set its reference_fasta path")
                source = str(choices[0])
            sources.append({"source": "VFDB", "path": source, "format": "fasta"})
        elif module == "antismash" and not pfam:
            choices = [p for p in (database / "pfam").glob("*/Pfam-A.hmm")
                       if re.fullmatch(r"\d+(?:\.\d+)*", p.parent.name)]
            if not choices:
                raise ValueError("Existing antiSMASH database lacks a versioned Pfam-A.hmm")
            pfam = str(max(choices, key=lambda p: tuple(map(int, p.parent.name.split(".")))))
    for dependency in _dependencies(spec):
        provenance = dependency.get("provenance", {})
        sources.extend(provenance.get("reference_sources", []))
        pfam = pfam or dependency.get("deep_updates", {}).get("domain_database")
        updates = dependency.get("module_updates", {})
        # Older/bootstrap imported manifests can declare exact resource paths
        # without the convenience reference_sources list.
        if not provenance.get("reference_sources"):
            for module, name, filename, fmt in (
                ("amrfinder", "AMRFinder", "AMRProt.fa", "fasta"),
                ("rgi", "CARD", None, "card_json"),
                ("vfdb", "VFDB", None, "fasta")):
                fields = updates.get(module, {})
                path = fields.get("reference_fasta")
                if module == "amrfinder" and fields.get("database"):
                    path = str(Path(fields["database"]) / filename)
                elif module == "rgi":
                    path = fields.get("database")
                if path:
                    sources.append({"source": name, "path": path, "format": fmt})
    normalized = {}
    for source in sources:
        if not isinstance(source, dict) or source.get("format") not in {"fasta", "card_json"}:
            raise ValueError("Reference source requires source, path and fasta/card_json format")
        name = source.get("source")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("Invalid reference source namespace")
        path = _required_file(source.get("path", ""))
        row = {"source": name, "path": str(path), "format": source["format"]}
        if name in normalized and normalized[name] != row:
            raise ValueError("Conflicting screening source paths for " + name)
        normalized[name] = row
    if not normalized and options.get("preserve_references") is not True:
        raise ValueError("Deep catalogue requires completed screening reference sources")
    if not pfam:
        raise ValueError("Deep catalogue requires the antiSMASH Pfam resource or an explicit domain_database")
    pfam = _required_file(pfam)
    for suffix in (".h3f", ".h3i", ".h3m", ".h3p"):
        _required_file(str(pfam) + suffix)
    return list(normalized.values()), pfam


def _bundled_panel():
    panel = Path(str(files("basis").joinpath("resources", "comparators", "reviewed-v1")))
    checksums = json.loads(_required_file(panel / "checksums.json").read_text())
    for relative, expected in checksums.items():
        target = _required_file(panel / relative)
        if not target.is_relative_to(panel.resolve()) or sha256_file(target) != expected:
            raise ValueError("Bundled comparator package checksum differs: " + relative)
    profiles, errors = validate_profiles(panel / "profiles")
    if errors or not profiles:
        raise ValueError("Bundled family profiles invalid: " + "; ".join(errors))
    return panel, load_catalog(panel / "references.faa", panel / "references.tsv")


def _catalogue(spec, destination, runner):
    del runner  # Parsing existing resources does not need another native command.
    sources, pfam = _sources_and_pfam(spec)
    options = _options(spec)
    preserve = options.get("preserve_references") is True
    sequences, metadata, counts = {}, {}, Counter()
    source_provenance = []
    external_catalogue = None
    if preserve:
        external_fasta = _required_file(options.get("reference_fasta", ""))
        external_metadata = _required_file(options.get("reference_metadata", ""))
        external_catalogue = load_catalog(external_fasta, external_metadata)
        source_provenance.extend([
            {"source": "external_reference_fasta", "path": str(external_fasta), "sha256": sha256_file(external_fasta)},
            {"source": "external_reference_metadata", "path": str(external_metadata), "sha256": sha256_file(external_metadata)}])

    def add(namespace, key, sequence, accession, label, source, model=""):
        sequence = "".join(str(sequence).split()).upper()
        if not sequence or not PROTEIN_ALPHABET.fullmatch(sequence):
            raise ValueError(f"Invalid screening reference protein: {namespace}/{key}")
        reference = namespace + "_" + hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:24]
        if reference in sequences:
            raise ValueError(f"Duplicate screening reference identifier: {namespace}/{key}")
        sequences[reference] = sequence
        metadata[reference] = {"reference_id": reference, "accession": str(accession or key),
            "family": str(label or "unassigned"), "comparison_group": "unassigned",
            "role": "reference", "review_status": "unreviewed", "source": source,
            "sequence_sha256": sequence_hash(sequence), "source_description": str(label),
            "source_model_type": str(model)}
        counts[namespace] += 1

    for source in ([] if preserve else sources):
        name, path = source["source"], Path(source["path"])
        emit("Preparing unreviewed screening references: " + name)
        before = sha256_file(path)
        if source["format"] == "card_json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("CARD reference database is not a JSON mapping")
            for model_id, model in data.items():
                if not isinstance(model, dict) or "model_sequences" not in model:
                    continue
                entries = model["model_sequences"].get("sequence", {})
                if not isinstance(entries, dict):
                    raise ValueError("Unsupported CARD sequence structure: " + str(model_id))
                for sid, entry in entries.items():
                    protein = entry.get("protein_sequence") or {}
                    sequence = protein.get("sequence")
                    if not sequence:
                        counts[name + "_entries_without_protein"] += 1
                        continue
                    add(name, f"{model_id}:{sid}", sequence, protein.get("accession"),
                        model.get("ARO_name", ""), path.as_uri() + f"#model={model_id};sequence={sid}",
                        model.get("model_type", ""))
        else:
            descriptions = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith(">"):
                    parts = line[1:].split(maxsplit=1)
                    if not parts:
                        raise ValueError("Empty screening reference header")
                    descriptions[parts[0]] = parts[1] if len(parts) > 1 else parts[0]
            for identifier, sequence in read_fasta(path).items():
                add(name, identifier, sequence, identifier, descriptions[identifier], path.as_uri())
        if not counts[name]:
            raise ValueError("No proteins extracted from screening reference source: " + name)
        if sha256_file(path) != before:
            raise ValueError("Reference source changed during catalogue preparation: " + str(path))
        source_provenance.append({**source, "sha256": before, "review_status": "unreviewed"})

    panel, seeds = _bundled_panel()
    if not preserve:
        for identifier, sequence in seeds.sequences.items():
            if identifier in sequences:
                raise ValueError("Bundled comparator identifier collides with screening catalogue")
            sequences[identifier] = sequence
            metadata[identifier] = dict(seeds.metadata[identifier])
    # Preserve the audit and profile declarations exactly; no inferred review
    # labels, comparison groups, anchors or outgroups are added here.
    copied_panel = destination / "reviewed-seeds"
    shutil.copytree(panel, copied_panel)
    if preserve:
        fasta, table = external_catalogue.fasta, external_catalogue.metadata_path
        catalog = external_catalogue
    else:
        fasta, table = destination / "references.faa", destination / "references.tsv"
        write_fasta(sequences, fasta)
        fields = sorted({key for row in metadata.values() for key in row})
        with table.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(metadata.values())
        catalog = load_catalog(fasta, table)
    motif_source = Path(str(files("basis").joinpath("resources", "motifs", "rules-v1.json")))
    motif = destination / "motif-rules.json"
    shutil.copyfile(_required_file(motif_source), motif)
    placement_source = Path(str(files("basis").joinpath("resources", "placement", "registry-v2.json")))
    placement = destination / "placement-registry-v2.json"
    shutil.copyfile(_required_file(placement_source), placement)
    updates = {"reference_fasta": str(fasta), "reference_metadata": str(table),
        "profiles_dir": str(copied_panel / "profiles"), "domain_database": str(pfam),
        "domain_scan": True, "motifs": True, "motif_rules": str(motif),
        "placement_registry": str(placement)}
    if _options(spec).get("enable_bounded_followup") is True:
        updates.update(auto_phylogeny=True, phylogeny=True, family_placement=True)
    provenance = {"provider": "deep_catalogue", "created_at": now(),
        "screening_counts": dict(counts), "reviewed_seed_count": 0 if preserve else len(seeds.sequences),
        "available_bundled_seed_count": len(seeds.sequences), "external_catalogue_preserved": preserve,
        "reference_count": len(catalog.sequences), "source_files": source_provenance,
        "panel_checksums_sha256": sha256_file(panel / "checksums.json"),
        "open_comparison_gaps": json.loads((panel / "coverage.json").read_text()),
        "pfam": {"path": str(pfam), "sha256": sha256_file(pfam)},
        "limitations": ["Imported screening references remain unreviewed and are not independent confirmation.",
            "Source-reviewed seeds retain their source evidence basis; they are not validated classifiers.",
            "CARD model reference sequences need not themselves carry resistance variants.",
            "Unsupported families and missing comparison groups retain existing abstention rules."],
        "files": _checksums(destination), "analyses_run": False}
    return {"module_updates": {}, "deep_updates": updates, "provenance": provenance}


def prepare_deep_resource(spec: dict, destination: Path, runner) -> dict:
    """Prepare one resource in a fresh caller-owned staging directory."""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("Deep resource staging directory must be empty: " + str(destination))
    provider = spec.get("provider") or spec.get("backend") or spec.get("id")
    if provider not in {"prosite", "prott5", "deep_catalogue"}:
        raise ValueError("Unsupported DeepSafe resource provider: " + str(provider))
    return {"prosite": _prosite, "prott5": _prott5, "deep_catalogue": _catalogue}[provider](
        spec, destination, runner)
