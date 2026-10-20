"""Classify verified embeddings using PF2's pretrained prediction defaults.

The pinned PF2 release validates --config with its training schema, even when
the CLI action is predict. Use its default prediction loader instead, after
verifying the bundled ensemble against the prepared model files. This helper
is a classification-only dependency so a fix here does not invalidate the
expensive, independently completed embedding job.
"""
from __future__ import annotations

import argparse
from importlib.metadata import version
from pathlib import Path
import shutil
import tempfile

from basis.adapters import extend as pf2
from safety.extend import (
    check_pf2_config, read_json, select_device, settings, validate_embeddings,
    write_json,
)
from safety.runtime import DirectRunner


def prepare_classifier_configuration(config):
    """Return actual default prediction settings and verified weight provenance."""
    from pathogenfinder2.utils.configuration import ConfigurationPF2

    # This is the same loader/schema path used by `pathogenfinder2 predict`
    # without --config. Do not fabricate a training configuration to satisfy
    # the unrelated USER_CONFIG_SCHEMA.
    value = dict(ConfigurationPF2(mode="Prediction"))
    weights = [Path(p).resolve(strict=True)
               for p in value["Model Parameters"]["Network Weights"]]
    if len(weights) != 4:
        raise ValueError("The pinned pretrained PF2 ensemble requires four bundled weights")
    actual = {str(p): pf2._sha256(pf2._nonempty(p)) for p in weights}
    if len(actual) != 4:
        raise ValueError("The pinned pretrained PF2 ensemble requires four distinct weight files")
    prepared = {}
    if config.get("weights_model"):
        selected = [Path(config["weights_model"]) / f"weights_model{n}.pt"
                    for n in range(1, 5)]
        for source, bundled in zip(selected, weights):
            source = pf2._nonempty(source).resolve(strict=True)
            digest = pf2._sha256(source)
            if digest != actual[str(bundled)]:
                raise ValueError("Prepared classifier weights differ from the pinned pretrained ensemble")
            prepared[str(source)] = digest
    value["Model Parameters"]["Network Weights"] = [str(p) for p in weights]
    return value, {
        "schema": "basis-pf2-classifier-weights-v2",
        "pf2_source_commit": pf2.PF2_SOURCE_COMMIT,
        "configuration_mode": "upstream_prediction_defaults",
        "matches_pinned_bundled_ensemble": True,
        "sha256": actual,
        "prepared_sha256": prepared,
    }


def classify(sample, config, embeddings, manifest, destination, logfile):
    """One native prediction job; input embeddings and manifest remain read-only."""
    check_pf2_config(config)
    if version("PathogenFinder2") != pf2.PF2_VERSION:
        raise ValueError("Classification requires pinned PathogenFinder2 0.7.1")
    embeddings = Path(embeddings).resolve(strict=True)
    pf2._validate_reuse(embeddings, Path(manifest), Path(sample["proteins"]),
                        config["prot_t5_identity"])
    validate_embeddings(embeddings, sample["proteins"])
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("Classifier output already exists; Snakemake must clean incomplete outputs")
    effective_config, weight_audit = prepare_classifier_configuration(config)
    device = select_device(config)
    stage = Path(tempfile.mkdtemp(prefix="classifier-attempt-", dir=destination.parent))
    native_config = stage / "classifier-config.json"
    write_json(native_config, effective_config)
    write_json(stage / "classifier-weights.json", weight_audit)
    # --config incorrectly selects PF2's training/fine-tuning schema. The
    # defaults use the identical, checksum-verified bundled ensemble above.
    argv = [config.get("executable", "pathogenfinder2"), "predict", "-i", str(embeddings),
            "-f", "embeddings", "-o", str(stage)]
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
    shutil.copyfile(stage / "classifier-weights.json", native / "classifier-weights.json")
    write_json(native / "device.json", device)
    native.rename(destination)
    shutil.rmtree(stage)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sample", "config", "embeddings", "manifest", "output", "log"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--threads", type=int)
    args = parser.parse_args(argv)
    config = settings(args.config, "pathogenfinder2")
    if args.threads is not None:
        config["threads"] = args.threads
    classify(read_json(args.sample), config, args.embeddings, args.manifest,
             args.output, args.log)


if __name__ == "__main__":
    main()
