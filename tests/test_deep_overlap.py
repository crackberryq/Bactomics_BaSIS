import unittest
from basis.deep import summarize_domain_architecture

def h(name,a,b,e=1e-10):return dict(domain_name=name,query_start=a,query_end=b,independent_evalue=e,domain_score=40)
class OverlapTests(unittest.TestCase):
 def test_alternative_models_and_repeats(self):
  hits=[h('GNAT_A',25,131),h('GNAT_B',41,132,1e-8),h('ABC',200,300),h('ABC',350,450)]
  r=summarize_domain_architecture(hits)
  self.assertEqual(r['overlap_region_count'],3);self.assertEqual(r['ambiguous_region_count'],1)
  self.assertEqual(r['regions'][0]['representative_label'],'GNAT_A')
  self.assertEqual(sum(len(g['annotations']) for g in r['regions']),4)
  self.assertEqual(r,summarize_domain_architecture(hits[::-1]))
 def test_chain_nested_and_boundary(self):
  r=summarize_domain_architecture([h('a',1,100),h('b',90,200),h('c',190,300),h('d',301,320)])
  self.assertEqual(r['overlap_region_count'],2)
  self.assertEqual(r['regions'][0]['end'],300)
  self.assertEqual(summarize_domain_architecture([])['overlap_region_count'],0)

class AnchorCoverageTests(unittest.TestCase):
 def test_reviewed_anchor_cannot_bypass_coverage(self):
  from basis.references import ReferenceCatalog,select_references
  from pathlib import Path
  from basis.references import sequence_hash
  seq='MAAA'
  meta={'R':dict(reference_id='R',accession='X.1',family='x',comparison_group='x',role='reference',review_status='reviewed',source='fixture',sequence_sha256=sequence_hash(seq))}
  catalog=ReferenceCatalog({'R':seq},meta,Path('x'),Path('y'),[])
  profile=dict(anchor_reference_ids=['R'],required_comparison_groups=['x'],validation_status='reviewed',require_anchor_similarity=True)
  refs,notes=select_references(catalog,[],profile,{},10)
  self.assertEqual(refs,[]);self.assertIn('anchor_without_eligible_whole_protein_hit:R',notes)
  refs,notes=select_references(catalog,[dict(reference_id='R',bitscore=50,evalue=1e-10)],profile,{},10)
  self.assertEqual(len(refs),1);self.assertIsNotNone(refs[0]['similarity'])
