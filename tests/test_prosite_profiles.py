import tempfile,unittest
from pathlib import Path
from basis.prosite_profiles import alignment_map,evaluate_sites,parse_xpsa

class ProfilesTest(unittest.TestCase):
    def test_insertions_deletions(self):
        m=alignment_map('ACggD-E','ACGGDE',1,6,5,1,-1)
        self.assertEqual(m[3]['query_position'],5)
        self.assertIsNone(m[4]['query_position'])
        self.assertEqual(m[5]['query_position'],6)
    def test_integrity(self):
        with self.assertRaises(ValueError):alignment_map('AC','AD',1,2,2,1,-1)
        with self.assertRaises(ValueError):alignment_map('AC','AC',1,2,3,1,-1)
    def test_conditions(self):
        hit={'accession':'PS1','rule_ids':['PRU1','missing'],'position_map':alignment_map('AX-C','AXC',1,3,4,1,-1)}
        features=[{'feature':'BINDING','position_expression':str(i),'profile':'PS1','conditions':['C'],'groups':[],'raw':[]} for i in range(1,5)]
        features.append(dict(features[0],position_expression='1..3'))
        r=evaluate_sites(hit,{'PRU1':{'features':features,'version':'1','source_url':'source'}})
        self.assertEqual([s['status'] for s in r[0]['sites']],['condition_not_satisfied','ambiguous_residue','deleted_in_query','condition_satisfied'])
        self.assertEqual(len(r[0]['unsupported_conditions']),1)
        self.assertFalse(r[0]['full_rule_evaluated']);self.assertEqual(r[1]['status'],'rule_unavailable')
    def test_partial_bounds(self):
        m=alignment_map('-AC-','AC',1,2,4,2,-2)
        self.assertFalse(m[1]['within_reported_profile_span']);self.assertTrue(m[2]['within_reported_profile_span'])
    def test_native_parser_fail_closed_mapping(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x';p.write_text('>Q/1-2 motif=PS1|test norm_score=10 raw_score=100 level=0 motif_start=1 motif_end=-1 motif_rev=F strand=+\nAD\n')
            h=parse_xpsa(p,{'Q':'AC'},{'PS1':{'length':2}})['Q'][0]
            self.assertEqual(h['mapping_status'],'not_assessable');self.assertFalse(h['position_map'])
if __name__=='__main__':unittest.main()
