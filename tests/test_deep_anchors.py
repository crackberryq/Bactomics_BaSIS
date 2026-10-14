import json
import tempfile
import unittest
from pathlib import Path
from basis.deep import search_anchor_panel
from basis.references import ReferenceCatalog

class Runner:
    def __init__(self, fail=False): self.commands=[]; self.fail=fail
    def run(self, argv):
        self.commands.append(argv)
        if self.fail: raise RuntimeError('native failure')
        output=Path(argv[argv.index('-out')+1])
        ids=json.loads((output.parent/'search_reference_ids.json').read_text())
        reverse={v:k for k,v in ids.items()}
        # A is eligible, B is partial, C has no reported alignment.
        rows=[]
        for q in ['Q1','Q2']:
            for ref,end in [('A',100),('B',40)]:
                rows.append(f'{q}\t{reverse[ref]}\t80\t{end}\t100\t100\t1\t{end}\t1\t{end}\t1e-20\t100')
        output.write_text('\n'.join(rows)+'\n')

class AnchorPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.catalog=ReferenceCatalog({x:'M'*100 for x in 'ABCU'},
            {x:{'review_status':'reviewed' if x!='U' else 'unreviewed'} for x in 'ABCU'},Path('f'),Path('m'),[])
        self.candidates=[{'query':{'id':'Q1'},'profile':{'anchor_reference_ids':['A','B','C','U','MISSING']}},
                         {'query':{'id':'Q2'},'profile':{'anchor_reference_ids':['C']}}]
        self.config={'min_identity':30,'min_coverage':70,'min_reference_coverage':70,
                     'search_max_targets':1,'_retrieval_audit':[]}
    def test_native_panel_recovery_filters_and_no_cross_family_leak(self):
        runner=Runner()
        hits,artifacts=search_anchor_panel({'Q1':'M'*100,'Q2':'M'*100},self.candidates,self.catalog,self.config,self.path,runner,{})
        self.assertEqual([x['reference_id'] for x in hits['Q1']],['A'])
        self.assertFalse(hits.get('Q2'))
        checks={r['reference_id']:r for r in self.config['_anchor_review']['Q1']}
        self.assertEqual(checks['A']['status'],'eligible')
        self.assertEqual(checks['B']['status'],'rejected_by_retrieval_filters')
        self.assertIn('query_coverage_below_threshold',checks['B']['alignment_rows'][0]['filter_reasons'])
        self.assertEqual(checks['C']['status'],'no_reported_alignment_at_search_settings')
        self.assertEqual(checks['U']['status'],'not_reviewed')
        self.assertEqual(checks['MISSING']['status'],'missing_from_catalogue')
        argv=runner.commands[0]
        self.assertGreaterEqual(int(argv[argv.index('-max_target_seqs')+1]),3)
        self.assertEqual(self.config['search_max_targets'],1)
        self.assertTrue(all(Path(p).exists() for p in artifacts))
    def test_failure_propagates(self):
        with self.assertRaisesRegex(RuntimeError,'native failure'):
            search_anchor_panel({'Q1':'M'},self.candidates,self.catalog,self.config,self.path,Runner(True),{})
    def test_metadata_does_not_claim_search(self):
        self.config['reference_search_tool']='metadata'
        runner=Runner()
        hits,_=search_anchor_panel({'Q1':'M'},self.candidates,self.catalog,self.config,self.path,runner,{})
        self.assertEqual(runner.commands,[])
        self.assertEqual(self.config['_anchor_review']['Q1'][0]['status'],'not_assessed_metadata_mode')
    def test_general_audit_and_hits_preserved(self):
        old={'query_id':'Q1','reference_id':'GENERAL'}
        self.config['_retrieval_audit']=[old]
        hits,_=search_anchor_panel({'Q1':'M','Q2':'M'},self.candidates,self.catalog,self.config,self.path,Runner(),
                                  {'Q1':[{'reference_id':'GENERAL','bitscore':200,'evalue':1e-30}]})
        self.assertEqual({h['reference_id'] for h in hits['Q1']},{'A','GENERAL'})
        self.assertEqual(self.config['_retrieval_audit'][0],old)
