import hashlib,json,tempfile,unittest
from pathlib import Path
from basis.prosite import load_snapshot,parse_gff,run_prosite,FILES

class PrositeTests(unittest.TestCase):
 def fixture(self,d):
  p=Path(d);data={'prosite.dat':'CC   Release 2026_03\n//\nID   TEST; PATTERN.\nAC   PS99999;\nDE   Test signature.\nPA   A-C-D.\nDO   PDOC99999;\n//\n',
       'prosite.aux':'CC   Release 2026_03\n//\nAC   PS99999;\nNR   /FALSE_POS=1(1);\nCC   /TAXO-RANGE=AB???;\n//\n',
       'prosite.doc':'Release 2026_03\n{PDOC99999}\nTest documentation',
       'ps_reldt.txt':'Release 2026_03','ps_scan.pl':'# scanner fixture'}
  for name,text in data.items():(p/name).write_text(text)
  (p/'provenance.json').write_text(json.dumps({'schema':'basis-prosite-snapshot-v1','release':'2026_03',
        'sha256':{n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in FILES}}))
  return p
 def test_snapshot_integrity(self):
  with tempfile.TemporaryDirectory() as d:
   p=self.fixture(d);patterns,meta=load_snapshot(p)
   self.assertEqual(meta['eligible_patterns'],1)
   self.assertEqual(patterns['PS99999']['taxonomy_range_as_recorded'],'AB???')
   (p/'ps_scan.pl').write_text('changed')
   with self.assertRaises(ValueError):load_snapshot(p)
 def test_release_mismatch(self):
  with tempfile.TemporaryDirectory() as d:
   p=self.fixture(d);m=json.loads((p/'provenance.json').read_text());m['release']='2025_01';(p/'provenance.json').write_text(json.dumps(m))
   with self.assertRaises(ValueError):load_snapshot(p)
 def test_gff_and_empty(self):
  with tempfile.TemporaryDirectory() as d:
   p=self.fixture(d);patterns,_=load_snapshot(p);g=p/'test.gff'
   g.write_text('Q\tps_scan|v1.91\tPS99999\t2\t4\t.\t.\t.\tName "TEST" ; Sequence "Ac..D"\n')
   h=parse_gff(g,{'Q':'MACDE'},patterns)['Q'][0]
   self.assertEqual(h['sequence'],'ACD');self.assertFalse(h['function_established'])
   patterns['PS99999']['skip_flag']=True
   with self.assertRaises(ValueError):parse_gff(g,{'Q':'MACDE'},patterns)
   g.write_text('');self.assertEqual(parse_gff(g,{'Q':'MACDE'},patterns),{'Q':[]})
 def test_mismatch_not_accepted(self):
  with tempfile.TemporaryDirectory() as d:
   p=self.fixture(d);patterns,_=load_snapshot(p);g=p/'test.gff'
   for query,sequence,start,end in [('unknown','ACD',2,4),('Q','ACE',2,4),('Q','ACD',0,99)]:
    g.write_text(f'{query}\tps_scan|v1.91\tPS99999\t{start}\t{end}\t.\t.\t.\tSequence "{sequence}"\n')
    with self.assertRaises(ValueError):parse_gff(g,{'Q':'MACDE'},patterns)
 def test_unconfigured_and_deferred(self):
  c=[{'selected':True},{'selected':False}]
  self.assertEqual(run_prosite(c,{},'.',None),[])
  self.assertEqual(c[0]['analyses']['prosite_patterns']['reason'],'prosite_snapshot_not_configured')
  self.assertEqual(c[1]['analyses']['prosite_patterns']['reason'],'candidate_deferred')
 def test_input_hash_guard(self):
  with tempfile.TemporaryDirectory() as d:
   p=self.fixture(d);q=p/'q.faa';q.write_text('>Q\nACD\n')
   c=[{'selected':True,'execution':'completed','query':{'path':str(q),'id':'Q','sequence_sha256':'bad'}}]
   with self.assertRaises(ValueError):run_prosite(c,{'prosite_snapshot':d},p/'out',None)
