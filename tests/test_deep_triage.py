import unittest
from basis.deep import select_candidates


def hit(pid, identity=50, coverage=90, tool='rgi', gene='x', model='protein homolog model'):
    return {'protein_id':pid, 'finding_id':pid+tool, 'category':'amr',
            'evidence_level':'exploratory', 'tool':tool, 'gene':gene,
            'identity':identity, 'coverage':coverage, 'attributes':{'model_type':model}}


class TriageTests(unittest.TestCase):
    def test_order_and_disabled(self):
        rows=[hit('weak',30),hit('strong',90),hit('missing',100,None)]
        selected=select_candidates(rows,{'maximum_exploratory_candidates':1})
        self.assertEqual([c['protein_id'] for c in selected if c['selected']],['strong'])
        self.assertEqual([c['protein_id'] for c in selected],['strong','weak','missing'])
        self.assertEqual([c['protein_id'] for c in selected],
                         [c['protein_id'] for c in select_candidates(rows[::-1],{})])
        self.assertFalse(any(c['selected'] for c in select_candidates(rows,{'include_exploratory':False})))

    def test_group_and_scope(self):
        a=hit('p',gene='Mycobacterium tuberculosis rpoC mutations confer resistance to rifampicin',model='protein variant model')
        a['evidence_level']='curated_model'
        for taxon,expected in [('Lysinibacillus irui','outside_declared_genus_review_required'),
                               ('','unresolved'),('Mycobacterium sp.','unresolved'),
                               ('Mycobacterium tuberculosis','declared_name_matches_review_required')]:
            c=select_candidates([a],{}, {'taxon':taxon})[0]
            self.assertEqual(c['taxonomy_review']['checks'][0]['status'],expected)
            self.assertTrue(c['selected'])
        b=hit('p',tool='amrfinder',gene='different label')
        c=select_candidates([a,b],{})
        self.assertEqual(len(c),1)
        self.assertEqual(len(c[0]['source_evidence']),2)
        self.assertEqual(len(c[0]['evidence_priority']['tools']),2)

    def test_unknown_variant_and_homolog(self):
        a=hit('p',model='protein variant model')
        self.assertEqual(select_candidates([a],{})[0]['taxonomy_review']['checks'][0]['status'],'unresolved')
        self.assertEqual(select_candidates([hit('p')],{})[0]['taxonomy_review']['status'],'not_evaluated')
