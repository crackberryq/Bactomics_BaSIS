"""PathogenFinder2 inference and strict, source-matched output handling.

Native schemas were inspected at PF2_SOURCE_COMMIT.  PF2's ensemble outputs
are model scores; attention is a ranking signal, not a virulence annotation.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import re
import statistics


PF2_SOURCE_COMMIT = "3837caf6710da5da3038f5d1246dd82ab26e003a"
PF2_VERSION = "0.7.1"

# Executed by runner in the SAME environment as PF2. Avoid loading PF2's
# object-dtype protIDs array with pickle. Names in the HDF5 are the exact
# ordered IDs used by ProteomeDataset.open_embedfile at the pinned commit.
_ATTENTION_EXPORT = r'''
import json, sys
import h5py
import numpy as np
with np.load(sys.argv[1], allow_pickle=False) as data:
    attention = data["attentions"]
    if attention.ndim != 2 or attention.dtype.kind not in "fiu":
        raise ValueError("Expected a numeric model-by-token attention matrix")
    scores = attention.astype(float).tolist()
with h5py.File(sys.argv[2], "r") as data:
    if "Embeddings" not in data or "Names" not in data:
        raise ValueError("Expected the pinned PF2 HDF5 Embeddings/Names schema")
    names = [v.decode("utf-8") if isinstance(v, bytes) else str(v)
             for v in data["Names"][:]]
    if data["Embeddings"].shape[0] != len(names):
        raise ValueError("Embedding Names and Embeddings have different lengths")
    if int(data.attrs["Amount Embeddings"]) != len(names):
        raise ValueError("Embedding count attribute mismatch")
print(json.dumps({"schema": "basis-pf2-attention-v1", "token_ids": names,
                  "model_scores": scores}, allow_nan=False))
'''


def _nonempty(path: Path) -> Path:
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"Missing or empty PathogenFinder2 output: {path}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _number(value, label: str, validate_scores: bool = True) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric, not boolean")
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric {label}: {value!r}") from exc
    if not math.isfinite(score):
        raise ValueError(f"Non-finite {label}")
    if validate_scores and not 0 <= score <= 1:
        raise ValueError(f"{label} is outside [0, 1]")
    return score


def parse_predictions(path: Path, *, expected_models: int = 4,
                      expected_filename: str = "PF2",
                      validate_scores: bool = True) -> dict:
    """Read the single-input PF2 TSV, retaining native ensemble statistics.

    Comments are ignored, but missing columns, extra rows, duplicated headers,
    unknown sample IDs and inconsistent model statistics are errors.
    """
    if expected_models < 1:
        raise ValueError("expected_models must be positive")
    _nonempty(path)
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(
            (line for line in handle if line.strip() and not line.startswith("#")),
            delimiter="\t",
        )
        fields = reader.fieldnames or []
        required = {"File Name", "Prediction Mean", "Prediction STD",
                    "Binary Prediction Mean", "Phenotype"}
        model_fields = [f"Prediction_{index}" for index in range(expected_models)]
        required.update(model_fields)
        if len(fields) != len(set(fields)) or not required.issubset(fields):
            raise ValueError("Unsupported PF2 prediction header or missing required columns")
        actual_model_fields = {f for f in fields if re.fullmatch(r"Prediction_\d+", f)}
        if actual_model_fields != set(model_fields):
            raise ValueError("PF2 ensemble member count differs from expected_models")
        rows = list(reader)
    if len(rows) != 1 or None in rows[0] or any(v is None for v in rows[0].values()):
        raise ValueError("Expected exactly one complete PF2 prediction row")
    row = rows[0]
    if row["File Name"] != expected_filename:
        raise ValueError(f"PF2 sample mismatch: {row['File Name']!r}")
    members = [_number(row[f], f, validate_scores) for f in model_fields]
    mean = _number(row["Prediction Mean"], "Prediction Mean", validate_scores)
    std = _number(row["Prediction STD"], "Prediction STD", validate_scores)
    if std < 0:
        raise ValueError("Negative Prediction STD")
    # Native inference can use half precision; retain native values, allowing
    # the small rounding differences from its NumPy summary calculations.
    if not math.isclose(mean, statistics.mean(members), rel_tol=.005, abs_tol=.001):
        raise ValueError("Prediction Mean is inconsistent with ensemble members")
    if not math.isclose(std, statistics.pstdev(members), rel_tol=.005, abs_tol=.001):
        raise ValueError("Prediction STD is inconsistent with ensemble members")
    binary_float = _number(row["Binary Prediction Mean"], "binary prediction")
    if binary_float not in (0.0, 1.0):
        raise ValueError("Binary Prediction Mean must be 0 or 1")
    binary = int(binary_float)
    label = "Human Pathogenic" if binary else "Human Non Pathogenic"
    if binary != int(mean > .5) or row["Phenotype"] != label:
        raise ValueError("PF2 native label is inconsistent with its score/threshold")
    return {
        "prediction_mean": mean, "prediction_std": std,
        "ensemble_member_scores": members, "ensemble_member_count": len(members),
        "native_binary_prediction": binary, "native_phenotype": row["Phenotype"],
        "uncertainty_type": "between_model_standard_deviation",
        "score_scope": "model human-pathogenic-capacity prediction; not exposure risk",
        "native_row": row,
    }


def _proteome_headers(proteins: Path) -> dict[str, str]:
    """Map PF2's complete-header normalization to original protein IDs."""
    headers = {}
    identifiers = set()
    with _nonempty(proteins).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith(">"):
                continue
            title = line[1:].strip()
            if not title:
                raise ValueError("Empty protein FASTA header")
            protein_id = title.split()[0]
            normalized = line.replace(">", "").strip().replace("/", "_")
            if protein_id in identifiers or normalized in headers:
                raise ValueError("Duplicate/colliding protein IDs in full proteome")
            headers[normalized] = protein_id
            identifiers.add(protein_id)
    if not headers:
        raise ValueError("No protein records in full proteome")
    return headers


def parse_attention_json(path: Path, proteins: Path, *, expected_models: int = 4,
                         validate_scores: bool = True) -> list[dict]:
    """Normalize native attention tokens, aggregating long-protein chunks.

    PF2 splits long proteins into '_Kmer-N' embedding tokens. Sum each model's
    weights across that protein's tokens, retain token scores, then rank on
    the maximum of these per-model protein sums. This aggregation is BaSIS's
    transparent ranking convention, not an upstream virulence classification.
    """
    with _nonempty(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or data.get("schema") != "basis-pf2-attention-v1":
        raise ValueError("Unsupported attention export schema")
    token_ids, matrix = data.get("token_ids"), data.get("model_scores")
    if (not isinstance(token_ids, list) or not token_ids
            or any(not isinstance(v, str) or not v for v in token_ids)
            or len(set(token_ids)) != len(token_ids)):
        raise ValueError("Missing, duplicate, or invalid attention token IDs")
    if (not isinstance(matrix, list) or len(matrix) != expected_models
            or any(not isinstance(row, list) or len(row) != len(token_ids) for row in matrix)):
        raise ValueError("Attention shape does not match ensemble and token counts")
    matrix = [[_number(v, "attention weight", validate_scores) for v in row] for row in matrix]
    if any(v < 0 for row in matrix for v in row):
        raise ValueError("Negative attention weight")
    if validate_scores and any(not math.isclose(sum(row), 1.0, rel_tol=.02, abs_tol=.02)
                               for row in matrix):
        raise ValueError("Attention weights do not sum to one per model")
    headers = _proteome_headers(proteins)
    aggregate = {}
    for index, token in enumerate(token_ids):
        header = token
        if header not in headers:
            match = re.fullmatch(r"(.+)_Kmer-\d+", header)
            if not match or match.group(1) not in headers:
                raise ValueError(f"Attention protein is absent from input proteome: {token}")
            header = match.group(1)
        protein_id = headers[header]
        record = aggregate.setdefault(protein_id, {
            "protein_id": protein_id, "token_ids": [], "token_scores": [],
            "model_attention_sums": [0.0] * expected_models,
        })
        scores = [row[index] for row in matrix]
        record["token_ids"].append(token)
        record["token_scores"].append(scores)
        record["model_attention_sums"] = [a + b for a, b in zip(record["model_attention_sums"], scores)]
    if set(aggregate) != set(headers.values()):
        raise ValueError("Attention does not cover the full input proteome")
    for record in aggregate.values():
        record["attention_max"] = max(record["model_attention_sums"])
        record["attention_mean"] = statistics.mean(record["model_attention_sums"])
        record["embedding_token_count"] = len(record["token_ids"])
        record["aggregation"] = "sum token weights within each protein/model; rank by maximum model sum"
    ranked = sorted(aggregate.values(), key=lambda r: (-r["attention_max"], r["protein_id"]))
    for rank, record in enumerate(ranked, 1):
        record["rank"] = rank
    return ranked


def _validate_reuse(embeddings: Path, manifest_path: Path, proteins: Path,
                    model_identity: str) -> dict:
    _nonempty(embeddings)
    with _nonempty(manifest_path).open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    required = {
        "schema": "basis-pf2-embeddings-v1",
        "proteome_sha256": _sha256(proteins),
        "embeddings_sha256": _sha256(embeddings),
        "pf2_source_commit": PF2_SOURCE_COMMIT,
        "prot_t5_identity": model_identity,
    }
    if not isinstance(manifest, dict) or any(manifest.get(k) != v for k, v in required.items()):
        raise ValueError("Embedding reuse manifest does not match proteome, embeddings, or model provenance")
    return manifest


def run(module: str, sample: dict, config: dict, outdir: Path, runner) -> dict:
    if module != "pathogenfinder2":
        raise ValueError(f"Unsupported Extend module: {module}")
    if config.get("pf2_source_commit", PF2_SOURCE_COMMIT) != PF2_SOURCE_COMMIT:
        raise ValueError("Configured PF2 source revision is outside this adapter's inspected contract")
    proteins = Path(sample["proteins"]).resolve()
    headers = _proteome_headers(proteins)
    expected_models = int(config.get("expected_models", 4))
    if expected_models < 1:
        raise ValueError("expected_models must be positive")
    attention = config.get("attention", True)
    map_landscape = config.get("map", False)
    if not isinstance(attention, bool) or not isinstance(map_landscape, bool):
        raise ValueError("attention and map must be booleans")
    top_n = int(config.get("attention_top_n", 20))
    if top_n < 0:
        raise ValueError("attention_top_n must be nonnegative (0 exports scores without findings)")
    validate_scores = config.get("validate_score_ranges", True)
    if not isinstance(validate_scores, bool):
        raise ValueError("validate_score_ranges must be a boolean")
    outdir = Path(outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    native = outdir / "native"
    if (native / "predictionPF2").exists():
        raise ValueError("Refusing to mix PF2 results with an existing native/predictionPF2 directory")
    native.mkdir(exist_ok=True)
    model_identity = str(config.get("prot_t5_identity", f"upstream-default@{PF2_SOURCE_COMMIT}"))
    if config.get("prot_t5_path") and not config.get("prot_t5_identity"):
        raise ValueError("Local prot_t5_path requires an explicit prot_t5_identity (version or digest)")
    embeddings = Path(config["embeddings"]).resolve() if config.get("embeddings") else None
    reused = embeddings is not None
    if reused:
        if not config.get("embeddings_manifest"):
            raise ValueError("Embedding reuse requires embeddings_manifest")
        _validate_reuse(embeddings, Path(config["embeddings_manifest"]), proteins, model_identity)
    elif config.get("embeddings_manifest"):
        raise ValueError("embeddings_manifest was set without embeddings")
    argv = [str(config.get("executable", "pathogenfinder2")), "predict",
            "-i", str(embeddings or proteins), "-o", str(native),
            "-f", "embeddings" if reused else "proteome"]
    if attention:
        argv += ["--attProteins", "report"]
    if map_landscape:
        argv += ["--embedProteome", "map"]
    for key, flag in [("prot_t5_path", "--protT5Path"), ("weights_model", "--weightsModel")]:
        if config.get(key):
            argv += [flag, str(config[key])]
    from ..devices import verify_inference_device
    device = verify_inference_device(config, runner)
    runner.run(argv, cwd=outdir)
    result = collect_outputs(sample, config, outdir, runner, native=native)
    if device is not None:
        result.setdefault("metrics", {})["inference_device"] = device
    return result


def collect_outputs(sample, config, outdir, runner, *, native=None):
    """Parse existing native outputs; no embedding or classifier inference."""
    proteins = Path(sample["proteins"]).resolve()
    headers = _proteome_headers(proteins)
    outdir = Path(outdir).resolve()
    native = Path(native).resolve() if native else outdir / "native"
    expected_models = int(config.get("expected_models", 4))
    attention = config.get("attention", True)
    top_n = int(config.get("attention_top_n", 20))
    map_landscape = config.get("map", False)
    validate_scores = config.get("validate_score_ranges", True)
    model_identity = str(config.get("prot_t5_identity", f"upstream-default@{PF2_SOURCE_COMMIT}"))
    embeddings = Path(config["embeddings"]).resolve() if config.get("embeddings") else None
    reused = embeddings is not None
    result_dir = native / "predictionPF2" / "results_PF2"
    prediction_path = result_dir / "predictions.tsv"
    metrics = parse_predictions(prediction_path, expected_models=expected_models,
                                validate_scores=validate_scores)
    embeddings = embeddings or native / "predictionPF2" / "preprocess_PF2" / "ProteinEmbeddings.h5"
    _nonempty(embeddings)
    manifest = {
        "schema": "basis-pf2-embeddings-v1", "proteome_sha256": _sha256(proteins),
        "embeddings_sha256": _sha256(embeddings), "pf2_source_commit": PF2_SOURCE_COMMIT,
        "prot_t5_identity": model_identity, "embeddings_path": str(embeddings),
        "input_proteome": str(proteins), "protein_count": len(headers),
        "upstream_model_weights_fully_pinned": bool(config.get("prot_t5_identity")),
    }
    manifest_path = outdir / "embeddings_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    findings = []
    artifacts = [str(prediction_path), str(embeddings), str(manifest_path)]
    if attention:
        attention_npz = _nonempty(result_dir / "attentions.npz")
        attention_json = outdir / "attention_native.json"
        runner.run([str(config.get("python_executable", "python")), "-c", _ATTENTION_EXPORT,
                    str(attention_npz), str(embeddings)], stdout_path=attention_json, cwd=outdir)
        scores = parse_attention_json(attention_json, proteins, expected_models=expected_models,
                                      validate_scores=validate_scores)
        scores_path = outdir / "attention_scores.tsv"
        with scores_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["protein_id", "rank", "attention_max", "attention_mean",
                             "embedding_token_count", "model_attention_sums", "token_ids"])
            for entry in scores:
                writer.writerow([entry["protein_id"], entry["rank"], entry["attention_max"],
                                 entry["attention_mean"], entry["embedding_token_count"],
                                 json.dumps(entry["model_attention_sums"]), json.dumps(entry["token_ids"])])
        artifacts.extend(map(str, [attention_npz, attention_json, scores_path]))
        for entry in scores[:top_n]:
            findings.append({
                "tool": "pathogenfinder2", "category": "ml_attention",
                "gene": None, "family": None, "protein_id": entry["protein_id"],
                "contig": None, "start": None, "end": None, "strand": None,
                "reference": f"PathogenFinder2@{PF2_SOURCE_COMMIT}",
                "identity": None, "coverage": None, "evidence_level": "exploratory",
                "interpretation": "Model attention prioritization; not proof of virulence or causality",
                "attributes": entry,
            })
        metrics["attention_protein_count"] = len(scores)
        metrics["attention_selected_count"] = len(findings)
    if map_landscape:
        for filename in ("embeddings.npz", "closeneighbors_bpl.tsv", "mapped_bpl.png"):
            artifacts.append(str(_nonempty(result_dir / filename)))
    metrics.update({"input_protein_count": len(headers), "embeddings_reused": reused,
                    "embedding_manifest": str(manifest_path), "adapter_source_commit": PF2_SOURCE_COMMIT,
                    "expected_tool_version": PF2_VERSION, "landscape_requested": map_landscape})
    return {
        "findings": findings, "metrics": metrics, "artifacts": artifacts,
        "notes": [
            "Inference uses the full supplied proteome and pretrained PF2 models; no training or MC dropout.",
            "The native standard deviation describes disagreement among model members, not a confidence interval.",
            "Attention ranks proteins for review; it does not establish virulence, function, or causal contribution.",
            "ProtT5 default remote weights are not fully pinned unless a reviewed local model identity is supplied.",
            "Native execution requires the pinned PF2 environment; parser tests alone do not validate the biological model.",
        ],
        "interpretation": "inconclusive",
    }
