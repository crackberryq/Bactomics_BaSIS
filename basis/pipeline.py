from __future__ import annotations

from .progress import tracked, emit, stage, enabled

import copy
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import __version__
from .config import ALL_MODULES, PATH_KEYS
from .execution import CommandRunner, probe_version
from .inputs import load_samples, validate_sample
from .normalize import build_registry, normalize_result
from .sample_policy import module_decision, plan_sample_modules, validate_sample_overrides
from .util import atomic_json, hash_object, now, read_json, sha256_file

AMR = {"amrfinder", "rgi", "vfdb", "bacmet"}


def path_fingerprint(path: str | Path):
    path = Path(path)
    if not path.exists():
        return {"path": str(path), "missing": True}
    if path.is_file():
        return {"path": str(path.resolve()), "sha256": sha256_file(path), "bytes": path.stat().st_size}
    return {"path": str(path.resolve()), "files": [
        {"path": str(p.relative_to(path)), "sha256": sha256_file(p), "bytes": p.stat().st_size}
        for p in sorted(path.rglob("*")) if p.is_file()]}


def code_fingerprint() -> str:
    from .release import runtime_hashes
    return hash_object(runtime_hashes())


def module_config(config: dict, module: str, sample: dict) -> dict:
    result = copy.deepcopy(config["modules"][module])
    for key in PATH_KEYS:
        if isinstance(result.get(key), str):
            result[key] = result[key].replace("{sample_id}", sample["sample_id"])
    result["threads"] = min(int(result.get("threads", 1)), int(config["execution"]["cores"]))
    if (module == "icescreen" and result.get("mode", "native") == "native"
            and str(sample.get("taxon_phylum") or "").strip().casefold() in {"bacillota", "firmicutes"}
            and (not result.get("phylum") or str(result["phylum"]).strip().casefold() in {"bacillota", "firmicutes"})):
        # The native adapter takes one canonical argument. The exact declaration
        # and its source remain in the sample and recorded module decision.
        result["phylum"] = "bacillota"
    return result


@tracked("Validate inputs and build execution plan")
def make_plan(config: dict, deep: bool, extend: bool) -> dict:
    samples = load_samples(config["samples"])
    validate_sample_overrides(config, samples)
    decisions = {s["sample_id"]: plan_sample_modules(config, s, extend) for s in samples}
    enabled_by_sample = {sid: [m for m, decision in row.items() if decision["action"] == "run"]
                         for sid, row in decisions.items()}
    validations = {s["sample_id"]: validate_sample(s) for s in samples}
    outdir = Path(config["outdir"])
    outdir.mkdir(parents=True, exist_ok=True)
    enabled = [m for m in ALL_MODULES if config["modules"][m].get("enabled") and (m != "pathogenfinder2" or extend)]
    runnable = {m for modules in enabled_by_sample.values() for m in modules}
    for name, resource in [(m, config["modules"][m]) for m in enabled if m in runnable] + ([("deepsafe", config["deep"])] if deep else []):
        if int(resource.get("memory_mb", 1)) > int(config["execution"]["memory_mb"]):
            raise ValueError(f"{name}: requested memory exceeds execution.memory_mb")
        if int(resource.get("gpus", 0)) > int(config["execution"].get("gpus", 0)):
            raise ValueError(f"{name}: requested GPUs exceed execution.gpus")
    plan = {"schema_version": 1, "version": __version__, "source_hash": code_fingerprint(),
            "config": config, "samples": samples, "validations": validations,
            "enabled_modules": enabled, "enabled_modules_by_sample": enabled_by_sample,
            "module_decisions": decisions, "options": {"deep": bool(deep), "extend": bool(extend)},
            "outdir": str(outdir.resolve())}
    plan["plan_hash"] = hash_object(plan)
    planpath = outdir / "provenance" / "plans" / f"{plan['plan_hash']}.json"
    if not planpath.exists():
        atomic_json(planpath, plan)
    plan["plan_path"] = str(planpath.resolve())
    return plan


def record_path(plan: dict, sid: str, module: str) -> Path:
    return Path(plan["outdir"]) / "results" / sid / module / "result.json"


def _base_record(plan: dict, sample: dict, module: str):
    return {"schema_version": 1, "sample_id": sample["sample_id"], "module": module,
            "tool": module, "record_id": f"{sample['sample_id']}:{module}",
            "version": __version__, "validation_mode": plan["config"]["validation_mode"],
            "findings": [], "metrics": {}, "artifacts": [], "notes": [],
            "execution_status": "not_assessed", "interpretation": "not_applicable",
            "input_hashes": plan["validations"][sample["sample_id"]]["input_hashes"]}


def _import_result(path: str, sample: dict, module: str, validation: dict) -> dict:
    source = Path(path).resolve(strict=True)
    source_bytes = source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    result = json.loads(source_bytes.decode("utf-8"))
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        raise ValueError("Imported evidence requires schema_version: 1")
    if result.get("sample_id") != sample["sample_id"] or result.get("module") != module:
        raise ValueError("Imported evidence sample_id/module does not match the requested job")
    if result.get("input_hashes") != validation["input_hashes"]:
        raise ValueError("Imported evidence must include matching assembly/proteins/gff SHA256 input_hashes")
    if result.get("execution_status") != "completed":
        raise ValueError("Only successfully completed evidence can be imported; failed/not-assessed files cannot become negative calls")
    normalized = normalize_result(result, module, sample, validation)
    # Keep historical execution evidence distinct from this import's own
    # provenance. Its cache claims are deliberately not promoted or copied.
    fields = ("schema_version", "sample_id", "module", "tool", "record_id", "version",
              "validation_mode", "execution_status", "input_hashes", "effective_config",
              "provenance", "artifacts", "artifact_checksums", "started_at", "finished_at",
              "elapsed_seconds", "module_decision", "recovery", "imported_evidence")
    normalized["imported_evidence"] = {
        "schema": "basis-imported-evidence-v1", "native_execution_performed": False,
        "input_hash_verification": "matches_validated_current_inputs",
        "source_artifact_checksum_scope": "historical_recorded_checksums_not_reverified_by_import",
        "source_record": {
            "path": str(source), "sha256": source_hash, "bytes": len(source_bytes),
            **{key: copy.deepcopy(result[key]) for key in fields if key in result},
        },
    }
    if sha256_file(source) != source_hash:
        raise ValueError("Imported evidence changed while it was being read; retry with stable source evidence")
    normalized.setdefault("notes", []).append("Imported evidence; BaSIS did not execute the upstream biological tool in this run.")
    normalized.setdefault("artifacts", []).append(str(source))
    return normalized


@tracked(lambda plan, sample, module, force=False: f"[{sample['sample_id']}] {module}" if module_decision(plan, sample, module)["action"] == "run" else None)
def execute_module(plan: dict, sample: dict, module: str, force=False) -> dict:
    config = module_config(plan["config"], module, sample)
    target = record_path(plan, sample["sample_id"], module)
    result = _base_record(plan, sample, module)
    decision = module_decision(plan, sample, module)
    result["module_decision"] = decision
    started = time.monotonic()
    result["started_at"] = now()
    if decision["action"] != "run":
        result["reason"] = decision["reason"]
        if decision["scope_incomplete"]:
            result["interpretation"] = "inconclusive"
        result["finished_at"] = now()
        atomic_json(target, result)
        return result
    work = target.parent / "attempts" / (now().replace(":", "-") + "-" + uuid.uuid4().hex[:6])
    work.mkdir(parents=True, exist_ok=False)
    runner = CommandRunner(config, work / "commands.log")
    result["effective_config"] = config
    result["artifacts"] = [str(runner.logfile), str(work)]
    version = {"status": "not_assessed", "reason": "imported_evidence"}
    fingerprints = {}
    try:
        current_inputs = {k: sha256_file(sample[k]) for k in result["input_hashes"]}
        if current_inputs != result["input_hashes"]:
            raise ValueError("Input files changed after planning; rerun the launcher to validate a fresh plan")
        for key in PATH_KEYS:
            if config.get(key):
                emit(f"[{sample['sample_id']}] {module}: verifying resource {key}")
                fingerprints[key] = path_fingerprint(config[key])
        if config.get("mode") == "native":
            version = probe_version(module, config, runner)
        key = hash_object({"source": plan["source_hash"], "module": module,
                           "sample": {k: sample.get(k) for k in ("sample_id", "taxon", "amrfinder_organism")},
                           "input_hashes": result["input_hashes"], "config": config,
                           "references": fingerprints, "tool_version": version,
                           "module_decision": decision})
        result["cache_key"] = key
        # Unknown implicit DB/model resources cannot support a reproducible native cache.
        has_resources = bool(fingerprints) and not any(p.get("missing") for p in fingerprints.values())
        native_cache_ok = version.get("status") == "completed" and bool(version.get("output")) and has_resources
        if module == "pathogenfinder2":
            native_cache_ok = (native_cache_ok and bool(config.get("prot_t5_path")) and bool(config.get("weights_model"))
                               and not config.get("map", False))
        cache_allowed = config.get("cache", True) and (config.get("mode") == "import" or native_cache_ok)
        if not force and cache_allowed and target.is_file():
            previous = read_json(target)
            paths = previous.get("artifacts", [])
            artifacts_exist = all(not isinstance(p, str) or Path(p).exists() for p in paths)
            artifact_hashes = previous.get("artifact_checksums", {})
            artifacts_intact = bool(artifact_hashes) and all(Path(p).is_file() and sha256_file(p) == h for p, h in artifact_hashes.items())
            if previous.get("cache_key") == key and previous.get("execution_status") == "completed" and artifacts_exist and artifacts_intact:
                emit(f"[{sample['sample_id']}] {module}: verified cache reused")
                previous["cache_hit"] = True
                previous["module_decision"] = decision
                previous["cache_checked_at"] = now()
                atomic_json(target, previous)
                shutil.rmtree(work)
                return previous
        if config.get("mode") == "import":
            if not config.get("result"):
                raise ValueError("Import mode requires result: /path/to/evidence.json")
            payload = _import_result(config["result"], sample, module, plan["validations"][sample["sample_id"]])
            imported = payload.get("imported_evidence")
            if imported is not None:
                if imported["source_record"]["sha256"] != fingerprints["result"].get("sha256"):
                    raise ValueError("Imported evidence changed after resource fingerprinting; retry with stable source evidence")
                result["imported_evidence"] = imported
        else:
            if module in AMR:
                adapter = importlib.import_module("basis.adapters.amr")
            elif module == "pathogenfinder2":
                adapter = importlib.import_module("basis.adapters.extend")
            elif module == "minced":
                adapter = importlib.import_module("basis.adapters.minced")
            else:
                adapter = importlib.import_module("basis.adapters.context")
            payload = adapter.run(module, sample, config, work, runner)
            payload = normalize_result(payload, module, sample, plan["validations"][sample["sample_id"]])
        for field in ("findings", "metrics", "notes", "interpretation"):
            result[field] = payload.get(field, result.get(field))
        result["artifacts"] += payload.get("artifacts", [])
        result["execution_status"] = "completed"
        result["cache_hit"] = False
        if not cache_allowed:
            result["notes"].append("Native cache disabled unless tool identity and explicit database/model resources are available.")
    except Exception as exc:
        result.update(execution_status="failed", interpretation="inconclusive", error=str(exc), findings=[])
    result["provenance"] = {"tool_version": version, "resources": fingerprints,
                            "commands": runner.commands, "source_hash": plan["source_hash"]}
    result["finished_at"] = now()
    result["elapsed_seconds"] = round(time.monotonic() - started, 4)
    result["artifact_checksums"] = {p: sha256_file(p) for p in result["artifacts"] if isinstance(p, str) and Path(p).is_file()}
    atomic_json(target, result)
    return result


@tracked(lambda plan, sample, force=False, upstream_records=None: f"[{sample['sample_id']}] DeepSafe" if plan["options"]["deep"] else None)
def execute_deep(plan: dict, sample: dict, force=False, upstream_records=None) -> dict:
    from .deep import run_deep
    from .deep_reporting import completion_summary
    sid = sample["sample_id"]
    target = record_path(plan, sid, "deepsafe")
    result = _base_record(plan, sample, "deepsafe")
    if not plan["options"]["deep"]:
        result["reason"] = "deepsafe_not_requested"
        result["candidates"] = []
        atomic_json(target, result)
        return result
    cache_allowed = False
    records = []
    for module in ALL_MODULES:
        decision = module_decision(plan, sample, module)
        if decision["action"] == "run":
            record = (read_json(record_path(plan, sid, module)) if upstream_records is None
                      else copy.deepcopy(upstream_records[module]))
            record["module_decision"] = decision
        else:
            record = _base_record(plan, sample, module)
            record.update(reason=decision["reason"], module_decision=decision)
            if decision["scope_incomplete"]:
                record["interpretation"] = "inconclusive"
        records.append(record)
    cfg = copy.deepcopy(plan["config"]["deep"])
    cfg["threads"] = min(int(cfg.get("threads", 1)), int(plan["config"]["execution"]["cores"]))
    if not cfg.get("profiles_dir"):
        cfg["profiles_dir"] = str(Path(__file__).parent / "resources" / "profiles")
    work = target.parent / "attempts" / (now().replace(":", "-") + "-" + uuid.uuid4().hex[:6])
    work.mkdir(parents=True, exist_ok=False)
    runner = CommandRunner(cfg, work / "commands.log")
    result.update(started_at=now(), effective_config=cfg, artifacts=[str(runner.logfile)])
    started = time.monotonic()
    try:
        if {k: sha256_file(sample[k]) for k in result["input_hashes"]} != result["input_hashes"]:
            raise ValueError("Input files changed after planning; rerun the launcher")
        emit(f"[{sid}] DeepSafe: verifying database and reference fingerprints")
        resources = {k: path_fingerprint(cfg[k]) for k in ("reference_fasta", "reference_metadata", "profiles_dir", "domain_database", "tree_review_registry") if cfg.get(k)}
        if cfg.get("family_placement"):
            from .placement import DEFAULT_REGISTRY
            resources["placement_registry"] = path_fingerprint(cfg.get("placement_registry") or DEFAULT_REGISTRY)
        from .deep_cache import engine_identity, stable_records, reusable
        if cfg.get('motifs', True):
            from .motifs import DEFAULT_RULES
            resources['motif_rules'] = path_fingerprint(cfg.get('motif_rules') or DEFAULT_RULES)
        if cfg.get('prosite_snapshot'):
            resources['prosite_snapshot'] = path_fingerprint(cfg['prosite_snapshot'])
        # Include pressed HMM companions and externally reviewed artifacts.
        if cfg.get('domain_database'):
            resources['domain_indices'] = {suffix:path_fingerprint(str(cfg['domain_database'])+suffix) for suffix in ('.h3f','.h3i','.h3m','.h3p')}
        if cfg.get('tree_review_registry'):
            registry = read_json(cfg['tree_review_registry'])
            resources['review_artifacts'] = {str(p):path_fingerprint(p) for row in registry.get('records',[]) for p in row.get('artifacts',{})}
        cache_allowed = bool(cfg.get('cache', True))
        result['cache_status'] = {'status': 'disabled', 'reason': 'configured_off'}
        if plan['config'].get('validation_mode') == 'synthetic_fixture':
            # Software fixtures do not use native biological engines. Their
            # launcher environment cannot identify a native DeepSafe engine.
            cache_allowed = False
            result['cache_status'] = {'status': 'not_applicable', 'reason': 'synthetic_fixture'}
        # Custom profile resources may refer outside the fingerprinted catalogue.
        # Until those are individually tracked, conservatively disable reuse.
        import yaml
        for profile_path in Path(cfg['profiles_dir']).glob('*.yaml'):
            profile_data = yaml.safe_load(profile_path.read_text()) or {}
            if profile_data.get('resources'):
                cache_allowed = False
                result.setdefault('notes', []).append('Deep cache disabled for custom profile resources.')
                if result['cache_status']['status'] != 'not_applicable':
                    result['cache_status'] = {'status': 'unavailable', 'reason': 'custom_profile_resources'}
        identity = None
        if cache_allowed:
            try:
                emit(f"[{sid}] DeepSafe: verifying environment files and executable identity")
                identity = engine_identity(cfg, runner)
                if identity.get('status') != 'available':
                    cache_allowed = False
                    result['cache_status'] = {'status': 'unavailable', 'reason': identity.get('reason', 'engine_identity_unavailable')}
                    result.setdefault('notes', []).append('Deep cache unavailable: '+result['cache_status']['reason'])
                else:
                    result['cache_status'] = {'status': 'available', 'reason': 'engine_identity_verified'}
            except Exception as exc:
                cache_allowed = False
                result['cache_status'] = {'status': 'unavailable', 'reason': 'engine_identity_failed'}
                result.setdefault('notes', []).append('Deep cache disabled: '+str(exc))
        result['provenance'] = {'resources':resources, 'source_hash':plan['source_hash'], 'engine_identity':identity}
        result['cache_key'] = hash_object({'inputs':result['input_hashes'], 'sample':sample,
            'records':stable_records(records), 'config':cfg, 'resources':resources, 'source':plan['source_hash'], 'engine_identity':identity})
        if not force and cache_allowed and target.is_file():
            try:
                previous = read_json(target)
                if reusable(previous,result['cache_key']):
                    emit(f"[{sid}] DeepSafe: verified cache reused")
                    previous['cache_hit']=True;previous['cache_checked_at']=now()
                    atomic_json(target,previous)
                    return previous
            except (ValueError, OSError):
                pass
        result['cache_hit'] = False
        emit(f"[{sid}] DeepSafe: cache unavailable or recomputation requested; starting analyses")
        payload = run_deep(sample, records, cfg, work, runner)
        # Analysis notes supplement execution/provenance warnings; neither
        # source should erase the other when the payload is merged.
        notes = result.get("notes", []) + payload.get("notes", [])
        result.update(payload)
        result["notes"] = notes
        result["artifacts"] = list(dict.fromkeys([str(runner.logfile)] + payload.get("artifacts", [])))
        result["execution_status"] = "completed"
    except Exception as exc:
        result.update(execution_status="failed", interpretation="inconclusive", error=str(exc), candidates=[])
    # Finalize upstream scope even when the deep stage failed. Requested holds
    # remain incomplete; explicit sample exclusions are outside requested scope.
    result["upstream_incomplete"] = [r["module"] for r in records
                                     if module_decision(plan, sample, r["module"])["requested"]
                                     and r["execution_status"] != "completed"]
    if result["upstream_incomplete"]:
        result.setdefault("notes", []).append("Candidate selection is incomplete because requested upstream analyses failed or were withheld pending review.")
    result["completion_summary"] = completion_summary(result)
    result.setdefault("provenance", {})["commands"] = runner.commands
    result["finished_at"] = now()
    result["elapsed_seconds"] = round(time.monotonic() - started, 4)
    if result.get('execution_status') == 'completed' and cache_allowed:
        from .deep_cache import seal
        try:
            result['deep_cache'] = seal(result, work)
        except OSError as exc:
            result.setdefault('notes', []).append('Deep cache manifest unavailable: '+str(exc))
    atomic_json(target, result)
    return result


def _scheduler_root(plan: dict, root: str | Path) -> Path:
    root = Path(root).resolve()
    expected = (Path(plan["outdir"]) / "provenance" / "scheduler").resolve()
    if root.parent != expected:
        raise ValueError("Scheduler receipts must use a run directory beneath provenance/scheduler")
    return root


def _read_scheduler_result(plan: dict, sample_id: str, module: str, root: str | Path) -> dict:
    """Read exactly the bytes attested by this run's completed worker receipt."""
    root = _scheduler_root(plan, root)
    receipt = read_json(root / sample_id / (module + ".json"))
    if not isinstance(receipt, dict):
        raise ValueError(f"Scheduler receipt must be a mapping: {sample_id}:{module}")
    expected = {"schema": "basis-scheduler-receipt-v1", "run_id": root.name,
                "plan_hash": plan["plan_hash"], "source_hash": plan["source_hash"],
                "sample_id": sample_id, "module": module}
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError(f"Scheduler receipt identity mismatch: {sample_id}:{module}")
    path = record_path(plan, sample_id, module)
    if receipt.get("result_path") != str(path.resolve()):
        raise ValueError(f"Scheduler receipt result path mismatch: {sample_id}:{module}")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != receipt.get("result_sha256"):
        raise ValueError(f"Scheduler result changed after worker completion: {sample_id}:{module}")
    record = json.loads(payload)
    if (not isinstance(record, dict) or record.get("schema_version") != 1
            or record.get("sample_id") != sample_id
            or record.get("module") != module
            or record.get("execution_status") != receipt.get("execution_status")
            or record.get("input_hashes") != plan["validations"][sample_id]["input_hashes"]):
        raise ValueError(f"Scheduler result identity mismatch: {sample_id}:{module}")
    return record


def run_worker(plan_path: str, sample_id: str, module: str, force=False, *,
               receipt: str | None = None, upstream_receipts: str | None = None):
    plan = read_json(plan_path)
    sample = next(s for s in plan["samples"] if s["sample_id"] == sample_id)
    receipt_path = Path(receipt).resolve() if receipt else None
    if receipt_path is not None:
        root = _scheduler_root(plan, receipt_path.parent.parent)
        if receipt_path != root / sample_id / (module + ".json"):
            raise ValueError("Worker receipt path does not match its sample and module")
    records = None
    if upstream_receipts is not None:
        if module != "deepsafe" or receipt_path is None:
            raise ValueError("Upstream receipts are only valid for a scheduled DeepSafe worker")
        if _scheduler_root(plan, upstream_receipts) != root:
            raise ValueError("DeepSafe upstream receipts must belong to the same scheduler run")
        records = {name: _read_scheduler_result(plan, sample_id, name, root) for name in ALL_MODULES}
    elif module == "deepsafe" and receipt_path is not None:
        raise ValueError("Scheduled DeepSafe requires current-run upstream receipts")
    if module == "deepsafe":
        result = execute_deep(plan, sample, force, upstream_records=records)
    elif module not in ALL_MODULES:
        raise ValueError(f"Unknown worker module {module}")
    else:
        result = execute_module(plan, sample, module, force)
    if receipt_path is not None:
        target = record_path(plan, sample_id, module)
        atomic_json(receipt_path, {"schema": "basis-scheduler-receipt-v1", "run_id": root.name,
                    "plan_hash": plan["plan_hash"], "source_hash": plan["source_hash"],
                    "sample_id": sample_id, "module": module,
                    "result_path": str(target.resolve()), "result_sha256": sha256_file(target),
                    "execution_status": result["execution_status"], "recorded_at": now()})
    return result


def _run_scheduler(command, logfile: Path, env: dict):
    """Save scheduler/worker output and mirror it to verbose stderr.

    A separate reader keeps the ordinary subprocess invocation (and its exit
    status) unchanged. Worker progress therefore reaches persistent GUI jobs,
    while machine-readable launcher stdout remains one JSON document.
    """
    with logfile.open("w", encoding="utf-8") as log:
        if not enabled():
            return subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env)
        finished = threading.Event()

        def forward():
            with logfile.open("r", encoding="utf-8", errors="replace") as reader:
                while not finished.is_set():
                    line = reader.readline()
                    if line:
                        sys.stderr.write(line)
                        sys.stderr.flush()
                    else:
                        finished.wait(0.1)
                # Drain the final lines after the child has exited.
                for line in reader:
                    sys.stderr.write(line)
                sys.stderr.flush()

        thread = threading.Thread(target=forward, daemon=True)
        thread.start()
        try:
            return subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env)
        finally:
            log.flush()
            finished.set()
            thread.join()


def run_pipeline(plan: dict, engine="local", dry_run=False, force=False) -> tuple[dict, int]:
    if engine not in {"local", "snakemake"}:
        raise ValueError("Unknown execution engine: " + str(engine))
    import fcntl
    lock_path = Path(plan["outdir"]) / ".basis.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"Another BaSIS process is using {plan['outdir']}; choose a different output directory or wait for it to finish") from exc
        try:
            return _run_pipeline_unlocked(plan, engine, dry_run, force)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@tracked("Pipeline execution and final reporting")
def _run_pipeline_unlocked(plan: dict, engine="local", dry_run=False, force=False) -> tuple[dict, int]:
    from .run_summary import build_plan_summary, build_run_summary, write_run_summary
    run_id = now().replace(":", "-") + "-" + uuid.uuid4().hex[:8]
    run = {"run_id": run_id, "version": __version__, "project": plan["config"]["project"],
           "started_at": now(), "options": plan["options"], "config_hash": hash_object(plan["config"]),
           "source_hash": plan["source_hash"], "validation_mode": plan["config"]["validation_mode"],
           "engine": engine, "force": bool(force), "input_validations": plan["validations"], "enabled_modules": plan["enabled_modules"]}
    run["module_decisions"] = {s["sample_id"]: {m: module_decision(plan, s, m) for m in ALL_MODULES}
                               for s in plan["samples"]}
    run["enabled_modules_by_sample"] = {sid: [m for m, d in row.items() if d["action"] == "run"]
                                         for sid, row in run["module_decisions"].items()}
    if dry_run:
        return {**run, "samples": [s["sample_id"] for s in plan["samples"]], "plan": plan["plan_path"],
                "summary": build_plan_summary(plan, engine, force)}, 0
    outdir = Path(plan["outdir"])
    engine_error = None
    if engine == "snakemake":
        receipt_root = outdir / "provenance" / "scheduler" / run_id
        run["scheduler_receipt_dir"] = str(receipt_root)
        snakefile = Path(__file__).parent / "resources" / "Snakefile"
        command = [sys.executable, "-m", "snakemake", "--snakefile", str(snakefile), "--configfile", plan["plan_path"],
                   "--directory", str(outdir), "--cores", str(plan["config"]["execution"]["cores"]),
                   "--resources", f"mem_mb={plan['config']['execution']['memory_mb']}",
                   f"gpu={plan['config']['execution'].get('gpus', 0)}", "--keep-going", "--rerun-incomplete", "--rerun-triggers", "mtime",
                   "--forcerun", "module", "deep"]
        env = os.environ.copy()
        env["BASIS_FORCE"] = "1" if force else "0"
        env["BASIS_SCHEDULER_RUN_ID"] = run_id
        env["PYTHONPATH"] = str(Path(__file__).parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
        emit(f"Scheduler log: {outdir / 'snakemake.log'}")
        try:
            with stage("Snakemake scheduling and worker execution"):
                completed = _run_scheduler(command, outdir / "snakemake.log", env)
            if completed.returncode:
                engine_error = f"Snakemake exited {completed.returncode}; see {outdir / 'snakemake.log'}"
        except OSError as exc:
            # Still produce an explicit failed/incomplete report if the
            # scheduler cannot start. Existing output files are not current
            # evidence merely because they remain on disk.
            engine_error = f"Snakemake could not start: {exc}; see {outdir / 'snakemake.log'}"
    else:
        for ordinal, sample in enumerate(plan["samples"], 1):
            emit(f"Sample {ordinal}/{len(plan['samples'])} [{sample['sample_id']}] — starting")
            for module in ALL_MODULES:
                decision = module_decision(plan, sample, module)
                record = execute_module(plan, sample, module, force)
                if decision["action"] == "run":
                    source = ("cache reused" if record.get("cache_hit") else
                              "imported evidence" if record.get("imported_evidence") else "current attempt")
                    emit(f"[{sample['sample_id']}] {module}: {record.get('execution_status', 'not_recorded')} | {source}")
                elif decision["requested"] or decision["reason"] == "sample_scope_exclusion":
                    emit(f"[{sample['sample_id']}] {module}: {decision['action']} | {decision['reason']}")
            deep_record = execute_deep(plan, sample, force)
            if plan["options"]["deep"]:
                reused = " | cache reused" if deep_record.get("cache_hit") else ""
                emit(f"[{sample['sample_id']}] DeepSafe: {deep_record.get('execution_status', 'not_recorded')}{reused}")
            emit(f"Sample {ordinal}/{len(plan['samples'])} [{sample['sample_id']}] — execution stages finished")
    records, deep_records = [], []
    incomplete = []
    for sample in plan["samples"]:
        for module in ALL_MODULES + ["deepsafe"]:
            path = record_path(plan, sample["sample_id"], module)
            decision = module_decision(plan, sample, module) if module != "deepsafe" else None
            if decision is not None and decision["action"] != "run":
                # Current scope takes precedence over any stale stored result,
                # including after a scheduler failure or a force request.
                record = _base_record(plan, sample, module)
                record.update(reason=decision["reason"], module_decision=decision)
                if decision["scope_incomplete"]:
                    record["interpretation"] = "inconclusive"
            elif module == "deepsafe" and not plan["options"]["deep"]:
                record = _base_record(plan, sample, module)
                record.update(reason="deepsafe_not_requested", candidates=[])
            elif engine == "snakemake":
                try:
                    record = _read_scheduler_result(plan, sample["sample_id"], module, receipt_root)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    # A saved result alone cannot establish execution in this
                    # run. Valid receipts preserve completed independent jobs
                    # even when another scheduler job failed.
                    record = _base_record(plan, sample, module)
                    record.update(execution_status="failed", interpretation="inconclusive",
                                  error=engine_error or f"Current scheduler receipt unavailable or invalid: {exc}")
            elif path.is_file():
                record = read_json(path)
            else:
                record = _base_record(plan, sample, module)
                record.update(execution_status="failed", interpretation="inconclusive", error="Expected module result absent")
            if module == "deepsafe":
                deep_records.append(record)
                if plan["options"]["deep"] and (record["execution_status"] != "completed" or record.get("complete") is False or record.get("upstream_incomplete")):
                    incomplete.append(f"{sample['sample_id']}:{module}")
            else:
                records.append(record)
                record["module_decision"] = decision
                if decision["requested"] and record["execution_status"] != "completed":
                    incomplete.append(f"{sample['sample_id']}:{module}")
    run["finished_at"] = now()
    run["completeness"] = "incomplete" if incomplete or engine_error else "completed_requested_scope"
    run["incomplete_modules"] = incomplete
    if engine_error:
        run["engine_error"] = engine_error
    summary = build_run_summary(run, plan["samples"], records, deep_records)
    run["batch_summary"] = summary
    from .report import write_report
    artifacts = write_report(run, plan["samples"], records, deep_records, outdir)
    artifacts.update(write_run_summary(summary, outdir))
    archive = outdir / "history" / run_id
    archive.mkdir(parents=True, exist_ok=False)
    for key in ("evidence_json", "methods_txt", "findings_tsv", "recommendations_tsv", "summary_json", "summary_tsv"):
        shutil.copy2(artifacts[key], archive / Path(artifacts[key]).name)
    run["evidence_snapshot"] = str(archive / "evidence.json")
    atomic_json(outdir / "locus_registry.json", build_registry(records))
    atomic_json(outdir / "provenance" / "runs" / f"{run_id}.json", run)
    atomic_json(outdir / "run.json", run)
    return {"run": run, "artifacts": artifacts, "summary": summary}, 2 if incomplete or engine_error else 0
