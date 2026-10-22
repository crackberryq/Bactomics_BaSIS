"""Single-stage Extend/CRISPR jobs; Snakemake owns scheduling and environments.

The pinned PF2 embedding call is identical to its proteome-mode preprocessing.
Its HDF5 is a declared, independently reusable output; classification cannot
delete or regenerate it. No model training or new interpretation is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from basis.adapters import extend as pf2
from basis.adapters import crisprcasfinder as ccf
from basis.inputs import read_fasta
from basis.normalize import normalize_result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def settings(config, module):
    value = read_json(config) if not isinstance(config, dict) else config
    result = dict(value["modules"][module])
    if module == "pathogenfinder2":
        result["device_policy"] = value.get("execution", {}).get("device", "cpu")
    return result


def completed_record(parsed, module, sample, config, validation):
    result = normalize_result(parsed, module, sample, validation)
    result.update(schema_version=1, sample_id=sample["sample_id"], module=module,
                  tool=module, record_id=f"{sample['sample_id']}:{module}",
                  version="snakemake-development", validation_mode="native",
                  input_hashes=validation.get("input_hashes", {}),
                  execution_status="completed", mode="native", reused=False,
                  effective_config=config)
    return result


def normalize_saved(module, sample, full_config, validation, native):
    config = settings(full_config, module)
    decision = full_config.get("module_decisions", {}).get(module, {})
    if decision.get("action", "run") != "run" or not config.get("enabled", True):
        return {"schema_version": 1, "sample_id": sample["sample_id"], "module": module,
                "tool": module, "record_id": f"{sample['sample_id']}:{module}",
                "execution_status": "not_assessed", "interpretation": "inconclusive",
                "reason": decision.get("reason", "module_disabled"), "scope_decision": decision,
                "effective_config": config, "input_hashes": validation.get("input_hashes", {}),
                "findings": [], "metrics": {}, "notes": [], "artifacts": []}
    parsed = read_json(native)
    result = completed_record(parsed, module, sample, config, validation)
    paths = {Path(native)}
    for value in result.get("artifacts", []):
        path = Path(value)
        if path.is_file():
            paths.add(path)
        elif path.is_dir():
            paths.update(p for p in path.rglob("*") if p.is_file() and p.name != ".snakemake_timestamp")
        else:
            raise ValueError(f"Recorded native artifact is missing: {path}")
    result["artifact_hashes"] = {str(path): pf2._sha256(path) for path in sorted(paths)}
    return result


def check_pf2_config(config):
    if config.get("pf2_source_commit", pf2.PF2_SOURCE_COMMIT) != pf2.PF2_SOURCE_COMMIT:
        raise ValueError("Unsupported PF2 source contract")
    if int(config.get("expected_models", 4)) != 4:
        raise ValueError("The pinned pretrained PF2 ensemble requires four models")
    if type(config.get("attention_top_n", 20)) is not int or config.get("attention_top_n", 20) < 0:
        raise ValueError("attention_top_n must be a nonnegative integer")
    for key, default in (("attention", True), ("map", False), ("validate_score_ranges", True)):
        if type(config.get(key, default)) is not bool:
            raise ValueError(f"{key} must be boolean")
    if type(config.get("gpus", 0)) is not int or config.get("gpus", 0) not in (0, 1):
        raise ValueError("PF2 gpus must be 0 or 1")
    policy = config.get("device_policy", "cpu")
    if policy not in {"cpu", "configured", "auto", "cuda"}:
        raise ValueError("Unrecognized PF2 device policy")
    if policy == "cuda" and config.get("gpus", 0) != 1:
        raise ValueError("CUDA requires modules.pathogenfinder2.gpus: 1 and a Snakemake GPU allocation")
    if config.get("gpus", 0) == 1 and policy != "cuda":
        raise ValueError("GPU inference requires explicit execution.device: cuda")
    if not config.get("prot_t5_identity"):
        raise ValueError("A pinned ProtT5 identity is required")


def select_device(config):
    """Use only the explicitly reserved GPU; never silently switch devices."""
    check_pf2_config(config)
    requested = "cuda" if config.get("device_policy") == "cuda" else "cpu"
    if requested == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    import torch
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("GPU requested but CUDA is unavailable in the PF2 rule environment")
    return {"policy": config.get("device_policy", "cpu"), "selected": requested,
            "gpu_reserved": int(config.get("gpus", 0)), "torch_version": str(torch.__version__)}


def validate_embeddings(path, proteins):
    """Check schema, finite embeddings and exact full-proteome token coverage."""
    import h5py
    import numpy as np

    headers = pf2._proteome_headers(Path(proteins))
    with h5py.File(path, "r") as data:
        if "Names" not in data or "Embeddings" not in data:
            raise ValueError("PF2 embedding HDF5 lacks Names or Embeddings")
        names = [v.decode("utf-8") if isinstance(v, bytes) else str(v) for v in data["Names"][:]]
        embeddings = data["Embeddings"]
        if (not names or len(set(names)) != len(names) or embeddings.ndim != 2
                or embeddings.shape != (len(names), 1024)
                or embeddings.dtype.kind not in "fiu"
                or int(data.attrs.get("Amount Embeddings", -1)) != len(names)
                or int(data.attrs.get("Amount Proteins", -1)) != len(headers)
                or data.attrs.get("Pooled") != "mean"):
            raise ValueError("PF2 embedding schema/count/pooling mismatch")
        observed = set()
        for token in names:
            if token in headers:
                observed.add(token)
                continue
            match = re.fullmatch(r"(.+)_Kmer-\d+", token)
            if not match or match.group(1) not in headers:
                raise ValueError("PF2 embedding token is absent from the input proteome")
            observed.add(match.group(1))
        if observed != set(headers):
            raise ValueError("PF2 embeddings do not cover the full supplied proteome")
        for start in range(0, len(names), 512):
            if not np.isfinite(embeddings[start:start + 512]).all():
                raise ValueError("Non-finite PF2 embeddings")
    return {"protein_count": len(headers), "embedding_token_count": len(names)}


def embedding_manifest(path, proteins, config, **extra):
    return {
        "schema": "basis-pf2-embeddings-v1",
        "proteome_sha256": pf2._sha256(Path(proteins)),
        "embeddings_sha256": pf2._sha256(Path(path)),
        "pf2_source_commit": pf2.PF2_SOURCE_COMMIT,
        "prot_t5_identity": config["prot_t5_identity"],
        "embeddings_path": str(Path(path).resolve()),
        "input_proteome": str(Path(proteins).resolve()),
        "upstream_model_weights_fully_pinned": True,
        **extra,
    }


def embed(sample, config, destination, manifest_path):
    """One Snakemake job: pinned upstream preprocessing, no classification."""
    from importlib.metadata import version

    check_pf2_config(config)
    if version("PathogenFinder2") != pf2.PF2_VERSION:
        raise ValueError("Embedding stage requires pinned PathogenFinder2 0.7.1")
    if config.get("pf2_source_commit", pf2.PF2_SOURCE_COMMIT) != pf2.PF2_SOURCE_COMMIT:
        raise ValueError("Unsupported PF2 source contract")
    if not config.get("prot_t5_identity"):
        raise ValueError("A pinned ProtT5 identity is required")
    proteins = Path(sample["proteins"]).resolve(strict=True)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    reused = bool(config.get("embeddings"))
    device = {"selected": "external_verified_embeddings"}
    if reused:
        source = Path(config["embeddings"]).resolve(strict=True)
        pf2._validate_reuse(source, Path(config["embeddings_manifest"]), proteins,
                           config["prot_t5_identity"])
        shutil.copyfile(source, temporary)
    else:
        model = Path(config.get("prot_t5_path") or "").resolve()
        if not config.get("prot_t5_path") or not model.is_dir():
            raise ValueError("Prepared local ProtT5 model is required; downloads are setup jobs")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        device = select_device(config)
        from pathogenfinder2.preprocessing.embedder import ProtT5Embedder
        embedder = ProtT5Embedder(model_dir=str(model))
        # Identical arguments/defaults to pinned PathogenFinder2.infer_embeddings.
        embedder.get_embeddings(seq_path=proteins, emb_path=temporary,
                                pool_mode="mean", split_kmer=True)
    metrics = validate_embeddings(temporary, proteins)
    temporary.replace(destination)
    write_json(manifest_path, embedding_manifest(destination, proteins, config,
               **metrics, inference_device=device, external_embeddings_reused=reused))


def prepare_classifier_configuration(config):
    """Honor prepared weights: pinned PF2 CLI --weightsModel is not applied."""
    from pathogenfinder2.utils.configuration import ConfigurationPF2

    value = ConfigurationPF2.load_baseconfig()
    bundled = ConfigurationPF2.load_baseweights(ConfigurationPF2.load_baseconfig())
    bundled_weights = [Path(p).resolve() for p in bundled["Model Parameters"]["Network Weights"]]
    weights_root = config.get("weights_model")
    if weights_root:
        weights = [Path(weights_root) / f"weights_model{n}.pt" for n in range(1, 5)]
        for weight in weights:
            pf2._nonempty(weight)
        if len(bundled_weights) != 4 or any(pf2._sha256(a) != pf2._sha256(b)
                                          for a, b in zip(weights, bundled_weights)):
            raise ValueError("Prepared classifier weights differ from the pinned pretrained ensemble")
        value["Model Parameters"]["Network Weights"] = [str(p.resolve()) for p in weights]
    else:
        value = bundled
        value["Model Parameters"]["Network Weights"] = [
            str(Path(p).resolve()) for p in value["Model Parameters"]["Network Weights"]]
    return value


def classify(sample, config, embeddings, manifest, destination, logfile):
    """One direct native classifier invocation; successful embeddings survive."""
    from safety.runtime import DirectRunner

    check_pf2_config(config)
    embeddings = Path(embeddings).resolve(strict=True)
    pf2._validate_reuse(embeddings, Path(manifest), Path(sample["proteins"]),
                       config["prot_t5_identity"])
    validate_embeddings(embeddings, sample["proteins"])
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("Classifier output already exists; Snakemake must clean incomplete outputs")
    device = select_device(config)
    stage = Path(tempfile.mkdtemp(prefix="classifier-attempt-", dir=destination.parent))
    native_config = stage / "classifier-config.json"
    write_json(native_config, prepare_classifier_configuration(config))
    argv = [config.get("executable", "pathogenfinder2"), "predict", "-i", str(embeddings),
            "-f", "embeddings", "-o", str(stage), "--config", str(native_config)]
    if config.get("attention", True):
        argv.extend(["--attProteins", "report"])
    if config.get("map", False):
        argv.extend(["--embedProteome", "map"])
    DirectRunner(config, logfile).run(argv, cwd=stage)
    native = stage / "predictionPF2"
    result_dir = native / "results_PF2"
    pf2.parse_predictions(result_dir / "predictions.tsv",
                          expected_models=int(config.get("expected_models", 4)))
    if config.get("attention", True):
        pf2._nonempty(result_dir / "attentions.npz")
    shutil.copyfile(native_config, native / "classifier-config.json")
    write_json(native / "device.json", device)
    write_json(native / "classifier-weights.json", {
        "pf2_source_commit": pf2.PF2_SOURCE_COMMIT,
        "matches_pinned_bundled_ensemble": True,
        "sha256": {path: pf2._sha256(Path(path)) for path in
                   read_json(native_config)["Model Parameters"]["Network Weights"]}})
    native.rename(destination)
    shutil.rmtree(stage)


def collect_pf2(sample, config, native, embeddings, manifest):
    """Pure output collection; no inference and no subprocess execution."""
    import csv
    import h5py
    import numpy as np

    check_pf2_config(config)
    native = Path(native)
    proteins = Path(sample["proteins"])
    expected = int(config.get("expected_models", 4))
    pf2._validate_reuse(Path(embeddings), Path(manifest), proteins, config["prot_t5_identity"])
    validate_embeddings(embeddings, proteins)
    result_dir = native / "results_PF2"
    prediction = result_dir / "predictions.tsv"
    metrics = pf2.parse_predictions(prediction, expected_models=expected,
                                   validate_scores=config.get("validate_score_ranges", True))
    findings = []
    artifacts = [str(prediction), str(embeddings), str(manifest)]
    for name in ("classifier-config.json", "classifier-weights.json", "device.json"):
        path = native / name
        if path.is_file():
            artifacts.append(str(path))
    if config.get("attention", True):
        with np.load(result_dir / "attentions.npz", allow_pickle=False) as data:
            scores = data["attentions"]
            if scores.ndim != 2 or scores.dtype.kind not in "fiu":
                raise ValueError("Expected numeric model-by-token attention matrix")
            scores = scores.astype(float).tolist()
        with h5py.File(embeddings, "r") as data:
            names = [v.decode("utf-8") if isinstance(v, bytes) else str(v) for v in data["Names"][:]]
        attention_path = native / "attention_native.json"
        write_json(attention_path, {"schema": "basis-pf2-attention-v1", "token_ids": names,
                                   "model_scores": scores})
        ranked = pf2.parse_attention_json(attention_path, proteins, expected_models=expected,
                                         validate_scores=config.get("validate_score_ranges", True))
        table = native / "attention_scores.tsv"
        with table.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["protein_id", "rank", "attention_max", "attention_mean", "embedding_token_count"])
            writer.writerows([r[k] for k in ("protein_id", "rank", "attention_max", "attention_mean", "embedding_token_count")] for r in ranked)
        for entry in ranked[:int(config.get("attention_top_n", 20))]:
            findings.append({"tool": "pathogenfinder2", "category": "ml_attention", "gene": None,
                "family": None, "protein_id": entry["protein_id"], "contig": None, "start": None,
                "end": None, "strand": None, "reference": f"PathogenFinder2@{pf2.PF2_SOURCE_COMMIT}",
                "identity": None, "coverage": None, "evidence_level": "exploratory",
                "interpretation": "Model attention prioritization; not proof of virulence or causality",
                "attributes": entry})
        artifacts.extend(map(str, [result_dir / "attentions.npz", attention_path, table]))
        metrics.update(attention_protein_count=len(ranked), attention_selected_count=len(findings))
    if config.get("map", False):
        artifacts.extend(str(pf2._nonempty(result_dir / name)) for name in
                         ("embeddings.npz", "closeneighbors_bpl.tsv", "mapped_bpl.png"))
    embedding_info = read_json(manifest)
    metrics.update(input_protein_count=len(pf2._proteome_headers(proteins)),
        embeddings_reused=bool(embedding_info.get("external_embeddings_reused", False)),
        classifier_input="verified_embedding_file", embedding_manifest=str(manifest),
        adapter_source_commit=pf2.PF2_SOURCE_COMMIT, expected_tool_version=pf2.PF2_VERSION,
        landscape_requested=config.get("map", False),
        inference_device=embedding_info.get("inference_device"),
        classifier_device=read_json(native / "device.json") if (native / "device.json").is_file() else None,
        embeddings_separate_snakemake_job=True)
    return {"findings": findings, "metrics": metrics, "artifacts": artifacts,
            "interpretation": "inconclusive", "notes": [
                "Full supplied proteome; pretrained PF2 ensemble; no training or MC dropout.",
                "The native model score is not exposure risk or a safety verdict.",
                "Between-model standard deviation is not a confidence interval.",
                "Attention is an exploratory ranking signal, not evidence of virulence or causal function."]}


def prepare_contigs(sample, destination, batch_size=32):
    from safety.ccf_batches import make_plan
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    mapping = {}
    for index, (contig, sequence) in enumerate(read_fasta(sample["assembly"]).items(), 1):
        if not ccf._BASES.fullmatch(sequence):
            raise ValueError("Unsupported assembly DNA characters")
        safe = f"CCF{index:06d}"
        path = destination / f"{safe}.fna"
        path.write_text(f">{safe}\n{sequence}\n", encoding="ascii")
        mapping[safe] = {"contig": contig, "length": len(sequence),
                         "sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest()}
    if not mapping:
        raise ValueError("No assembly contigs")
    write_json(destination / "contigs.json", mapping)
    write_json(destination / "batches.json", make_plan(mapping, batch_size))


def prepare_ccf_execution(config):
    """Validate static installation/settings once for one bounded worker."""
    import yaml

    root = Path(config["database"]).resolve(strict=True)
    level = ccf._int(config.get("min_evidence_level", 3), "minimum array evidence level", 1)
    if level > 4:
        raise ValueError("Minimum CRISPR evidence level must be 1..4")
    script = root / "CRISPRCasFinder.pl"
    if pf2._sha256(script) != ccf.SOURCE_SHA256:
        raise ValueError("CRISPRCasFinder source checksum differs from pinned 4.3.2")
    library = pf2._nonempty(root / "sel392v2.so")
    models = root / "models" / "CASFinder"
    model_info = yaml.safe_load((models / "metadata.yml").read_text())
    if (not isinstance(model_info, dict) or str(model_info.get("vers")) != ccf.CAS_MODELS_VERSION
            or not any((models / "profiles").glob("*.hmm"))
            or not any((models / "definitions").rglob("*.xml"))):
        raise ValueError("Prepared CASFinder 3.1.0 models are required")
    return {"script": script, "library": library, "models": models, "root": root,
            "level": level, "settings": dict(config)}


def run_contig(config, staged, safe, destination, logfile, *, prepared=None, contig_name=None):
    """One native invocation; return the parsed output already verified here."""
    from safety.runtime import DirectRunner

    prepared = prepare_ccf_execution(config) if prepared is None else prepared
    if prepared["settings"] != config:
        raise ValueError("CRISPR prepared execution settings changed within a worker")
    root, script, library, models = (prepared[key] for key in ("root", "script", "library", "models"))
    level = prepared["level"]
    sequences = read_fasta(staged)
    if list(sequences) != [safe]:
        raise ValueError("Staged CRISPR contig identity mismatch")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("Contig output already exists; Snakemake must clean incomplete outputs")
    work = Path(tempfile.mkdtemp(prefix="basis-ccf-", dir="/tmp"))
    try:
        shutil.copyfile(script, work / "CRISPRCasFinder.pl")
        for name in ("COPYING", "COPYRIGHT"):
            if (root / name).is_file():
                shutil.copyfile(root / name, work / name)
        (work / "sel392v2.so").symlink_to(library)
        (work / "models").symlink_to(models.parent, target_is_directory=True)
        shutil.copyfile(staged, work / "input.fna")
        (work / "macsyfinder.conf").write_text("[directories]\nmodels_dir = models\n", encoding="ascii")
        (work / "bin").mkdir()
        shim = work / "bin" / "macsyfinder"
        shim.write_text(ccf._MACSY_SHIM)
        shim.chmod(0o700)
        (work / "basis_ccf_launch.py").write_text(ccf._CCF_LAUNCHER)
        log_offset = Path(logfile).stat().st_size if Path(logfile).exists() else 0
        DirectRunner(config, logfile).run([
            "python", "basis_ccf_launch.py", config.get("executable", "perl"), "CRISPRCasFinder.pl",
            "-in", "input.fna", "-out", "native", "-so", "./sel392v2.so", "-cas", "-keep", "-log",
            "-cpuM", str(config.get("threads", 1)), "-gcode", str(config.get("genetic_code", 11))],
            cwd=work, stdout_path=work / "stdout.txt")
        with Path(logfile).open("rb") as handle:
            handle.seek(log_offset)
            if ccf._ERROR.search(ccf._ANSI.sub("", handle.read().decode("utf-8", errors="replace"))):
                raise ValueError("CRISPRCasFinder command log records a native error")
        stdout = (work / "stdout.txt").read_text()
        rows, status = ccf.parse_contig(work / "native", safe, contig_name or safe,
                                       sequences[safe], stdout, level)
        ccf._verify_runtime_isolation(work, status)
        shutil.move(str(work), destination)
        return {"findings": rows, "status": status}
    except BaseException:
        # Native diagnostics remain linked from the rule's log; no success output.
        failed = destination.parent / (safe + ".failed-" + work.name)
        if work.exists():
            shutil.move(str(work), failed)
        with Path(logfile).open("a") as log:
            log.write(f"\nFailed contig diagnostics: {failed}\n")
        raise


def collect_ccf(sample, config, contigs, native, verified_parsed=None):
    mapping = read_json(Path(contigs) / "contigs.json")
    sequences = read_fasta(sample["assembly"])
    if {r["contig"] for r in mapping.values()} != set(sequences):
        raise ValueError("CRISPR contig map does not cover supplied assembly")
    if verified_parsed is not None and set(verified_parsed) != set(mapping):
        raise ValueError("Verified CRISPR parsed records do not cover supplied assembly")
    findings, statuses, artifacts = [], {}, [str(Path(contigs) / "contigs.json")]
    for safe, info in mapping.items():
        sequence = sequences[info["contig"]]
        if len(sequence) != info["length"] or hashlib.sha256(sequence.encode("ascii")).hexdigest() != info["sequence_sha256"]:
            raise ValueError("CRISPR staged contig differs from supplied assembly")
        work = Path(native) / safe
        if verified_parsed is None:
            stdout = ccf._read(work / "stdout.txt")
            rows, status = ccf.parse_contig(work / "native", safe, info["contig"], sequence, stdout,
                                           int(config.get("min_evidence_level", 3)))
            ccf._verify_runtime_isolation(work, status)
        else:
            rows, status = verified_parsed[safe]["findings"], verified_parsed[safe]["status"]
        findings.extend(rows)
        statuses[info["contig"]] = status
        artifacts.append(str(work))
    arrays = [f for f in findings if f["attributes"]["feature_type"] == "array"]
    complete = all(v["status"] == "completed" for v in statuses.values())
    return {"findings": findings, "artifacts": artifacts,
        "interpretation": "detected" if findings else ("not_detected" if complete else "inconclusive"),
        "metrics": {"crisprcasfinder_version": ccf.VERSION, "source_sha256": ccf.SOURCE_SHA256,
            "macsyfinder_version": ccf.MACSYFINDER_VERSION, "casfinder_models_version": ccf.CAS_MODELS_VERSION,
            "array_count": len(arrays), "array_count_at_reporting_level": sum(f["attributes"]["passes_threshold"] for f in arrays),
            "repeat_count": sum(f["attributes"]["repeat_count"] for f in arrays),
            "spacer_count": sum(f["attributes"]["spacer_count"] for f in arrays),
            "cas_system_count": (sum(v["system_count"] or 0 for v in statuses.values())
                                 if any(v.get("mode") == "gembase" for v in statuses.values()) else None),
            "typed_cas_assessed_contig_count": sum(v.get("mode") == "gembase" for v in statuses.values()),
            "cas_unordered_candidate_model_count": sum(v["candidate_model_count"] or 0 for v in statuses.values()),
            "cas_assessment": "completed" if complete else "partially_assessed",
            "cas_contig_assessments": statuses, "assessed_contigs": list(sequences),
            "assessed_contig_count": len(sequences), "genetic_code": int(config.get("genetic_code", 11)),
            "completion_check": "all_contig_JSON_native_Cas_artifacts_and_coordinates_verified"},
        "notes": ["CRISPR/Cas predictions describe genomic context, not activity, pathogenicity or organism safety.",
                  "Upstream circular topology is an algorithm setting, not confirmed assembly topology.",
                  "Short-contig unordered candidates are retained without asserting typed systems.",
                  "No predicted CDS means Cas was not assessed; it does not establish Cas absence."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["embed", "classify", "pf2-collect", "ccf-prepare", "ccf-contig", "ccf-batch", "ccf-collect", "normalize"])
    parser.add_argument("--sample", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--validation")
    parser.add_argument("--embeddings")
    parser.add_argument("--manifest")
    parser.add_argument("--native")
    parser.add_argument("--contigs")
    parser.add_argument("--contig")
    parser.add_argument("--batch")
    parser.add_argument("--batch-receipts", nargs="+")
    parser.add_argument("--log-directory")
    parser.add_argument("--log")
    parser.add_argument("--threads", type=int)
    parser.add_argument("--module", choices=["pathogenfinder2", "crisprcasfinder"])
    args = parser.parse_args(argv)
    # Contig workers need only the staged input and their module settings.
    # sample.json contains the full annotation coordinate map and can be large.
    sample = None if args.stage in {"ccf-contig", "ccf-batch"} else read_json(args.sample)
    module = args.module or ("crisprcasfinder" if args.stage.startswith("ccf-") else "pathogenfinder2")
    full_config = read_json(args.config)
    config = settings(full_config, module)
    if args.threads is not None:
        config["threads"] = args.threads
    if args.stage == "normalize":
        write_json(args.output, normalize_saved(module, sample, full_config,
                   read_json(args.validation), args.native))
    elif args.stage == "embed":
        embed(sample, config, args.output, args.manifest)
    elif args.stage == "classify":
        classify(sample, config, args.embeddings, args.manifest, args.output, args.log)
    elif args.stage == "ccf-prepare":
        prepare_contigs(sample, args.output, config.get("batch_size", 32))
    elif args.stage == "ccf-contig":
        run_contig(config, Path(args.contigs) / f"{args.contig}.fna", args.contig, args.output, args.log)
    elif args.stage == "ccf-batch":
        from safety.ccf_batches import run_batch
        if not all((args.batch, args.contigs, args.native, args.log_directory)):
            parser.error("ccf-batch requires --batch, --contigs, --native and --log-directory")
        run_batch(full_config, args.contigs, args.batch, args.native,
                  args.output, args.log_directory, threads=args.threads)
    else:
        audit = None
        if args.stage == "ccf-collect":
            from safety.ccf_batches import verify_batches
            if not args.batch_receipts:
                parser.error("ccf-collect requires --batch-receipts")
            audit = verify_batches(full_config, args.contigs, args.native, args.batch_receipts)
        parsed = (collect_pf2(sample, config, args.native, args.embeddings, args.manifest)
                  if args.stage == "pf2-collect" else collect_ccf(sample, config, args.contigs, args.native,
                                                                 verified_parsed=audit["parsed"] if audit else None))
        if audit:
            parsed["metrics"].update(scheduler_batch_count=audit["batch_count"],
                                     scheduler_contigs_per_batch=audit["batch_size"])
            parsed["artifacts"].extend(audit["artifacts"])
        write_json(args.output, parsed)


if __name__ == "__main__":
    main()
