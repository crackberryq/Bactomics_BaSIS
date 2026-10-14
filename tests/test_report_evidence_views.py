"""Scientific presentation gates for the linked evidence and attention views."""
import copy
from html.parser import HTMLParser
import unittest

from basis.report_evidence_views import render_attention_explorer, render_evidence_matrix


class Text(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.feed(html)

    def handle_data(self, data):
        self.parts.append(data)

    @property
    def value(self):
        return " ".join(self.parts)


def candidate(protein="P1", queue="native_screen"):
    return {"protein_id": protein, "selected": True, "execution": "completed",
            "selection_queue": queue, "source_evidence": [],
            "assessment": {"interpretation": "unresolved"},
            "analyses": {"domains": {"status": "completed", "hits": []},
                         "motifs": {"status": "no_applicable_rule"},
                         "prosite_patterns": {"status": "completed", "hits": []},
                         "prosite_profiles": {"status": "not_assessed"},
                         "references": {"status": "completed", "selected_count": 0}}}


def source(module, status="completed"):
    return {"sample_id": "S1", "module": module, "execution_status": status, "findings": []}


def attention(protein="P1", maximum=0, mean=0):
    return {"protein_id": protein, "category": "ml_attention", "finding_id": "f1",
            "attributes": {"rank": 1, "attention_max": maximum, "attention_mean": mean,
                           "model_attention_sums": [0, 0, 0, 0]}}


class EvidenceViewTests(unittest.TestCase):
    def test_matrix_distinguishes_negative_missing_inapplicable_and_hold(self):
        c = candidate()
        c["assessment"]["missing_comparison_groups"] = ["reviewed-negative-comparator"]
        text = Text(render_evidence_matrix([("S1", c)], [source("rgi")])).value
        for label in ["No match", "No linked hit", "No applicable rule", "Not assessed", "Hold · 1 gaps", "Not evaluated"]:
            self.assertIn(label, text)
        self.assertNotIn("confidence score: ", text)

    def test_failed_source_hides_retained_provisional_gene(self):
        c = candidate()
        c["source_evidence"] = [{"tool": "rgi", "gene": "provisional_gene_not_for_display",
                                "evidence_level": "curated_model", "attributes": {"cutoff": "strict"}}]
        html = render_evidence_matrix([("S1", c)], [source("rgi", "failed")])
        self.assertNotIn("provisional_gene_not_for_display", html)
        self.assertIn("Failed", Text(html).value)

    def test_loose_label_not_promoted_and_methods_distinct(self):
        c = candidate()
        c["source_evidence"] = [
            {"tool": "amrfinder", "gene": "cfr", "attributes": {"native_method": "HMM"}},
            {"tool": "rgi", "gene": "clbA", "attributes": {"cutoff": "strict"}},
            {"tool": "rgi", "gene": "NEVER_PROMOTE_LOOSE", "attributes": {"cutoff": "loose"}}]
        html = render_evidence_matrix([("S1", c)], [source("amrfinder"), source("rgi")])
        self.assertIn("HMM: cfr", Text(html).value)
        self.assertIn("Strict: clbA", Text(html).value)
        self.assertNotIn("NEVER_PROMOTE_LOOSE", html)
        c["source_evidence"] = c["source_evidence"][-1:]
        self.assertIn("Exploratory only", Text(render_evidence_matrix([("S1", c)], [source("rgi")])).value)

    def test_taxonomic_hold_attaches_only_exact_held_finding(self):
        c = candidate()
        c["source_evidence"] = [
            {"tool": "rgi", "gene": "rpoC-model", "finding_id": "held", "attributes": {"cutoff": "strict"}},
            {"tool": "amrfinder", "gene": "other-call", "finding_id": "other", "attributes": {"native_method": "HMM"}}]
        c["taxonomy_review"] = {"status": "review_required", "checks": [
            {"finding_id": "held", "status": "outside_declared_genus_review_required"}]}
        text = Text(render_evidence_matrix([("S1", c)], [source("rgi"), source("amrfinder")])).value
        self.assertIn("Strict: rpoC-model · taxon hold", text)
        self.assertNotIn("HMM: other-call · taxon hold", text)

    def test_failed_candidate_does_not_promote_retained_layers(self):
        c = candidate()
        c["execution"] = "failed"
        c["analyses"]["prosite_profiles"] = {"status": "completed", "hits": [{"name": "old"} for _ in range(9)]}
        text = Text(render_evidence_matrix([("S1", c)], [])).value
        self.assertNotIn("9 matches", text)

    def test_attention_zero_is_not_missing_and_failed_record_not_shown(self):
        pf = source("pathogenfinder2")
        pf["findings"] = [attention()]
        html = render_attention_explorer([pf], [("S1", candidate())])
        self.assertIn('<td class="eve-numeric">0</td>', html)
        missing = copy.deepcopy(pf)
        missing["findings"][0]["attributes"]["attention_max"] = None
        self.assertIn('<td class="eve-numeric">Not recorded</td>', render_attention_explorer([missing], []))
        pf["execution_status"] = "failed"
        pf["findings"][0]["protein_id"] = "PROVISIONAL_SHOULD_NOT_APPEAR"
        html = render_attention_explorer([pf], [])
        self.assertNotIn("PROVISIONAL_SHOULD_NOT_APPEAR", html)
        self.assertIn("provisional findings are not displayed", Text(html).value)

    def test_identifiers_escaped_and_links_have_event_targets(self):
        protein = 'P1<img src=x onerror="alert(1)">'
        html = render_evidence_matrix([("S1", candidate(protein))], [])
        self.assertNotIn('<img', html)
        self.assertIn('&lt;img', html)
        self.assertIn('data-eve-candidate=', html)
        self.assertIn('data-eve-protein=', html)

    def test_attention_all_recorded_nominations_retained_without_weak_labels(self):
        pf = source("pathogenfinder2")
        pf["findings"] = [attention("P1"), attention("P2")]
        pf["metrics"] = {"input_protein_count": 4511, "attention_protein_count": 4511}
        c = candidate("P1", "ml_attention")
        c["gene"] = "LOOSE_IMPLIES_RESISTANCE_WRONG"
        html = render_attention_explorer([pf], [("S1", c)])
        text = Text(html).value
        self.assertIn("2 recorded nominations", text)
        self.assertIn("4511 / 4511", text)
        self.assertIn("Not selected for DeepSafe", text)
        self.assertNotIn("LOOSE_IMPLIES_RESISTANCE_WRONG", html)
        self.assertIn("not the full proteome score distribution", text)

    def test_custom_motif_and_ga_count_are_recorded_not_guessed(self):
        c = candidate()
        c["analyses"]["motifs"] = {"status": "completed", "rules": [{"status": "present"}]}
        c["analyses"]["domains"] = {"status": "completed", "hits": [{"passes_ga": True}]}
        text = Text(render_evidence_matrix([("S1", c)], [])).value
        self.assertIn("1 present", text)
        self.assertIn("1 GA hits", text)
        c["analyses"]["domains"]["hits"] = [{}]
        text = Text(render_evidence_matrix([("S1", c)], [])).value
        self.assertNotIn("1 GA hits", text)
        self.assertIn("1 hits", text)

    def test_long_source_call_is_compact_but_traceable_in_tooltip(self):
        c = candidate()
        long_name = "Mycobacterium tuberculosis rpoC mutations confer resistance to rifampicin"
        c["source_evidence"] = [{"tool": "rgi", "gene": long_name, "finding_id": "held",
                                 "attributes": {"cutoff": "strict"}}]
        c["taxonomy_review"] = {"status": "review_required", "checks": [
            {"finding_id": "held", "status": "outside_declared_genus_review_required"}]}
        html = render_evidence_matrix([("S1", c)], [source("rgi")])
        self.assertIn('title="Strict: ' + long_name + ' · taxon hold"', html)
        self.assertNotIn(long_name, Text(html).value)
        self.assertIn("Strict · taxon hold", Text(html).value)
        self.assertIn('<col class="eve-protein-column">', html)


if __name__ == "__main__":
    unittest.main()
