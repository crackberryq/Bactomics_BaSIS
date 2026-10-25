"""Explicit follow-up over verified saved screening inputs; no screening/setup."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re

from basis.aware import resolve_policy
from basis.config import ALL_MODULES, PATH_KEYS, default_config, merge_dict
from .deep_resources import config_input_paths
from .reporting import aggregate
from .setup import resolved_config, write_stable

SCHEMA = "basis-saved-screening-followup-v1"


def _read(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected a saved JSON object: " + str(path))
    return value


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _positive(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(label + " must be a positive integer")
    return value


def _validate_overlay(update, schema):
    unknown = set(update) - set(schema)
    if unknown:
        raise ValueError("Unknown follow-up settings: " + str(sorted(unknown)))
    for key, value in update.items():
        expected = schema[key]
        if isinstance(expected, bool) and not isinstance(value, bool):
            raise ValueError(key + " must be a YAML boolean")
        if key in PATH_KEYS and value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(key + " must be a nonempty path or null")


def _owned(root, relative, *, required=True):
    path = root / relative
    if not path.is_relative_to(root):
        raise ValueError("Path is outside the saved screening directory")
    for part in (path, *path.parents):
        if part == root.parent:
            break
        if part.is_symlink():
            raise ValueError("Follow-up input/output must not use a symlink: " + str(part))
    if required and not path.is_file():
        raise ValueError("Missing saved screening prerequisite: " + str(path)
                         + ". Complete or repair screening separately; follow-up will not rerun it.")
    return path


def verify_sources(sources):
    for row in sources:
        path = Path(row["path"])
        if not path.is_file() or _sha(path) != row["sha256"]:
            raise ValueError("Saved screening prerequisite is missing or changed: " + str(path)
                             + ". Complete or repair screening separately.")


class SavedFollowupContext:
    """One reviewed isolate; no discovery, native rules or setup dependencies."""

    followup_only = True
    deep_enabled = True

    def __init__(self, supplied, root):
        self.root = Path(root).resolve()
        allowed = {"isolate", "deep", "resource_receipts", "threads", "memory_mb"}
        if set(supplied) - allowed:
            raise ValueError("Follow-up configuration cannot change screening settings: "
                             + str(sorted(set(supplied) - allowed)))
        overlay = supplied.get("deep")
        if not isinstance(overlay, dict) or overlay.get("enabled") is not True:
            raise ValueError("Follow-up is disabled. Review the screening report, then explicitly set deep.enabled: true.")
        isolate = supplied.get("isolate")
        if not isinstance(isolate, str) or not isolate.strip():
            raise ValueError("Supply one reviewed isolate with --config isolate=/absolute/path")
        self.isolate = str((self.root / Path(isolate).expanduser()).resolve())
        self.safety = Path(self.isolate) / "safety"
        self.supplied = copy.deepcopy(supplied)
        sample, original, tracked = self._screening_snapshot()
        self.samples = {self.isolate: sample}
        self.snapshot = {"schema": SCHEMA, "sample_id": sample["sample_id"],
                         "sources": [{"path": str(p), "sha256": self.source_hashes[str(p)]}
                                     for p in dict.fromkeys(tracked)]}
        verify_sources(self.snapshot["sources"])
        schema = merge_dict(default_config()["deep"], original.get("deep", {}))
        schema.setdefault("domain_scan", True)
        schema.setdefault("executables", {})
        schema.setdefault("heartbeat_seconds", 60)
        _validate_overlay(overlay, schema)
        deep = merge_dict(original.get("deep", default_config()["deep"]), overlay)
        # Saved settings were recorded while follow-up could be disabled. Check
        # their types again before they can become active, without coercion.
        typed_schema = {**schema, **default_config()["deep"], "domain_scan": True}
        _validate_overlay(deep, typed_schema)
        _positive(deep.get("maximum_candidates", 5), "maximum_candidates")
        for key in ("maximum_attention_candidates", "maximum_exploratory_candidates",
                    "maximum_tree_candidates", "maximum_placement_candidates"):
            value = deep.get(key, default_config()["deep"][key])
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(key + " must be a nonnegative integer")
        for key in PATH_KEYS:
            if deep.get(key):
                deep[key] = str((self.root / Path(os.path.expandvars(str(deep[key]))).expanduser()).resolve())
        if deep.get("command_prefix"):
            raise ValueError("Follow-up uses Snakemake environments; command_prefix is unsupported")
        resolve_policy(deep)
        self.threads = _positive(supplied.get("threads", deep.get("threads", 4)), "threads")
        self.memory_mb = _positive(supplied.get("memory_mb", deep.get("memory_mb", 8000)), "memory_mb")
        deep.update(threads=self.threads, memory_mb=self.memory_mb)
        receipts = supplied.get("resource_receipts", [])
        if not isinstance(receipts, list) or any(not isinstance(p, str) or not p for p in receipts):
            raise ValueError("resource_receipts must be a list of existing receipt paths")
        receipts = [str((self.root / Path(p).expanduser()).resolve()) for p in receipts]
        effective = resolved_config({"deep": deep}, receipts, [])
        self.config = copy.deepcopy(original)
        self.config["deep"] = effective["deep"]
        self.config.setdefault("options", {})["deep"] = True
        self.config["followup_review"] = {**self.snapshot, "requested_config": copy.deepcopy(supplied),
                                          "resource_receipts": effective["resource_receipts"]}
        self._check_resources()
        self.prerequisites = list(dict.fromkeys([str(p) for p in tracked] + receipts + self.resource_paths))
        for relative in ("deepsafe/native", "deepsafe/states", "deepsafe/candidates",
                         "deepsafe/review/config.json", "deepsafe/review/inputs.json",
                         "evidence/deepsafe.json",
                         "deepsafe-report/report.html"):
            _owned(self.safety, relative, required=False)

    def _screening_snapshot(self):
        files = {key: _owned(self.safety, relative) for key, relative in {
            "sample": "metadata/sample.json", "config": "metadata/config.json",
            "validation": "metadata/validation.json", "ready": "metadata/setup.ready.json",
            "report": "report.html", "bundle": "evidence.json",
            "navigation": "metadata/report-navigation.json"}.items()}
        self.source_hashes = {str(path): _sha(path) for path in files.values()}
        sample, original = _read(files["sample"]), _read(files["config"])
        validation, ready = _read(files["validation"]), _read(files["ready"])
        bundle, navigation = _read(files["bundle"]), _read(files["navigation"])
        run = bundle.get("run", {})
        if run.get("schema") != "basis-snakemake-run-v1" or run.get("report_view") != "screening":
            raise ValueError("A saved screening-only report from this workflow is required; render it separately first.")
        if (navigation.get("report_sha256") != _sha(files["report"])
                or navigation.get("evidence_sha256") != _sha(files["bundle"])):
            raise ValueError("Screening report snapshot is stale or changed; render it separately first.")
        for key in ("sample", "config"):
            expected = run.get("report_inputs", {}).get(key, {})
            if expected.get("path") != str(files[key]) or expected.get("sha256") != _sha(files[key]):
                raise ValueError("Screening report does not match saved " + key + " metadata")
        hashes = validation.get("input_hashes", {})
        if (not isinstance(hashes, dict) or not {"assembly", "proteins", "gff"} <= set(hashes)
                or sample.get("input_validation") != validation
                or ready.get("status") != "completed" or ready.get("inputs") != hashes):
            raise ValueError("Saved screening input validation/setup snapshot is incompatible")
        tracked = list(files.values())
        for key, digest in hashes.items():
            path = Path(sample.get(key, ""))
            if (not path.is_file() or not isinstance(digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest) or _sha(path) != digest):
                raise ValueError("Screening annotation is missing or changed: " + key)
            tracked.append(path)
            self.source_hashes[str(path)] = digest
        self.records = []
        sources = run.get("evidence_sources")
        if not isinstance(sources, list):
            raise ValueError("Screening evidence source manifest is missing")
        for row in sources:
            module = row.get("module")
            if module not in ALL_MODULES:
                raise ValueError("Unexpected module in screening source manifest: " + str(module))
            path = _owned(self.safety, "evidence/" + module + ".json")
            if row.get("path") != str(path) or row.get("sha256") != _sha(path):
                raise ValueError("Saved screening evidence changed: " + module)
            record = _read(path)
            if record.get("input_hashes") != hashes:
                raise ValueError("Screening evidence input identity differs: " + module)
            self.records.append(str(path))
            tracked.append(path)
            self.source_hashes[str(path)] = row["sha256"]
        # Checks exact requested scope, module/sample identity, and completion.
        aggregate(sample, original, list(map(Path, self.records)), view="screening")
        return sample, original, tracked

    def _check_resources(self):
        deep = self.config["deep"]
        required = []
        if deep.get("domain_scan", True):
            required.append("domain_database")
        if deep.get("phylogeny", True) or deep.get("auto_phylogeny", False):
            required.extend(("reference_fasta", "reference_metadata"))
        if deep.get("motifs", True):
            required.append("motif_rules")
        if deep.get("family_placement", False):
            required.append("placement_registry")
        if deep.get("prosite_profiles", False):
            required.append("prosite_snapshot")
        if bool(deep.get("reference_fasta")) != bool(deep.get("reference_metadata")):
            raise ValueError("Reference FASTA and metadata must be configured together")
        missing = [key for key in required if not deep.get(key)]
        if missing:
            raise ValueError("Missing prepared DeepSafe resources: " + ", ".join(missing)
                             + ". Supply existing paths or resource_receipts; this entry point does not prepare databases.")
        paths = config_input_paths(deep)
        for key in ("reference_fasta", "reference_metadata", "domain_database"):
            if deep.get(key):
                path = Path(deep[key])
                if not path.is_file():
                    raise ValueError("Missing prepared DeepSafe resource: " + str(path))
                paths.append(str(path))
        if deep.get("prosite_snapshot"):
            directory = Path(deep["prosite_snapshot"])
            from basis.prosite import FILES
            names = [*FILES, "provenance.json"]
            if deep.get("prosite_profiles"):
                names.append("prorule.dat")
            if not directory.is_dir() or any(not (directory / name).is_file() for name in names):
                raise ValueError("Missing prepared DeepSafe PROSITE snapshot files: " + str(directory))
            paths.extend(str(directory / name) for name in names)
        if any(not Path(p).exists() for p in paths):
            raise ValueError("Missing configured DeepSafe resource; prepare it separately")
        self.resource_paths = sorted(set(paths))

    def module_config_path(self, isolate, module):
        if isolate != self.isolate or module != "deepsafe":
            raise ValueError("Saved follow-up cannot request screening configurations")
        return str(self.safety / "deepsafe/review/config.json")

    def followup_config_path(self, isolate):
        return self.module_config_path(isolate, "deepsafe")

    def setup_target(self, isolate):
        return [str(self.safety / "deepsafe/review/inputs.json")]

    def evidence_targets(self, isolate):
        return list(self.records)

    def deep_config(self, isolate):
        return copy.deepcopy(self.config["deep"])

    def env(self, module):
        if module != "deepsafe":
            raise ValueError("Saved follow-up cannot request screening environments")
        return str(self.root / "envs/deepsafe.yml")

    def followup_report_targets(self):
        return [str(self.safety / "deepsafe-report/report.html")]

    def publish(self):
        verify_sources(self.snapshot["sources"])
        for relative in ("deepsafe/review/config.json", "deepsafe/review/inputs.json"):
            _owned(self.safety, relative, required=False)
        write_stable(self.followup_config_path(self.isolate), self.config)
        write_stable(self.setup_target(self.isolate)[0], self.snapshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requested-json", required=True)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    SavedFollowupContext(json.loads(args.requested_json), args.root).publish()


if __name__ == "__main__":
    main()
