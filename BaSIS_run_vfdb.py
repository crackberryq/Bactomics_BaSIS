#!/usr/bin/env python3
"""Add the prepared VFDB Set A screen to Jimi's existing BaSIS AMR study.

Run from the active basis environment after copying this file into the BaSIS
checkout. No database download, rebuild, or package-source changes are made.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys


SNAPSHOT = "setA-20261007T204847989999Z/utf8-20261007T205409255854Z"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def without_config_path(config: dict) -> dict:
    config = copy.deepcopy(config)
    config.pop("config_path", None)
    return config


def prepare_config(project: Path, database_dir: Path) -> tuple[Path, dict]:
    import yaml
    from basis.config import load_config

    source = project / "study-isolate1/config.amr.yaml"
    target = source.with_name("config.amr-vfdb.yaml")
    config = without_config_path(load_config(source))
    enabled = {name for name, cfg in config["modules"].items()
               if cfg.get("enabled") and name != "pathogenfinder2"}
    if enabled - {"amrfinder", "rgi", "vfdb"}:
        raise ValueError("The AMR config enables unexpected modules: "
                         + ", ".join(sorted(enabled - {"amrfinder", "rgi", "vfdb"})))

    database_dir = database_dir.resolve()
    index = database_dir / "vfdb_setA.dmnd"
    manifest = database_dir / "provenance.json"
    if not index.is_file() or index.stat().st_size == 0:
        raise FileNotFoundError(f"Prepared VFDB index missing or empty: {index}")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    expected = metadata.get("files", {}).get(index.name, {}).get("sha256")
    if not isinstance(expected, str) or sha256(index) != expected.lower():
        raise ValueError("VFDB index SHA256 does not match its preparation manifest.")
    version = metadata.get("database_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("VFDB manifest has no database_version.")

    env = project / ".basis-envs/basis-rgi"
    if not os.access(env / "bin/diamond", os.X_OK):
        raise FileNotFoundError(f"DIAMOND executable not found in {env}")
    config["modules"]["vfdb"].update(
        enabled=True, mode="native", executable="diamond",
        database=str(index), database_manifest=str(manifest),
        database_version=version, version_args=["version"],
        command_prefix=["conda", "run", "--no-capture-output", "-p", str(env)],
    )

    # Keep the original config and all existing AMR settings intact.
    if target.exists():
        existing = without_config_path(load_config(target))
        if existing != config:
            raise ValueError(f"{target} already contains different settings; "
                             "leaving it unchanged. Share this message for review.")
    else:
        with target.open("x", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, sort_keys=False)
    return target, load_config(target)


def print_summary(result: dict, code: int, enabled: list[str]) -> None:
    print(f"\nExit code: {code}")
    print("Run status:", result["run"]["completeness"])
    evidence_path = Path(result["artifacts"]["evidence_json"])
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    for record in evidence["records"]:
        module = record["module"]
        if module not in enabled:
            continue
        status = record.get("execution_status", "unknown")
        print(f"[{record['sample_id']}] {module} | status: {status}"
              f" | reused: {record.get('cache_hit', False)}")
        if status != "completed":
            print("  Error:", record.get("error", record.get("reason", "See evidence")))
            for artifact in record.get("artifacts", []):
                if isinstance(artifact, str) and Path(artifact).name == "commands.log":
                    print("  Log:", artifact)
            continue
        findings = record.get("findings", [])
        if module != "vfdb":
            print("  Finding records:", len(findings))
            continue
        passing = [f for f in findings
                   if f.get("attributes", {}).get("passes_threshold") is True]
        proteins = {f["protein_id"] for f in passing if f.get("protein_id")}
        print("  Alignment records:", len(findings))
        print("  Records passing configured thresholds:", len(passing))
        print("  Distinct proteins with passing homology matches:", len(proteins))
    print("Evidence:", evidence_path)
    print("Report:", result["artifacts"]["html"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--database-dir", type=Path,
                        help="Directory containing vfdb_setA.dmnd and provenance.json")
    parser.add_argument("--prepare-only", action="store_true",
                        help="Verify resources and write the config without running analyses")
    args = parser.parse_args(argv)
    project = args.project.expanduser().resolve()
    if not (project / "basis/config.py").is_file():
        print("ERROR: Copy this launcher into the BaSIS checkout before running it.",
              file=sys.stderr)
        return 2
    sys.path.insert(0, str(project))
    try:
        from basis.pipeline import make_plan, run_pipeline

        database_dir = (args.database_dir.expanduser() if args.database_dir else
                        project.parent / "db/vfdb" / SNAPSHOT)
        target, config = prepare_config(project, database_dir)
        print("Config:", target, flush=True)
        if args.prepare_only:
            print("Prepared; no analyses were run.")
            return 0
        print("Running AMR + VFDB assessment (using valid caches where available)...",
              flush=True)
        plan = make_plan(config, deep=False, extend=False)
        result, code = run_pipeline(plan, engine="local", force=False)
        print_summary(result, code, plan["enabled_modules"])
        return code
    except KeyboardInterrupt:
        print("\nInterrupted. Re-running this launcher can reuse completed results.",
              file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
