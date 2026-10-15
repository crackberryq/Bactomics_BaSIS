import copy
from html.parser import HTMLParser
import unittest

from basis.report_motif_views import candidate_motif_details, motif_search_terms, render_motif_overview


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


def protein(pid='P1'):
    return {'protein_id': pid, 'sample_id': 'S1', 'selected': True, 'execution': 'completed',
            'gene': 'family-alias', 'family_labels': ['family-alias', 'another-alias'],
            'analyses': {'motifs': {'status': 'completed', 'rules': [
                {'name': 'Shared catalytic motif', 'rule_id': 'family_rule_1',
                 'pattern': 'C-x(3)-C-x(2)-C', 'status': 'present',
                 'matches': [{'start': 110, 'end': 117, 'sequence': 'CNFGCKFC'}],
                 'limitations': ['Shared by multiple families; not a functional discriminator.']}]},
                'prosite_patterns': {'status': 'completed', 'hits': []},
                'prosite_profiles': {'status': 'completed', 'hits': [
                    {'accession': 'PS00001', 'name': 'FAMILY_PROFILE', 'start': 96, 'end': 331}]}}}


class MotifViewTests(unittest.TestCase):
    def test_custom_rule_pattern_sequence_and_limits_visible(self):
        c = protein()
        before = copy.deepcopy(c)
        text = Text(candidate_motif_details(c)).value
        for token in ['Shared catalytic motif', 'C-x(3)-C-x(2)-C', 'CNFGCKFC', '110–117', 'present',
                      'Shared by multiple families', 'No match detected', 'PS00001 FAMILY_PROFILE', '96–331',
                      'does not negate a custom motif or profile match']:
            self.assertIn(token, text)
        self.assertEqual(c, before)

    def test_search_covers_sequence_pattern_human_name_and_family_alias(self):
        terms = motif_search_terms(protein())
        for token in ['motif', 'pattern', 'cnfgckfc', 'shared catalytic motif', 'c-x(3)-c-x(2)-c',
                      'another-alias', 'ps00001']:
            self.assertIn(token, terms)

    def test_unavailable_residue_rules_visible_without_hiding_valid_profile(self):
        c = protein()
        hit = c['analyses']['prosite_profiles']['hits'][0]
        hit['unavailable_rule_ids'] = ['PRU99999']
        hit['residue_rules'] = [{'rule_id': 'PRU99999', 'status': 'rule_unavailable', 'sites': []}]
        before = copy.deepcopy(c)
        text = Text(candidate_motif_details(c)).value
        self.assertIn('PS00001 FAMILY_PROFILE', text)
        self.assertIn('Residue annotations not assessed for unavailable release rules: PRU99999', text)
        self.assertEqual(c, before)

    def test_no_applicable_rule_is_not_a_negative_scan(self):
        c = protein()
        c['analyses']['motifs'] = {'status': 'no_applicable_rule', 'rules': []}
        text = Text(candidate_motif_details(c)).value
        self.assertIn('No applicable custom rule', text)
        self.assertIn('does not mean that the protein lacks motifs', text)
        self.assertIn('PS00001 FAMILY_PROFILE', text)

    def test_failure_with_retained_results_not_promoted(self):
        c = protein()
        for status in ['failed', 'not_assessed']:
            text = Text(candidate_motif_details(c, parent_status=status)).value
            self.assertIn('provisional', text)
            self.assertNotIn('CNFGCKFC', text)
        c['execution'] = 'failed'
        self.assertNotIn('CNFGCKFC', candidate_motif_details(c))

    def test_failed_layer_with_retained_hits_is_not_promoted(self):
        c = protein()
        c['analyses']['prosite_profiles']['status'] = 'failed'
        html = candidate_motif_details(c)
        self.assertNotIn('PS00001', html)
        self.assertIn('failed', Text(html).value)

    def test_zero_hits_and_missing_count_are_distinct(self):
        c = protein()
        c['analyses']['prosite_patterns'] = {'status': 'completed'}
        text = Text(candidate_motif_details(c)).value
        self.assertIn('Match count not recorded', text)
        self.assertNotIn('No match detected', text)

    def test_source_text_escaped(self):
        c = protein()
        c['analyses']['motifs']['rules'][0]['name'] = '<img src=x onerror="alert(1)">'
        html = candidate_motif_details(c)
        self.assertNotIn('<img', html)
        self.assertIn('&lt;img', html)

    def test_overview_counts_parent_status_and_links(self):
        matched, no_rule = protein('P1'), protein('P2')
        no_rule['analyses']['motifs'] = {'status': 'no_applicable_rule', 'rules': []}
        pairs = [('S1', matched), ('S1', no_rule)]
        parent = {'sample_id': 'S1', 'execution_status': 'completed', 'candidates': [matched, no_rule]}
        html = render_motif_overview(pairs, [parent])
        text = Text(html).value
        self.assertIn('Custom motif rules 1 1 0 1 0 0', text)
        self.assertIn('PROSITE patterns 2 0 2 0 0 0', text)
        self.assertIn('PROSITE profiles 2 2 0 0 0 0', text)
        self.assertIn('href="#candidate-', html)
        parent['execution_status'] = 'failed'
        html = render_motif_overview(pairs, [parent])
        self.assertNotIn('href="#candidate-', html)
        self.assertIn('Custom motif rules 0 0 0 0 0 2', Text(html).value)

    def test_completed_empty_custom_registry_not_called_absent(self):
        c = protein()
        c['analyses']['motifs'] = {'status': 'completed', 'rules': []}
        text = Text(candidate_motif_details(c)).value
        self.assertIn('no per-rule observations are recorded', text)


if __name__ == '__main__':
    unittest.main()
