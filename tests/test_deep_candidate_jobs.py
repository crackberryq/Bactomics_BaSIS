"""Synthetic parity and restart contracts for candidate-sized workflow jobs."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from basis import deep
from basis import deep_candidates as workers
from basis import workflow_runtime as runtime
from basis.deep_candidate_jobs import (apply_fragment, candidate_ids, finalize_state,
    merge_fragments, placement_budget_plan, run_candidate_stage, tree_budget_plan)
from basis.deep_stages import run_stage
from tests import test_deep


class CandidateJobTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_deep.DeepTests()
        self.fixture.setUp()
        self.fixture.catalog()
        self.root, self.sample = self.fixture.root, self.fixture.sample
        self.work = self.root / "work"
        self.config = self.fixture.config(domain_database=self.fixture.domain_database())
        self.records = [self.fixture.finding(), self.fixture.finding(
            finding_id="f2", protein_id="p2", start=100, end=135), self.fixture.finding(
            finding_id="defer", protein_id="p3", start=200, end=235,
            evidence_level="exploratory", attributes={"cutoff": "loose"})]
        self.config["include_exploratory"] = False

    def tearDown(self):
        self.fixture.tearDown()

    def prepared(self):
        return run_stage("prepare", self.sample, self.records, self.config,
                         self.work, test_deep.StubRunner())

    def fragments(self, state, phase, runner=None, offsets=None):
        return [run_candidate_stage(phase, self.sample, self.records, self.config,
                self.work, runner or test_deep.StubRunner(), state, cid,
                budget_start=(offsets or {}).get(cid, 0)) for cid in candidate_ids(state)]

    def test_candidate_partition_preserves_existing_payload_and_native_calls(self):
        expected_runner = test_deep.StubRunner()
        expected = deep.run_deep(self.sample, self.records, self.config, self.work, expected_runner)
        state = self.prepared()
        runner = test_deep.StubRunner()
        state = merge_fragments(state, "domains", self.fragments(state, "domains", runner))
        state.update(completed_stages=["prepare", "domains"])
        state = run_stage("references", self.sample, self.records, self.config, self.work, runner, state)
        budget = tree_budget_plan(state, self.config)
        for phase in ("phylogeny", "patterns", "profiles", "motifs", "placement"):
            state = merge_fragments(state, phase, self.fragments(state, phase, runner, budget["offsets"]))
        result = finalize_state(self.sample, self.records, self.config, self.work, runner, state)["payload"]
        for field in ("candidates", "metrics", "notes", "complete", "completion_summary"):
            self.assertEqual(result[field], expected[field], field)
        self.assertEqual(set(result["artifacts"]), set(expected["artifacts"]))
        self.assertEqual(runner.calls, expected_runner.calls)

    def test_fragment_rejects_changed_candidate_and_duplicate_or_missing_jobs(self):
        state = self.prepared()
        fragments = self.fragments(state, "domains")
        with self.assertRaisesRegex(ValueError, "exactly one fragment"):
            merge_fragments(state, "domains", fragments[:1])
        with self.assertRaisesRegex(ValueError, "exactly one fragment"):
            merge_fragments(state, "domains", fragments + fragments[:1])
        changed = copy.deepcopy(state)
        changed["candidates"][0]["gene"] = "changed"
        with self.assertRaisesRegex(ValueError, "input checksum"):
            apply_fragment(changed, fragments[0])
        changed = copy.deepcopy(fragments[0])
        changed["candidate"]["candidate_id"] = "other"
        with self.assertRaisesRegex(ValueError, "identifier"):
            apply_fragment(state, changed)

    def test_budget_order_is_reserved_before_parallel_execution(self):
        state = self.prepared()
        state = merge_fragments(state, "domains", self.fragments(state, "domains"))
        state.update(completed_stages=["prepare", "domains"])
        state = run_stage("references", self.sample, self.records, self.config,
                          self.work, test_deep.StubRunner(), state)
        config = {**self.config, "auto_phylogeny": True, "maximum_tree_candidates": 1}
        with patch("basis.deep_trees.eligible", return_value=None), patch("basis.deep.reuse_review", return_value=None):
            plan = tree_budget_plan(state, config)
        self.assertEqual(list(plan["offsets"].values()), [0, 1])
        self.assertEqual(plan["used"], 1)
        with patch("basis.deep_trees.eligible", return_value=None), patch("basis.deep.reuse_review", return_value={"status": "completed"}):
            plan = tree_budget_plan(state, config)
        self.assertEqual(list(plan["offsets"].values()), [0, 0])
        self.assertEqual(plan["used"], 0)

    def test_placement_offsets_count_only_existing_adequacy_gate(self):
        state = self.prepared()
        state.update(catalogue_configured=False, catalogue_files=[])
        config = {"family_placement": True, "maximum_placement_candidates": 1}
        panels = [{"adequacy": {"eligible": False}}, {"adequacy": {"eligible": True}}]
        with patch("basis.placement.placement_gate", return_value=None), \
             patch("basis.placement_panels.load_registry", return_value={}), \
             patch("basis.placement_panels.prepare_panel", side_effect=panels):
            plan = placement_budget_plan(state, config, self.work)
        self.assertEqual(list(plan["offsets"].values()), [0, 0])
        self.assertEqual(plan["used"], 1)
        self.assertEqual(list(plan["panels"].values()), panels)

    def _runtime_state(self):
        state = self.prepared()
        plan = {"plan_hash": "plan", "source_hash": "source"}
        root = self.root / "scheduler" / "stable"
        envelope = {"records": self.records, "config": self.config,
                    "work": str(self.work), "stopped": False, "commands": []}
        parent = workers.stage_path(root, "A", "prepare")
        runtime._write(parent, {**runtime._identity(plan, root, "A"),
            "schema": "basis-workflow-candidate-job-v1", "state": state,
            "status": "completed", "envelope": envelope})
        runtime._ready(parent, plan, root, "A")
        return plan, root, state

    def test_failed_candidate_retry_preserves_completed_sibling_and_attempt(self):
        plan, root, state = self._runtime_state()
        ids = candidate_ids(state)
        failed = {ids[1]}
        invocations = []

        def runner_factory(config, logfile):
            class Runner(test_deep.StubRunner):
                def __init__(self):
                    super().__init__(fail_hmm=any(cid in str(logfile) for cid in failed))
                    self.commands = self.calls
                    Path(logfile).parent.mkdir(parents=True, exist_ok=True)
                    Path(logfile).write_text("synthetic command log")
                def run(self, argv, stdout_path=None, cwd=None):
                    invocations.append((str(logfile), argv[0]))
                    return super().run(argv, stdout_path, cwd)
            return Runner()

        with patch.object(runtime, "_sample", return_value=self.sample), patch.object(workers, "CommandRunner", side_effect=runner_factory):
            good = workers.run_candidate(plan, root, "A", ids[0], "domains")
            bad = workers.run_candidate(plan, root, "A", ids[1], "domains")
            self.assertEqual(good["status"], "completed")
            self.assertEqual(bad["status"], "failed")
            good_path = workers.candidate_path(root, "A", ids[0], "domains")
            good_bytes = good_path.read_bytes()
            self.assertTrue(runtime.ready_path(good_path).is_file())
            self.assertFalse(runtime.ready_path(workers.candidate_path(root, "A", ids[1], "domains")).exists())
            failed.clear()
            retried = workers.run_candidate(plan, root, "A", ids[1], "domains")
            self.assertEqual(retried["status"], "completed")
            self.assertEqual(good_path.read_bytes(), good_bytes)
            self.assertEqual(sum(ids[0] in path for path, _ in invocations), 1)
            self.assertEqual(sum(ids[1] in path for path, _ in invocations), 2)
            self.assertEqual(len(list((self.work / ids[1] / "job-logs").glob("*.log"))), 2)

    def test_failure_diagnostics_require_current_nonempty_dependency_snapshot(self):
        plan, root, state = self._runtime_state()
        cid = candidate_ids(state)[0]
        parent = workers.stage_path(root, "A", "prepare")
        marker = runtime.ready_path(parent)
        marker_bytes = marker.read_bytes()
        failed = workers.candidate_path(root, "A", cid, "domains")
        workers._commit(failed, {"status": "failed", "candidate_id": cid,
            "phase": "domains", "state": workers._single(state, cid),
            "error": "synthetic worker failure"}, [parent], plan, root, "A")
        failed_bytes = failed.read_bytes()
        # Legacy failed preparation receipts have no bound prerequisites.
        legacy = workers.stage_path(root, "A", "legacy-prepare-failure")
        runtime._write(legacy, {**runtime._identity(plan, root, "A"),
            "status": "failed", "stage": "prepare", "error": "old unbound failure"})
        errors = workers.failure_diagnostics(plan, root, "A")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["candidate_id"], cid)
        self.assertEqual(errors[0]["producer_launch_id"], root.name)
        self.assertEqual(errors[0]["diagnostic_scope"], "validated_dependency_snapshot")
        marker.write_bytes(marker_bytes + b"\n")
        self.assertEqual(workers.failure_diagnostics(plan, root, "A"), [])
        marker.write_bytes(marker_bytes)
        self.assertEqual(len(workers.failure_diagnostics(plan, root, "A")), 1)
        marker.unlink()
        self.assertEqual(workers.failure_diagnostics(plan, root, "A"), [])
        self.assertEqual(failed.read_bytes(), failed_bytes)
        self.assertTrue(legacy.is_file())

    def test_runtime_shared_stages_collect_every_candidate_without_rerunning_tools(self):
        plan, root, state = self._runtime_state()
        class Runner(test_deep.StubRunner):
            def __init__(self, config, logfile):
                super().__init__()
                self.commands = self.calls
                Path(logfile).parent.mkdir(parents=True, exist_ok=True)
                Path(logfile).write_text("synthetic log")
        with patch.object(runtime, "_sample", return_value=self.sample), \
             patch.object(workers, "CommandRunner", Runner), \
             patch.object(runtime, "finish_deep_execution", return_value={"execution_status": "completed"}) as finish, \
             patch.object(runtime, "_attest") as attest:
            ids = candidate_ids(state)
            for cid in ids:
                self.assertEqual(workers.run_candidate(plan, root, "A", cid, "domains")["status"], "completed")
            for stage in ("references", "tree-plan"):
                self.assertEqual(workers.run_shared(plan, root, "A", stage)["status"], "completed")
            for cid in reversed(ids):
                for phase in ("phylogeny", "patterns", "profiles", "motifs"):
                    self.assertEqual(workers.run_candidate(plan, root, "A", cid, phase)["status"], "completed")
            self.assertEqual(workers.run_shared(plan, root, "A", "placement-plan")["status"], "completed")
            for cid in ids:
                self.assertEqual(workers.run_candidate(plan, root, "A", cid, "placement")["status"], "completed")
            result = workers.run_shared(plan, root, "A", "finalize")
            self.assertEqual(result["status"], "completed")
            metrics = result["state"]["payload"]["metrics"]
            self.assertEqual(metrics["completed_candidates"], 2)
            self.assertEqual(metrics["deferred_candidates"], 1)
            self.assertTrue(runtime.ready_path(workers.stage_path(root, "A", "finalize")).is_file())
            self.assertEqual(finish.call_count, 1)
            self.assertEqual(attest.call_count, 1)
            self.assertEqual(len(result["dependency_checksums"]), 3)

    def test_phase_attestation_never_owns_stale_descendant_outputs(self):
        state = self.prepared()
        cid = candidate_ids(state)[0]
        directory = self.work / cid
        (directory / "domains.domtblout").write_text("old domain result")
        (directory / "alignment.faa").write_text("old downstream alignment")
        names = {p.name for p in workers._phase_files(self.work, cid, "domains")}
        self.assertNotIn("alignment.faa", names)
        archive = workers._archive_interrupted(self.work, cid, "domains")
        self.assertTrue((Path(archive) / "domains.domtblout").is_file())
        self.assertTrue((directory / "alignment.faa").is_file())

    def test_external_returned_artifact_is_bound_to_candidate_receipt(self):
        from basis.scheduler_persistence import prepare_scheduler_state
        plan, root, state = self._runtime_state()
        plan["config"] = {}
        cid = candidate_ids(state)[0]
        external = self.root / "shared-tree-cache" / "tree.newick"
        external.parent.mkdir()
        external.write_text("(synthetic_A,synthetic_B);")
        runner = test_deep.StubRunner()
        runner.commands = runner.calls
        original = workers.run_candidate_stage
        def generated(*args, **kwargs):
            fragment = original(*args, **kwargs)
            fragment["artifacts"].append(str(external))
            return fragment
        with patch.object(runtime, "_sample", return_value=self.sample), \
             patch.object(workers, "CommandRunner", return_value=runner), \
             patch.object(workers, "run_candidate_stage", side_effect=generated):
            result = workers.run_candidate(plan, root, "A", cid, "domains")
        self.assertIn(str(external), result["artifact_checksums"])
        external.write_text("changed shared reference tree")
        audit = prepare_scheduler_state(plan, root, root.parent / "audit")
        self.assertGreater(audit["revoked_targets"], 0)
        self.assertFalse(runtime.ready_path(workers.candidate_path(root, "A", cid, "domains")).exists())

    def test_candidate_gate_detects_artifact_tampering(self):
        plan, root, state = self._runtime_state()
        cid = candidate_ids(state)[0]
        runner = test_deep.StubRunner()
        runner.commands = runner.calls
        with patch.object(runtime, "_sample", return_value=self.sample), patch.object(workers, "CommandRunner", return_value=runner):
            workers.run_candidate(plan, root, "A", cid, "domains")
        (self.work / cid / "domains.domtblout").write_text("changed")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            workers._gate(workers.candidate_path(root, "A", cid, "domains"), plan, root, "A")


import importlib.util
import sys
import tempfile


@unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake unavailable")
class CandidateSnakemakeRecoveryTests(unittest.TestCase):
    def test_real_checkpoint_retry_preserves_successful_candidate_target(self):
        from basis.config import load_config
        from basis.demo import create_demo
        from basis.pipeline import make_plan, run_pipeline
        with tempfile.TemporaryDirectory(prefix="basis-candidate-dag-") as folder:
            root = Path(folder)
            config = load_config(create_demo(root))
            config["execution"]["cores"] = 2
            for value in config["modules"].values():
                value.update(threads=1, memory_mb=32)
            database = root / "synthetic.hmm"
            database.write_text("HMMER3/f synthetic software fixture\n")
            for suffix in (".h3f", ".h3i", ".h3m", ".h3p"):
                Path(str(database) + suffix).write_text("synthetic index")
            tool = root / "hmmscan_fixture.py"
            tool.write_text("#!" + sys.executable + "\n" + r'''import sys
from pathlib import Path
root=Path(__file__).parent
query=Path(sys.argv[-1])
lines=query.read_text().splitlines()
qid=lines[0][1:]; sequence=''.join(lines[1:]); length=len(sequence)
with (root/'native.calls').open('a') as stream:
    stream.write(str(query)+'\n')
if 'DEMO_A' in query.parts and 'G' in sequence and (root/'fail-native').exists():
    raise SystemExit('intentional synthetic candidate failure')
output=Path(sys.argv[sys.argv.index('--domtblout')+1])
output.write_text(f'Example PFTEST.1 10 {qid} - {length} 1e-20 40 0 1 1 1e-20 1e-20 40 0 1 10 1 10 1 10 0.99 Synthetic domain\n# Program: hmmscan\n# [ok]\n')
print('synthetic HMMER output')
''')
            tool.chmod(0o755)
            config["deep"].update(threads=1, memory_mb=64, domain_database=str(database),
                domain_native_cutoff=None, executables={"hmmscan": str(tool)})
            failure = root / "fail-native"
            failure.touch()
            plan = make_plan(config, True, True)
            first, code = run_pipeline(plan, engine="snakemake")
            self.assertEqual(code, 2)
            stable = Path(first["run"]["scheduler_receipt_dir"])
            prepared = json.loads((stable / "DEMO_A/deep-stages/prepare.json").read_text())
            candidates = prepared["state"]["candidates"]
            selected = [c for c in candidates if c["selected"]]
            self.assertGreaterEqual(len(selected), 2)
            good = next(c for c in selected if c["protein_id"] == "DEMO_A_0001")
            bad = next(c for c in selected if c["protein_id"] == "DEMO_A_0002")
            good_receipt = workers.candidate_path(stable, "DEMO_A", good["candidate_id"], "domains")
            good_marker = runtime.ready_path(good_receipt)
            self.assertTrue(good_marker.is_file(), Path(first["run"]["scheduler_log"]).read_text()[-5000:])
            original = good_marker.read_bytes(), good_receipt.read_bytes()
            self.assertFalse(runtime.ready_path(workers.candidate_path(stable, "DEMO_A", bad["candidate_id"], "domains")).exists())
            failure.unlink()
            second, code = run_pipeline(plan, engine="snakemake")
            self.assertEqual(code, 0, Path(second["run"]["scheduler_log"]).read_text()[-8000:])
            self.assertEqual(original, (good_marker.read_bytes(), good_receipt.read_bytes()))
            calls = (root / "native.calls").read_text().splitlines()
            self.assertEqual(calls.count(good["query"]["path"]), 1)
            self.assertEqual(calls.count(bad["query"]["path"]), 2)
            result = json.loads((Path(plan["outdir"]) / "results/DEMO_A/deepsafe/result.json").read_text())
            self.assertEqual(result["execution_status"], "completed")
            self.assertEqual(result["metrics"]["failed_candidates"], 0)
            self.assertEqual(result["metrics"]["completed_candidates"], len(selected))


if __name__ == "__main__":
    unittest.main()
