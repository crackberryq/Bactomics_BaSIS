import unittest
from basis.deep_reporting import completion_summary, tree_reference_summary
from basis.report import _render_candidate
from pathlib import Path

class ReportingTests(unittest.TestCase):
    def test_deferred_separate_from_success(self):
        r={'candidates':[{'selected':True,'execution':'completed'}, {'selected':False,'execution':'deferred'}]}
        s=completion_summary(r)
        self.assertEqual(s['selected_execution'],'completed')
        self.assertEqual(s['deferred_candidates'],1)
        r['candidates'][0]['execution']='failed'
        self.assertEqual(completion_summary(r)['selected_execution'],'incomplete')
    def candidate(self):
        refs=[dict(reference_id='a',sequence_sha256='raw1',accession='A',source_description='<label>',review_status='unreviewed'),dict(reference_id='b',sequence_sha256='raw2',accession='B')]
        return {'query':{'id':'Q'},'references':refs,'analyses':{'phylogeny':{'policy':'bounded_phylogeny_v1','normalization':{'a':{'source_sha256':'raw1','alignment_sequence_sha256':'same'},'b':{'source_sha256':'raw2','alignment_sequence_sha256':'same'}},'runs':[{'name':'base','execution':'completed','query_side':['Q','R00001'],'sh_alrt':0,'ufboot':34}]}}}
    def test_duplicate_aliases_and_escaping(self):
        c=self.candidate();s=tree_reference_summary(c)
        self.assertEqual(len(s['runs'][0]['query_side_references']),2)
        h=_render_candidate(c,Path('/tmp'))
        self.assertIn('&lt;label&gt;',h);self.assertNotIn('<label>',h)
        self.assertIn('<td>0</td>',h)
    def test_corrupt_metadata_not_mapped(self):
        c=self.candidate();c['references'][0]['sequence_sha256']='wrong'
        self.assertEqual(tree_reference_summary(c)['runs'],[])
    def test_group_change(self):
        c=self.candidate();t=c['analyses']['phylogeny'];t['runs'].append({'execution':'completed','query_side':['Q','R00001','R00002']})
        self.assertFalse(tree_reference_summary(c)['split_consistent'])
    def test_historical_not_invented(self):
        self.assertEqual(tree_reference_summary({'analyses':{'phylogeny':{'reason':'reused_verified_review'}}})['runs'],[])
