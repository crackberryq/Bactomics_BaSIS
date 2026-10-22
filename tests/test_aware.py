"""Synthetic policy and identity tests; no biological analyses or downloads."""
import copy
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from basis.aware import audit, evaluate, resolve_policy
from basis.config import default_config
from basis.deep import select_candidates
from basis.report_aware import render_aware
from safety.context import SafetyContext
sys.path.insert(0, str(ROOT / "scripts"))
from preview_aware import preview


def cfg(preset="auto-medium", **overrides):
    return {"aware": {"enabled": True, "preset": preset, **overrides},
            "maximum_candidates": 100, "maximum_exploratory_candidates": 100,
            "maximum_attention_candidates": 100}


def finding(tool="amrfinder", ident="a", pid="p1", **kwargs):
    value = {"finding_id": ident, "tool": tool, "category": "amr", "protein_id": pid,
             "gene": "synthetic_label", "family": "synthetic_family", "identity": 100.,
             "coverage": 100., "evidence_level": "curated_model", "attributes": {}}
    if tool == "amrfinder":
        value["attributes"] = {"native_method": "EXACTP"}
    elif tool == "rgi":
        value["attributes"] = {"cutoff": "strict", "nudged": False}
    elif tool in {"vfdb", "bacmet"}:
        value.update(category="virulence" if tool == "vfdb" else "stress", evidence_level="homology",
                     attributes={"query_coverage": 100., "evalue": 1e-20, "passes_threshold": True})
    elif tool == "pathogenfinder2":
        value.update(category="ml_attention", evidence_level="ml_attention", attributes={"rank": 1})
    value.update(kwargs)
    return value


class AwareTests(unittest.TestCase):
    def test_standard_default_is_medium_and_legacy_api_stays_explicit(self):
        self.assertEqual(default_config()["deep"]["aware"]["preset"], "auto-medium")
        self.assertFalse(resolve_policy({})["enabled"])
        original = [finding("pathogenfinder2")]
        self.assertTrue(select_candidates(original, {})[0]["selected"])
        self.assertFalse(select_candidates(original, cfg())[0]["selected"])

    def test_overrides_apply_after_preset_without_mutating_input(self):
        config = cfg(tools={"vfdb": {"min_identity": 95.}})
        before = copy.deepcopy(config)
        policy = resolve_policy(config)
        self.assertEqual(policy["tools"]["vfdb"]["min_identity"], 95.)
        self.assertEqual(policy["tools"]["vfdb"]["min_reference_coverage"], 70.)
        policy["tools"]["vfdb"]["min_identity"] = 1
        self.assertEqual(config, before)
        self.assertEqual(resolve_policy(cfg())["tools"]["vfdb"]["min_identity"], 80.)

    def test_invalid_config_rejected_before_dag_setup(self):
        for bad in (None, True, {"preset": "typo"}, {"enabled": "false"}, {"typo": True},
                    {"tools": {"rgi": {"identity": 90}}}, {"tools": {"unknown": {}}},
                    {"tools": {"vfdb": {"min_identity": True}}},
                    {"tools": {"vfdb": {"min_identity": 101}}},
                    {"tools": {"vfdb": {"max_evalue": float("nan")}}},
                    {"tools": {"rgi": {"cutoffs": ["Strong"]}}},
                    {"tools": {"amrfinder": {"methods": []}}},
                    {"queues": {"attention": "false"}},
                    {"tools": {"pathogenfinder2": {"maximum_rank": 1.5}}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                SafetyContext({"deep": {"aware": bad}}, ROOT)

    def test_presets_use_native_categories_not_universal_identity(self):
        rows = [finding(), finding("rgi", "r", "p2"),
                finding("amrfinder", "h", "p3", identity=None, coverage=None, attributes={"native_method": "HMM"})]
        self.assertEqual(sum(c["selected"] for c in select_candidates(rows, cfg("auto-strict"))), 1)
        self.assertEqual(sum(c["selected"] for c in select_candidates(rows, cfg())), 3)
        rows[1]["attributes"]["nudged"] = True
        self.assertFalse(next(c for c in select_candidates(rows, cfg()) if c["protein_id"] == "p2")["selected"])

    def test_missing_metrics_do_not_pass_numeric_overrides(self):
        config = cfg(tools={"rgi": {"min_reference_coverage": 70.}})
        row = finding("rgi", coverage=None)
        row["attributes"]["reference_length_ratio_percent"] = 100
        result = select_candidates([row], config)[0]
        self.assertFalse(result["selected"])
        self.assertIn("missing_or_invalid:min_reference_coverage", result["aware"]["evidence"][0]["reasons"])

    def test_numeric_boundaries_and_nonfinite_values(self):
        config = cfg()
        row = finding("vfdb", identity=80., coverage=70.)
        row["attributes"].update(query_coverage=70., evalue=1e-5)
        self.assertTrue(select_candidates([row], config)[0]["selected"])
        for field, values in (("identity", [79.999, None, True, float("nan"), 101]),
                              ("coverage", [69.999, None]),
                              ("query_coverage", [69.999, None]),
                              ("evalue", [1.01e-5, -1, float("inf")])):
            for value in values:
                changed = copy.deepcopy(row)
                (changed if field in {"identity", "coverage"} else changed["attributes"])[field] = value
                with self.subTest(field=field, value=value):
                    self.assertFalse(select_candidates([changed], config)[0]["selected"])

    def test_loose_retains_exploratory_status_and_independent_queue_gates(self):
        row = finding("rgi", evidence_level="exploratory", attributes={"cutoff": "loose", "nudged": False})
        rows = [row, finding("pathogenfinder2", "p", "p2")]
        self.assertFalse(any(c["selected"] for c in select_candidates(rows, cfg())))
        loose = select_candidates(rows, cfg("auto-loose"))
        self.assertTrue(all(c["selected"] for c in loose))
        self.assertEqual(loose[0]["source_evidence"][0]["evidence_level"], "exploratory")
        disabled = cfg("auto-loose", queues={"attention": False})
        disabled["include_exploratory"] = False
        self.assertFalse(any(c["selected"] for c in select_candidates(rows, disabled)))

    def test_stress_scope_does_not_leak_through_exploratory_queue(self):
        rows = [finding("bacmet", evidence_level="exploratory")]
        self.assertFalse(select_candidates(rows, cfg("auto-loose"))[0]["selected"])
        config = cfg("auto-loose")
        config["include_stress"] = True
        self.assertTrue(select_candidates(rows, config)[0]["selected"])

    def test_different_tools_same_protein_one_candidate_all_sources_kept(self):
        rows = [finding(), finding("rgi", "r"), finding("vfdb", "v")]
        before = copy.deepcopy(rows)
        selected = select_candidates(rows, cfg())
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0]["selected"])
        self.assertEqual(len(selected[0]["source_evidence"]), 3)
        self.assertEqual(rows, before)

    def test_several_weak_hits_never_become_strong(self):
        rows = [finding("vfdb", str(i), identity=79.) for i in range(4)]
        result = select_candidates(rows, cfg())
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["selected"])
        self.assertEqual(result[0]["aware"]["qualifying_finding_ids"], [])

    def test_one_qualifying_source_admits_locus_without_promoting_weak_source(self):
        rows = [finding("rgi", "weak", evidence_level="exploratory",
                        attributes={"cutoff": "loose", "nudged": False}), finding()]
        result = select_candidates(rows, cfg())[0]
        self.assertTrue(result["selected"])
        self.assertEqual(result["selection_queue"], "native_screen")
        self.assertEqual(result["aware"]["qualifying_finding_ids"], ["a"])
        self.assertEqual(result["evidence_priority"]["tools"], ["amrfinder"])

    def test_validated_partial_span_reconciles_without_altering_original(self):
        sample = {"input_validation": {"protein_coordinates": {"p1": {
            "contig": "c1", "start": 1, "end": 90, "strand": "+"}}}}
        rows = [finding(), finding("rgi", "r", contig="c1", start=4, end=87, strand="+")]
        result = select_candidates(rows, cfg(), sample)
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]["start"], result[0]["end"]), (1, 90))
        self.assertEqual(result[0]["source_evidence"][1]["start"], 4)

    def test_contig_alias_and_protein_only_finding_resolve_to_one_locus(self):
        sample = {"input_validation": {"contig_aliases": {"alias": "c1"}}}
        rows = [finding(contig="alias", start=1, end=90, strand="+"), finding("rgi", "r")]
        result = select_candidates(rows, cfg(), sample)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["contig"], "c1")
        self.assertEqual(result[0]["source_evidence"][0]["contig"], "alias")

    def test_disjoint_or_opposite_strand_id_conflicts_not_merged(self):
        sample = {"input_validation": {"protein_coordinates": {"p1": {
            "contig": "c1", "start": 1, "end": 90, "strand": "+"}}}}
        for location in ({"contig": "c1", "start": 100, "end": 189, "strand": "+"},
                         {"contig": "c1", "start": 1, "end": 90, "strand": "-"}):
            result = select_candidates([finding(), finding("rgi", "r", **location)], cfg(), sample)
            self.assertEqual(len(result), 2)
            self.assertEqual(sum(c["selected"] for c in result), 1)
            self.assertIn("identity_review_required", result[1]["aware"]["evidence"][0]["reasons"])

    def test_identical_names_at_different_loci_stay_separate(self):
        rows = [finding(pid="p1", contig="c1", start=1, end=90, strand="+"),
                finding("rgi", "r", "p2", contig="c1", start=100, end=189, strand="+")]
        self.assertEqual(len(select_candidates(rows, cfg())), 2)

    def test_multiple_annotation_proteins_at_same_location_not_assumed_identical(self):
        feature = {"contig": "c1", "start": 1, "end": 90, "strand": "+"}
        sample = {"input_validation": {"protein_coordinates": {"p1": feature, "p2": feature}}}
        self.assertEqual(len(select_candidates([finding(), finding("rgi", "r", "p2")], cfg(), sample)), 2)

    def test_ambiguous_protein_only_mapping_is_deferred(self):
        rows = [finding(ident="l1", contig="c1", start=1, end=90, strand="+"),
                finding(ident="l2", contig="c1", start=100, end=189, strand="+"), finding("rgi", "r")]
        result = select_candidates(rows, cfg())
        self.assertEqual(len(result), 3)
        self.assertEqual(sum(c["selected"] for c in result), 2)
        self.assertTrue(result[-1]["aware"]["identity"][0]["conflict"])

    def test_unlocalized_ids_do_not_merge_across_tools(self):
        self.assertEqual(len(select_candidates([finding(pid=None), finding("rgi", pid=None)], cfg())), 2)

    def test_wrong_sample_rejected(self):
        with self.assertRaisesRegex(ValueError, "another sample"):
            select_candidates([finding(sample_id="other")], cfg(), {"sample_id": "wanted"})

    def test_budget_is_after_dedup_and_never_backfills_failed_criteria(self):
        rows = [finding(), finding("rgi", "r"), finding("vfdb", "v", "p2", identity=30.)]
        config = cfg()
        config["maximum_candidates"] = 1
        result = select_candidates(rows, config)
        self.assertEqual(sum(c["selected"] for c in result), 1)
        self.assertEqual(len(result[0]["source_evidence"]), 2)
        config["maximum_candidates"] = 100
        self.assertEqual(sum(c["selected"] for c in select_candidates(rows, config)), 1)

    def test_admission_and_budget_deferral_are_distinct(self):
        rows = [finding(), finding("rgi", "r", "p2"), finding("vfdb", "v", "p3", identity=1.)]
        config = cfg()
        config["maximum_candidates"] = 1
        result = select_candidates(rows, config)
        summary = audit(result, rows, config)
        self.assertEqual(summary["counts"]["admission_deferred"], 1)
        self.assertEqual(summary["counts"]["budget_deferred"], 1)
        self.assertEqual(summary["counts"]["findings_qualifying"], 2)
        self.assertEqual(len(json.loads(json.dumps(summary))["by_tool"]), 3)

    def test_nested_preset_eligibility_on_seeded_synthetic_scores(self):
        rng = random.Random(9000)
        configs = [cfg(p) for p in ("auto-strict", "auto-medium", "auto-loose")]
        policies = [resolve_policy(c) for c in configs]
        for i in range(300):
            row = finding("vfdb", str(i), identity=rng.uniform(0, 100), coverage=rng.uniform(0, 100))
            row["attributes"].update(query_coverage=rng.uniform(0, 100), evalue=10 ** rng.uniform(-20, 0))
            flags = [evaluate(row, p, c)["eligible"] for p, c in zip(policies, configs)]
            self.assertEqual(flags, sorted(flags))

    def test_stable_candidate_selection_under_input_reordering(self):
        rows = [finding(), finding("rgi", "r", "p2"), finding("vfdb", "v", "p3")]
        config = cfg()
        config["maximum_candidates"] = 1
        chosen = lambda values: [c["candidate_id"] for c in select_candidates(values, config) if c["selected"]]
        self.assertEqual(chosen(rows), chosen(list(reversed(rows))))

    def test_report_renders_recorded_policy_and_escapes_labels(self):
        rows = [finding(), finding("rgi", "r")]
        result = select_candidates(rows, cfg())
        html = render_aware([{"sample_id": "<script>", "aware_selection": audit(result, rows, cfg())}])
        self.assertIn("BaSIS AWARE", html)
        self.assertIn("auto-medium", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertEqual(render_aware([{}]), "")

    def test_mismatched_tool_category_cannot_enter_attention_queue(self):
        row = finding(category="ml_attention")
        self.assertFalse(select_candidates([row], cfg("auto-loose"))[0]["selected"])

    def test_preview_is_read_only_and_preserves_manual_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "safety"
            (root / "metadata").mkdir(parents=True)
            (root / "evidence").mkdir()
            documents = {
                "metadata/sample.json": {"sample_id": "sample"},
                "metadata/config.json": {"deep": {"aware": {"tools": {"amrfinder": {"enabled": False}}}},
                                         "modules": {"amrfinder": {"enabled": True}}},
                "evidence/amrfinder.json": {"sample_id": "sample", "module": "amrfinder",
                    "execution_status": "completed", "findings": [finding()]}}
            for name, value in documents.items():
                (root / name).write_text(json.dumps(value))
            snapshot = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*.json")}
            result = preview(tmp)
            self.assertEqual(set(result["presets"]), {"auto-strict", "auto-medium", "auto-loose"})
            self.assertTrue(all(r["counts"]["selected_candidates"] == 0 for r in result["presets"].values()))
            self.assertEqual(snapshot, {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*.json")})
            missing = documents["metadata/config.json"]
            missing["modules"]["rgi"] = {"enabled": True}
            (root / "metadata/config.json").write_text(json.dumps(missing))
            with self.assertRaisesRegex(ValueError, "Missing enabled"):
                preview(tmp)


if __name__ == "__main__":
    unittest.main()
