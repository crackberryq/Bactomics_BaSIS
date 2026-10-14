"""Stage boundaries retain evidence and failures across separate JSON workers."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from basis import deep
from basis.deep_stages import STAGES, run_stage
from tests import test_deep

StubRunner = test_deep.StubRunner


class DeepStageTests(unittest.TestCase):
    def setUp(self):
        # Reuse the established synthetic sequences and native-tool stub without
        # inheriting (and rerunning) the entire original regression suite.
        self.fixture = test_deep.DeepTests()
        self.fixture.setUp()
        self.root, self.sample = self.fixture.root, self.fixture.sample
        self.outdir = self.root / "stages"

    def tearDown(self):
        self.fixture.tearDown()

    def roundtrip(self, state):
        return json.loads(json.dumps(state, allow_nan=False))

    def staged(self, records, config, runner, stop=None):
        state = None
        for stage in STAGES:
            state = self.roundtrip(run_stage(stage, self.sample, records, config,
                                            self.outdir, runner, state))
            if stage == stop:
                break
        return state

    def test_json_workers_equal_compatibility_entry_and_keep_domain_caches_private(self):
        self.fixture.catalog()
        config = self.fixture.config(domain_database=self.fixture.domain_database())
        records = [self.fixture.finding(), self.fixture.finding(
            finding_id="deferred", protein_id="p2", start=100, end=135,
            evidence_level="exploratory", attributes={"cutoff": "loose"})]
        config["include_exploratory"] = False
        expected_config = copy.deepcopy(config)
        direct_runner = StubRunner()
        expected = self.roundtrip(deep.run_deep(self.sample, records, config,
                                               self.outdir, direct_runner))
        staged_runner = StubRunner()
        state = self.staged(records, config, staged_runner)
        self.assertEqual(state["payload"], expected)
        self.assertEqual(staged_runner.calls, direct_runner.calls)
        self.assertEqual(config, expected_config)
        self.assertTrue(state["internal"]["_retrieval_audit"])
        self.assertNotIn("_domain_hash_cache", state["internal"])
        self.assertEqual(state["completed_stages"], list(STAGES))
        self.assertEqual(state["payload"]["metrics"]["deferred_candidates"], 1)
        self.assertNotIn("catalog", state)
        self.assertNotIn("sequences", state)

    def test_native_commands_occur_in_their_declared_stage(self):
        self.fixture.catalog()
        records = [self.fixture.finding()]
        config = self.fixture.config(domain_database=self.fixture.domain_database())
        runner = StubRunner()
        state = run_stage("prepare", self.sample, records, config, self.outdir, runner)
        self.assertEqual(runner.calls, [])
        self.assertNotIn("domains", state["candidates"][0]["analyses"])
        state = run_stage("domains", self.sample, records, config, self.outdir,
                          runner, self.roundtrip(state))
        self.assertEqual([r[0] for r in runner.calls], ["hmmscan"])
        state = run_stage("references", self.sample, records, config, self.outdir,
                          runner, self.roundtrip(state))
        self.assertEqual([r[0] for r in runner.calls], ["hmmscan", "blastp"])
        self.assertNotIn("phylogeny", state["candidates"][0]["analyses"])
        state = run_stage("phylogeny", self.sample, records, config, self.outdir,
                          runner, self.roundtrip(state))
        self.assertEqual([r[0] for r in runner.calls], ["hmmscan", "blastp", "mafft", "iqtree"])
        self.assertEqual(state["candidates"][0]["execution"], "completed")

    def test_failures_and_no_assessment_are_preserved_across_workers(self):
        self.fixture.catalog()
        config = self.fixture.config(domain_database=self.fixture.domain_database())
        records = [self.fixture.finding(), self.fixture.finding(
            finding_id="missing", protein_id="absent", start=100, end=135)]
        result = self.staged(records, config, StubRunner(fail_hmm=True))["payload"]
        by_protein = {c["protein_id"]: c for c in result["candidates"]}
        self.assertEqual(by_protein["p1"]["execution"], "failed")
        self.assertEqual(by_protein["p1"]["analyses"]["domains"]["status"], "failed")
        self.assertEqual(by_protein["absent"]["execution"], "not_assessed")
        self.assertEqual(result["metrics"]["failed_candidates"], 1)
        self.assertEqual(result["metrics"]["not_assessed_candidates"], 1)
        self.assertFalse(result["complete"])

    def test_global_search_failure_stays_failure_not_empty_reference_result(self):
        self.fixture.catalog()
        records, config = [self.fixture.finding()], self.fixture.config()
        state = self.staged(records, config, StubRunner(), stop="domains")
        with self.assertRaisesRegex(RuntimeError, "external-tool failure"):
            run_stage("references", self.sample, records, config, self.outdir,
                      StubRunner(fail=True), state)
        self.assertEqual(state["completed_stages"], ["prepare", "domains"])

    def test_order_and_binding_reject_incompatible_checkpoint(self):
        records, config = [self.fixture.finding()], {}
        with self.assertRaisesRegex(ValueError, "prepare must run first"):
            run_stage("domains", self.sample, records, config, self.outdir, StubRunner())
        state = self.staged(records, config, StubRunner(), stop="prepare")
        with self.assertRaisesRegex(ValueError, "out of order"):
            run_stage("references", self.sample, records, config, self.outdir, StubRunner(), state)
        for sample, rows, cfg, outdir in (
                ({**self.sample, "sample_id": "other"}, records, config, self.outdir),
                (self.sample, [], config, self.outdir),
                (self.sample, records, {"min_identity": 90}, self.outdir),
                (self.sample, records, config, self.root / "other")):
            with self.subTest(sample=sample["sample_id"], cfg=cfg, outdir=outdir):
                with self.assertRaisesRegex(ValueError, "state does not match"):
                    run_stage("domains", sample, rows, cfg, outdir, StubRunner(), state)

    def test_unquoted_yaml_review_date_is_checkpoint_serializable(self):
        profiles = self.root / "profiles"
        profiles.mkdir()
        (profiles / "example.yaml").write_text(
            "schema_version: 1\nprofile_id: example\nvalidation_status: unvalidated_template\n"
            "interpretation_policy: exploratory_only\ncandidate_aliases: [example]\n"
            "required_comparison_groups: []\nanchor_reference_ids: []\n"
            "review:\n  date: 2026-10-14\n")
        state = self.staged([self.fixture.finding()], {"profiles_dir": str(profiles)},
                            StubRunner(), stop="prepare")
        self.assertEqual(state["candidates"][0]["profile"]["review"]["date"], "2026-10-14")

    def test_changed_query_and_catalogue_are_rejected_between_stages(self):
        self.fixture.catalog()
        records, config = [self.fixture.finding()], self.fixture.config()
        state = self.staged(records, config, StubRunner(), stop="domains")
        query = state["candidates"][0]["query"]
        path = Path(query["path"])
        original = path.read_text()
        path.write_text(original.replace("MCKAC", "MCQAC"))
        with self.assertRaisesRegex(ValueError, "query sequence changed"):
            run_stage("references", self.sample, records, config, self.outdir, StubRunner(), state)
        path.write_text(original)
        state = run_stage("references", self.sample, records, config, self.outdir, StubRunner(), state)
        references = Path(config["reference_fasta"])
        references.write_text(references.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "catalogue changed"):
            run_stage("phylogeny", self.sample, records, config, self.outdir,
                      StubRunner(), self.roundtrip(state))

    def test_tree_budget_keeps_candidate_order_after_reference_checkpoint(self):
        self.fixture.catalog()
        records = [self.fixture.finding(), self.fixture.finding(
            finding_id="second", protein_id="p2", start=100, end=135)]
        config = self.fixture.config(auto_phylogeny=True)
        state = self.staged(records, config, StubRunner(), stop="references")
        visited = []

        def bounded(candidate, catalog, cfg, directory, runner, budget):
            visited.append((candidate["candidate_id"], budget["used"]))
            budget["used"] += 1
            return {"status": "not_assessed", "reason": "synthetic_budget_fixture"}

        with patch("basis.deep.reuse_review", return_value=None), \
                patch("basis.deep.run_bounded", side_effect=bounded):
            state = run_stage("phylogeny", self.sample, records, config, self.outdir,
                              StubRunner(), self.roundtrip(state))
        self.assertEqual(visited, [(c["candidate_id"], i)
                                  for i, c in enumerate(state["candidates"])])
        self.assertEqual(state["tree_budget"]["used"], 2)
        self.assertTrue(all(c["execution"] == "completed" for c in state["candidates"]))

    def test_layers_remain_separate_and_catalogue_warnings_are_not_duplicated(self):
        self.fixture.catalog()
        records, config = [self.fixture.finding()], self.fixture.config()
        state = self.staged(records, config, StubRunner(), stop="phylogeny")
        notes = list(state["notes"])
        with patch("basis.prosite.run_prosite", return_value=["patterns-file"]) as patterns, \
                patch("basis.prosite_profiles.run_profiles", return_value=["profiles-file"]) as profiles:
            state = run_stage("patterns", self.sample, records, config, self.outdir,
                              StubRunner(), self.roundtrip(state))
            patterns.assert_called_once()
            profiles.assert_not_called()
            state = run_stage("profiles", self.sample, records, config, self.outdir,
                              StubRunner(), self.roundtrip(state))
            profiles.assert_called_once()
        for stage in ("motifs", "placement", "finalize"):
            state = run_stage(stage, self.sample, records, config, self.outdir,
                              StubRunner(), self.roundtrip(state))
        self.assertEqual(state["payload"]["notes"], notes)
        self.assertIn("patterns-file", state["payload"]["artifacts"])
        self.assertIn("profiles-file", state["payload"]["artifacts"])


if __name__ == "__main__":
    unittest.main()
