"""Meaningful report checks: execution state, existing evidence and escaping."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from basis.report import build_recommendations, write_report


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.run = {"version": "0.1.0", "run_id": "test", "config_hash": "abc"}
        self.samples = [{"sample_id": "A", "taxon": "Test organism"}]

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, **changes):
        result = {"sample_id": "A", "module": "amrfinder", "execution_status": "completed",
                  "interpretation": "detected", "findings": [{"finding_id": "A-f1", "gene": "example",
                  "category": "amr", "evidence_level": "curated_model", "contig": "c1", "start": 10, "end": 30}]}
        result.update(changes)
        return result

    def test_failed_empty_is_unavailable_not_no_hit(self):
        record = self.record(execution_status="failed", interpretation="not_detected", findings=[], error="malformed output")
        paths = write_report(self.run, self.samples, [record], [], self.out)
        html = Path(paths["html"]).read_text()
        self.assertIn('unavailable', html)
        self.assertNotIn('No findings were detected within', html)
        recs = json.loads(Path(paths["evidence_json"]).read_text())["recommendations"]
        self.assertEqual([r["category"] for r in recs], ["execution"])
        self.assertIn('repeat the affected screen', recs[0]["recommendation"])

    def test_no_hits_does_not_trigger_wet_lab(self):
        record = self.record(interpretation="not_detected", findings=[])
        paths = write_report(self.run, self.samples, [record], [], self.out)
        evidence = json.loads(Path(paths["evidence_json"]).read_text())
        self.assertEqual(evidence["recommendations"], [])
        self.assertIn('No findings were detected within', Path(paths["html"]).read_text())
        with Path(paths["findings_tsv"]).open() as handle:
            self.assertEqual(list(csv.DictReader(handle, delimiter='\t')), [])

    def test_known_susceptibility_requests_review_not_automatic_repeat(self):
        samples = [{"sample_id": "A", "phenotypes": {"antibiotic susceptibility": "susceptible"}}]
        paths = write_report(self.run, samples, [self.record()], [], self.out)
        evidence = json.loads(Path(paths["evidence_json"]).read_text())
        rec = evidence["recommendations"][0]
        self.assertEqual(rec["provided_phenotype_status"], "available_unreviewed")
        self.assertIn('only if', rec["recommendation"])
        self.assertEqual(rec["assessment_type"], "phenotype_evidence_review")
        self.assertEqual(evidence["provided_phenotypes"]["A"][0]["evidence_status"], "available_unreviewed")

    def test_malicious_text_and_links_are_inert_and_original_json_preserved(self):
        attack = '<script>alert("x")</script>'
        record = self.record(notes=[attack], artifacts=['javascript:alert(1)', '../secret.txt', '/etc/passwd'])
        record["findings"][0]["gene"] = '=HYPERLINK("https://evil.example")'
        record["findings"][0]["attributes"] = {"text": '</script><img src=x onerror=alert(1)>'}
        (self.out / 'results').mkdir()
        native = self.out / 'results' / 'native file.tsv'
        native.write_text('native')
        record["artifacts"].append(str(native))
        paths = write_report(self.run, self.samples, [record], [], self.out)
        rendered = Path(paths["html"]).read_text()
        self.assertNotIn(attack, rendered)
        self.assertNotIn('<img src=x', rendered)
        self.assertNotIn('href="javascript:', rendered)
        self.assertNotIn('href="../secret.txt', rendered)
        self.assertNotIn('href="/etc/passwd', rendered)
        self.assertIn('href="results/native%20file.tsv"', rendered)
        with Path(paths["findings_tsv"]).open() as handle:
            row = next(csv.DictReader(handle, delimiter='\t'))
        self.assertTrue(row['gene'].startswith("'="))
        evidence = json.loads(Path(paths["evidence_json"]).read_text())
        self.assertEqual(evidence['records'][0]['notes'], [attack])
        self.assertEqual(record['findings'][0]['gene'], '=HYPERLINK("https://evil.example")')

    def test_failed_retained_findings_do_not_trigger_phenotype_assessment(self):
        recs = build_recommendations(self.samples, [self.record(execution_status="failed")], [])
        self.assertEqual([r['category'] for r in recs], ['execution'])

    def test_planned_assay_does_not_suppress_relevant_assessment(self):
        samples = [{"sample_id": "A", "phenotypes": [{"assay": "antibiotic susceptibility", "status": "planned"}]}]
        rec = build_recommendations(samples, [self.record()], [])[0]
        self.assertEqual(rec['provided_phenotype_status'], 'not_provided_for_this_assessment')
        self.assertEqual(rec['assessment_type'], 'targeted_assessment_review')

    def test_multiple_execution_failures_have_distinct_recommendation_ids(self):
        records = [self.record(module='amrfinder', execution_status='failed'),
                   self.record(module='rgi', execution_status='failed')]
        recs = build_recommendations(self.samples, records, [])
        self.assertEqual(len({r['recommendation_id'] for r in recs}), 2)

    def test_mobile_context_requires_actual_overlapping_risk_locus(self):
        amr = self.record()
        mobile = self.record(module="mobsuite", findings=[{"finding_id": "mob1", "category": "plasmid",
                            "contig": "other", "start": 1, "end": 100}])
        recs = build_recommendations(self.samples, [amr, mobile], [])
        self.assertNotIn('mobile_context', [r['category'] for r in recs])
        mobile['findings'][0]['contig'] = 'c1'
        recs = build_recommendations(self.samples, [amr, mobile], [])
        context = next(r for r in recs if r['category'] == 'mobile_context')
        self.assertEqual(context['evidence_ids'], ['A-f1', 'mob1'])

    def vfdb_finding(self, finding_id, *, passing=True, protein_id=None, **changes):
        finding = {"finding_id": finding_id, "category": "virulence",
                   "protein_id": protein_id or finding_id, "gene": "VFG-reference",
                   "contig": "passing" if passing else "weak", "start": 10, "end": 30,
                   "evidence_level": "homology" if passing else "exploratory",
                   "interpretation": "homology_requires_functional_review" if passing else "below_reporting_threshold",
                   "attributes": {"passes_threshold": passing, "function_confirmed": False}}
        finding.update(changes)
        return finding

    def test_only_passing_vfdb_targets_trigger_phenotype_or_mobile_context_review(self):
        passing = [self.vfdb_finding("eutM2-match"), self.vfdb_finding("clpP-match", start=200, end=230)]
        weak = [self.vfdb_finding("weak-" + str(i), passing=False) for i in range(64)]
        # Either explicit weak-evidence flag wins even if other imported fields
        # are inconsistent or omit a reporting-threshold flag.
        weak[0]["evidence_level"] = "homology"
        weak[0]["interpretation"] = "homology_requires_functional_review"
        weak[1]["evidence_level"] = "homology"
        weak[1]["attributes"].pop("passes_threshold")
        weak[2]["attributes"]["passes_threshold"] = True
        weak[2]["interpretation"] = "homology_requires_functional_review"
        vfdb = self.record(module="vfdb", findings=passing + weak)
        mobile = self.record(module="genomad", findings=[{
            "finding_id": "mobile", "category": "plasmid", "evidence_level": "prediction",
            "contig": "weak", "start": 1, "end": 100}])
        recs = build_recommendations(self.samples, [vfdb, mobile], [])
        review = next(r for r in recs if r["category"] == "virulence")
        self.assertEqual(review["evidence_ids"], ["clpP-match", "eutM2-match"])
        self.assertIn("2 threshold-passing alignment record(s), 2 distinct", review["trigger"])
        self.assertNotIn("mobile_context", [r["category"] for r in recs])
        computational = next(r for r in recs if r["category"] == "exploratory_evidence")
        self.assertEqual(computational["assessment_type"], "computational_review")
        self.assertEqual(set(computational["evidence_ids"]), {f["finding_id"] for f in weak})
        mobile["findings"][0]["contig"] = "passing"
        context = next(r for r in build_recommendations(self.samples, [vfdb, mobile], [])
                       if r["category"] == "mobile_context")
        self.assertEqual(context["evidence_ids"], ["eutM2-match", "mobile"])

    def test_vfdb_alignment_records_are_not_counted_as_distinct_proteins(self):
        findings = [self.vfdb_finding("ref-1", protein_id="protein-1"),
                    self.vfdb_finding("ref-2", protein_id="protein-1"),
                    self.vfdb_finding("ref-3", protein_id="protein-2")]
        review = build_recommendations(self.samples, [self.record(module="vfdb", findings=findings)], [])[0]
        self.assertIn("3 threshold-passing alignment record(s), 2 distinct", review["trigger"])
        self.assertEqual(review["evidence_ids"], ["ref-1", "ref-2", "ref-3"])

    def test_all_weak_vfdb_evidence_is_preserved_and_not_reported_as_negative(self):
        record = self.record(module="vfdb", interpretation="inconclusive", findings=[
            self.vfdb_finding("weak-1", passing=False), self.vfdb_finding("weak-2", passing=False)])
        paths = write_report(self.run, self.samples, [record], [], self.out)
        evidence = json.loads(Path(paths["evidence_json"]).read_text())
        self.assertEqual(evidence["records"], [record])
        self.assertEqual([r["assessment_type"] for r in evidence["recommendations"]], ["computational_review"])
        self.assertEqual(evidence["recommendations"][0]["evidence_ids"], ["weak-1", "weak-2"])
        self.assertIn("does not establish a hazard", evidence["recommendations"][0]["recommendation"])
        self.assertIn("does not establish absence", evidence["recommendations"][0]["recommendation"])
        rendered = Path(paths["html"]).read_text()
        self.assertIn("below_reporting_threshold", rendered)
        self.assertIn("inconclusive", rendered)
        self.assertNotIn("No findings were detected within", rendered)
        with Path(paths["findings_tsv"]).open() as handle:
            self.assertEqual(len(list(csv.DictReader(handle, delimiter="\t"))), 2)

    def test_rgi_loose_evidence_does_not_trigger_phenotype_or_mobile_concern(self):
        strict = self.record()["findings"][0]
        loose = dict(strict, finding_id="loose", evidence_level="exploratory", contig="other",
                     interpretation="exploratory_homologue")
        record = self.record(module="rgi", findings=[strict, loose])
        mobile = self.record(module="mobsuite", findings=[{"finding_id": "mob", "category": "plasmid",
                            "contig": "other", "start": 1, "end": 100}])
        recs = build_recommendations(self.samples, [record, mobile], [])
        self.assertEqual(next(r for r in recs if r["category"] == "amr")["evidence_ids"], ["A-f1"])
        self.assertNotIn("mobile_context", [r["category"] for r in recs])
        self.assertEqual(next(r for r in recs if r["category"] == "exploratory_evidence")["evidence_ids"], ["loose"])

    def test_exploratory_attention_keeps_separate_model_review(self):
        record = self.record(module="pathogenfinder2", findings=[{
            "finding_id": "attention-1", "category": "ml_attention", "evidence_level": "exploratory"}])
        recs = build_recommendations(self.samples, [record], [])
        self.assertEqual([r["category"] for r in recs], ["ml_attention"])
        self.assertIn("model applicability", recs[0]["recommendation"])

    def test_optional_not_assessed_and_deep_candidates_remain_visible(self):
        skipped = self.record(module='pathogenfinder2', execution_status='not_assessed', findings=[], interpretation='not_applicable')
        deep = self.record(module='deep', interpretation='inconclusive', findings=[], candidates=[{
            'candidate_id': 'candidate-1', 'finding_id': 'A-f1', 'family': 'example family',
            'interpretation': 'inconclusive', 'references': ['ref-1'], 'evidence_paths': []}])
        paths = write_report(self.run, self.samples, [skipped], [deep], self.out)
        rendered = Path(paths['html']).read_text()
        self.assertIn('not assessed', rendered)
        self.assertIn('candidate-1', rendered)
        self.assertIn('example family', rendered)
        self.assertIn('Deep family assessment', rendered)

    def test_declared_species_and_hybas_target_remain_separate_and_escaped(self):
        context = {"status": "imported", "taxonomy_status": "resolved", "selected_target": {
            "name": "Lysinibacillus", "rank": "G", "taxid": "400634",
            "source": "inferred_from_kraken"}, "binding": {"status": "sequence_verified"},
            "warnings": ['<script>untrusted upstream text</script>'],
            "sources": [{"path": "/project/metadata/kraken_decision.tsv", "sha256": "a" * 64}]}
        sample = {"sample_id": "A", "taxon": "Lysinibacillus irui", "taxon_rank": "species",
                  "taxon_source": "user_confirmed", "taxon_status": "declared", "hybas_context": context}
        paths = write_report(self.run, [sample], [], [], self.out)
        rendered = Path(paths['html']).read_text()
        self.assertIn('Lysinibacillus irui · user-confirmed', rendered)
        self.assertIn('HybAs operational target', rendered)
        self.assertIn('Lysinibacillus</', rendered)
        self.assertIn('sequence_verified', rendered)
        self.assertIn('HybAs taxonomy status', rendered)
        self.assertNotIn('<script>untrusted', rendered)
        self.assertIn('&lt;script&gt;untrusted', rendered)
        evidence = json.loads(Path(paths['evidence_json']).read_text())
        self.assertEqual(evidence['samples'][0], sample)

    def test_auto_taxon_is_qualified_in_collapsed_sample_summary(self):
        sample = {"sample_id": "A", "taxon": "Lysinibacillus", "taxon_rank": "genus",
                  "taxon_source": "hybas_kraken_operational_target",
                  "taxon_status": "read_based_operational_target"}
        paths = write_report(self.run, [sample], [], [], self.out)
        rendered = Path(paths['html']).read_text()
        self.assertIn('Lysinibacillus · HybAs read-based operational target</summary>', rendered)

    def test_legacy_target_and_read_species_are_visible_as_different_evidence(self):
        sample = {"sample_id": "A", "taxon": "Lysinibacillus irui", "taxon_source": "user_confirmed",
                  "hybas_context": {"status": "imported", "taxonomy_status": "resolved",
                      "selected_target": {"name": "Lysinibacillus", "rank": "G", "taxid": "400634",
                          "source": "legacy_run_summary_target", "selection_method": "not_recorded"},
                      "read_taxonomy": {"illumina": {"status": "available", "top_species": [
                          {"name": "Synthetic <species>", "taxid": "999999", "percentage": 71.2}]},
                          "nanopore": {"status": "empty", "top_species": []}}}}
        paths = write_report(self.run, [sample], [], [], self.out)
        rendered = Path(paths['html']).read_text()
        self.assertIn('legacy_run_summary_target', rendered)
        self.assertIn('Kraken read classifications', rendered)
        self.assertIn('Synthetic &lt;species&gt; · 71.2% (Kraken report)', rendered)
        self.assertNotIn('Synthetic <species>', rendered)
        self.assertIn('nanopore</strong> · empty', rendered)
        self.assertEqual(json.loads(Path(paths['evidence_json']).read_text())['samples'][0], sample)


if __name__ == '__main__':
    unittest.main()
