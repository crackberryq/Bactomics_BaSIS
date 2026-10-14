"""Diagnostic reporting separates location concentration from group support."""
import copy
from html.parser import HTMLParser
import unittest

from basis.placement_diagnostics import diagnose_jplace
from basis.placement_scoring import analyze_jplace
from basis.report_placement import (diagnostic_summary, render_placement_diagnostics,
                                    candidate_placement_details)


def fixture():
    labels = {tip:{'group':'Cfr' if tip in 'AB' else 'RlmN',
                   'role':'amr' if tip in 'AB' else 'alternative', 'accessions':[tip]}
              for tip in 'ABCD'}
    data = {'version':3, 'tree':'((A:1{0},B:1{1}):1{2},C:1{3},D:1{4});',
            'fields':['edge_num','likelihood','like_weight_ratio','distal_length','pendant_length'],
            'placements':[{'n':['Q'], 'p':[[2,-1,.96,.5,.1],[0,-2,.02,.5,.1],[3,-2,.02,.5,.1]]}]}
    scores = analyze_jplace(data,labels,'Q')
    diagnostics = diagnose_jplace(data,labels,'Q',expected_scores=scores)
    return scores,diagnostics


class ReportPlacementDiagnostics(unittest.TestCase):
    def test_boundary_concentration_is_not_function_assignment(self):
        scores,diagnostics = fixture()
        before = copy.deepcopy((scores,diagnostics))
        page = render_placement_diagnostics(diagnostics,scores)
        for text in ['Localization and functional support are different','Boundary weight remains unassigned: 0.96',
                     '0.96','95% placement-weight split set','1 biological split',
                     'Cfr: 2; RlmN: 0; context: 0','Cfr: 0; RlmN: 2; context: 0',
                     'not a confidence interval','Original group support','Boundary weight is not reassigned']:
            self.assertIn(text,page)
        self.assertEqual(scores['group_support'],{'Cfr':.02,'RlmN':.02})
        self.assertEqual((scores,diagnostics),before)

    def test_stored_diagnostics_appear_without_changing_candidate(self):
        scores,diagnostics=fixture()
        candidate={'execution':'completed','analyses':{'family_placement':{
            'status':'completed','scores':scores,'diagnostics':diagnostics}}}
        before=copy.deepcopy(candidate)
        page=candidate_placement_details(candidate)
        self.assertIn('Boundary weight remains unassigned: 0.96',page)
        self.assertIn('0.0200',page)
        self.assertEqual(candidate,before)

    def test_missing_failed_and_malformed_diagnostics_preserve_original_scores(self):
        scores,valid=fixture()
        malformed=copy.deepcopy(valid)
        malformed['exclusive_category_mass']['assigned_group_interior']=.5
        mismatch=copy.deepcopy(valid)
        mismatch['original_group_support']={'Cfr':.04,'RlmN':0}
        for diagnostic in [None,{'status':'failed','error':'diagnostic only error'},malformed,mismatch]:
            with self.subTest(diagnostic=diagnostic):
                candidate={'execution':'completed','analyses':{'family_placement':{
                    'status':'completed','scores':scores,'diagnostics':diagnostic}}}
                page=candidate_placement_details(candidate)
                self.assertIn('0.0200',page)
                self.assertNotIn('fp-diagnostic-cards',page)
                self.assertEqual(candidate['analyses']['family_placement']['scores'],scores)

    def test_original_weight_set_does_not_renormalize_unreported_mass(self):
        scores,diagnostics=fixture()
        # Diagnostic display must reject incomplete/mismatched fields rather
        # than interpreting a plausible-looking but unaudited confidence set.
        diagnostics['split_set_95']['target_original_mass']=.9
        self.assertEqual(diagnostic_summary(diagnostics,scores)['status'],'failed')

    def test_diagnostic_error_text_is_escaped(self):
        page=render_placement_diagnostics({'status':'failed','error':'</p><script>bad()</script>'})
        self.assertNotIn('<script>',page)
        self.assertIn('&lt;script&gt;',page)

    def test_trace_boundary_does_not_override_largest_category_or_assignment(self):
        labels={tip:{'group':'Cfr' if tip in 'AB' else 'RlmN','role':'amr' if tip in 'AB' else 'alternative'} for tip in 'ABCD'}
        labels['E']={'group':None,'role':'context'}
        for assigned,boundary,context,expected in [
                (.999985901376,3.8516e-8,1.4060108e-5,'Inside a qualified group (0.999985901376)'),
                (.795282432477,3e-12,.20471756752,'Inside a qualified group (0.795282432477)'),
                (.162620666685,1.062e-9,.837379332254,'Context branch (0.837379332254)')]:
            with self.subTest(assigned=assigned):
                data={'version':3,'tree':'((A:1{0},B:1{1}):1{2},C:1{3},D:1{4},E:1{5});',
                      'fields':['edge_num','likelihood','like_weight_ratio','distal_length','pendant_length'],
                      'placements':[{'n':['Q'],'p':[[0,-1,assigned,.5,.1],[2,-2,boundary,.5,.1],[5,-3,context,.5,.1]]}]}
                scores=analyze_jplace(data,labels,'Q')
                diagnostic=diagnose_jplace(data,labels,'Q',expected_scores=scores)
                before=copy.deepcopy((scores,diagnostic))
                self.assertEqual(diagnostic['reason'],'competing_boundary_unresolved')
                page=render_placement_diagnostics(diagnostic,scores)
                self.assertIn('Largest placement-weight category: '+expected,page)
                self.assertIn('Largest unassigned location category: Context branch',page)
                self.assertIn(f'Boundary weight remains unassigned: {boundary:.12g}',page)
                self.assertIn('does not establish that the overall assignment is unresolved',page)
                self.assertIn('weight-category description, not an overall decision',page)
                self.assertNotIn('Competing boundary remains unresolved.',page)
                self.assertEqual((scores,diagnostic),before)


if __name__=='__main__':
    unittest.main()
