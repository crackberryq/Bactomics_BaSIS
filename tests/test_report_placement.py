import copy
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest

from basis.report_placement import candidate_placement_details, placement_search_terms, render_placement_overview


class Text(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.feed(value)

    def handle_data(self, data):
        self.parts.append(data)

    @property
    def value(self):
        return ' '.join(self.parts)


def candidate():
    return {"sample_id": "isolate1", "protein_id": "P1", "candidate_id": "C1", "selected": True,
            "execution": "completed", "analyses": {"family_placement": {
                "status": "completed", "reason": "reference_placement_completed", "function_probability": None,
                "scope": "family relationships in the supplied panel",
                "panel": {"panel_id": "family-v1", "panel_sha256": "a" * 64,
                          "adequacy": {"status": "eligible", "independent_sequences": 7},
                          "provenance": {"registry_version": "v1", "review": "source audit"}},
                "scores": {"group_support": {"AMR-family": 0.6, "Alternative": 0.2},
                           "group_roles": {"AMR-family": "amr", "Alternative": "alternative"},
                           "assigned_mass": 0.8, "unresolved_mass": 0.1, "missing_mass": 0.1,
                           "reported_mass": 0.9, "best_group": "AMR-family", "best_group_support": 0.6,
                           "function_probability": None, "confidence_assessment": "uncalibrated_placement",
                           "reference_group_counts": {"AMR-family": 3, "Alternative": 4},
                           "warnings": ["Placement depends on comparator representation."]}}}}


def record(c):
    return {"sample_id": "isolate1", "module": "deepsafe", "execution_status": "completed", "candidates": [c]}


class PlacementReportTests(unittest.TestCase):
    def test_tree_shortcut_requires_completed_candidate_and_local_export(self):
        c = candidate()
        key = ('isolate1', 'C1', 'P1')
        href = 'report-data/placement-tree/verified/view.html'
        html = render_placement_overview([('isolate1', c)], [record(c)], {key: href})
        self.assertIn('Open placement tree', html)
        self.assertIn('href="' + href + '"', html)
        for invalid in ('javascript:alert(1)', '/outside.html', '../outside.html'):
            html = render_placement_overview([('isolate1', c)], [record(c)], {key: invalid})
            self.assertNotIn('Open placement tree', html)
        c['execution'] = 'failed'
        html = render_placement_overview([('isolate1', c)], [record(c)], {key: href})
        self.assertNotIn('Open placement tree', html)

    def test_scores_are_raw_and_unresolved_missing_are_visible(self):
        c = candidate()
        before = copy.deepcopy(c)
        html = candidate_placement_details(c)
        text = Text(html).value
        for token in ["0.6000", "0.2000", "Unresolved placement", "Unreported mass", "not renormalized",
                      "Competing function", "not the probability", "Functional probability:", "Not estimated",
                      "family-v1", "independent sequences", "Reference group · Alternative", "source audit"]:
            self.assertIn(token, text)
        self.assertEqual(c, before)
        self.assertIn('width:60.000000%', html)
        self.assertNotIn('width:66.666', html)

    def test_zero_support_is_not_missing_and_no_mass_is_renormalized(self):
        c = candidate()
        s = c["analyses"]["family_placement"]["scores"]
        s.update(group_support={"AMR-family": 0.0, "Alternative": 0.0}, assigned_mass=0.0,
                 unresolved_mass=0.75, missing_mass=0.25, reported_mass=0.75, best_group=None, best_group_support=0.0)
        html = candidate_placement_details(c)
        self.assertEqual(html.count('width:0.000000%'), 2)
        self.assertIn('0.7500', html)
        self.assertIn('0.2500', html)
        overview = Text(render_placement_overview([("isolate1", c)], [record(c)])).value
        self.assertIn('No mass assigned to a group', overview)

    def test_hold_never_displays_retained_score(self):
        c = candidate()
        c["analyses"]["family_placement"].update(status="insufficient_evidence", reason="insufficient_distinct_reference_groups")
        html = candidate_placement_details(c)
        self.assertIn("Evidence hold", html)
        self.assertIn("neither a resistance call nor evidence of absence", html)
        self.assertNotIn("fp-mass-track", html)
        self.assertNotIn("0.6000", html)
        self.assertIn("independent sequences", Text(html).value)

    def test_historical_absence_does_not_imply_placement_was_run(self):
        c = candidate()
        del c["analyses"]["family_placement"]
        html = candidate_placement_details(c)
        self.assertIn("Not recorded", html)
        self.assertIn("earlier exploratory tree does not imply", html)
        self.assertEqual(placement_search_terms(c), "")

    def test_failed_parent_candidate_and_layer_withhold_scores(self):
        c = candidate()
        for parent in ("failed", "not_assessed"):
            html = candidate_placement_details(c, parent)
            self.assertIn("provisional", html)
            self.assertNotIn("0.6000", html)
        c["execution"] = "failed"
        self.assertNotIn("0.6000", candidate_placement_details(c))
        c["execution"] = "completed"
        c["analyses"]["family_placement"]["status"] = "failed"
        self.assertNotIn("0.6000", candidate_placement_details(c))

    def test_malformed_or_inconsistent_score_mass_is_withheld(self):
        for key, value in [("assigned_mass", 0.9), ("missing_mass", -0.1), ("reported_mass", float("nan")),
                           ("unresolved_mass", True), ("group_support", {"AMR-family": float("inf")})]:
            c = candidate()
            c["analyses"]["family_placement"]["scores"][key] = value
            html = candidate_placement_details(c)
            self.assertIn("score fields are missing or inconsistent", html)
            self.assertNotIn("fp-mass-track", html)

    def test_rendered_metadata_is_escaped_and_no_source_urls_executed(self):
        c = candidate()
        a = c["analyses"]["family_placement"]
        a["reason"] = '<img src=x onerror="alert(1)">'
        a["panel"]["provenance"]["source_url"] = 'javascript:alert(2)'
        a["scores"]["warnings"] = ['</li><script>alert(3)</script>']
        a["scores"]["group_support"] = {'<svg onload="alert(4)">': 0.8}
        html = candidate_placement_details(c)
        for tag in ["<img", "<script", "<svg", 'href="javascript:']:
            self.assertNotIn(tag, html)
        self.assertIn("&lt;img", html)
        self.assertIn("&lt;svg", html)
        self.assertIn("&lt;/li&gt;&lt;script&gt;", html)

    def test_foreign_function_probability_never_interpreted_or_displayed(self):
        c = candidate()
        c["analyses"]["family_placement"]["scores"]["function_probability"] = 0.987654
        html = candidate_placement_details(c)
        self.assertNotIn("0.987654", html)
        self.assertIn("does not interpret or display it", html)

    def test_overview_uses_parent_status_and_sample_scoped_candidate_links(self):
        c = candidate()
        parent = record(c)
        html = render_placement_overview([("isolate1", c)], [parent])
        self.assertIn('href="#candidate-', html)
        self.assertIn("AMR-family · 0.6000", Text(html).value)
        parent["execution_status"] = "failed"
        html = render_placement_overview([("isolate1", c)], [parent])
        self.assertNotIn("0.6000", html)
        self.assertIn("Execution failed", html)
        c["selected"] = False
        html = render_placement_overview([("isolate1", c)], [parent])
        self.assertNotIn('href="#candidate-', html)

    def test_search_terms_contain_panel_groups_without_sequences(self):
        c = candidate()
        c["analyses"]["family_placement"]["panel"]["members"] = [{"sequence": "MMMAAACCC"}]
        terms = placement_search_terms(c)
        self.assertIn("family-v1", terms)
        self.assertIn("amr-family", terms)
        self.assertNotIn("mmmaaaccc", terms)

    def test_cross_sample_candidate_is_excluded_from_overview(self):
        c = candidate()
        html = render_placement_overview([("other-sample", c)], [record(c)])
        self.assertNotIn('href="#candidate-', html)
        self.assertNotIn("0.6000", html)

    def test_malformed_optional_role_and_best_group_do_not_crash(self):
        c = candidate()
        scores = c["analyses"]["family_placement"]["scores"]
        scores["group_roles"]["AMR-family"] = {"unexpected": "object"}
        scores["best_group"] = []
        self.assertIn("Role not recorded", candidate_placement_details(c))
        self.assertNotIn("AMR-family · 0.6000", render_placement_overview([("isolate1", c)], [record(c)]))

    def test_real_panel_schema_exposes_scope_and_member_evidence(self):
        from basis.placement import DEFAULT_REGISTRY
        from basis.placement_panels import load_registry, prepare_panel
        c = candidate()
        c['source_evidence'] = [{'gene': 'cfr', 'evidence_level': 'curated_model'}]
        with tempfile.TemporaryDirectory() as directory:
            panel = prepare_panel(c, load_registry(DEFAULT_REGISTRY), directory, allow_download=False)
        self.assertTrue(panel['adequacy']['eligible'])
        c['analyses']['family_placement']['panel'] = panel
        html = candidate_placement_details(c)
        text = Text(html).value
        for token in ['RlmN is a competing methyltransferase family, not a universal non-resistance control',
                      'C2 chemistry is inferred', 'No phylogenetic root or outgroup is asserted',
                      'source-qualified anchor: Yes', 'source-qualified anchor: No', 'PubMed: 22547628',
                      'native-host activity unresolved; not a negative anchor',
                      '2 accession records · one unique sequence', 'No assigned functional group',
                      'research_pilot_source_audited']:
            self.assertIn(token, text)
        for member in panel['members']:
            self.assertNotIn(member['sequence'], html)

    def test_panel_definition_and_member_notes_are_escaped(self):
        c = candidate()
        p = c['analyses']['family_placement']['panel']
        p['provenance']['panel_definition'] = {'interpretation_scope': '<svg onload="bad()">',
                                              'interpretation_notes': ['<script>bad()</script>']}
        p['members'] = [{'accessions':['<img src=x onerror="bad()">'], 'group':None, 'role':'context',
                         'evidence':[{'evidence_notes':'</td><script>bad()</script>', 'pubmed_ids':['<svg>']}]}]
        html = candidate_placement_details(c)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('<svg', html)
        self.assertIn('&lt;svg', html)

    def test_native_flat_panel_audit_form_is_readable(self):
        c = candidate()
        a = c['analyses']['family_placement']
        panel = a.pop('panel')
        a.update(panel_id=panel['panel_id'], panel_sha256=panel['panel_sha256'],
                 panel_adequacy=panel['adequacy'], panel_provenance=panel['provenance'])
        text = Text(candidate_placement_details(c)).value
        self.assertIn('family-v1', text)
        self.assertIn('independent sequences', text)
        self.assertIn('source audit', text)

    def test_dashboard_integrates_overview_detail_and_css(self):
        from basis.report import write_report
        c = candidate()
        c["selection_queue"] = "native_screen"
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            paths = write_report({"version": "0.1.0", "run_id": "placement-test", "options": {"deep": True}},
                                 [{"sample_id": "isolate1"}], [], [record(c)], out)
            html = Path(paths["html"]).read_text()
            self.assertIn('class="fp-overview"', html)
            self.assertIn('class="fp-candidate"', html)
            self.assertIn(".fp-mass-track", html)
            self.assertIn("Reference-panel placement", html)
            self.assertIn("0.6000", html)

    def test_saved_review_banner_separates_new_review_from_historical_run(self):
        from basis.report import write_report
        c = candidate()
        c['selection_queue'] = 'native_screen'
        run = {'version':'0.1.0', 'run_id':'saved-review', 'finished_at':'2026-10-10T09:00:00+00:00',
               'options':{'deep':True}, 'placement_review':{
                   'started_at':'2026-10-11T10:00:00+00:00', 'finished_at':'2026-10-11T10:05:00+00:00',
                   'source_evidence':'/saved/evidence.json', 'source_sha256':'a' * 64,
                   'core_and_extend_rerun':False, 'other_deep_analyses_rerun':False,
                   'scope':'New family-placement analyses appended to historical evidence.'}}
        with tempfile.TemporaryDirectory() as directory:
            paths = write_report(run, [{'sample_id':'isolate1'}], [], [record(c)], Path(directory))
            html = Path(paths['html']).read_text()
            text = Text(html).value
            for token in ['New placement-only review of saved evidence', 'remain historical',
                          'Core screening and Extend / GPU inference were not rerun',
                          'Other DeepSafe analyses were not rerun', 'Placement review audit · JSON',
                          '2026-10-10T09:00:00+00:00', '2026-10-11T10:05:00+00:00', '/saved/evidence.json']:
                self.assertIn(token, text)
            self.assertIn('id="placement-review-notice"', html)
            run.pop('placement_review')
            paths = write_report(run, [{'sample_id':'isolate1'}], [], [record(c)], Path(directory))
            self.assertNotIn('id="placement-review-notice"', Path(paths['html']).read_text())


if __name__ == '__main__':
    unittest.main()
