"""Benchmark plumbing tests using recorded seed sequences and synthetic HSPs.

Synthetic scores below exercise partitions and execution accounting only; they
are not biological results and must never be reported as native validation.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from basis.placement import DEFAULT_REGISTRY
from basis.placement_benchmark_native import (
    BLAST_FIELDS, blast_search, parse_blast, run_seed_benchmark, training_panel,
)
from basis.placement_benchmark_protocol import build_protocol, normalize_policy
from basis.placement_panels import load_registry, prepare_panel


def fasta(path):
    records, current = {}, None
    for line in Path(path).read_text().splitlines():
        if line.startswith(">"):
            current = line[1:].split()[0]
            records[current] = ""
        elif line.strip():
            records[current] += line.strip()
    return records


def hsp(query, reference, sequences, identity=40.0, bitscore=100):
    qlen, rlen = len(sequences[query]), len(sequences[reference])
    return {"query_id": query, "reference_id": reference, "identity": identity,
            "alignment_length": max(qlen, rlen), "query_length": qlen,
            "reference_length": rlen, "query_start": 1, "query_end": qlen,
            "reference_start": 1, "reference_end": rlen, "evalue": 1e-20,
            "bitscore": bitscore}


def tsv(rows):
    return "".join("\t".join(str(row[field]) for field in BLAST_FIELDS) + "\n" for row in rows)


class PlacementBenchmarkNativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.panel = prepare_panel({}, load_registry(DEFAULT_REGISTRY), self.root / "cache",
                                   allow_download=False, panel_id="cfr_rlmn_source_audited_v1")
        self.assertTrue(self.panel["adequacy"]["eligible"])
        self.members = {m["tip_id"]: m for m in self.panel["members"]}
        self.sequences = {tip: m["sequence"] for tip, m in self.members.items()}
        self.ids = sorted(self.members)
        self.policy = normalize_policy()

    def write_rows(self, rows):
        path = self.root / "blast.tsv"
        path.write_text(tsv(rows))
        return path

    def test_parse_accepts_full_self_hits_and_preserves_scope(self):
        rows = [hsp(tip, tip, self.sequences, identity=100) for tip in self.ids]
        parsed = parse_blast(self.write_rows(rows), self.members, set(self.ids), set(self.ids), True)
        self.assertEqual(len(parsed), len(self.ids))
        self.assertTrue(all(row["query_coverage"] == row["reference_coverage"] == 100 for row in parsed))

    def test_parse_refuses_missing_partial_nonidentical_and_out_of_scope_self_hits(self):
        rows = [hsp(tip, tip, self.sequences, identity=100) for tip in self.ids]
        for change in ("missing", "partial", "nonidentical"):
            changed = deepcopy(rows)
            if change == "missing":
                changed.pop()
            elif change == "partial":
                changed[0]["query_end"] -= 1
            else:
                changed[0]["identity"] = 99.9
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "self hits missing"):
                parse_blast(self.write_rows(changed), self.members, set(self.ids), set(self.ids), True)
        with self.assertRaisesRegex(ValueError, "same query and reference"):
            parse_blast(self.write_rows(rows[:1]), self.members, {self.ids[0]}, set(self.ids), True)

    def test_parse_refuses_duplicate_pairs_unknown_ids_wrong_lengths_and_truncated_columns(self):
        row = hsp(self.ids[0], self.ids[1], self.sequences)
        cases = ([row, row], [{**row, "reference_id": "unknown"}],
                 [{**row, "query_id": "unknown"}], [{**row, "query_length": row["query_length"] + 1}])
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                parse_blast(self.write_rows(rows), self.members, set(self.ids), set(self.ids))
        path = self.root / "short.tsv"
        path.write_text("q\tr\t40\n")
        with self.assertRaisesRegex(ValueError, "twelve columns"):
            parse_blast(path, self.members, set(self.ids), set(self.ids))

    def test_blast_command_reports_every_subject_without_top_k_truncation(self):
        calls = []
        runner = SimpleNamespace(run=lambda args, **kw: calls.append((args, kw)))
        blast_search("q.faa", "train.faa", 9, "out.tsv", runner, "/pinned/blastp")
        args, options = calls[0]
        self.assertEqual(args[0], "/pinned/blastp")
        self.assertEqual(args[args.index("-max_target_seqs") + 1], "9")
        self.assertEqual(args[args.index("-max_hsps") + 1], "1")
        self.assertEqual(args[args.index("-evalue") + 1], "1000")
        self.assertEqual(args[args.index("-subject") + 1], "train.faa")
        self.assertEqual(options, {"stdout_path": "out.tsv"})
        with self.assertRaises(ValueError):
            blast_search("q", "train", 0, "out", runner)

    def test_training_payload_excludes_heldout_sequences_and_source_evidence(self):
        protocol = build_protocol(self.panel, [], self.policy)
        fold = next(f for f in protocol["folds"] if f["status"] == "ready")
        train = training_panel(self.panel, fold, self.policy)
        self.assertEqual({m["tip_id"] for m in train["members"]}, set(fold["reference_ids"]))
        self.assertTrue(train["provenance"]["training_only"])
        serialized = json.dumps(train, sort_keys=True)
        for tip in fold["held_out_ids"]:
            self.assertNotIn(self.members[tip]["sequence"], serialized)
            self.assertNotIn(tip, serialized)
            for accession in self.members[tip]["accessions"]:
                self.assertNotIn('"' + accession + '"', serialized)
        self.assertNotIn("panel_definition", train["provenance"])
        self.assertNotIn("retrievals", train["provenance"])
        # Training copies cannot mutate the source seed panel.
        original = deepcopy(self.panel)
        train["members"][0]["accessions"].append("not-real")
        self.assertEqual(self.panel, original)

    def test_training_payload_rejects_overlap_omissions_and_inadequate_folds(self):
        protocol = build_protocol(self.panel, [], self.policy)
        fold = next(f for f in protocol["folds"] if f["status"] == "ready")
        overlap = deepcopy(fold)
        overlap["reference_ids"].append(overlap["held_out_ids"][0])
        missing = deepcopy(fold)
        missing["reference_ids"].pop()
        held = deepcopy(fold)
        held["status"] = "held"
        for changed in (overlap, missing, held):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                training_panel(self.panel, changed, self.policy)

    def mocks(self, native_fail=False):
        owner = self
        calls, inference_payloads = [], []
        alternative = sorted(t for t, m in self.members.items() if m["role"] == "alternative")
        context = sorted(t for t, m in self.members.items() if m["role"] == "context")
        # A deliberately synthetic context bridge tests that the whole component
        # is excluded. Its HSP identity values are not biological observations.
        edges = {frozenset((alternative[0], context[0])), frozenset((context[0], alternative[1]))}

        class FakeRunner:
            def __init__(self, config, log):
                self.commands = []

            def run(self, args, stdout_path=None):
                calls.append(list(args))
                self.commands.append({"argv": list(args)})
                if args[0] == "python":
                    return SimpleNamespace(stdout=json.dumps({"test_fixture": "synthetic_runner"}))
                query = fasta(args[args.index("-query") + 1])
                subjects = fasta(args[args.index("-subject") + 1])
                owner.assertEqual(int(args[args.index("-max_target_seqs") + 1]), len(subjects))
                seqs = {**query, **subjects}
                rows = []
                for q in query:
                    for r in subjects:
                        identity = 100 if q == r else 75 if frozenset((q, r)) in edges else 40
                        rows.append(hsp(q, r, seqs, identity))
                Path(stdout_path).write_text(tsv(rows))
                return SimpleNamespace(stdout="")

        def fake_placement(candidate, train, config, directory, runner):
            inference_payloads.append((deepcopy(candidate), deepcopy(train)))
            query = candidate["query"]["id"]
            owner.assertEqual(config["gpus"], 0)
            owner.assertTrue(set(candidate) <= {"candidate_id", "protein_id", "selected", "execution", "query", "analyses"})
            for field in ("true_group", "gene", "family", "source_evidence", "accessions"):
                owner.assertNotIn(field, candidate)
            owner.assertNotIn(query, {m["tip_id"] for m in train["members"]})
            owner.assertNotIn(owner.members[query]["sequence"], json.dumps(train))
            if native_fail and len(inference_payloads) == 1:
                raise RuntimeError("synthetic execution failure")
            groups = sorted({m["group"] for m in train["members"] if m["role"] != "context"})
            return {"status": "completed", "reason": "synthetic_test_only",
                    "scores": {"group_support": {groups[0]: .9, groups[1]: .1}},
                    "artifacts": [], "reference_cache": {"reused": False}}

        def fake_report(bundle, outdir):
            path = Path(outdir) / "report.html"
            path.write_text("<p>Test fixture only</p>")
            return {"html": str(path)}

        return FakeRunner, fake_placement, fake_report, calls, inference_payloads

    def run_mocked(self, name, native_fail=False, prepare_only=False):
        fake_runner, fake_placement, fake_report, calls, payloads = self.mocks(native_fail)
        config = {"gpus": 4, "threads": 2, "placement_model": "LG+G4"}
        original = deepcopy(config)
        sentinel = self.root / "existing-study-results.txt"
        sentinel.write_text("preserve")
        registry_sha = hashlib.sha256(Path(DEFAULT_REGISTRY).read_bytes()).hexdigest()
        with patch("basis.placement_benchmark_native.CommandRunner", fake_runner), \
                patch("basis.placement_benchmark_native.run_placement", fake_placement), \
                patch("basis.placement_benchmark_native.write_benchmark_report", fake_report):
            result, artifacts = run_seed_benchmark(self.root / name, config, prepare_only=prepare_only)
        self.assertEqual(config, original)
        self.assertEqual(sentinel.read_text(), "preserve")
        self.assertEqual(hashlib.sha256(Path(DEFAULT_REGISTRY).read_bytes()).hexdigest(), registry_sha)
        return result, artifacts, calls, payloads

    def test_mocked_complete_pilot_freezes_protocol_and_accounts_for_held_queries(self):
        result, artifacts, calls, payloads = self.run_mocked("complete")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["results"]), 14)
        self.assertEqual(len(payloads), 5)
        self.assertTrue(Path(artifacts["html"]).is_file())
        frozen_protocol = self.root / "complete/frozen-protocol.json"
        self.assertTrue(frozen_protocol.is_file())
        self.assertEqual(hashlib.sha256(frozen_protocol.read_bytes()).hexdigest(),
                         result["frozen_protocol_file_sha256"])
        self.assertTrue((self.root / "complete/preflight.json").is_file())
        self.assertEqual(result["evaluation"]["planned_query_count"], 7)
        for method in ("blastp", "placement"):
            metrics = result["evaluation"]["methods"][method]
            self.assertEqual(metrics["held_count"], 2)
            self.assertEqual(metrics["failed_count"], 0)
        folds = {f["fold_id"]: f for f in result["protocol"]["folds"]}
        for candidate, train in payloads:
            qid = candidate["query"]["id"]
            fold = next(f for f in folds.values() if qid in f["query_ids"])
            self.assertTrue(set(fold["held_out_ids"]).isdisjoint(m["tip_id"] for m in train["members"]))
        self.assertEqual(len([c for c in calls if "-query" in c]), 6)  # One discovery + five training-only searches.
        self.assertIsNone(result["function_probability"])

    def test_mocked_execution_failure_kept_separate_and_remaining_queries_continue(self):
        result, _, _, payloads = self.run_mocked("failure", native_fail=True)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(len(payloads), 5)
        metrics = result["evaluation"]["methods"]["placement"]
        self.assertEqual(metrics["planned_count"], 7)
        self.assertEqual(metrics["failed_count"], 1)
        self.assertEqual(metrics["held_count"], 2)
        self.assertEqual(metrics["completed_count"], 4)
        failed = [r for r in result["results"] if r["status"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertIsNone(failed[0]["predicted_group"])
        self.assertFalse(failed[0]["abstained"])

    def test_prepare_only_runs_no_inference_and_existing_output_directory_refused(self):
        result, _, calls, payloads = self.run_mocked("prepared", prepare_only=True)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["results"], [])
        self.assertEqual(payloads, [])
        self.assertEqual(len([c for c in calls if "-query" in c]), 1)
        with self.assertRaises(FileExistsError):
            run_seed_benchmark(self.root / "prepared", {})

    def test_final_checkpoint_survives_report_renderer_failure(self):
        runner, placement, _, _, _ = self.mocks()
        outdir = self.root / "report-failure"
        with patch("basis.placement_benchmark_native.CommandRunner", runner), \
                patch("basis.placement_benchmark_native.run_placement", placement), \
                patch("basis.placement_benchmark_native.write_benchmark_report",
                      side_effect=SyntaxError("synthetic renderer import failure")):
            with self.assertRaises(SyntaxError):
                run_seed_benchmark(outdir, {})
        checkpoint = json.loads((outdir / "checkpoint.json").read_text())
        self.assertEqual(checkpoint["status"], "completed")
        self.assertEqual(len(checkpoint["results"]), 14)
        self.assertEqual(checkpoint["evaluation"]["planned_query_count"], 7)
        self.assertIsNotNone(checkpoint["finished_at"])


if __name__ == "__main__":
    unittest.main()
