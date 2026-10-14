#!/usr/bin/env python3
"""Read-only normalization acceptance for installed BaSIS 0.2.0rc13.

Uses synthetic records and, by default, three checksum-pinned saved snapshots.
No native tools, reference downloads, inference, or original input reads occur.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import statistics
import sys
import tempfile
import traceback
import zipfile

ROOT = Path(__file__).resolve().parent
RELEASE = "0.2.0rc13"
SAMPLE_IDS = ("isolate4", "isolate5", "isolate6")
EXPECTED_PINS_SHA256 = "0e5733508855cc2371d409e003e335f3a8b2655422cad8ae1f54764b20799365"
COORDINATES = ("contig", "start", "end", "strand")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def object_sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_identity(path, content=None):
    path = Path(path)
    if content is not None:
        return {"path": str(path), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return {"path": str(path), "bytes": size, "sha256": digest.hexdigest()}


def reject_constant(value):
    raise ValueError("Non-finite JSON constant: " + value)


def load_verified(pin):
    require(isinstance(pin, dict) and set(pin) >= {"path", "bytes", "sha256"}, "Invalid evidence pin")
    path = Path(pin["path"])
    require(path.is_absolute(), "Pinned evidence path must be absolute")
    content = path.read_bytes()
    actual = file_identity(path, content)
    require(actual == {k: pin[k] for k in ("path", "bytes", "sha256")},
            "Pinned evidence bytes changed: " + str(path))
    return json.loads(content, parse_constant=reject_constant), actual


def installed_runtime():
    import basis
    require(basis.__version__ == RELEASE, f"Expected installed BaSIS {RELEASE}; found {basis.__version__}")
    prefix = Path(sys.prefix).resolve()
    package = Path(basis.__file__).resolve().parent
    require(package.is_relative_to(prefix), "BaSIS must import from the active installed prefix")
    release = importlib.import_module("basis.release")
    require(Path(release.__file__).resolve().parent == package, "Release verifier belongs to another package")
    verify_install = getattr(release, "verify_install", None)
    require(callable(verify_install), "Installed package lacks its release verifier")
    verification = verify_install()
    require(verification.get("status") == "passed", "Installed package integrity verification did not pass")
    normalize = importlib.import_module("basis.normalize")
    require(Path(normalize.__file__).resolve().parent == package, "Normalizer belongs to another package")
    manifest = file_identity(package / release.MANIFEST)
    return normalize.normalize_result, {
        "basis_version": basis.__version__, "package_path": str(package),
        "environment_prefix": str(prefix), "python_executable": sys.executable,
        "python_version": sys.version, "verification": verification,
        "manifest": manifest, "runtime_files_sha256": object_sha(release.runtime_hashes(package)),
    }


def validate_saved_inputs(evidence, sid):
    require(isinstance(evidence, dict) and evidence.get("schema_version") == "basis-report-1", "Unexpected evidence schema")
    samples = evidence.get("samples")
    require(isinstance(samples, list) and len(samples) == 1 and isinstance(samples[0], dict)
            and samples[0].get("sample_id") == sid, "Pinned sample identity mismatch")
    validation = evidence.get("run", {}).get("input_validations", {}).get(sid)
    require(isinstance(validation, dict) and validation.get("sample_id") == sid, "Missing saved sample validation")
    lengths = validation.get("contig_lengths")
    coordinates = validation.get("protein_coordinates")
    aliases = validation.get("contig_aliases")
    require(isinstance(lengths, dict) and bool(lengths)
            and all(isinstance(k, str) and k and type(v) is int and v > 0 for k, v in lengths.items()),
            "Invalid saved contig lengths")
    require(isinstance(coordinates, dict), "Missing saved protein coordinates")
    for pid, row in coordinates.items():
        require(isinstance(pid, str) and pid and isinstance(row, dict), "Invalid saved protein coordinate row")
        require(isinstance(row.get("contig"), str) and row["contig"] in lengths
                and type(row.get("start")) is int and type(row.get("end")) is int
                and 1 <= row["start"] <= row["end"] <= lengths[row["contig"]]
                and row.get("strand") in {"+", "-"}, "Invalid saved coordinates for " + pid)
    require(isinstance(aliases, dict) and all(isinstance(k, str) and k and isinstance(v, str)
            and v in lengths for k, v in aliases.items()), "Invalid saved contig aliases")
    require(type(validation.get("protein_count")) is int and validation["protein_count"] == len(coordinates)
            and type(validation.get("contigs")) is int and validation["contigs"] == len(lengths),
            "Saved validation inventory count mismatch")
    return samples[0], validation


def synthetic_checks(normalize):
    sample = {"sample_id": "SYNTHETIC"}
    feature = {"contig": "chr", "start": 10, "end": 99, "strand": "+"}
    validation = {"protein_coordinates": {"P": feature}, "contig_lengths": {"chr": 1000},
                  "contig_aliases": {"chr": "chr", "accession.1": "chr"}}
    cases = [
        ("protein_only", {}, {k: None for k in COORDINATES}, None),
        ("self_alias", {"contig": "chr", "start": 12, "end": 90, "strand": "+"},
         {"contig": "chr", "start": 12, "end": 90, "strand": "+"}, None),
        ("native_alias", {"contig": "accession.1", "start": 12, "end": 90, "strand": "+"},
         {"contig": "chr", "start": 12, "end": 90, "strand": "+"}, "accession.1"),
    ]
    results = []
    for name, raw_coordinates, expected_reported, expected_alias in cases:
        raw = {"interpretation": "detected", "findings": [{"category": "amr", "protein_id": "P",
               "gene": "synthetic", "reference": "fixture", "identity": 81.5, "coverage": 97.9,
               "attributes": {"synthetic": True}, **raw_coordinates}]}
        before = object_sha(raw)
        first = normalize(raw, "rgi", sample, validation)
        second = normalize(first, "rgi", sample, validation)
        finding = first["findings"][0]
        require(object_sha(raw) == before, name + ": input record mutated")
        require(canonical(first) == canonical(second), name + ": second normalization changed result")
        require({k: finding[k] for k in COORDINATES} == feature, name + ": canonical coordinates changed")
        require(finding["attributes"].get("reported_coordinates") == expected_reported,
                name + ": first-reported coordinate provenance changed")
        require(finding["attributes"].get("native_contig_alias") == expected_alias,
                name + ": incorrect native contig alias")
        require(finding["identity"] == 81.5 and finding["coverage"] == 97.9, name + ": scores changed")
        results.append({"case": name, "status": "passed", "normalized_sha256": object_sha(first),
                        "finding_id": finding["finding_id"]})
    members = [.1, .2, .3, .4]
    raw = {"interpretation": "detected", "findings": [], "metrics": {
        "ensemble_member_scores": members, "prediction_mean": statistics.mean(members),
        "prediction_std": statistics.pstdev(members)}}
    first = normalize(raw, "pathogenfinder2", sample, validation)
    second = normalize(first, "pathogenfinder2", sample, validation)
    require(first["interpretation"] == "inconclusive" and first["metrics"] == raw["metrics"],
            "PF2 wrapper interpretation or prediction preservation failed")
    require(canonical(first) == canonical(second), "PF2 second normalization changed result")
    results.append({"case": "pf2_wrapper", "status": "passed", "normalized_sha256": object_sha(first)})
    return results


def replay_evidence(evidence, sid, normalize):
    sample, validation = validate_saved_inputs(evidence, sid)
    records = evidence.get("records")
    require(isinstance(records, list), "Missing saved module records")
    output = {"sample_id": sid, "modules": [], "skipped_records": [], "finding_count": 0,
              "deep_records_replayed": False, "validation_sha256": object_sha(validation)}
    modules = set()
    for record in records:
        require(isinstance(record, dict) and record.get("sample_id") == sid, "Unexpected module sample identity")
        module = record.get("module", record.get("tool"))
        require(isinstance(module, str) and module and module not in modules, "Missing or duplicate module identity")
        modules.add(module)
        if record.get("execution_status") != "completed":
            output["skipped_records"].append({"module": module, "execution_status": record.get("execution_status")})
            continue
        require(isinstance(record.get("findings"), list), "Completed module lacks findings: " + module)
        original_digest = object_sha(record)
        findings_digest = object_sha(record["findings"])
        first = normalize(record, module, sample, validation)
        require(object_sha(record) == original_digest, module + ": saved in-memory record mutated")
        require(canonical(first.get("findings")) == canonical(record["findings"]),
                module + ": normalization changed saved findings, including scores, coordinates, provenance or IDs")
        first_digest = object_sha(first)
        second = normalize(first, module, sample, validation)
        require(object_sha(first) == first_digest, module + ": first-pass record mutated")
        require(canonical(first) == canonical(second), module + ": second normalization changed full result")
        require(canonical(first.get("metrics")) == canonical(record.get("metrics")), module + ": saved metrics changed")
        require(first.get("interpretation") == record.get("interpretation"), module + ": saved interpretation changed")
        if module == "pathogenfinder2":
            require(first.get("interpretation") == "inconclusive", "Saved PF2 wrapper must remain inconclusive")
        output["modules"].append({"module": module, "status": "passed", "finding_count": len(record["findings"]),
            "saved_findings_sha256": findings_digest, "replayed_findings_sha256": object_sha(first["findings"]),
            "saved_record_sha256": original_digest, "first_pass_sha256": first_digest,
            "second_pass_sha256": object_sha(second), "saved_findings_unchanged": True,
            "full_second_pass_unchanged": True})
        output["finding_count"] += len(record["findings"])
    require(bool(output["modules"]), "No completed modules were available to replay")
    output["completed_modules_replayed"] = len(output["modules"])
    return output


def write_summary(out, summary):
    path = out / "summary.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    archive = out / "normalization-acceptance-summary.zip"
    row = file_identity(path)
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(path, "summary.json")
        bundle.writestr("UPLOAD_MANIFEST.json", json.dumps({"files": {"summary.json": {
            k: row[k] for k in ("bytes", "sha256")}}}, indent=2))
    return archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-only", action="store_true", help="Do not read the three pinned pilot snapshots")
    parser.add_argument("--outdir-parent", type=Path, default=ROOT, help="Existing directory for fresh acceptance output")
    args = parser.parse_args(argv)
    out = Path(tempfile.mkdtemp(prefix="normalization-acceptance-", dir=args.outdir_parent))
    print("READ ONLY: normalization replay; no native tools or inference.", flush=True)
    print("OUTPUT:", out, flush=True)
    summary = {"schema": "basis-normalization-acceptance-v1", "status": "running", "started_at_utc": now(),
        "synthetic_only": args.synthetic_only, "sources": {}, "samples": {}, "native_analyses_run": False,
        "biological_validation": "not_performed", "helper": file_identity(Path(__file__).resolve()),
        "limitations": ["Checks normalization preservation and repeatability only; no tools or biological analyses are rerun.",
                        "Saved validation dictionaries are reused after evidence checksum and shape verification; original inputs are not reread.",
                        "Only completed top-level module records are replayed; DeepSafe and noncompleted records are not recomputed.",
                        "No historical evidence, findings, IDs, or reports are rewritten."]}
    seen, runtime = [], None
    try:
        normalize, runtime = installed_runtime()
        summary["installed_package"] = runtime
        print("Installed package verified. Checking synthetic provenance cases...", flush=True)
        summary["synthetic_checks"] = synthetic_checks(normalize)
        if not args.synthetic_only:
            pin_path = ROOT / "validation/normalization-pilot-pins.json"
            pin_bytes = pin_path.read_bytes()
            pin_identity = file_identity(pin_path, pin_bytes)
            require(pin_identity["sha256"] == EXPECTED_PINS_SHA256, "Frozen pilot pins file changed")
            seen.append(pin_identity)
            summary["pins_file"] = pin_identity
            pins = json.loads(pin_bytes, parse_constant=reject_constant)
            require(pins.get("schema") == "basis-normalization-pilot-pins-v1"
                    and isinstance(pins.get("samples"), dict) and set(pins["samples"]) == set(SAMPLE_IDS),
                    "Expected the three frozen pilot evidence pins")
            for sid in SAMPLE_IDS:
                print(sid + ": verifying saved evidence and replaying completed module records...", flush=True)
                expected = pins["samples"][sid]
                require(isinstance(expected, dict) and set(expected) >= {"path", "bytes", "sha256"},
                        "Invalid pilot evidence pin")
                seen.append({k: expected[k] for k in ("path", "bytes", "sha256")})
                evidence, source = load_verified(pins["samples"][sid])
                summary["sources"][sid] = source
                summary["samples"][sid] = replay_evidence(evidence, sid, normalize)
                print(sid + ": " + str(summary["samples"][sid]["finding_count"]) + " saved findings unchanged", flush=True)
                del evidence
        summary["status"] = "passed"
    except (Exception, KeyboardInterrupt) as exc:
        summary["status"] = "failed"
        summary["error"] = type(exc).__name__ + ": " + str(exc)
        traceback.print_exc()
    finally:
        try:
            require(all(file_identity(row["path"]) == row for row in seen), "Pinned source changed during replay")
            summary["source_files_unchanged"] = True
        except (Exception, KeyboardInterrupt) as exc:
            summary["status"] = "failed"
            summary["final_verification_error"] = type(exc).__name__ + ": " + str(exc)
            traceback.print_exc()
        if runtime is not None:
            try:
                _, after = installed_runtime()
                require(after == runtime, "Installed package changed during replay")
                summary["installed_package_unchanged"] = True
            except (Exception, KeyboardInterrupt) as exc:
                summary["status"] = "failed"
                summary["final_package_verification_error"] = type(exc).__name__ + ": " + str(exc)
                traceback.print_exc()
    summary["finished_at_utc"] = now()
    archive = write_summary(out, summary)
    print("STATUS:", summary["status"], flush=True)
    print("UPLOAD:", archive, flush=True)
    return 0 if summary["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
