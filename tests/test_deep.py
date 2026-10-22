"""Scientific state survives candidate job boundaries and honest abstentions."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from safety import deep


class NoNativeRunner:
    commands = []

    def run(self, *args, **kwargs):
        raise AssertionError("This synthetic context-only case requires no native command")


class DeepWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.isolate = Path(self.tmp.name) / "isolate"
        self.isolate.mkdir()
        self.sample = {"sample_id": "sample1", "assembly": str(self.isolate / "assembly.fna"),
                       "proteins": str(self.isolate / "proteins.faa")}
        Path(self.sample["assembly"]).write_text(">contig1\n" + "A" * 600 + "\n")
        Path(self.sample["proteins"]).write_text(">p1\nMAAAAAAAAAAAAAAAAAAA\n>p2\nMAAAAAAAAAAAAAAAAAAA\n")
        self.cfg = {"domain_scan": False, "motifs": False, "phylogeny": False,
                    "prosite_patterns": False, "prosite_profiles": False,
                    "family_placement": False, "include_exploratory": False,
                    "threads": 4, "maximum_candidates": 10}
        deep.write(self.isolate / "safety/metadata/sample.json", self.sample)
        deep.write(self.isolate / "safety/metadata/config.json", {"deep": self.cfg})
        finding = {"finding_id": "f1", "tool": "amrfinder", "category": "amr",
                   "gene": "synthetic", "protein_id": "p1", "contig": "contig1",
                   "start": 1, "end": 60, "strand": "+", "identity": 100,
                   "coverage": 100, "evidence_level": "curated_model",
                   "attributes": {"scope": "core", "native_method": "EXACTP"}}
        exploratory = {**copy.deepcopy(finding), "finding_id": "f2", "protein_id": "p2",
                       "start": 100, "end": 159, "evidence_level": "exploratory"}
        self.record = {"sample_id": "sample1", "module": "amrfinder", "execution_status": "completed",
                       "findings": [finding, exploratory]}
        self.record_path = self.isolate / "safety/evidence/amrfinder.json"
        deep.write(self.record_path, self.record)
        self.runner = NoNativeRunner()

    def tearDown(self):
        self.tmp.cleanup()

    def native(self, name):
        return deep.initialize_output(self.isolate / "safety/deepsafe/native" / name, self.isolate)

    def prepared(self):
        result = deep.prepare(self.isolate, [self.record_path], self.native("prepare"), 1, self.runner)
        return json.loads(json.dumps(result))

    def step(self, document, phase, children=(), candidate=None):
        native = self.native(phase + ("/" + candidate if candidate else ""))
        return deep.invoke(document, phase, native, 1, self.runner, candidate, children)

    def completed(self):
        prepared = self.prepared()
        cid = prepared["selected_candidate_ids"][0]
        domains = self.step(prepared, "domains", candidate=cid)
        references = self.step(prepared, "references", [domains])
        tree_plan = self.step(references, "tree-plan")
        current = self.step(tree_plan, "phylogeny", candidate=cid)
        for phase in ("patterns", "profiles", "motifs"):
            current = self.step(current, phase, candidate=cid)
        placement_plan = self.step(tree_plan, "placement-plan", [current])
        placement = self.step(placement_plan, "placement", candidate=cid)
        return self.step(placement_plan, "finalize", [placement])

    def test_selected_and_deferred_survive_all_layers(self):
        document = self.completed()
        result = deep.evidence_record(document)
        self.assertEqual(result["execution_status"], "completed")
        self.assertEqual(result["metrics"]["selected_candidates"], 1)
        self.assertEqual(result["metrics"]["completed_candidates"], 1)
        self.assertEqual(result["metrics"]["deferred_candidates"], 1)
        self.assertFalse(result["complete"])
        selected = [c for c in result["candidates"] if c["selected"]]
        self.assertEqual(selected[0]["execution"], "completed")
        self.assertIn("consolidated_evidence", selected[0])
        self.assertIn("motifs", selected[0]["analyses"])

    def test_all_deferred_still_produces_honest_summary(self):
        self.record["findings"] = self.record["findings"][1:]
        deep.write(self.record_path, self.record)
        prepared = self.prepared()
        self.assertEqual(prepared["selected_candidate_ids"], [])
        references = self.step(prepared, "references")
        plan = self.step(references, "tree-plan")
        placement = self.step(plan, "placement-plan")
        result = deep.evidence_record(self.step(placement, "finalize"))
        self.assertEqual(result["metrics"]["deferred_candidates"], 1)
        self.assertEqual(result["metrics"]["completed_candidates"], 0)
        self.assertFalse(result["complete"])

    def test_aware_audit_survives_candidate_stages_and_evidence_output(self):
        self.cfg["aware"] = {"enabled": True, "preset": "auto-medium"}
        deep.write(self.isolate / "safety/metadata/config.json", {"deep": self.cfg})
        result = deep.evidence_record(self.completed())
        audit = result["aware_selection"]
        self.assertEqual(audit["effective_policy"]["preset"], "auto-medium")
        self.assertEqual(audit["counts"]["selected_candidates"], 1)
        self.assertEqual(audit["counts"]["admission_deferred"], 1)
        self.assertEqual(audit["counts"]["unique_selected_sequences"], 1)
        self.assertFalse(result["complete"])
        saved = self.isolate / "safety/deepsafe/native/prepare/aware_selection.json"
        self.assertEqual(deep.read(saved), audit)

    def test_missing_references_is_not_promoted_to_completed(self):
        self.record["findings"][0]["identity"] = 80
        deep.write(self.record_path, self.record)
        result = deep.evidence_record(self.completed())
        selected = next(c for c in result["candidates"] if c["selected"])
        self.assertEqual(selected["execution"], "not_assessed")
        self.assertEqual(selected["reason"], "reference_catalogue_not_configured")

    def test_candidate_failure_raises_to_snakemake_without_mutating_parent(self):
        prepared = self.prepared()
        before = copy.deepcopy(prepared)
        cid = prepared["selected_candidate_ids"][0]
        def failure(sample, records, config, outdir, runner, state):
            state["candidates"][0].update(execution="failed", reason="synthetic native failure")
        with patch.dict(deep.stages._HANDLERS, {"domains": failure}):
            with self.assertRaisesRegex(RuntimeError, "synthetic native failure"):
                self.step(prepared, "domains", candidate=cid)
        self.assertEqual(prepared, before)

    def test_merge_rejects_missing_or_duplicate_candidate_tasks(self):
        prepared = self.prepared()
        cid = prepared["selected_candidate_ids"][0]
        domains = self.step(prepared, "domains", candidate=cid)
        for children in ([], [domains, domains]):
            with self.assertRaisesRegex(ValueError, "one state"):
                deep.combine(prepared, children)

    def test_native_cleanup_only_touches_this_rule_output(self):
        first = self.native("domains/cand_" + "1" * 20)
        second = self.native("patterns/cand_" + "1" * 20)
        (first / "old.txt").write_text("interrupted")
        (second / "keep.txt").write_text("completed")
        deep.initialize_output(first, self.isolate)
        self.assertFalse((first / "old.txt").exists())
        self.assertTrue((second / "keep.txt").exists())
        with self.assertRaises(ValueError):
            deep.initialize_output(self.isolate, self.isolate)

    def test_stage_identity_tampering_is_rejected(self):
        document = self.prepared()
        path = self.isolate / "state.json"
        deep.write(path, document)
        self.assertEqual(deep.load_document(path)["context"], document["context"])
        document["context"]["sample"]["sample_id"] = "other"
        deep.write(path, document)
        with self.assertRaisesRegex(ValueError, "context changed"):
            deep.load_document(path)

    def test_invalid_parent_does_not_delete_existing_native_output(self):
        native = self.native("profiles/cand_" + "1" * 20)
        sentinel = native / "keep.txt"
        sentinel.write_text("saved work")
        bad = self.isolate / "bad-state.json"
        bad.write_text('{"schema": "unknown"}')
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            deep.main(["profiles", "--isolate", str(self.isolate), "--parent", str(bad),
                       "--candidate", "cand_" + "1" * 20,
                       "--output", str(self.isolate / "new.json"),
                       "--native", str(native), "--log", str(self.isolate / "job.log")])
        self.assertEqual(sentinel.read_text(), "saved work")

    def test_execution_config_has_no_old_environment_or_shared_cache(self):
        config = deep.execution_config({"command_prefix": ["conda", "run"],
                                         "placement_cache_dir": "/old/cache", "min_identity": 30},
                                        self.isolate / "native", 2)
        self.assertEqual(config["command_prefix"], [])
        self.assertEqual(config["threads"], 2)
        self.assertEqual(config["min_identity"], 30)
        self.assertTrue(config["placement_cache_dir"].startswith(str(self.isolate)))

    @unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake is not installed in the test interpreter")
    def test_checkpoint_resume_and_report_change_do_not_rerun_deep(self):
        """Run the real DAG using context-only synthetic evidence; no tool stubs."""
        profiles = self.isolate / "custom-profiles"
        profiles.mkdir()
        profile = profiles / "synthetic.yaml"
        profile.write_text("schema_version: 1\nprofile_id: synthetic_registry_only\n"
                           "validation_status: unvalidated_template\n"
                           "interpretation_policy: exploratory_only\n"
                           "candidate_aliases: [unmatched_fixture]\n"
                           "required_comparison_groups: []\nanchor_reference_ids: []\n")
        self.cfg["profiles_dir"] = str(profiles)
        self.cfg["aware"] = {"enabled": True, "preset": "auto-medium"}
        deep.write(self.isolate / "safety/metadata/config.json", {"deep": self.cfg})
        upstream = {str(path): path.stat().st_mtime_ns for path in
                    [self.record_path, Path(self.sample["assembly"]), Path(self.sample["proteins"])]}
        setup = self.isolate / "setup.txt"
        setup.write_text("ready\n")
        template = self.isolate / "template.txt"
        template.write_text("report version one\n")
        report = self.isolate / "report.txt"
        evidence = self.isolate / "safety/evidence/deepsafe.json"
        snakefile = self.isolate / "test.smk"
        rules = self.isolate / "deep.smk"
        # This test targets DAG/resume semantics. Benchmark process sampling is
        # exercised by deployment CI, not a short synthetic no-native workload.
        rules.write_text("\n".join(line for line in (ROOT / "workflow/rules/deep.smk").read_text().splitlines()
                                   if not line.lstrip().startswith("benchmark:")) + "\n")
        snakefile.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            f"ROOT = Path({str(ROOT)!r})\n"
            "sys.path[:0] = [str(ROOT / 'workflow/lib'), str(ROOT / 'workflow/vendor')]\n"
            "PYTHON = sys.executable\n"
            f"CODE_PATH = {str(ROOT / 'workflow/lib') + ':' + str(ROOT / 'workflow/vendor')!r}\n"
            "class Context:\n"
            f"    def setup_target(self, isolate): return {str(setup)!r}\n"
            f"    def evidence_targets(self, isolate): return [{str(self.record_path)!r}]\n"
            f"    def env(self, name): return {str(ROOT / 'envs/deepsafe.yml')!r}\n"
            "CTX = Context()\n"
            "rule all:\n"
            f"    input: {str(report)!r}\n"
            "rule render:\n"
            f"    input: evidence={str(evidence)!r}, template={str(template)!r}\n"
            f"    output: {str(report)!r}\n"
            "    shell: 'cat {input.template:q} > {output:q}'\n"
            f"include: {str(rules)!r}\n")
        command = [sys.executable, "-m", "snakemake", "--snakefile", str(snakefile),
                   "--directory", str(self.isolate), "--cores", "2", "--rerun-incomplete"]
        first = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        snapshots = {str(path): path.stat().st_mtime_ns
                     for path in (self.isolate / "safety/deepsafe").rglob("*") if path.is_file()}
        second = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("Nothing to be done", second.stdout + second.stderr)
        template.write_text("report version two, edited\n")
        third = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
        self.assertIn("report version two", report.read_text())
        self.assertEqual(snapshots, {str(path): path.stat().st_mtime_ns
                                   for path in (self.isolate / "safety/deepsafe").rglob("*") if path.is_file()})
        prepared = self.isolate / "safety/deepsafe/states/prepare.json"
        old_identity = deep.read(prepared)["context_sha256"]
        profile.write_text(profile.read_text() + "notes: [same-path resource edit]\n")
        fourth = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(fourth.returncode, 0, fourth.stdout + fourth.stderr)
        self.assertNotEqual(deep.read(prepared)["context_sha256"], old_identity)
        self.assertEqual(upstream, {path: Path(path).stat().st_mtime_ns for path in upstream})
        fifth = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(fifth.returncode, 0, fifth.stdout + fifth.stderr)
        self.assertIn("Nothing to be done", fifth.stdout + fifth.stderr)
        self.cfg["maximum_candidates"] = 11
        deep.write(self.isolate / "safety/metadata/config.json", {"deep": self.cfg})
        sixth = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(sixth.returncode, 0, sixth.stdout + sixth.stderr)
        self.assertEqual(deep.load_document(prepared)["context"]["config"]["maximum_candidates"], 11)
        # Admission-only configuration changes must recompute the selection;
        # old candidate outputs must not make an excluded candidate reappear.
        self.cfg["aware"]["tools"] = {"amrfinder": {"enabled": False}}
        deep.write(self.isolate / "safety/metadata/config.json", {"deep": self.cfg})
        reselection = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(reselection.returncode, 0, reselection.stdout + reselection.stderr)
        self.assertEqual(deep.read(prepared)["selected_candidate_ids"], [])
        self.assertEqual(deep.read(evidence)["aware_selection"]["counts"]["admission_deferred"], 2)
        self.assertEqual(upstream, {path: Path(path).stat().st_mtime_ns for path in upstream})
        from safety.state_storage import object_paths
        shared = object_paths(prepared)[0]
        shared.unlink()
        # Require a consumer of saved state; an already-complete report need
        # not read unused checkpoint objects at all.
        evidence.unlink()
        report.unlink()
        missing = subprocess.run(command + ["--dry-run"], text=True, capture_output=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Missing shared state object", missing.stdout + missing.stderr)


if __name__ == "__main__":
    unittest.main()
