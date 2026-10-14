"""Report-only recovery audit checks; synthetic holdouts are not scientific data."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.placement import DEFAULT_REGISTRY
from basis.placement_benchmark_native import BLAST_FIELDS, BLAST_SEARCH
from basis.placement_benchmark_protocol import aggregate, build_protocol, normalize_policy
from basis.placement_benchmark_recovery import recover_benchmark_report, validate_saved_benchmark
from basis.placement_panels import load_registry, prepare_panel
from basis.util import atomic_json, sha256_file


class PlacementBenchmarkRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "saved-run"
        self.root.mkdir()
        panel = prepare_panel({}, load_registry(DEFAULT_REGISTRY), self.root / "panel-cache",
                              allow_download=False, panel_id="cfr_rlmn_source_audited_v1")
        atomic_json(self.root / "seed-panel.json", panel)
        # Deliberately synthetic all-pairs HSPs collapse the seed panel into
        # one held cluster. No native tools execute and no predictions exist.
        pairwise = []
        for q in panel["members"]:
            for r in panel["members"]:
                qlen, rlen = len(q["sequence"]), len(r["sequence"])
                pairwise.append({"query_id": q["tip_id"], "reference_id": r["tip_id"],
                    "identity": 100 if q == r else 99, "alignment_length": max(qlen, rlen),
                    "query_length": qlen, "reference_length": rlen,
                    "query_start": 1, "query_end": qlen,
                    "reference_start": 1, "reference_end": rlen,
                    "evalue": 1e-20, "bitscore": 100})
        (self.root / "all-versus-all.tsv").write_text("".join(
            "\t".join(str(row[k]) for k in BLAST_FIELDS) + "\n" for row in pairwise))
        policy = normalize_policy()
        package = Path(__file__).resolve().parents[1] / "basis"
        preflight = {"schema": "basis-placement-seed-benchmark-preflight-v1",
            "created_at": "2026-10-10T01:00:00+00:00", "decision_policy": policy,
            "search_policy": BLAST_SEARCH, "native_config": {},
            "registry_path": str(DEFAULT_REGISTRY), "registry_sha256": sha256_file(DEFAULT_REGISTRY),
            "panel_sha256": panel["panel_sha256"],
            "source_sha256": {name: sha256_file(package / name) for name in (
                "placement_benchmark_native.py", "placement_benchmark_protocol.py",
                "placement_native.py", "placement_panels.py", "placement_scoring.py")}}
        atomic_json(self.root / "preflight.json", preflight)
        protocol = build_protocol(panel, pairwise, policy)
        protocol["native_search_audit"] = {
            "blast_identity": {"test_fixture": "synthetic_no_execution"},
            "search_policy": BLAST_SEARCH, "self_hits_verified": len(panel["members"]),
            "all_versus_all_sha256": sha256_file(self.root / "all-versus-all.tsv"),
            "preflight_sha256": sha256_file(self.root / "preflight.json"),
            "commands": [], "elapsed_seconds": 0}
        atomic_json(self.root / "frozen-protocol.json", protocol)
        queries = {q["query_id"]: q for q in protocol["queries"]}
        rows = []
        for fold in protocol["folds"]:
            self.assertEqual(fold["status"], "held")
            for qid in fold["query_ids"]:
                for method in ("blastp", "placement"):
                    q = queries[qid]
                    rows.append({**{k: q[k] for k in (
                        "query_id", "cluster_id", "fold_id", "true_group", "accessions")},
                        "method": method, "status": "held", "predicted_group": None,
                        "abstained": True, "reason": ";".join(fold["reasons"]),
                        "metrics": {}, "elapsed_seconds": 0})
        self.bundle = {"schema": "basis-placement-seed-benchmark-run-v1", "status": "completed",
            "started_at": preflight["created_at"], "finished_at": "2026-10-10T01:01:00+00:00",
            "protocol": protocol, "frozen_protocol_file_sha256": sha256_file(self.root / "frozen-protocol.json"),
            "results": rows, "evaluation": aggregate(rows), "placement_candidates": [],
            "function_probability": None}
        self.save()

    def save(self):
        atomic_json(self.root / "benchmark.json", self.bundle)

    def test_finalized_evidence_is_preferred_and_original_time_preserved(self):
        checkpoint = deepcopy(self.bundle)
        checkpoint.update(status="prepared", evaluation={}, finished_at=None)
        atomic_json(self.root / "checkpoint.json", checkpoint)
        before = (self.root / "benchmark.json").read_bytes()
        result, audit = validate_saved_benchmark(self.root)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["finished_at"], self.bundle["finished_at"])
        self.assertEqual(Path(audit["source"]).name, "benchmark.json")
        self.assertFalse(audit["native_analyses_run"])
        self.assertEqual((self.root / "benchmark.json").read_bytes(), before)

    def test_checkpoint_fallback_preserves_unknown_finalization_time(self):
        checkpoint = deepcopy(self.bundle)
        checkpoint.update(status="prepared", evaluation={}, finished_at=None)
        atomic_json(self.root / "checkpoint.json", checkpoint)
        (self.root / "benchmark.json").unlink()
        result, audit = validate_saved_benchmark(self.root)
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(result["finished_at"])
        self.assertIsNone(audit["original_finished_at"])
        self.assertTrue(audit["report_recovered_at"])
        self.assertEqual(json.loads((self.root / "checkpoint.json").read_text())["status"], "prepared")

    def test_prepared_only_or_partial_checkpoint_cannot_be_finalized(self):
        (self.root / "benchmark.json").unlink()
        for rows in ([], self.bundle["results"][:-1]):
            checkpoint = deepcopy(self.bundle)
            checkpoint.update(status="prepared", results=rows, evaluation={}, finished_at=None)
            atomic_json(self.root / "checkpoint.json", checkpoint)
            with self.subTest(count=len(rows)), self.assertRaisesRegex(ValueError, "Incomplete|duplicate"):
                validate_saved_benchmark(self.root)

    def test_both_methods_for_every_frozen_query_are_required(self):
        for change in ("method", "query", "duplicate"):
            edited = deepcopy(self.bundle)
            if change == "method":
                edited["results"] = [r for r in edited["results"] if r["method"] == "blastp"]
            elif change == "query":
                qid = edited["results"][0]["query_id"]
                edited["results"] = [r for r in edited["results"] if r["query_id"] != qid]
            else:
                edited["results"][-1] = deepcopy(edited["results"][0])
            edited["evaluation"] = {}
            atomic_json(self.root / "benchmark.json", edited)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "Incomplete|duplicate"):
                validate_saved_benchmark(self.root)

    def test_changed_frozen_protocol_bytes_are_rejected(self):
        path = self.root / "frozen-protocol.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "Frozen protocol checksum"):
            validate_saved_benchmark(self.root)

    def test_changed_preflight_bytes_are_rejected(self):
        path = self.root / "preflight.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "Preflight checksum"):
            validate_saved_benchmark(self.root)

    def test_changed_raw_clustering_search_is_rejected(self):
        path = self.root / "all-versus-all.tsv"
        path.write_text(path.read_text().replace("1e-20", "1e-19", 1))
        with self.assertRaisesRegex(ValueError, "Clustering search checksum"):
            validate_saved_benchmark(self.root)

    def test_row_truth_and_fold_must_match_frozen_protocol(self):
        for key in ("true_group", "cluster_id", "fold_id", "accessions"):
            edited = deepcopy(self.bundle)
            edited["results"][0][key] = ["changed"] if key == "accessions" else "changed"
            atomic_json(self.root / "benchmark.json", edited)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "metadata differs"):
                validate_saved_benchmark(self.root)

    def test_modified_evaluation_cannot_override_recorded_outcomes(self):
        self.bundle["evaluation"]["methods"]["placement"]["correct_count"] += 1
        self.save()
        with self.assertRaisesRegex(ValueError, "evaluation disagrees"):
            validate_saved_benchmark(self.root)

    def test_held_panel_gate_cannot_be_promoted_to_completed(self):
        self.bundle["results"][0].update(status="completed", reason="synthetic_gate_bypass")
        self.bundle["evaluation"] = aggregate(self.bundle["results"])
        self.save()
        with self.assertRaisesRegex(ValueError, "reference-adequacy gate"):
            validate_saved_benchmark(self.root)

    def test_render_is_offline_and_cannot_overwrite_existing_output(self):
        destination = Path(self.temp.name) / "recovered-report"
        original = {p.name: p.read_bytes() for p in self.root.glob("*.json")}
        with patch("basis.execution.CommandRunner.run", side_effect=AssertionError("Native call forbidden")):
            result, artifacts = recover_benchmark_report(self.root, destination)
        self.assertTrue(Path(artifacts["html"]).is_file())
        self.assertTrue((destination / "recovery.json").is_file())
        self.assertEqual(result["finished_at"], self.bundle["finished_at"])
        self.assertEqual(original, {p.name: p.read_bytes() for p in self.root.glob("*.json")})
        with self.assertRaises(FileExistsError):
            recover_benchmark_report(self.root, destination)


class PlacementBenchmarkLiveRecoveryTests(unittest.TestCase):
    def test_local_native_pilot_can_render_offline_when_available(self):
        # Optional developer smoke check: the distributable tests above require
        # no native installation or private study data.
        root = Path(__file__).resolve().parents[2] / "update32-native-pilot"
        if not (root / "benchmark.json").is_file():
            self.skipTest("Saved local native benchmark fixture not available")
        original = (root / "benchmark.json").read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "report"
            with patch("basis.execution.CommandRunner.run", side_effect=AssertionError("Native call forbidden")):
                bundle, artifacts = recover_benchmark_report(root, destination)
            self.assertEqual(bundle["report_recovery"]["verified_placement_count"], 5)
            self.assertEqual(bundle["report_recovery"]["verified_blast_count"], 5)
            self.assertIn("Open placement tree", Path(artifacts["html"]).read_text())
            self.assertEqual((root / "benchmark.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
