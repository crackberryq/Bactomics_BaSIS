"""Report-only placement graphics preserve geometry and source provenance."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.placement_scoring import _Newick, analyze_jplace
from basis.report_placement_tree import (_grafted, _splits, render_placement_tree,
                                         exported_tree_href, _validated,
                                         verified_placement_diagnostics)


def sha(value):
    return hashlib.sha256(value).hexdigest()


class PlacementTreeViews(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.native = self.root / 'native'
        self.native.mkdir()
        self.out = self.root / 'report'
        self.seqs = {'A':'ACDE', 'B':'ACDF', 'C':'ACDG', 'D':'ACDH'}
        self.labels = {tip: {'role':'amr' if tip in 'AB' else 'alternative',
            'group':'AMR_group' if tip in 'AB' else 'Alternative_group',
            'accessions':['REF_'+tip], 'sequence_sha256':sha(seq.encode())}
            for tip, seq in self.seqs.items()}
        self.jp = {'version':3, 'tree':'((A:1{0},B:1{1})95/99:1{2},C:1{3},D:1{4});',
            'fields':['edge_num','likelihood','like_weight_ratio','distal_length','pendant_length'],
            'placements':[{'n':['Q_test'],'p':[[0,-10,.8,.25,.1],[3,-11,.15,.4,.2]]}]}
        sources = {
            'reference.treefile':'((A:1,B:1)95/99:1,C:1,D:1);',
            'labels.json':json.dumps(self.labels),
            'epa_result.jplace':json.dumps(self.jp),
            'reference_alignment.faa':''.join('>'+k+'\n'+v+'\n' for k,v in self.seqs.items()),
            'query_alignment.faa':'>Q_test\nACDE\n', 'query.faa':'>Q_test\nACDE\n'}
        for name, content in sources.items():
            (self.native/name).write_text(content)
        self.layer = {'status':'completed', 'artifacts':[str(self.native/n) for n in sources],
            'scores':analyze_jplace(self.jp,self.labels,'Q_test'),
            'normalization':{'query':{'source_sha256':sha(b'ACDE'),
                'alignment_sequence_sha256':sha(b'ACDE')}},
            'query_alignment':{'dropped_insertion_positions_1based':[]}}
        self.candidate = {'protein_id':'P_test','execution':'completed',
            'query':{'id':'Q_test','sequence_sha256':sha(b'ACDE')},
            'analyses':{'family_placement':self.layer}}
        self.record = {'execution_status':'completed', 'placement_review':{
            'artifact_checksums':{str(self.native/n):sha((self.native/n).read_bytes()) for n in sources}}}

    def render(self):
        return render_placement_tree(self.candidate,self.record,self.out)

    def update_source(self,name,value):
        path=self.native/name
        path.write_text(value)
        self.record['placement_review']['artifact_checksums'][str(path)]=sha(path.read_bytes())

    def test_portable_view_native_downloads_and_no_evidence_mutation(self):
        before=copy.deepcopy((self.candidate,self.record))
        rendered=self.render()
        self.assertIn('Open placement tree',rendered)
        href=exported_tree_href(self.candidate,self.record,self.out)
        self.assertTrue(href)
        page=(self.out/href).read_text()
        for text in ['REF_A','REF_D','P_test · QUERY','95/99','arbitrary',
                     'not a probability of resistance','All reported placements',
                     'data-show-placement="3"','Reference alignment · FASTA']:
            self.assertIn(text,page)
        self.assertNotIn('<script src=',page)
        self.assertEqual(before,(self.candidate,self.record))
        self.assertEqual(next((self.out/'report-data').glob('placement-native-*.jplace')).read_bytes(),
                         (self.native/'epa_result.jplace').read_bytes())

    def test_exact_distal_geometry_and_reference_root_unchanged(self):
        root=_Newick(self.jp['tree']).parse()
        before=copy.deepcopy(root)
        result=_grafted(root,{'edge_num':0,'distal_length':.25,'pendant_length':.1},'Q_test')
        point=result.children[0].children[0]
        self.assertAlmostEqual(point.length,.75)
        self.assertAlmostEqual(point.children[0].length,.25)
        self.assertAlmostEqual(point.children[1].length,.1)
        self.assertEqual(point.children[1].name,'Q_test')
        self.assertEqual(root,before)

    def test_native_tampering_withholds_and_invalidates_old_link(self):
        self.render()
        (self.native/'epa_result.jplace').write_text('{}')
        self.assertIn('checksum mismatch',self.render())
        self.assertIsNone(exported_tree_href(self.candidate,self.record,self.out))

    def test_html_tampering_or_missing_seal_withholds_link(self):
        self.render()
        href=exported_tree_href(self.candidate,self.record,self.out)
        path=self.out/href
        path.write_text('changed')
        self.assertIsNone(exported_tree_href(self.candidate,self.record,self.out))
        self.render()
        path.with_suffix('.sha256').unlink()
        self.assertIsNone(exported_tree_href(self.candidate,self.record,self.out))

    def test_conflicting_hashes_fail_closed(self):
        self.record['deep_cache']={'checksums':{str(self.native/'epa_result.jplace'):'0'*64}}
        self.assertIn('conflicting recorded checksums',self.render())

    def test_cache_hashes_accept_safe_paths_reject_traversal(self):
        self.layer['reference_cache']={'directory':str(self.native),
            'verified_artifact_hashes':{'labels.json':sha((self.native/'labels.json').read_bytes())}}
        del self.record['placement_review']['artifact_checksums'][str(self.native/'labels.json')]
        self.assertIn('Open placement tree',self.render())
        self.layer['reference_cache']['verified_artifact_hashes']['../secret']='0'*64
        self.assertIn('unsafe reference cache artifact path',self.render())

    def test_missing_unverified_or_ambiguous_artifacts_withheld(self):
        hashes=self.record['placement_review']['artifact_checksums']
        digest=hashes.pop(str(self.native/'epa_result.jplace'))
        self.assertIn('recorded checksum unavailable',self.render())
        hashes[str(self.native/'epa_result.jplace')]=digest
        self.layer['artifacts'].append(str(self.native/'elsewhere/epa_result.jplace'))
        self.assertIn('expected exactly one recorded artifact',self.render())

    def test_query_mismatch_and_saved_score_mismatch_withheld(self):
        self.candidate['query']['sequence_sha256']=sha(b'OTHER')
        self.assertIn('query sequence differs',self.render())
        self.candidate['query']['sequence_sha256']=sha(b'ACDE')
        self.layer['scores']['assigned_mass']=.99
        self.assertIn('saved placement scores differ',self.render())

    def test_reference_labels_sequence_hash_and_tree_agreement(self):
        self.update_source('reference_alignment.faa','>A\nZZZZ\n>B\nACDF\n>C\nACDG\n>D\nACDH\n')
        self.assertIn('reference sequence differs',self.render())
        self.update_source('reference_alignment.faa',''.join('>'+k+'\n'+v+'\n' for k,v in self.seqs.items()))
        self.update_source('reference.treefile','((A:1,C:1):1,B:1,D:1);')
        self.assertIn('jplace tree differs',self.render())

    def test_rerooted_native_tree_and_degree_two_root_are_equivalent(self):
        self.update_source('reference.treefile','(A:1,B:1,(C:1,D:1):1);')
        self.assertIn('Open placement tree',self.render())
        self.update_source('reference.treefile','((A:1,B:1):.4,(C:1,D:1):.6);')
        self.assertIn('Open placement tree',self.render())

    def test_failed_parent_and_candidate_do_not_export(self):
        self.record['execution_status']='failed'
        self.assertIn('execution is not completed',self.render())
        self.assertIsNone(exported_tree_href(self.candidate,self.record,self.out))
        self.record['execution_status']='completed'
        self.candidate['execution']='failed'
        self.assertIn('execution is not completed',self.render())

    def test_html_escaping_and_no_executable_reference_labels(self):
        self.labels['A']['accessions']=['</text><script>alert(1)</script>']
        self.update_source('labels.json',json.dumps(self.labels))
        self.render()
        page=(self.out/exported_tree_href(self.candidate,self.record,self.out)).read_text()
        self.assertNotIn('<script>alert(1)',page)
        self.assertIn('&lt;script&gt;alert(1)',page)

    def test_artificial_root_placement_never_draws_false_attachment(self):
        root=_Newick(self.jp['tree'][:-1]+':0{5};').parse()
        self.assertIsNone(_grafted(root,{'edge_num':5,'distal_length':0,'pendant_length':.1},'Q_test'))

    def test_historical_boundary_diagnostics_are_derived_without_mutation(self):
        self.jp['placements'][0]['p']=[[2,-1,.96,.5,.1],[0,-2,.02,.5,.1],[3,-2,.02,.5,.1]]
        self.update_source('epa_result.jplace',json.dumps(self.jp))
        self.layer['scores']=analyze_jplace(self.jp,self.labels,'Q_test')
        before=copy.deepcopy((self.candidate,self.record))
        launch=self.render()
        self.assertIn('Boundary weight remains unassigned: 0.96',launch)
        data=_validated(self.candidate,self.record)
        self.assertAlmostEqual(data['diagnostics']['exclusive_category_mass']['competing_group_boundary'],.96)
        page=(self.out/exported_tree_href(self.candidate,self.record,self.out)).read_text()
        for text in ['Localization and functional support','Reason and cut sides','Side A:',
                     'Side B:','context: 0','Localization diagnostics · JSON','95% placement-weight split set']:
            self.assertIn(text,page)
        self.assertEqual((self.candidate,self.record),before)

    def test_diagnostic_failure_does_not_hide_valid_placement_tree(self):
        before=copy.deepcopy(self.candidate)
        with patch('basis.report_placement_tree.diagnose_jplace',side_effect=ValueError('diagnostic fault')):
            launch=self.render()
            self.assertIn('Open placement tree',launch)
            self.assertIn('Localization diagnostics unavailable',launch)
            href=exported_tree_href(self.candidate,self.record,self.out)
            self.assertTrue(href)
            self.assertIn('diagnostic fault',(self.out/href).read_text())
            self.assertEqual(_validated(self.candidate,self.record)['scores'],self.layer['scores'])
        self.assertEqual(self.candidate,before)

    def test_missing_artifacts_give_explicit_unassessed_diagnostics(self):
        (self.native/'epa_result.jplace').unlink()
        diagnostics=verified_placement_diagnostics(self.candidate,self.record)
        self.assertEqual(diagnostics['status'],'not_assessed')
        self.assertFalse(diagnostics['classification_policy_changed'])

    def test_all_saved_scoring_fields_are_verified_before_diagnostics(self):
        for field,value in [('policy','changed_policy'),('warnings',['invented warning'])]:
            with self.subTest(field=field):
                original=copy.deepcopy(self.layer['scores'])
                self.layer['scores'][field]=value
                self.assertIn('saved placement scores differ',self.render())
                self.assertEqual(verified_placement_diagnostics(self.candidate,self.record)['status'],'not_assessed')
                self.layer['scores']=original


if __name__ == '__main__':
    unittest.main()
