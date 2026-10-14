import unittest
from basis.deep_trees import parse_splits,mask_alignment,eligible
class TreePolicyTests(unittest.TestCase):
 def test_unrooted_complement_support(self):
  x=parse_splits('(Q:0.1,((B:0.1,C:0.1)99/100:0.1,D:0.1)24.4/53:0.1,A:0.1);',{'Q','A','B','C','D'},'Q')
  self.assertEqual(x,{'query_side':['A','Q'],'sh_alrt':24.4,'ufboot':53})
 def test_equivalent_rerooting(self):
  a=parse_splits('((Q:0.1,A:0.1)24.4/53:0.1,B:0.1,(C:0.1,D:0.1)80/95:0.1);',{'Q','A','B','C','D'},'Q')
  self.assertEqual(a['query_side'],['A','Q']);self.assertEqual(a['ufboot'],53)
 def test_duplicate_or_missing_tip_rejected(self):
  for t in ['(Q:0.1,Q:0.1,A:0.1);','(Q:0.1,A:0.1);']:
   with self.assertRaises(ValueError):parse_splits(t,{'Q','A','B'},'Q')
 def test_mask_boundary(self):
  a,k=mask_alignment({'a':'AA-','b':'AA-','c':'A--','d':'AA-','e':'AA-'})
  self.assertEqual(k,[0,1]);self.assertEqual(a['c'],'A-')
 def test_taxon_and_comparator_gates(self):
  c={'analysis_plan':{'analyses':{'phylogeny':True}},'profile':{'validation_status':'reviewed','required_comparison_groups':['A']},'references':[]}
  self.assertEqual(eligible(c),'missing_reviewed_comparison_groups')
  c['references']=[{'review_status':'reviewed','comparison_group':'A'}];c['taxonomy_review']={'status':'review_required'}
  self.assertEqual(eligible(c),'taxonomic_scope_review_required')

class ReviewReuseTests(unittest.TestCase):
 def test_review_identity_and_artifact_guards(self):
  import tempfile,json,hashlib
  from pathlib import Path
  from basis.deep_trees import reuse_review
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);artifact=p/'tree';artifact.write_text('fixture')
   c={'sample_id':'s','protein_id':'p','query':{'sequence_sha256':'q'},'references':[{'reference_id':'r','sequence_sha256':'h'}],'profile':{'required_comparison_groups':['A']}}
   row={'sample_id':'s','protein_id':'p','query_sha256':'q','references':[['r','h']],'required_groups':['A'],'artifacts':{str(artifact):hashlib.sha256(b'fixture').hexdigest()},'followup':{'provenance_verified':True,'budget_exhausted':True}}
   registry=p/'registry.json';registry.write_text(json.dumps({'records':[row]}));cfg={'tree_review_registry':str(registry)}
   self.assertEqual(reuse_review(c,cfg)['reason'],'reused_verified_review')
   c['query']['sequence_sha256']='changed';self.assertIsNone(reuse_review(c,cfg))
   c['query']['sequence_sha256']='q';artifact.write_text('changed')
   with self.assertRaises(ValueError):reuse_review(c,cfg)
