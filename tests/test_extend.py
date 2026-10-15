"""Contract fixtures for PF2 0.7.1 output; no model or biological validation."""
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest

from basis.adapters.extend import (
    PF2_SOURCE_COMMIT, _ATTENTION_EXPORT, _sha256, _validate_reuse,
    parse_attention_json, parse_predictions, run, collect_outputs,
)


HEADER = ["File Name", "Prediction_0", "Prediction_1", "Prediction_2", "Prediction_3",
          "Prediction Mean", "Prediction STD", "Binary Prediction Mean", "Phenotype"]


def prediction_text(scores=(.1, .2, .3, .4), **overrides):
    mean = sum(scores) / len(scores)
    std = math.sqrt(sum((v - mean) ** 2 for v in scores) / len(scores))
    row = dict(zip(HEADER, ["PF2", *scores, mean, std, int(mean > .5),
                           "Human Pathogenic" if mean > .5 else "Human Non Pathogenic"]))
    row.update(overrides)
    return "# Results From PathogenFinder2\n## ensemble metadata\n" + "\t".join(HEADER) + "\n" + "\t".join(str(row[k]) for k in HEADER) + "\n"


class PredictionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "predictions.tsv"

    def test_native_mean_std_and_members_retained(self):
        self.path.write_text(prediction_text())
        parsed = parse_predictions(self.path)
        self.assertEqual(parsed["ensemble_member_scores"], [.1, .2, .3, .4])
        self.assertAlmostEqual(parsed["prediction_mean"], .25)
        self.assertEqual(parsed["uncertainty_type"], "between_model_standard_deviation")

    def test_missing_empty_and_comments_only_are_errors(self):
        with self.assertRaises(ValueError):
            parse_predictions(self.path)
        for value in ("", "# completed\n", "NO_OUTPUT\n"):
            self.path.write_text(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_predictions(self.path)

    def test_missing_column_duplicate_column_or_extra_row_rejected(self):
        valid = prediction_text()
        for value in (valid.replace("Prediction_3\t", "other\t", 1),
                      valid.replace("Prediction_3\t", "Prediction_2\t", 1),
                      valid + valid.splitlines()[-1] + "\n"):
            self.path.write_text(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_predictions(self.path)

    def test_score_and_label_validation(self):
        for values in ({"Prediction_0": "nan"}, {"Prediction_0": 1.5},
                       {"Prediction Mean": .9}, {"Prediction STD": -.1},
                       {"Binary Prediction Mean": 1}, {"File Name": "wrong-isolate"}):
            self.path.write_text(prediction_text(**values))
            with self.subTest(values=values), self.assertRaises(ValueError):
                parse_predictions(self.path)

    def test_native_half_boundary_is_nonpathogenic_label(self):
        self.path.write_text(prediction_text(scores=(.5, .5, .5, .5)))
        self.assertEqual(parse_predictions(self.path)["native_binary_prediction"], 0)


class AttentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.faa = self.root / "proteins.faa"
        self.faa.write_text(">p/1 description\nMAAA\n>p2\nMQQQ\n")
        self.path = self.root / "attention.json"
        self.data = {"schema": "basis-pf2-attention-v1",
                     "token_ids": ["p_1 description_Kmer-0", "p_1 description_Kmer-1", "p2"],
                     "model_scores": [[.1, .2, .7]] * 4}

    def parse(self):
        self.path.write_text(json.dumps(self.data))
        return parse_attention_json(self.path, self.faa)

    def test_full_header_slash_and_long_protein_chunks(self):
        rows = self.parse()
        self.assertEqual([r["protein_id"] for r in rows], ["p2", "p/1"])
        self.assertAlmostEqual(rows[1]["attention_max"], .3)
        self.assertEqual(rows[1]["embedding_token_count"], 2)
        self.assertEqual(rows[1]["rank"], 2)

    def test_embedded_angle_brackets_match_upstream_reader(self):
        self.faa.write_text(">p/1 description <locus_tag>\nMAAA\n>p2\nMQQQ\n")
        self.data["token_ids"] = ["p_1 description <locus_tag_Kmer-0", "p_1 description <locus_tag_Kmer-1", "p2"]
        self.assertEqual({r["protein_id"] for r in self.parse()}, {"p/1", "p2"})

    def test_header_normalization_collision_rejected(self):
        self.faa.write_text(">p/1 desc>\nMAAA\n>p_1 desc\nMQQQ\n")
        with self.assertRaises(ValueError):self.parse()

    def test_arbitrary_header_truncation_is_not_accepted(self):
        self.data["token_ids"][0]="p_1"
        with self.assertRaises(ValueError):self.parse()

    def test_wrong_shape_rejected(self):
        self.data["model_scores"] = [[.1, .2]] * 4
        with self.assertRaises(ValueError):
            self.parse()

    def test_unknown_or_missing_proteins_rejected(self):
        self.data["token_ids"][2] = "unknown"
        with self.assertRaises(ValueError):
            self.parse()
        self.data["token_ids"] = self.data["token_ids"][:2]
        self.data["model_scores"] = [[.1, .2]] * 4
        with self.assertRaises(ValueError):
            self.parse()

    def test_range_and_nonfinite_rejected(self):
        for score in (-.1, 1.1, float("nan")):
            self.data["model_scores"] = [[score, .2, .7]] * 4
            with self.subTest(score=score), self.assertRaises(ValueError):
                self.parse()

    def test_unnormalized_attention_rejected(self):
        self.data["model_scores"] = [[0, 0, 0]] * 4
        with self.assertRaises(ValueError):
            self.parse()

    def test_npz_conversion_uses_hdf5_names_without_unpickling_ids(self):
        try:
            import h5py
            import numpy as np
        except ImportError:
            self.skipTest("Optional PF2-format conversion dependencies unavailable")
        import sys
        embeddings = self.root / "embeddings.h5"
        with h5py.File(embeddings, "w") as handle:
            handle.create_dataset("Embeddings", data=np.zeros((3, 1024)))
            handle.create_dataset("Names", data=self.data["token_ids"], dtype=h5py.string_dtype())
            handle.attrs["Amount Embeddings"] = 3
        npz = self.root / "attentions.npz"
        np.savez_compressed(npz, protIDs=np.array(self.data["token_ids"], dtype=object),
                            attentions=np.array(self.data["model_scores"]))
        result = subprocess.run([sys.executable, "-c", _ATTENTION_EXPORT, str(npz), str(embeddings)],
                                check=True, text=True, capture_output=True)
        self.path.write_text(result.stdout)
        self.assertEqual(len(parse_attention_json(self.path, self.faa)), 2)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.faa = self.root / "all proteins.faa"
        self.faa.write_text(">p1\nMQQ\n>p2\nMAA\n")

    def test_missing_output_after_successful_process_is_failure(self):
        class Runner:
            def run(self, argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, "", "")
        with self.assertRaises(ValueError):
            run("pathogenfinder2", {"proteins": str(self.faa)}, {"attention": False}, self.root / "out", Runner())

    def test_native_nested_path_and_argv_without_shell_interpolation(self):
        calls = []
        options = []
        class Runner:
            def run(inner, argv, **kwargs):
                calls.append(argv)
                options.append(kwargs)
                native = Path(argv[argv.index("-o") + 1])
                results = native / "predictionPF2" / "results_PF2"
                results.mkdir(parents=True)
                (results / "predictions.tsv").write_text(prediction_text())
                embeddings = native / "predictionPF2" / "preprocess_PF2" / "ProteinEmbeddings.h5"
                embeddings.parent.mkdir()
                embeddings.write_bytes(b"fixture; no native model run")
                return subprocess.CompletedProcess(argv, 0, "", "")
        output = run("pathogenfinder2", {"proteins": str(self.faa)}, {"attention": False}, self.root / "out", Runner())
        self.assertEqual(calls[0][calls[0].index("-i") + 1], str(self.faa))
        self.assertTrue(options[0]["stream_stdout"])
        self.assertEqual(output["metrics"]["input_protein_count"], 2)
        self.assertEqual(output["interpretation"], "inconclusive")
        self.assertTrue(Path(output["metrics"]["embedding_manifest"]).is_file())

    def test_collect_saved_outputs_without_inference(self):
        native=self.root/'old/native'
        results=native/'predictionPF2/results_PF2';results.mkdir(parents=True)
        pred=results/'predictions.tsv';pred.write_text(prediction_text())
        (results/'attentions.npz').write_bytes(b'fixture')
        embeddings=native/'predictionPF2/preprocess_PF2/ProteinEmbeddings.h5'
        embeddings.parent.mkdir();embeddings.write_bytes(b'fixture')
        out=self.root/'recovery';out.mkdir()
        class ExportOnly:
            def run(inner,argv,stdout_path=None,**kwargs):
                if 'predict' in argv:raise AssertionError('Inference rerun forbidden')
                Path(stdout_path).write_text(json.dumps({'schema':'basis-pf2-attention-v1',
                    'token_ids':['p1','p2'],'model_scores':[[.3,.7]]*4}))
        before=pred.read_bytes()
        result=collect_outputs({'proteins':str(self.faa)}, {}, out, ExportOnly(), native=native)
        self.assertEqual(result['metrics']['attention_protein_count'],2)
        self.assertEqual(pred.read_bytes(),before)
        self.assertTrue((out/'attention_scores.tsv').is_file())

    def test_reuse_requires_matching_proteome_and_embedding_bytes(self):
        embeddings = self.root / "ProteinEmbeddings.h5"
        embeddings.write_bytes(b"embedding fixture")
        manifest = self.root / "manifest.json"
        data = {"schema": "basis-pf2-embeddings-v1", "proteome_sha256": _sha256(self.faa),
                "embeddings_sha256": _sha256(embeddings), "pf2_source_commit": PF2_SOURCE_COMMIT,
                "prot_t5_identity": "reviewed-local-model-sha256"}
        manifest.write_text(json.dumps(data))
        _validate_reuse(embeddings, manifest, self.faa, "reviewed-local-model-sha256")
        embeddings.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            _validate_reuse(embeddings, manifest, self.faa, "reviewed-local-model-sha256")


if __name__ == "__main__":
    unittest.main()
