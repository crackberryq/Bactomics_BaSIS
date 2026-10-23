"""Read-only preflight for the standalone workflow; never deploys or runs tools."""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timezone
import html
import io
import json
from pathlib import Path

import yaml

from basis.config import merge_dict
from basis.inputs import validate_sample
from .context import SafetyContext
from .resources import SCHEMA as RESOURCE_SCHEMA, external_inputs, preparation_spec, verify_receipt


SCHEMA = "basis-doctor-v1"


def load_configuration(root, configfiles=()):
    """Use the Snakefile's base configuration, then ordered recursive overlays."""
    root = Path(root).resolve()
    files = [root / "config.yaml", *(Path(path).expanduser().resolve() for path in configfiles)]
    config = {}
    for path in files:
        supplied = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(supplied, dict):
            raise ValueError("Configuration must be a YAML mapping: " + str(path))
        config = merge_dict(config, supplied)
    return config, list(map(str, files))


def _resource(name, spec, context, verify):
    path = Path(context.resource_receipt(name))
    external = spec.get("mode") == "external"
    paths = external_inputs(spec) if external else [str(path.parent / "data")]
    row = {"name": name, "targets": spec.get("targets", []),
           "mode": "external" if external else "managed", "receipt": str(path),
           "paths": [{"path": item, "exists": Path(item).exists()} for item in paths],
           "status": "not_prepared", "inventory_verified": False,
           "request_match": "not_checked", "preparation_required": not path.is_file(),
           "download_possible_on_preparation": not external and name != "minced"
               and context.resource_policy == "auto", "errors": [], "warnings": []}
    if external and any(not item["exists"] for item in row["paths"]):
        row["errors"].append("Configured external resource path is missing")
        row["status"] = "missing_external"
    if not path.is_file():
        if external and not row["errors"]:
            row["status"] = "external_present_unverified"
        row["warnings"].append("No completed preparation receipt; workflow setup is required")
        return row
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or receipt.get("schema") != RESOURCE_SCHEMA or receipt.get("status") != "completed":
            raise ValueError("Receipt does not record completed resource preparation")
        identity = receipt.get("preparation_identity") or {}
        if not isinstance(identity, dict):
            raise ValueError("Resource preparation identity must be a mapping")
        recorded = identity.get("spec")
        if recorded is None:
            row["request_match"] = "unavailable_legacy_receipt"
            row["warnings"].append("Legacy receipt cannot establish a match to current preparation settings")
        elif recorded != preparation_spec(spec):
            row["request_match"] = "different"
            row["preparation_required"] = True
            row["warnings"].append("Preparation settings differ from the recorded resource request")
        else:
            row["request_match"] = "spec_matches"
        if verify:
            verify_receipt(path, full=True)
            row["inventory_verified"] = True
            if not row["errors"]:
                row["status"] = "inventory_verified"
        elif not row["errors"]:
            row["status"] = "receipt_recorded_unverified"
            row["warnings"].append("Artifact contents were not checked; use --verify-resources to hash them")
        # A receipt for another selected path must not make missing explicit input look ready.
        if any(not item["exists"] for item in row["paths"]):
            row["errors"].append("A selected resource path is missing despite its saved receipt")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        row["status"] = "invalid_receipt"
        row["errors"].append(str(exc))
    return row


def inspect_configuration(config, root, *, validate_inputs=False, verify_resources=False):
    """Inspect configuration, local records and optionally their bytes, without writes.

    This is a preflight report, not a replacement for Snakemake's dependency
    evaluation or native installation checks. A saved receipt is not live proof
    of an executable's compatibility or an unchanged installed environment.
    """
    report = {"schema": SCHEMA, "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "workflow_root": str(Path(root).resolve()), "status": "blocked",
              "read_only": True, "tools_executed": False, "downloads_performed": False,
              "checks": {"input_consistency": bool(validate_inputs),
                         "resource_artifact_checksums": bool(verify_resources)},
              "samples": [], "resources": [], "environments": [],
              "errors": [], "warnings": [],
              "limitations": ["Resource receipt verification checks recorded artifacts, not native tool compatibility.",
                  "Recorded environment receipts are inspected without executing their environments.",
                  "Preparation predictions do not replace a Snakemake dry-run; changed source or environments may schedule work.",
                  "Manual resource policy does not disable Conda deployment or separately configured reference downloads."]}
    try:
        context = SafetyContext(copy.deepcopy(config), root)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        report["errors"].append(str(exc))
        return report
    report["resource_policy"] = context.resource_policy
    report["resource_root"] = context.resource_root
    report["execution"] = {"configured_threads": context.threads,
        "configured_memory_mb": context.memory_mb,
        "device": context.config.get("execution", {}).get("device", "cpu"),
        "scheduler_allocation": "Not probed; Snakemake --cores/--resources may cap these settings"}
    for isolate, sample in context.samples.items():
        row = {"sample_id": sample["sample_id"], "isolate_dir": isolate,
               "inputs": {key: {"path": sample[key], "exists": Path(sample[key]).is_file()}
                          for key in ("assembly", "proteins", "gff", "genbank") if sample.get(key)},
               "input_validation": {"status": "not_requested"}, "modules": [], "errors": []}
        for name, decision in context.decisions[isolate].items():
            settings = context.module_config(isolate, name)
            row["modules"].append({"module": name, "action": decision["action"],
                "reason": decision.get("reason"),
                "settings": {key: settings[key] for key in
                             ("threads", "memory_mb", "timeout_seconds", "gpus", "execution_scope", "database")
                             if key in settings}})
        if validate_inputs:
            try:
                value = validate_sample(sample)
                row["input_validation"] = {"status": "passed", **{key: value[key] for key in
                    ("contigs", "assembly_bp", "protein_count", "input_hashes", "warnings") if key in value}}
            except (OSError, ValueError, TypeError, KeyError) as exc:
                row["input_validation"] = {"status": "failed", "error": str(exc)}
                row["errors"].append(str(exc))
        report["samples"].append(row)
    for name, spec in sorted(context.resource_specs().items()):
        report["resources"].append(_resource(name, spec, context, verify_resources))
    for path in context.environment_receipts():
        row = {"receipt": path, "status": "not_recorded", "executables_checked": False}
        if Path(path).is_file():
            try:
                value = json.loads(Path(path).read_text(encoding="utf-8"))
                if not isinstance(value, dict) or value.get("schema") != "basis-snake-environment-v1" or value.get("status") != "completed":
                    raise ValueError("Environment receipt does not record completed setup")
                prefix = value.get("prefix")
                exists = isinstance(prefix, str) and Path(prefix).is_dir()
                row.update(status="recorded_unverified" if exists else "missing_prefix",
                           prefix=prefix, prefix_exists=exists)
            except (OSError, ValueError, TypeError) as exc:
                row.update(status="invalid_receipt", error=str(exc))
                report["errors"].append(str(exc) + ": " + path)
        report["environments"].append(row)
    report["deep"] = {"enabled": context.deep_enabled,
        "threads": context.config["deep"].get("threads"),
        "memory_mb": context.config["deep"].get("memory_mb"),
        "timeout_seconds": context.config["deep"].get("timeout_seconds"),
        "reference_downloads_allowed": bool(context.deep_enabled and
                                             context.config["deep"].get("placement_allow_download", False))}
    if report["deep"]["reference_downloads_allowed"]:
        report["warnings"].append("Optional downstream reference downloads are allowed by the effective DeepSafe configuration")
    if not validate_inputs:
        report["warnings"].append("Input paths were checked; sequence/annotation consistency was not checked")
    if not context.samples:
        report["warnings"].append("No samples selected; this report covers setup resources only")
    errors = report["errors"] + [error for row in report["samples"] + report["resources"] for error in row["errors"]]
    needs_setup = any(row["preparation_required"] for row in report["resources"]) or any(
        row["status"] in {"not_recorded", "missing_prefix"} for row in report["environments"])
    report["status"] = "blocked" if errors else "needs_setup" if needs_setup else "checked"
    return report


def render_text(report):
    lines = ["BaSIS preflight: " + report["status"],
             "Read-only: no tools, downloads or workflow changes.",
             "Resource policy: " + report.get("resource_policy", "configuration invalid")]
    for row in report["samples"]:
        active = ", ".join(item["module"] for item in row["modules"] if item["action"] == "run")
        lines.append(f"Sample {row['sample_id']}: {active or 'no runnable modules'}")
        for name, item in row["inputs"].items():
            lines.append(f"  {name}: {item['path']}")
        validation = row["input_validation"]
        lines.append("  Input consistency: " + validation["status"])
        if validation["status"] == "passed":
            lines.append(f"  Assembly: {validation.get('contigs')} records, {validation.get('assembly_bp')} bases")
        lines.extend("  ERROR: " + error for error in row["errors"])
    for row in report["resources"]:
        risk = "; download possible if preparation runs" if row["download_possible_on_preparation"] else "; no resource download planned"
        lines.append(f"Resource {row['name']}: {row['status']}{risk}")
        lines.extend("  " + item["path"] for item in row["paths"])
        lines.extend("  ERROR: " + error for error in row["errors"])
        lines.extend("  Note: " + value for value in row["warnings"])
    for row in report["environments"]:
        lines.append(f"Environment {Path(row['receipt']).stem}: {row['status']}")
    lines.extend("ERROR: " + error for error in report["errors"])
    lines.extend("Note: " + value for value in report["warnings"])
    lines.append("Environment executables were not tested; inspect JSON for recorded setup receipts.")
    return "\n".join(lines)


def write_report(report, directory):
    """Create an explicitly requested report directory; never overwrite anything."""
    directory = Path(directory).expanduser().resolve()
    protected = [report.get("resource_root")] + [item["isolate_dir"] for item in report["samples"]]
    if any(value and directory.is_relative_to(Path(value).resolve()) for value in protected):
        raise ValueError("Report directory must be outside resource and isolate directories")
    text = render_text(report)
    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow(["kind", "name", "status", "path"])
    for row in report["resources"]:
        for item in row["paths"]:
            writer.writerow(["resource", row["name"], row["status"], item["path"]])
    for row in report["samples"]:
        for name, item in row["inputs"].items():
            writer.writerow(["input", row["sample_id"] + ":" + name,
                             "present" if item["exists"] else "missing", item["path"]])
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "doctor.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (directory / "doctor.tsv").write_text(output.getvalue(), encoding="utf-8")
    (directory / "doctor.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>BaSIS preflight</title>"
        "<style>body{max-width:1000px;margin:2rem auto;font:16px system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
        "<h1>BaSIS preflight</h1><pre>" + html.escape(text) + "</pre>", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--configfile", type=Path, action="append", nargs="+", default=[],
                        help="Ordered overlays; base config.yaml is loaded first. Repeatable.")
    parser.add_argument("--validate-inputs", action="store_true", help="Read annotations and check consistency (may take time)")
    parser.add_argument("--verify-resources", action="store_true", help="Hash all artifacts in existing resource receipts (may take time)")
    parser.add_argument("--json", action="store_true", help="Print the complete machine-readable report")
    parser.add_argument("--outdir", type=Path, help="Explicitly create a new directory containing JSON, TSV and HTML reports")
    args = parser.parse_args(argv)
    try:
        config, files = load_configuration(args.workflow_root, [p for group in args.configfile for p in group])
        report = inspect_configuration(config, args.workflow_root, validate_inputs=args.validate_inputs,
                                       verify_resources=args.verify_resources)
        report["configfiles"] = files
        if args.outdir:
            write_report(report, args.outdir)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, "Preflight error: " + str(exc) + "\n")
    print(json.dumps(report, indent=2, allow_nan=False) if args.json else render_text(report))
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
