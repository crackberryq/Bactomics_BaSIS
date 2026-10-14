"""Optional real-tool integration check, not a biological validation benchmark.

Run with BASIS_NATIVE_PLACEMENT_BIN pointing to a directory containing MAFFT,
IQ-TREE 2 and EPA-ng >=0.3.4.  No network or model downloads occur in this test.
"""
import hashlib
import json
import os
from pathlib import Path
import random
import tempfile
import unittest

from basis.execution import CommandRunner
from basis.placement_native import run_placement


def _sha(sequence):
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


def synthetic_panel():
    """Two divergent synthetic groups; labels are test fixtures only."""
    randomizer = random.Random(424242)
    alphabet = "ACDEFGHIKLMNPQRSTVWY"
    root = "".join(randomizer.choice(alphabet) for _ in range(240))

    def mutate(sequence, changes):
        sequence = list(sequence)
        for position in randomizer.sample(range(len(sequence)), changes):
            sequence[position] = randomizer.choice(
                alphabet.replace(sequence[position], ""))
        return "".join(sequence)

    groups = [mutate(root, 42), mutate(root, 42)]
    members = []
    for group_number, group in enumerate(groups):
        for index in range(3):
            sequence = mutate(group, 12)
            tip = f"R{group_number}{index}"
            members.append({
                "tip_id": tip, "sequence": sequence,
                "sequence_sha256": _sha(sequence),
                "group": f"synthetic_group_{group_number}",
                "role": "amr" if group_number == 0 else "alternative",
                "accessions": [f"SYNTHETIC_{tip}"],
            })
    query = mutate(groups[0], 8)
    # MAFFT --keeplength must explicitly account for this inserted residue run.
    query = query[:100] + "PAPAP" + query[100:]
    panel = {
        "panel_id": "synthetic_native_adapter_test",
        "panel_sha256": hashlib.sha256(json.dumps(members, sort_keys=True).encode()).hexdigest(),
        "members": members,
        "adequacy": {"status": "synthetic_fixture"},
        "provenance": {"synthetic": True, "biological_validation": False},
    }
    return panel, query


@unittest.skipUnless(os.environ.get("BASIS_NATIVE_PLACEMENT_BIN"),
                     "Set BASIS_NATIVE_PLACEMENT_BIN to enable real native integration")
class LivePlacementTests(unittest.TestCase):
    def test_real_protein_placement_and_reference_cache(self):
        native = Path(os.environ["BASIS_NATIVE_PLACEMENT_BIN"]).resolve()
        for tool in ("mafft", "iqtree", "epa-ng"):
            self.assertTrue((native / tool).is_file(), str(native / tool))
        panel, sequence = synthetic_panel()
        with tempfile.TemporaryDirectory(prefix="basis-native-placement-") as temporary:
            work = Path(temporary)
            query = work / "query.faa"
            query.write_text(">QUERY\n" + sequence + "\n")
            candidate = {
                "query": {"id": "QUERY", "path": str(query),
                          "sequence_sha256": _sha(sequence)},
                "analyses": {"integrity": {"flags": []}},
            }
            config = {
                "threads": 2, "timeout_seconds": 300,
                "executables": {tool: str(native / tool)
                                for tool in ("mafft", "iqtree", "epa-ng")},
                "placement_cache_dir": str(work / "cache"),
                "placement_min_columns": 50,
                "placement_min_query_coverage": 0.7,
            }
            for attempt in range(2):
                directory = work / f"attempt{attempt}"
                directory.mkdir()
                runner = CommandRunner(config, directory / "commands.log")
                result = run_placement(candidate, panel, config, directory, runner)
                detail = json.dumps({key: result.get(key) for key in
                    ("status", "reason", "error", "tool_identities")}, indent=2)
                if result["status"] != "completed":
                    detail += "\nCOMMAND LOG TAIL:\n" + "\n".join(
                        runner.logfile.read_text().splitlines()[-45:])
                self.assertEqual(result["status"], "completed", detail)
                self.assertIsNone(result["function_probability"])
                self.assertTrue(result["query_alignment"]["all_residues_accounted_for"])
                self.assertGreater(result["query_alignment"]["dropped_insertion_count"], 0)
                self.assertEqual(result["reference_cache"]["reused"], bool(attempt))
                jplace = json.loads((directory / "placement/epa/epa_result.jplace").read_text())
                fields = jplace["fields"]
                weights = [row[fields.index("like_weight_ratio")]
                           for placement in jplace["placements"]
                           for row in placement["p"]]
                self.assertGreater(len(weights), 1)
                self.assertAlmostEqual(sum(weights), 1.0, places=8)
                native_commands = [Path(row["argv"][0]).name for row in runner.commands]
                self.assertEqual(sum(name.startswith("iqtree") for name in native_commands),
                                 0 if attempt else 1)
                self.assertEqual(native_commands.count("epa-ng"), 1)

            # The same real reference tree must not yield a placement when most
            # of a substantially extended query is discarded by --keeplength.
            extended = sequence[:100] + "PAPAP" * 60 + sequence[100:]
            query.write_text(">QUERY\n" + extended + "\n")
            candidate["query"]["sequence_sha256"] = _sha(extended)
            directory = work / "insufficient_coverage"
            directory.mkdir()
            runner = CommandRunner(config, directory / "commands.log")
            result = run_placement(candidate, panel, config, directory, runner)
            self.assertEqual(result["status"], "insufficient_evidence", result)
            self.assertEqual(result["reason"], "query_retained_coverage_below_threshold")
            self.assertTrue(result["reference_cache"]["reused"])
            self.assertTrue(result["query_alignment"]["all_residues_accounted_for"])
            self.assertFalse(any(Path(row["argv"][0]).name == "epa-ng"
                                 for row in runner.commands))


if __name__ == "__main__":
    unittest.main()
