import copy,unittest
from basis.deep_consolidation import consolidate,render_summary
from BaSIS_consolidate_deep import final_config
from basis.config import default_config
from pathlib import Path

class ConsolidationTests(unittest.TestCase):
    def test_residue_dedup_preserves_conditions(self):
        s={'query_position':116,'observed_residue':'E','status':'condition_satisfied','expected_residues':'E','feature':'BINDING'}
        c={'analyses':{'prosite_profiles':{'hits':[{'accession':'PS1','start':5,'end':120,'residue_rules':[{'rule_id':'PRU1','sites':[s,{**s,'feature':'ACT_SITE'}]}]}]}}}
        r=consolidate(c)
        self.assertEqual(len(r['residue_observations']),1)
        self.assertEqual(len(r['residue_observations'][0]['conditions']),2)
        self.assertFalse(r['function_established'])
    def test_overlap_repeats_and_rejected(self):
        h={'passes_ga':True,'domain_name':'ABC','query_start':1,'query_end':100}
        c={'analyses':{'domains':{'hits':[h,dict(h),{**h,'query_start':200,'query_end':300},{**h,'passes_ga':False,'query_end':400}]},'prosite_patterns':{'hits':[{'accession':'PS1','start':30,'end':40}]}}}
        before=copy.deepcopy(c);r=consolidate(c)
        self.assertEqual(c,before);self.assertEqual(len(r['overlap_groups']),2)
        self.assertEqual(len(r['overlap_groups'][0]['annotations']),2)
    def test_missing_and_escape(self):
        r=consolidate({'analyses':{'domains':{'status':'failed','reason':'<bad>'}}})
        self.assertEqual(r['layers'][2]['status'],'failed')
        self.assertIn('&lt;bad&gt;',render_summary(r))
        self.assertEqual(r['layers'][3]['status'],'not_assessed')
    def test_legacy_reference_names(self):
        r=consolidate({"references":["legacy_reference"]})
        self.assertEqual(r["reference_records"],1)
        self.assertEqual(r["unique_reference_sequences"],0)
    def test_final_config(self):
        core=default_config();deep=default_config();deep['deep']['prosite_snapshot']='/snapshot'
        result=final_config(core,deep,Path('/study'))
        self.assertTrue(result['deep']['prosite_profiles']);self.assertTrue(result['deep']['motifs'])
        self.assertEqual(result['outdir'],'/study/results-final')
        core['modules']['rgi']['mode']='import'
        with self.assertRaises(ValueError):final_config(core,deep,Path('/study'))
if __name__=='__main__':unittest.main()
