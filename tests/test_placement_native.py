"""Native adapter contract tests with deterministic command stand-ins.

These fixtures validate orchestration and data guards, not biological accuracy
or performance of MAFFT, IQ-TREE, or EPA-ng.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from basis.placement_native import iqtree_epa_model, run_placement, validate_query_mapping
from basis.references import read_fasta, write_fasta


def sha(sequence):
    return hashlib.sha256(sequence.encode()).hexdigest()


MODEL_REPORT = """Model of substitution: LG+G4
State frequencies: (model)
Model of rate heterogeneity: Gamma with 4 categories
Gamma shape alpha: 0.9896
 Category  Relative_rate  Proportion
 1         0.1349         0.2500
 2         0.4736         0.2500
 3         0.9983         0.2500
 4         2.3933         0.2500
Relative rates are computed as MEAN of the portion of the Gamma distribution falling in the category.
"""


class NativeStandIn:
    def __init__(self, fault=None, drop_positions=(), epa_version="0.3.8", tool_revision="v1"):
        self.commands = []
        self.fault = fault
        self.drop_positions = set(drop_positions)
        self.epa_version = epa_version
        self.tool_revision = tool_revision

    def run(self, argv, stdout_path=None, cwd=None):
        argv = [str(value) for value in argv]
        self.commands.append(argv)
        if argv[0] == "python":
            name = Path(argv[-2]).name
            version = self.epa_version if name == "epa-ng" else "7.526" if name == "mafft" else "2.4.0"
            data = {"resolved_executable": "/fake/" + name,
                    "executable_sha256": sha(name + self.tool_revision),
                    "version_output": name + " v" + version}
            return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")
        name = Path(argv[0]).name
        if name == "mafft":
            rows = read_fasta(Path(argv[-1]))
            if "--addfull" not in argv:
                write_fasta(rows, Path(stdout_path))
                return subprocess.CompletedProcess(argv, 0, "", "")
            query_path = Path(argv[argv.index("--addfull") + 1])
            query_id, sequence = next(iter(read_fasta(query_path).items()))
            columns = len(next(iter(rows.values())))
            retained = [(i, letter) for i, letter in enumerate(sequence, 1) if i not in self.drop_positions]
            retained = retained[:columns]
            mapping = {position: column for column, (position, _) in enumerate(retained, 1)}
            aligned = "".join(letter for _, letter in retained).ljust(columns, "-")
            if self.fault == "query_residue":
                aligned = ("Y" if aligned[0] != "Y" else "A") + aligned[1:]
            rows[query_id] = aligned
            if self.fault == "reference_alignment":
                tip = next(iter(rows))
                rows[tip] = "Y" + rows[tip][1:]
            write_fasta(rows, Path(stdout_path))
            lines = [">" + query_id, "# letter, original position, alignment column"]
            for position, letter in enumerate(sequence, 1):
                if self.fault == "missing_map_residue" and position == 2:
                    continue
                lines.append(f"{letter}, {position}, {mapping.get(position, '-')}")
            Path(str(query_path) + ".map").write_text("\n".join(lines) + "\n")
        elif name == "iqtree":
            if self.fault == "native_tree":
                raise RuntimeError("fixture native tree failure")
            prefix = Path(argv[argv.index("-pre") + 1])
            prefix.with_suffix(".treefile").write_text("(R1:0.1,R2:0.1,(R3:0.1,R4:0.1)90/99:0.1);\n")
            model = "LG+R4" if self.fault == "wrong_model" else "LG+G4"
            prefix.with_suffix(".iqtree").write_text(MODEL_REPORT.replace("LG+G4", model))
            prefix.with_suffix(".log").write_text("fixture tree completed\n")
        elif name == "epa-ng":
            if self.fault == "native_epa":
                raise RuntimeError("fixture native placement failure")
            folder = Path(argv[argv.index("--outdir") + 1])
            query_id = next(iter(read_fasta(Path(argv[argv.index("--query") + 1]))))
            data = {"version": 3,
                    "tree": "(R1:0.1{0},R2:0.1{1},(R3:0.1{2},R4:0.1{3})90/99:0.1{4});",
                    "fields": ["edge_num", "likelihood", "like_weight_ratio", "distal_length", "pendant_length"],
                    "placements": [{"n": [query_id], "p": [[0, -123, 0.8, 0.03, 0.02], [4, -124, 0.15, 0.03, 0.02]]}]}
            (folder / "epa_result.jplace").write_text(json.dumps(data))
        else:
            raise AssertionError("Unexpected native command: " + repr(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")


class NativePlacementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sequence = "ACDEFGHIKLMNPQRSTVWY" * 3
        write_fasta({"Q_test": self.sequence}, self.root / "query.faa")
        self.candidate = {"query": {"id": "Q_test", "path": str(self.root / "query.faa"),
                                    "sequence_sha256": sha(self.sequence)},
                          "analyses": {"integrity": {"flags": []}}}
        members = []
        for index, letter in enumerate("AHIL", 1):
            sequence = letter + self.sequence[1:]
            members.append({"tip_id": "R" + str(index), "sequence": sequence,
                            "sequence_sha256": sha(sequence), "group": "AMR" if index < 3 else "ALT",
                            "role": "amr" if index < 3 else "alternative", "accessions": ["ACC" + str(index)]})
        self.panel = {"panel_id": "fixture", "panel_sha256": sha("fixture"), "members": members,
                      "adequacy": {"status": "adequate"}, "provenance": {"source": "test fixture"}}
        self.config = {"placement_cache_dir": str(self.root / "cache"), "threads": 2}

    def tearDown(self):
        self.temporary.cleanup()

    def run_candidate(self, runner=None, attempt="one", candidate=None, panel=None, config=None):
        return run_placement(candidate or self.candidate, panel or self.panel,
                             config or self.config, self.root / attempt, runner or NativeStandIn())

    def test_reference_only_tree_and_exact_model_parameters(self):
        runner = NativeStandIn()
        result = self.run_candidate(runner)
        self.assertEqual(result["status"], "completed", result)
        self.assertIsNone(result["function_probability"])
        tree = next(command for command in runner.commands if Path(command[0]).name == "iqtree")
        reference_input = read_fasta(Path(tree[tree.index("-s") + 1])) if Path(tree[tree.index("-s") + 1]).exists() else read_fasta(Path(result["reference_cache"]["directory"]) / "reference_alignment.faa")
        self.assertNotIn("Q_test", reference_input)
        self.assertEqual(tree[tree.index("-m") + 1], "LG+G4")
        for flag, value in (("-B", "1000"), ("-alrt", "1000"), ("-seed", "42")):
            self.assertEqual(tree[tree.index(flag) + 1], value)
        epa = next(command for command in runner.commands if Path(command[0]).name == "epa-ng")
        self.assertIn("--no-heur", epa)
        self.assertEqual(epa[epa.index("--filter-min-lwr") + 1], "0")
        self.assertEqual(epa[epa.index("--filter-max") + 1], "5")
        self.assertEqual(epa[epa.index("--model") + 1], "LG+G4{0.9896}")
        self.assertEqual(result["fitted_model"]["reported_alpha"], "0.9896")
        self.assertAlmostEqual(result["scores"]["missing_mass"], 0.05)
        self.assertTrue(result["query_alignment"]["all_residues_accounted_for"])

    def test_reference_cache_reused_between_queries_but_query_placement_runs(self):
        first = self.run_candidate()
        runner = NativeStandIn()
        second = self.run_candidate(runner, attempt="two")
        self.assertEqual(second["status"], "completed", second)
        self.assertFalse(first["reference_cache"]["reused"])
        self.assertTrue(second["reference_cache"]["reused"])
        self.assertFalse(any(Path(command[0]).name == "iqtree" for command in runner.commands))
        self.assertTrue(any("--addfull" in command for command in runner.commands))
        self.assertTrue(any(Path(command[0]).name == "epa-ng" for command in runner.commands))

    def test_concurrent_attempts_publish_one_complete_reference_cache(self):
        runners = [NativeStandIn(), NativeStandIn()]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.run_candidate, runner, attempt="concurrent-" + str(index))
                       for index, runner in enumerate(runners)]
            results = [future.result() for future in futures]
        self.assertTrue(all(row["status"] == "completed" for row in results), results)
        self.assertEqual(sorted(row["reference_cache"]["reused"] for row in results), [False, True])
        self.assertEqual(sum(Path(command[0]).name == "iqtree" for runner in runners
                             for command in runner.commands), 1)
        self.assertEqual(len({row["reference_cache"]["directory"] for row in results}), 1)
        self.assertFalse(list((self.root / "cache").glob(".build-*")))

    def test_tool_binary_identity_invalidates_cache_even_same_version(self):
        first = self.run_candidate()
        second = self.run_candidate(NativeStandIn(tool_revision="v2"), attempt="two")
        self.assertEqual(second["status"], "completed", second)
        self.assertFalse(second["reference_cache"]["reused"])
        self.assertNotEqual(first["reference_cache"]["cache_key"], second["reference_cache"]["cache_key"])

    def test_corrupted_cache_refused_before_query_placement(self):
        first = self.run_candidate()
        tree = Path(first["reference_cache"]["directory"]) / "reference.treefile"
        tree.write_text(tree.read_text().replace("0.1", "0.2", 1))
        runner = NativeStandIn()
        second = self.run_candidate(runner, attempt="two")
        self.assertEqual(second["status"], "failed")
        self.assertIn("artifact changed", second["error"])
        self.assertFalse(any("--addfull" in command for command in runner.commands))

    def test_reference_sequence_change_invalidates_cache(self):
        first = self.run_candidate()
        panel = deepcopy(self.panel)
        panel["members"][0]["sequence"] = "Y" + self.sequence[1:]
        panel["members"][0]["sequence_sha256"] = sha(panel["members"][0]["sequence"])
        second = self.run_candidate(attempt="two", panel=panel)
        self.assertEqual(second["status"], "completed", second)
        self.assertNotEqual(first["reference_cache"]["cache_key"], second["reference_cache"]["cache_key"])

    def test_preserve_old_attempt_on_accidental_reexecution(self):
        self.run_candidate()
        original = (self.root / "one/placement/placement.json").read_bytes()
        result = self.run_candidate()
        self.assertEqual(result["status"], "failed")
        self.assertEqual((self.root / "one/placement/placement.json").read_bytes(), original)

    def test_dropped_insertions_are_recorded_and_coverage_gate_holds(self):
        result = self.run_candidate(NativeStandIn(drop_positions=range(1, 21)))
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["reason"], "query_retained_coverage_below_threshold")
        self.assertEqual(result["query_alignment"]["dropped_insertion_count"], 20)
        self.assertAlmostEqual(result["query_alignment"]["retained_query_fraction"], 40 / 60)

    def test_retained_insertions_below_overlap_gate_hold(self):
        result = self.run_candidate(NativeStandIn(drop_positions=range(1, 12)))
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["reason"], "query_reference_overlap_below_minimum_columns")

    def test_alignment_changes_fail_and_are_never_negative_classifications(self):
        for fault in ("reference_alignment", "query_residue", "missing_map_residue"):
            with self.subTest(fault=fault):
                result = self.run_candidate(NativeStandIn(fault=fault), attempt=fault)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["scores"], {})
                self.assertIsNone(result["function_probability"])

    def test_native_failures_and_wrong_model_fail_closed(self):
        for fault in ("native_tree", "wrong_model", "native_epa"):
            with self.subTest(fault=fault):
                config = {**self.config, "placement_cache_dir": str(self.root / ("cache-" + fault))}
                result = self.run_candidate(NativeStandIn(fault=fault), attempt=fault, config=config)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["scores"], {})

    def test_old_epa_version_rejected(self):
        result = self.run_candidate(NativeStandIn(epa_version="0.3.3"))
        self.assertEqual(result["status"], "failed")
        self.assertIn("EPA-ng >=0.3.4", result["error"])

    def test_insufficient_or_biologically_held_inputs_run_no_native_commands(self):
        cases = []
        fewer = deepcopy(self.panel)
        fewer["members"] = fewer["members"][:3]
        cases.append((self.candidate, fewer))
        positives = deepcopy(self.panel)
        for member in positives["members"]:
            member["role"] = "amr"
        cases.append((self.candidate, positives))
        taxon = deepcopy(self.candidate)
        taxon["taxonomy_review"] = {"status": "review_required"}
        cases.append((taxon, self.panel))
        integrity = deepcopy(self.candidate)
        integrity["analyses"]["integrity"]["flags"] = ["internal_stop"]
        cases.append((integrity, self.panel))
        for index, (candidate, panel) in enumerate(cases):
            runner = NativeStandIn()
            result = self.run_candidate(runner, str(index), candidate, panel)
            self.assertEqual(result["status"], "insufficient_evidence")
            self.assertEqual(runner.commands, [])

    def test_query_or_reference_hash_mismatch_refused(self):
        candidate = deepcopy(self.candidate)
        candidate["query"]["sequence_sha256"] = "0" * 64
        self.assertEqual(self.run_candidate(candidate=candidate)["status"], "failed")
        panel = deepcopy(self.panel)
        panel["members"][0]["sequence_sha256"] = "0" * 64
        self.assertEqual(self.run_candidate(panel=panel)["status"], "failed")


class ResidueMappingTests(unittest.TestCase):
    def test_full_map_identity_and_insertions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "query.map"
            path.write_text(">Q\n# letter, original, reference\nA, 1, 1\nC, 2, -\nD, 3, 2\nE, 4, 4\n")
            result = validate_query_mapping("Q", "ACDE", "AD-E", path)
            self.assertEqual(result["dropped_insertion_positions_1based"], [2])
            self.assertEqual(result["retained_query_fraction"], 0.75)

    def test_map_without_residue_accounting_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "query.map"
            for text in (
                    ">Q\nA, 1, 1\nD, 3, 2\n",  # dropped residue must still be recorded
                    ">Q\nA, 1, 1\nC, 2, 1\nD, 3, 2\n",
                    ">Q\nA, 1, 1\nY, 2, -\nD, 3, 2\n",
                    ">OTHER\nA, 1, 1\nC, 2, -\nD, 3, 2\n"):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    validate_query_mapping("Q", "ACD", "AD", path)


class FittedModelConversionTests(unittest.TestCase):
    def test_supported_fitted_gamma_model_with_precision_audit(self):
        model = iqtree_epa_model(MODEL_REPORT)
        self.assertEqual(model["descriptor"], "LG+G4{0.9896}")
        self.assertEqual(model["gamma_discretization"], "mean")
        self.assertIn("report text precision", model["parameter_precision"])

    def test_unsupported_or_incomplete_fitted_model_is_rejected(self):
        for old, new in (("LG+G4", "LG+F+G4"), ("(model)", "(empirical)"),
                         ("4 categories", "8 categories"), ("MEAN", "MEDIAN"),
                         ("0.9896", "0.0000"), ("0.9896", "nan"),
                         ("0.9896", "1e999"), ("0.2500", "0.3000"),
                         ("Gamma shape alpha:", "Unrecognized alpha:")):
            with self.subTest(old=old, new=new):
                with self.assertRaises(ValueError):
                    iqtree_epa_model(MODEL_REPORT.replace(old, new))


if __name__ == "__main__":
    unittest.main()
