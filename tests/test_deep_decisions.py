import unittest
from basis.deep_decisions import assess_candidate
class DecisionTests(unittest.TestCase):
 def candidate(self):return {'execution':'completed','profile':{'required_comparison_groups':['A']},'references':[{'comparison_group':'A','review_status':'reviewed'}]}
 def test_execution_is_not_function(self):
  c=self.candidate();c['analyses']={'phylogeny':{'status':'completed'}}
  a=assess_candidate(c);self.assertEqual(a['execution'],'completed');self.assertEqual(a['interpretation'],'unresolved');self.assertFalse(a['function_established'])
 def test_missing_groups_preserved(self):
  c=self.candidate();c['references']=[]
  self.assertEqual(assess_candidate(c)['followup'],'hold_for_comparator_review')
 def test_bounded_weak_followup_stops(self):
  f={'provenance_verified':True,'budget_exhausted':True,'runs':[{'execution':'completed','sh_alrt':24.4,'ufboot':53},{'execution':'completed','sh_alrt':9.6,'ufboot':44}]}
  self.assertEqual(assess_candidate(self.candidate(),f)['followup'],'stop_automatic_tree_followup')
  f['runs'][0]['ufboot']=float('nan')
  self.assertNotEqual(assess_candidate(self.candidate(),f)['followup'],'stop_automatic_tree_followup')
 def test_unverified_or_strong_is_not_promoted(self):
  for verified in [False,True]:
   f={'provenance_verified':verified,'budget_exhausted':True,'runs':[{'execution':'completed','sh_alrt':100,'ufboot':100}]}
   self.assertFalse(assess_candidate(self.candidate(),f)['function_established'])
 def test_failed_and_deferred_remain_so(self):
  for state in ['failed','deferred']:
   c=self.candidate();c['execution']=state
   self.assertEqual(assess_candidate(c)['execution'],state)
