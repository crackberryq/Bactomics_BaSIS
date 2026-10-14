#!/usr/bin/env python3
"""Run the inspected three-isolate BaSIS pilot without changing BaSIS source.

Default: prepare a new study and run samples sequentially with verbose progress.
--prepare-only: write configurations/audit/index without native analyses.
--resume PATH: retry unfinished samples from this helper's existing study.
This is an operational pilot, not independent biological validation.
"""
from __future__ import annotations

import argparse
import base64
import copy
import csv
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
import uuid
import zipfile
import zlib

INSPECTION_B64 = "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"
SCHEMA = "basis-pilot456-runner-v1"
FIELDS = ["sample_id", "assembly", "proteins", "gff", "genbank", "taxon",
          "amrfinder_organism", "phenotypes", "taxon_rank", "taxon_source", "hybas_dir"]


def inspection():
    return json.loads(zlib.decompress(base64.b64decode(INSPECTION_B64)))


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def say(message):
    print(f"[{datetime.now().astimezone():%H:%M:%S}] {message}", flush=True)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def check_hash(path, expected):
    path = Path(path)
    if not path.is_file() or sha(path) != expected:
        raise ValueError(f"File missing or changed since inspection/preparation: {path}")


def verify_inspection(data):
    if data.get("schema") != "basis-pilot456-input-review-v1":
        raise ValueError("Unexpected inspection schema")
    if [s["sample_id"] for s in data["samples"]] != ["isolate4", "isolate5", "isolate6"]:
        raise ValueError("Unexpected inspection sample IDs")
    for captured in data["configurations"]:
        check_hash(captured["path"], captured["sha256"])
    for sample in data["samples"]:
        say(f"{sample['sample_id']}: verifying inspected input and HybAs metadata checksums")
        if sample.get("errors") or sample.get("file_consistency") != "passed":
            raise ValueError(f"Inspection did not pass for {sample['sample_id']}")
        for key, digest in sample["validation"]["input_hashes"].items():
            check_hash(sample["paths"][key], digest)
        for source in sample["hybas_context"].get("sources", []):
            check_hash(source["path"], source["sha256"])
        binding = sample["hybas_context"].get("binding", {})
        if binding.get("final_assembly") and binding.get("final_assembly_sha256"):
            check_hash(binding["final_assembly"], binding["final_assembly_sha256"])


def source_identity(project):
    import basis
    root = Path(basis.__file__).resolve().parent
    if root != (project / "basis").resolve():
        raise ValueError(f"Python imports BaSIS from {root}, expected {project / 'basis'}")
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*.py"))}


def report_phylum(path, target):
    """Read the target's ancestor from a standard six-column Kraken report.

    The hierarchy comes from indentation; no genus-to-phylum lookup is guessed.
    A missing/unindented phylum is handled as unknown.
    """
    ancestors = []
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        for line, row in enumerate(csv.reader(stream, delimiter="\t"), 1):
            if not row:
                continue
            if len(row) != 6:
                raise ValueError(f"Malformed Kraken report at {path}:{line}")
            name = row[5]
            depth = len(name) - len(name.lstrip(" "))
            while ancestors and ancestors[-1][0] >= depth:
                ancestors.pop()
            rank, taxid = row[3].strip(), row[4].strip()
            node = {"rank": rank, "taxid": taxid, "name": name.strip()}
            ancestors.append((depth, node))
            if taxid == target:
                if int(row[1]) <= 0:
                    return None
                return next((v for _, v in reversed(ancestors) if v["rank"] == "P"), None)
    return None


def ice_scope(sample):
    target = str(sample["hybas_context"].get("selected_target", {}).get("taxid") or "")
    evidence = []
    if target:
        for source in sample["hybas_context"].get("sources", []):
            if source["role"] in {"illumina_report", "nanopore_report"}:
                evidence.append({"path": source["path"],
                                 "phylum": report_phylum(source["path"], target)})
    known = [e["phylum"] for e in evidence if e["phylum"]]
    supported = bool(evidence) and len(known) == len(evidence) and all(
        p["taxid"] == "1239" and p["name"].casefold() in {"bacillota", "firmicutes"}
        for p in known)
    return {"enabled": supported, "target_taxid": target, "evidence": evidence,
            "reason": ("Bacillota ancestry recorded for the operational target in all inspected reports"
                       if supported else "ICEscreen Bacillota scope not supported by the inspected target ancestry")}


def build_config(template, sample, directory, batch, scope):
    cfg = copy.deepcopy(template)
    sid = sample["sample_id"]
    cfg.pop("config_path", None)
    cfg.update(project=f"BaSIS operational pilot — {sid}",
               samples=str(directory / "samples.tsv"), outdir=str(directory / "results"))
    for module in cfg["modules"].values():
        module["mode"] = "native"
        module.pop("result", None)
    pf2 = cfg["modules"]["pathogenfinder2"]
    pf2.pop("embeddings", None)
    pf2.pop("embeddings_manifest", None)
    deep = cfg["deep"]
    deep["tree_review_registry"] = None
    deep["circular_contigs"] = []
    deep["placement_cache_dir"] = str(batch / "placement-cache")
    ice = cfg["modules"]["icescreen"]
    ice["enabled"] = bool(ice.get("enabled") and scope["enabled"])
    if ice["enabled"]:
        ice["phylum"] = "bacillota"
        ice.pop("disabled_reason", None)
    else:
        ice.pop("phylum", None)
        ice["disabled_reason"] = scope["reason"]
    if sid == "isolate6":
        cfg["project"] += " — limited-input screening"
        pf2["enabled"] = False
        pf2["disabled_reason"] = (
            "Whole-organism ML prediction withheld: unresolved taxonomy, 29 annotated proteins "
            "and HybAs bacteria_odb10 completeness 0/124; input requires review")
    return cfg


def check_resources(cfg):
    from basis.config import PATH_KEYS
    keys = set(PATH_KEYS) | {"placement_registry"}
    skip = {"result", "placement_cache_dir", "tree_review_registry", "embeddings", "embeddings_manifest"}
    sections = [(n, c) for n, c in cfg["modules"].items() if c.get("enabled")]
    sections.append(("deep", cfg["deep"]))
    for name, section in sections:
        for key in sorted(keys - skip):
            value = section.get(key)
            if isinstance(value, str) and value:
                path = Path(value)
                if not path.is_absolute() or not path.exists():
                    raise ValueError(f"{name}.{key}: missing or non-absolute resource: {value}")
        prefix = section.get("command_prefix", [])
        if prefix and not shutil.which(prefix[0]):
            raise ValueError(f"Missing command launcher: {prefix[0]}")
        if "-p" in prefix:
            env = Path(prefix[prefix.index("-p") + 1])
            if not (env / "conda-meta" / "history").is_file():
                raise ValueError(f"Missing Conda environment for {name}: {env}")


def prepare(project, data):
    import yaml
    candidates = [c for c in data["configurations"] if c["effective_config"]["deep"].get("family_placement")]
    if len(candidates) != 1:
        raise ValueError("Expected one captured family-placement configuration")
    captured = candidates[0]
    template = captured["effective_config"]
    source = source_identity(project)
    if "placement_native.py" not in source:
        raise ValueError("The installed BaSIS source lacks the family-placement module")
    batch = project / "study-pilot456" / (stamp() + "-" + uuid.uuid4().hex[:6])
    # Check all scope decisions and resources before creating the study.
    prepared = []
    for sample in data["samples"]:
        scope = ice_scope(sample)
        directory = batch / sample["sample_id"]
        cfg = build_config(template, sample, directory, batch, scope)
        check_resources(cfg)
        prepared.append((sample, directory, cfg, scope))
    batch.mkdir(parents=True, exist_ok=False)
    saved_helper = batch / "BaSIS_run_pilot456.py"
    shutil.copy2(Path(__file__).resolve(), saved_helper)
    manifest = {"schema": SCHEMA, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "project": str(project), "batch": str(batch), "source_identity": source,
                "helper_sha256": sha(saved_helper), "template": captured["path"],
                "template_sha256": captured["sha256"], "samples": [],
                "interpretation": "Operational pilot only; no independent accuracy validation or threshold fitting.",
                "settings": "Captured registry and thresholds retained; fresh sample sheets and outputs. "
                            "No imported isolate1 results, embeddings, taxonomy, topology or query reviews."}
    write_json(batch / "input-review.json", data)
    for sample, directory, cfg, scope in prepared:
        directory.mkdir()
        row = {k: sample["paths"].get(k, "") for k in FIELDS}
        with (directory / "samples.tsv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter="\t")
            writer.writeheader()
            writer.writerow(row)
        config_path = directory / "config.yaml"
        config_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        disabled = {n: m.get("disabled_reason", "module_disabled")
                    for n, m in cfg["modules"].items() if not m.get("enabled")}
        item = {"sample_id": sample["sample_id"], "config": str(config_path),
                "config_sha256": sha(config_path), "samples_sha256": sha(directory / "samples.tsv"),
                "outdir": cfg["outdir"], "counts": sample["counts"], "taxonomy": sample["taxonomy"],
                "input_hashes": sample["validation"]["input_hashes"],
                "busco": sample["hybas_summary_metrics"].get("busco_summary", "not recorded"),
                "scope": "limited-input screening" if sample["sample_id"] == "isolate6" else "Safety + DeepSafe + Extend",
                "ice_scope": scope, "disabled_modules": disabled, "status": "prepared"}
        manifest["samples"].append(item)
        say(f"{item['sample_id']}: {item['scope']} | ICEscreen: {'enabled' if scope['enabled'] else 'not assessed'}")
    registry = Path(template["deep"]["placement_registry"])
    manifest["placement_registry"] = {"path": str(registry), "sha256": sha(registry)}
    manifest["input_review_sha256"] = sha(batch / "input-review.json")
    save_state(batch, manifest)
    return batch, manifest


def read_result(path):
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(item):
    import yaml
    cfg = yaml.safe_load(Path(item["config"]).read_text(encoding="utf-8"))
    root = Path(item["outdir"])
    sid = item["sample_id"]
    statuses, problems = {}, []
    for module, setting in cfg["modules"].items():
        path = root / "results" / sid / module / "result.json"
        record = read_result(path) if path.is_file() else {}
        statuses[module] = {"execution_status": record.get("execution_status", "missing"),
                            "error": record.get("error"), "reason": record.get("reason"),
                            "requested": bool(setting.get("enabled"))}
        if setting.get("enabled"):
            if record.get("execution_status") != "completed":
                problems.append(module)
            elif (record.get("sample_id") != sid or record.get("module") != module
                  or record.get("input_hashes") != item["input_hashes"]):
                problems.append(module + ":identity_mismatch")
    path = root / "results" / sid / "deepsafe" / "result.json"
    deep = read_result(path) if path.is_file() else {}
    selected = [c for c in deep.get("candidates", []) if c.get("selected")]
    completed = sum(c.get("execution") == "completed" for c in selected)
    deep_summary = {"execution_status": deep.get("execution_status", "missing"),
                    "selected": len(selected), "completed_selected": completed,
                    "deferred": deep.get("metrics", {}).get("deferred_candidates"),
                    "upstream_incomplete": deep.get("upstream_incomplete", []),
                    "error": deep.get("error")}
    if deep.get("execution_status") != "completed" or completed != len(selected) or deep.get("upstream_incomplete"):
        problems.append("deepsafe")
    metrics = deep.get("metrics", {})
    if (deep.get("sample_id") != sid or deep.get("module") != "deepsafe"
            or deep.get("input_hashes") != item["input_hashes"]
            or metrics.get("selected_candidates") != len(selected)
            or metrics.get("completed_candidates") != completed):
        problems.append("deepsafe:identity_or_count_mismatch")
    run = read_result(root / "run.json") if (root / "run.json").is_file() else {}
    if not run or not (root / "report.html").is_file():
        problems.append("final_report")
    return {"modules": statuses, "deep": deep_summary,
            "pipeline_scope": run.get("completeness", "not_recorded"),
            "execution_problems": problems, "selected_execution_complete": not problems}


def save_state(batch, manifest):
    write_json(batch / "pilot.json", manifest)
    esc = lambda value: html.escape(str(value), quote=True)
    cards = []
    for item in manifest["samples"]:
        sid = item["sample_id"]
        result = item.get("result", {})
        deep = result.get("deep", {})
        report = Path(item["outdir"]) / "report.html"
        href = str(report.relative_to(batch))
        link = f'<a href="{esc(href)}">Open full BaSIS report →</a>' if report.is_file() else '<span>Report pending</span>'
        omissions = "".join(f"<li><strong>{esc(k)}</strong>: {esc(v)}</li>" for k, v in item["disabled_modules"].items())
        problems = ", ".join(result.get("execution_problems", [])) or "none recorded"
        cards.append(f'''<article><p class="eyebrow">{esc(item['scope'])}</p><h2>{esc(sid)}</h2>
<p>{esc(item['taxonomy'].get('taxon') or 'Taxonomy unresolved')} · {item['counts']['assembly_bp']:,} bp · {item['counts']['protein_count']:,} proteins</p>
<p class="status">{esc(item['status'].replace('_',' '))}</p>
<dl><dt>HybAs bacterial BUSCO</dt><dd>{esc(item['busco'])}</dd>
<dt>Selected DeepSafe execution</dt><dd>{esc(deep.get('completed_selected','—'))} / {esc(deep.get('selected','—'))}</dd>
<dt>Deferred candidates</dt><dd>{esc(deep.get('deferred','—'))}</dd>
<dt>Pipeline scope</dt><dd>{esc(result.get('pipeline_scope','not run'))}</dd>
<dt>Execution problems</dt><dd>{esc(problems)}</dd></dl>{link}
<details><summary>Modules outside this run's scope</summary><ul>{omissions}</ul></details></article>''')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BaSIS · Three-isolate pilot</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:#183148;font:16px/1.55 system-ui,sans-serif}
main{max-width:1200px;margin:auto;padding:48px 24px}header{background:#102b40;color:#fff;padding:32px;border-radius:18px;margin-bottom:24px}
h1{font-size:36px;margin:8px 0}h2{margin:6px 0;font-size:27px}.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#317875}
header .eyebrow{color:#8fd4cc}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}
article{background:#fff;border:1px solid #dbe4eb;border-radius:16px;padding:24px;overflow-wrap:anywhere}
.status{font-weight:700;background:#edf3f7;padding:10px;border-radius:8px}dl{font-size:14px}dt{color:#60758a;margin-top:10px}dd{margin:0}
a{color:#086c85;font-weight:650}details{margin-top:24px;font-size:13px}summary{cursor:pointer;font-weight:650}li{margin:8px 0}
.note{padding:20px;border-left:4px solid #ca9235;background:#fff7e7;border-radius:8px}footer{color:#52677a;margin-top:28px;font-size:14px}</style>
<main><header><p class="eyebrow">BaSIS / Operational pilot</p><h1>Three isolates. Traceable execution.</h1>
<p>Existing models, resources and thresholds. Separate sample reports and explicit assessment limits.</p></header>
<p class="note">Isolate6 is a limited-input screening case: HybAs reports 0/124 complete bacterial BUSCOs, 29 annotated proteins and unresolved taxonomy. Whole-organism ML prediction is withheld. Negative screens cannot establish organism safety or genome completeness.</p>
<section class="grid">''' + "".join(cards) + '''</section>
<footer><p>Completed execution is separate from interpretation. Deferred candidates and unresolved findings remain visible in each report. These three samples do not estimate sensitivity, specificity or calibrated functional probability.</p>
<p><a href="pilot.json">Pilot status and audit</a> · <a href="input-review.json">Inspected inputs</a></p></footer></main></html>'''
    (batch / "index.html").write_text(page, encoding="utf-8")


def worker(config):
    os.environ["BASIS_VERBOSE"] = "1"
    from basis.config import load_config
    from basis.pipeline import make_plan, run_pipeline
    cfg = load_config(config)
    result, code = run_pipeline(make_plan(cfg, deep=True, extend=True))
    say(f"Pipeline exit: {code} | scope: {result['run']['completeness']}")
    say(f"Report: {result['artifacts']['html']}")
    return code


def result_seal(item):
    root = Path(item["outdir"])
    paths = [root / "run.json", root / "report.html", root / "evidence.json"]
    paths += sorted((root / "results" / item["sample_id"]).glob("*/result.json"))
    return {str(p): sha(p) for p in paths}


def run_samples(batch, manifest):
    for item in manifest["samples"]:
        if item.get("status") == "selected_execution_completed":
            if not item.get("result_seal"):
                raise ValueError("Completed pilot entry has no saved result checksums")
            for path, digest in item["result_seal"].items():
                check_hash(path, digest)
            say(f"{item['sample_id']}: already recorded complete; leaving its results unchanged")
            continue
        sid = item["sample_id"]
        item["status"] = "running"
        save_state(batch, manifest)
        say(f"START {sid} — {item['scope']}")
        log = batch / sid / f"console-{stamp()}.log"
        command = [sys.executable, "-u", str(batch / "BaSIS_run_pilot456.py"), "--worker", item["config"]]
        item.setdefault("attempts", []).append({"command": command, "console_log": str(log)})
        env = dict(os.environ, PYTHONUNBUFFERED="1", BASIS_VERBOSE="1")
        project = Path(manifest["project"])
        env["PYTHONPATH"] = str(project) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        process = None
        try:
            with log.open("w", encoding="utf-8") as stream:
                process = subprocess.Popen(command, cwd=project, env=env, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
                for line in process.stdout:
                    print(line, end="", flush=True)
                    stream.write(line)
                    stream.flush()
                code = process.wait()
            item["exit_code"] = code
            item["result"] = summarize(item)
            complete = code in (0, 2) and item["result"]["selected_execution_complete"]
            if complete:
                item["result_seal"] = result_seal(item)
            item["status"] = "selected_execution_completed" if complete else "needs_execution_review"
            say(f"FINISH {sid}: {item['status']} | native scope: {item['result']['pipeline_scope']}")
            d = item["result"]["deep"]
            say(f"{sid}: DeepSafe selected completed {d['completed_selected']}/{d['selected']} | deferred {d['deferred']}")
            for name, record in item["result"]["modules"].items():
                if record["requested"] and record["execution_status"] != "completed":
                    say(f"CHECK {sid}/{name}: {record['execution_status']} | {record.get('error') or record.get('reason')}")
        except KeyboardInterrupt:
            if process is not None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait()
            item["status"] = "interrupted"
            save_state(batch, manifest)
            raise
        except Exception as exc:
            item["status"] = "needs_execution_review"
            item["launcher_error"] = str(exc)
            say(f"{sid}: {exc}; continuing to the next sample")
        save_state(batch, manifest)
    # Compact review package: no sequences, embeddings or giant native outputs.
    bundle = batch / "pilot456-summary.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("pilot.json", "input-review.json", "index.html"):
            archive.write(batch / name, name)
    say(f"PILOT INDEX: {batch / 'index.html'}")
    say(f"UPLOAD SUMMARY: {bundle}")
    if shutil.which("wslpath"):
        converted = subprocess.run(["wslpath", "-w", str(bundle)], capture_output=True, text=True)
        if converted.returncode == 0:
            say(f"WINDOWS UPLOAD PATH: {converted.stdout.strip()}")
    return 2 if any(s["status"] != "selected_execution_completed" for s in manifest["samples"]) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker)
    data = inspection()
    project = Path(data["project"]).resolve()
    if Path.cwd().resolve() != project:
        raise ValueError(f"Run this helper from {project}")
    verify_inspection(data)
    if args.resume:
        batch = args.resume.resolve()
        if not batch.is_relative_to(project / "study-pilot456"):
            raise ValueError("Resume path must be inside this project's study-pilot456 directory")
        manifest = read_result(batch / "pilot.json")
        if manifest.get("schema") != SCHEMA or manifest.get("batch") != str(batch) or manifest.get("project") != str(project):
            raise ValueError("Pilot manifest identity mismatch")
        check_hash(batch / "BaSIS_run_pilot456.py", manifest["helper_sha256"])
        check_hash(batch / "input-review.json", manifest["input_review_sha256"])
        if source_identity(project) != manifest["source_identity"]:
            raise ValueError("BaSIS source changed; start a new pilot rather than mixing software versions")
        check_hash(manifest["placement_registry"]["path"], manifest["placement_registry"]["sha256"])
        for item in manifest["samples"]:
            check_hash(item["config"], item["config_sha256"])
            check_hash(Path(item["config"]).with_name("samples.tsv"), item["samples_sha256"])
    else:
        batch, manifest = prepare(project, data)
    say(f"Study: {batch}")
    say("Thresholds and configured registry preserved; this is an operational pilot, not independent validation.")
    say(f"Resume command: python BaSIS_run_pilot456.py --resume {batch}")
    if args.prepare_only:
        say(f"Prepared only; no native analyses run. Index: {batch / 'index.html'}")
        return 0
    return run_samples(batch, manifest)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        say("Interrupted. Existing attempts preserved; use the printed resume command.")
        raise SystemExit(130)
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
