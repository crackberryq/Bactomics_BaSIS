import hashlib,tempfile,json,unittest
from pathlib import Path
from basis.motifs import load_rules,assess_motifs,compile_pattern
class MotifTests(unittest.TestCase):
 def setUp(self):self.rules,self.registry=load_rules()
 def candidate(self,seq):
  return {'selected':True,'query':{'sequence_sha256':hashlib.sha256(seq.encode()).hexdigest()},'profile':{'profile_id':'cfr_family'},'analyses':{'integrity':{'status':'completed','flags':[]},'domains':{'hits':[{'passes_ga':True,'domain_accession':'PF04055.24','query_start':1,'query_end':len(seq),'hmm_start':1,'hmm_end':100,'model_length':100}]}}}
 def assess(self,seq,change=None):
  c=self.candidate(seq)
  if change:change(c)
  return assess_motifs(c,seq,self.rules,self.registry)
 def test_present_and_positions(self):
  r=self.assess('ACAAACAACA');m=r['rules'][0]['matches'][0]
  self.assertEqual((m['start'],m['end'],m['sequence']),(2,9,'CAAACAAC'))
  self.assertFalse(r['function_established'])
 def test_absence_and_ambiguous(self):
  self.assertEqual(self.assess('AAAAAAAAAA')['rules'][0]['status'],'not_detected_in_assessed_region')
  self.assertEqual(self.assess('CAAACXACAA')['rules'][0]['status'],'not_assessable')
 def test_truncated(self):
  r=self.assess('AAAAAAAAAA',lambda c:c['analyses']['domains']['hits'][0].update(hmm_end=50))
  self.assertEqual(r['rules'][0]['status'],'not_assessable')
 def test_outside_domain(self):
  r=self.assess('CAAACAACAAAAAAAAAAAA',lambda c:c['analyses']['domains']['hits'][0].update(query_start=10))
  self.assertEqual(r['rules'][0]['matches'],[])
 def test_no_ga(self):
  r=self.assess('CAAACAAC',lambda c:c['analyses']['domains']['hits'][0].update(passes_ga=False))
  self.assertEqual(r['rules'][0]['status'],'not_assessable')
 def test_other_family_and_deferred(self):
  c={'selected':True,'analyses':{}}
  self.assertEqual(assess_motifs(c,None,self.rules,self.registry)['status'],'no_applicable_rule')
  c['selected']=False
  self.assertEqual(assess_motifs(c,None,self.rules,self.registry)['reason'],'candidate_deferred')
 def test_wrong_hash(self):
  self.assertEqual(assess_motifs(self.candidate('AAA'),'CCC',self.rules,self.registry)['status'],'failed')
 def test_pattern_safety(self):
  for pattern in ['C.*C','(a+)+','x(9999)','C||C']:
   with self.assertRaises(ValueError):compile_pattern(pattern)
  self.assertEqual(len(compile_pattern('[ST]-x(2)-C')),4)
 def test_duplicate_rules(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'rules.json';p.write_text(json.dumps({'schema_version':1,'rules':self.rules*2}))
   with self.assertRaises(ValueError):load_rules(p)
 def test_custom_family_rule(self):
  rule=dict(self.rules[0],rule_id='test_other_family',pattern='[ST]-x-C',domain_accessions=['PF99999'],profile_ids=[])
  c=self.candidate('SAC');c['profile']={};c['analyses']['domains']['hits'][0]['domain_accession']='PF99999.1'
  r=assess_motifs(c,'SAC',[rule],self.registry)
  self.assertEqual(r['rules'][0]['status'],'present')
