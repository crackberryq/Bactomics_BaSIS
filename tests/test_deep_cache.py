import tempfile,unittest
from pathlib import Path
from basis.deep_cache import seal,reusable,stable_records
class CacheTests(unittest.TestCase):
 def test_integrity(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'tree';p.write_text('original')
   r={'execution_status':'completed','cache_key':'k','metrics':{'failed_candidates':0},'artifacts':[str(p)]}
   r['deep_cache']=seal(r,d)
   self.assertTrue(reusable(r,'k'))
   r['cache_hit']=True;r['cache_checked_at']='later'
   self.assertTrue(reusable(r,'k'));self.assertFalse(reusable(r,'different'))
   r['metrics']['failed_candidates']=1;self.assertFalse(reusable(r,'k'));r['metrics']['failed_candidates']=0
   p.write_text('changed');self.assertFalse(reusable(r,'k'))
   p.unlink();self.assertFalse(reusable(r,'k'))
 def test_legacy_and_volatile(self):
  self.assertFalse(reusable({'execution_status':'completed','cache_key':'k'},'k'))
  self.assertEqual(stable_records([{'cache_hit':True,'cache_checked_at':'x','findings':[1]}]),[{'findings':[1]}])
 def test_tampered_payload(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a';p.write_text('x')
   r={'execution_status':'completed','cache_key':'k','candidates':[{'interpretation':'unresolved'}]}
   r['deep_cache']=seal(r,d);r['candidates'][0]['interpretation']='safe'
   self.assertFalse(reusable(r,'k'))

class CacheIntegration(unittest.TestCase):
 def test_two_runs_force_and_changed_artifact(self):
  from unittest.mock import patch
  from basis.pipeline import execute_deep,record_path
  from basis.config import ALL_MODULES
  from basis.util import atomic_json
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);profiles=root/'profiles';profiles.mkdir()
   plan={'outdir':td,'config':{'outdir':td,'validation_mode':'native','deep':{'profiles_dir':str(profiles)},'execution':{'cores':1}},'options':{'deep':True},'validations':{'s':{'input_hashes':{}}},'source_hash':'code','enabled_modules':[]}
   sample={'sample_id':'s'}
   for m in ALL_MODULES:atomic_json(record_path(plan,'s',m),{'module':m,'execution_status':'completed'})
   def native(sample,records,cfg,work,runner):
    artifact=work/'tree';artifact.write_text('tree')
    return {'artifacts':[str(artifact)],'metrics':{'failed_candidates':0,'deferred_candidates':10},'candidates':[],'complete':False}
   with patch('basis.deep_cache.engine_identity',return_value={'status':'available','hash':'engine'}),patch('basis.deep.run_deep',side_effect=native) as run:
    one=execute_deep(plan,sample);two=execute_deep(plan,sample)
    self.assertTrue(two['cache_hit']);self.assertEqual(run.call_count,1)
    Path(one['artifacts'][-1]).write_text('tampered')
    execute_deep(plan,sample);self.assertEqual(run.call_count,2)
    execute_deep(plan,sample,force=True);self.assertEqual(run.call_count,3)
