"""Synthetic contract/restart tests; no inference or biological validation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety import extend

try:
    import h5py
    import numpy as np
except ImportError:
    h5py = np = None


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def predictions(path):
    fields = ["File Name", "Prediction Mean", "Prediction STD", "Binary Prediction Mean",
              "Phenotype", "Prediction_0", "Prediction_1", "Prediction_2", "Prediction_3"]
    values = ["PF2", "0.25", "0.11180339887498948", "0", "Human Non Pathogenic", "0.1", "0.2", "0.3", "0.4"]
    write(path, "\t".join(fields) + "\n" + "\t".join(values) + "\n")


@unittest.skipIf(h5py is None, "h5py/numpy required for pinned HDF5 contract fixtures")
class PF2StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="extend fixtures ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proteins = self.root / "proteins.faa"
        self.proteins.write_text(">P1 descriptive / title\nMACDE\n>P2 long descriptive title\nMACDEF\n")
        self.sample = {"sample_id": "fixture", "proteins": str(self.proteins)}
        self.config = {"enabled": True, "prot_t5_identity": "fixture@immutable", "gpus": 0,
                       "expected_models": 4, "attention": True, "attention_top_n": 2}
        self.embedding = self.root / "ProteinEmbeddings.h5"
        self.names = ["P1 descriptive _ title", "P2 long descriptive title_Kmer-1", "P2 long descriptive title_Kmer-2"]
        with h5py.File(self.embedding, "w") as data:
            data.create_dataset("Names", data=self.names, dtype=h5py.string_dtype())
            data.create_dataset("Embeddings", data=np.zeros((3, 1024), dtype=np.float32))
            data.attrs["Amount Proteins"] = 2
            data.attrs["Amount Embeddings"] = 3
            data.attrs["Pooled"] = "mean"
        self.manifest = self.root / "embeddings_manifest.json"
        extend.write_json(self.manifest, extend.embedding_manifest(self.embedding, self.proteins, self.config))
        self.native = self.root / "predictionPF2"
        self.results = self.native / "results_PF2"
        predictions(self.results / "predictions.tsv")
        np.savez(self.results / "attentions.npz", attentions=np.array([[.2, .3, .5]] * 4))

    def test_full_headers_and_split_tokens_preserve_exact_original_coverage(self):
        self.assertEqual(extend.validate_embeddings(self.embedding, self.proteins),
                         {"protein_count": 2, "embedding_token_count": 3})
        parsed = extend.collect_pf2(self.sample, self.config, self.native, self.embedding, self.manifest)
        self.assertEqual(parsed["metrics"]["ensemble_member_scores"], [.1, .2, .3, .4])
        self.assertEqual(parsed["metrics"]["prediction_mean"], .25)
        self.assertEqual(parsed["metrics"]["attention_protein_count"], 2)
        self.assertEqual([f["protein_id"] for f in parsed["findings"]], ["P2", "P1"])
        self.assertEqual(parsed["interpretation"], "inconclusive")
        self.assertTrue(all(f["evidence_level"] == "exploratory" for f in parsed["findings"]))

    def test_wrong_hdf5_count_or_finite_values_fail_before_inference(self):
        with h5py.File(self.embedding, "a") as data:
            data.attrs["Amount Proteins"] = 1
        with self.assertRaisesRegex(ValueError, "schema/count"):
            extend.validate_embeddings(self.embedding, self.proteins)
        with h5py.File(self.embedding, "a") as data:
            data.attrs["Amount Proteins"] = 2
            data["Embeddings"][1, 1] = float("nan")
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            extend.validate_embeddings(self.embedding, self.proteins)

    def test_embedding_uses_the_same_pinned_upstream_arguments(self):
        calls = []
        fixture = self.embedding
        fake = types.ModuleType("pathogenfinder2.preprocessing.embedder")
        class Embedder:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))
            def get_embeddings(self, **kwargs):
                calls.append(("embed", kwargs))
                shutil.copyfile(fixture, kwargs["emb_path"])
        fake.ProtT5Embedder = Embedder
        model = self.root / "model"
        model.mkdir()
        target = self.root / "separate" / "ProteinEmbeddings.h5"
        manifest = target.with_name("embeddings_manifest.json")
        with patch("importlib.metadata.version", return_value="0.7.1"), \
             patch("safety.extend.select_device", return_value={"selected": "cpu"}), \
             patch.dict(sys.modules, {"pathogenfinder2.preprocessing.embedder": fake}), patch.dict(os.environ):
            extend.embed(self.sample, {**self.config, "prot_t5_path": str(model)}, target, manifest)
        self.assertEqual(calls[0], ("init", {"model_dir": str(model)}))
        self.assertEqual(calls[1][1]["seq_path"], self.proteins)
        self.assertEqual(calls[1][1]["pool_mode"], "mean")
        self.assertIs(calls[1][1]["split_kmer"], True)
        self.assertEqual(target.read_bytes(), self.embedding.read_bytes())
        self.assertEqual(extend.read_json(manifest)["inference_device"]["selected"], "cpu")

    def test_missing_protein_is_not_silently_accepted(self):
        with h5py.File(self.embedding, "a") as data:
            data["Names"][1] = "unknown_Kmer-1"
        with self.assertRaisesRegex(ValueError, "absent"):
            extend.validate_embeddings(self.embedding, self.proteins)

    def test_attention_shape_mismatch_fails_output_collection(self):
        np.savez(self.results / "attentions.npz", attentions=np.array([[.5, .5]] * 4))
        with self.assertRaisesRegex(ValueError, "Attention shape"):
            extend.collect_pf2(self.sample, self.config, self.native, self.embedding, self.manifest)

    def test_classifier_failure_keeps_verified_embeddings_unchanged(self):
        expected = self.embedding.read_bytes()
        target = self.root / "retry" / "predictionPF2"
        with patch("safety.extend.prepare_classifier_configuration", return_value={"Model Parameters": {"Network Weights": []}}), \
             patch("safety.extend.select_device", return_value={"selected": "cpu"}), \
             patch("safety.runtime.DirectRunner.run", side_effect=RuntimeError("synthetic native failure")), \
             patch.dict(os.environ):
            with self.assertRaisesRegex(RuntimeError, "synthetic native failure"):
                extend.classify(self.sample, self.config, self.embedding, self.manifest,
                                target, self.root / "command.log")
        self.assertEqual(self.embedding.read_bytes(), expected)
        self.assertFalse(target.exists())
        extend.pf2._validate_reuse(self.embedding, self.manifest, self.proteins, "fixture@immutable")

    def test_classifier_uses_embedding_input_and_never_repeats_embedder(self):
        target = self.root / "retry" / "predictionPF2"
        invocations = []
        def native_run(runner, argv, **kwargs):
            invocations.append(argv)
            folder = Path(argv[argv.index("-o") + 1]) / "predictionPF2" / "results_PF2"
            predictions(folder / "predictions.tsv")
            np.savez(folder / "attentions.npz", attentions=np.array([[.2, .3, .5]] * 4))
        with patch("safety.extend.prepare_classifier_configuration", return_value={"Model Parameters": {"Network Weights": []}}), \
             patch("safety.extend.select_device", return_value={"selected": "cpu"}), \
             patch("safety.runtime.DirectRunner.run", native_run), patch.dict(os.environ):
            extend.classify(self.sample, self.config, self.embedding, self.manifest,
                            target, self.root / "command.log")
        self.assertEqual(len(invocations), 1)
        self.assertEqual(invocations[0][0:2], ["pathogenfinder2", "predict"])
        self.assertEqual(invocations[0][invocations[0].index("-f") + 1], "embeddings")
        self.assertEqual(invocations[0][invocations[0].index("-i") + 1], str(self.embedding))
        self.assertTrue((target / "results_PF2/predictions.tsv").is_file())


class ExtendMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_scope_hold_does_not_open_absent_model_results(self):
        config = {"modules": {"pathogenfinder2": {"enabled": False}},
                  "module_decisions": {"pathogenfinder2": {"action": "disabled", "reason": "user_disabled"}}}
        result = extend.normalize_saved("pathogenfinder2", {"sample_id": "x"}, config,
                                        {}, self.root / "absent")
        self.assertEqual(result["execution_status"], "not_assessed")
        self.assertEqual(result["reason"], "user_disabled")

    def test_explicit_classifier_weights_are_supplied_through_config(self):
        fake = types.ModuleType("pathogenfinder2.utils.configuration")
        root = self.root
        class Configuration:
            @staticmethod
            def load_baseconfig():
                return {"Model Parameters": {"Network Weights": []}}
            @staticmethod
            def load_baseweights(value):
                value["Model Parameters"]["Network Weights"] = [root / f"weights_model{n}.pt" for n in range(1, 5)]
                return value
        fake.ConfigurationPF2 = Configuration
        for n in range(1, 5):
            (self.root / f"weights_model{n}.pt").write_bytes(b"fixture")
        with patch.dict(sys.modules, {"pathogenfinder2.utils.configuration": fake}):
            config = extend.prepare_classifier_configuration({"weights_model": str(self.root)})
        self.assertEqual(config["Model Parameters"]["Network Weights"],
                         [str(self.root / f"weights_model{n}.pt") for n in range(1, 5)])

    def test_changed_classifier_weights_cannot_silently_change_the_model(self):
        fake = types.ModuleType("pathogenfinder2.utils.configuration")
        bundled = self.root / "bundled"
        selected = self.root / "selected"
        for n in range(1, 5):
            write(bundled / f"weights_model{n}.pt", "original")
            write(selected / f"weights_model{n}.pt", "original" if n != 4 else "different")
        class Configuration:
            @staticmethod
            def load_baseconfig():
                return {"Model Parameters": {"Network Weights": []}}
            @staticmethod
            def load_baseweights(value):
                value["Model Parameters"]["Network Weights"] = [bundled / f"weights_model{n}.pt" for n in range(1, 5)]
                return value
        fake.ConfigurationPF2 = Configuration
        with patch.dict(sys.modules, {"pathogenfinder2.utils.configuration": fake}):
            with self.assertRaisesRegex(ValueError, "differ from the pinned"):
                extend.prepare_classifier_configuration({"weights_model": str(selected)})

    def test_default_is_cpu_and_cuda_requires_both_intent_and_budget(self):
        base = {"prot_t5_identity": "fixture@pinned"}
        fake_torch = types.SimpleNamespace(__version__="fixture", cuda=types.SimpleNamespace(is_available=lambda: True))
        with patch.dict(sys.modules, {"torch": fake_torch}), patch.dict(os.environ):
            self.assertEqual(extend.select_device(base)["selected"], "cpu")
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "")
        with self.assertRaisesRegex(ValueError, "explicit execution.device"):
            extend.check_pf2_config({**base, "gpus": 1})
        with self.assertRaisesRegex(ValueError, "CUDA requires"):
            extend.check_pf2_config({**base, "device_policy": "cuda"})
        with patch.dict(sys.modules, {"torch": fake_torch}):
            self.assertEqual(extend.select_device({**base, "device_policy": "cuda", "gpus": 1})["selected"], "cuda")
        fake_torch.cuda.is_available = lambda: False
        with patch.dict(sys.modules, {"torch": fake_torch}):
            with self.assertRaisesRegex(RuntimeError, "CUDA is unavailable"):
                extend.select_device({**base, "device_policy": "cuda", "gpus": 1})

    def test_contig_staging_retains_identity_and_does_not_modify_source(self):
        assembly = self.root / "assembly.fna"
        original = ">arbitrary|one description\nACGTACGT\n>two\nNNACGT\n"
        assembly.write_text(original)
        output = self.root / "contigs"
        extend.prepare_contigs({"assembly": str(assembly)}, output)
        mapping = extend.read_json(output / "contigs.json")
        self.assertEqual(list(mapping), ["CCF000001", "CCF000002"])
        self.assertEqual(mapping["CCF000001"]["contig"], "arbitrary|one")
        self.assertEqual(assembly.read_text(), original)
        self.assertEqual(extend.read_fasta(output / "CCF000002.fna"), {"CCF000002": "NNACGT"})

    def test_static_ccf_preparation_keeps_checks_and_binds_worker_settings(self):
        root = self.root / "synthetic-installation"
        script = root / "CRISPRCasFinder.pl"
        write(script, "# synthetic installation fixture; not executable\n")
        write(root / "sel392v2.so", "fixture")
        models = root / "models/CASFinder"
        write(models / "metadata.yml", "vers: 3.1.0\n")
        write(models / "profiles/fixture.hmm", "fixture")
        write(models / "definitions/fixture.xml", "fixture")
        config = {"database": str(root), "threads": 2, "min_evidence_level": 3}
        with patch.object(extend.ccf, "SOURCE_SHA256", hashlib.sha256(script.read_bytes()).hexdigest()):
            prepared = extend.prepare_ccf_execution(config)
            self.assertEqual(prepared["models"], models)
            self.assertEqual(prepared["level"], 3)
            staged, logfile = self.root / "one.fna", self.root / "commands.log"
            write(staged, ">CCF000001\nACGT\n")
            def fake_native(argv, *, cwd, stdout_path):
                write(stdout_path, "synthetic stdout")
                logfile.touch()
            from safety.runtime import DirectRunner
            result = ([{"contig": "original|identifier"}], {"status": "not_assessed_no_predicted_cds"})
            with patch.object(DirectRunner, "run", side_effect=fake_native) as runner, \
                    patch.object(extend.ccf, "parse_contig", return_value=result) as parser, \
                    patch.object(extend.ccf, "_verify_runtime_isolation"):
                parsed = extend.run_contig(config, staged, "CCF000001", self.root / "contig-output",
                                           logfile, prepared=prepared, contig_name="original|identifier")
            self.assertEqual(parser.call_args.args[2], "original|identifier")
            self.assertEqual(parsed, {"findings": result[0], "status": result[1]})
            self.assertEqual(runner.call_args.args[0], [
                "python", "basis_ccf_launch.py", "perl", "CRISPRCasFinder.pl", "-in", "input.fna",
                "-out", "native", "-so", "./sel392v2.so", "-cas", "-keep", "-log",
                "-cpuM", "2", "-gcode", "11"])
            with self.assertRaisesRegex(ValueError, "settings changed"):
                extend.run_contig({**config, "threads": 3}, self.root / "absent.fna", "CCF000001",
                                  self.root / "absent-output", self.root / "absent-log", prepared=prepared)
            write(models / "metadata.yml", "- unexpected-list\n")
            with self.assertRaisesRegex(ValueError, "Prepared CASFinder"):
                extend.prepare_ccf_execution(config)
            write(script, "modified installation")
            with self.assertRaisesRegex(ValueError, "checksum differs"):
                extend.prepare_ccf_execution(config)

    def test_unassessed_cas_is_not_reported_as_a_negative_result(self):
        assembly = self.root / "assembly.fna"
        sequence = "A" * 90
        assembly.write_text(">source\n" + sequence + "\n")
        sample = {"sample_id": "x", "assembly": str(assembly)}
        contigs = self.root / "contigs"
        extend.prepare_contigs(sample, contigs)
        work = self.root / "native" / "CCF000001"
        native = work / "native"
        extend.write_json(native / "result.json", {"Version": "4.3.2", "Sequences": [{
            "Id": "CCF000001", "Version": "CCF000001", "Length": 90, "Crisprs": [], "Cas": []}]})
        write(native / "rawFASTA/analyzedSequences/CCF000001.fna", ">CCF000001\n" + sequence + "\n")
        write(native / "GFF/annotation_CCF000001.gff", "##gff-version 3\n")
        write(native / "Prodigal/prodigal_CCF000001/CCF000001.faa", "")
        write(work / "stdout.txt", "All CRISPRs = 0\nNo Cas search because no CDS was detected!\nThank you for using CRISPRCasFinder.pl!\n")
        write(work / "macsyfinder.conf", "[directories]\nmodels_dir = models\n")
        extend.write_json(work / "runtime_isolation.json", {
            "launcher_sha256": hashlib.sha256(extend.ccf._CCF_LAUNCHER.encode()).hexdigest(),
            "wrapper_sha256": hashlib.sha256(extend.ccf._MACSY_SHIM.encode()).hexdigest(),
            "config_sha256": hashlib.sha256((work / "macsyfinder.conf").read_bytes()).hexdigest(),
            "policy": "disable_system_and_user_config_readers", "home_environment_unchanged": True})
        parsed = extend.collect_ccf(sample, {}, contigs, self.root / "native")
        self.assertEqual(parsed["interpretation"], "inconclusive")
        self.assertEqual(parsed["metrics"]["cas_assessment"], "partially_assessed")
        self.assertEqual(parsed["metrics"]["assessed_contig_count"], 1)
        (native / "result.json").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            extend.collect_ccf(sample, {}, contigs, self.root / "native")


if __name__ == "__main__":
    unittest.main()
