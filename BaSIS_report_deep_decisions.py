#!/usr/bin/env python3
"""Apply reporting policy to historical evidence; never launch native analyses."""
import hashlib,html,json
from datetime import datetime,timezone
from pathlib import Path
from basis.deep_decisions import assess_candidate

VERIFIED_FILES = {'summary.json': '9b9b24436f200ec0263f32e69baca323cd5277002228fc01abe4bae36945bb21', 'baseline/labels.json': '7fd699d020a9fd98bfc903660d0a738543dc5d418631586cbf929257714adad3', 'baseline/alignment.faa': 'd86f73b34f534b586abeb7b63b87f505e0ce1467b6aa16cb0b981c34651241a7', 'baseline/cfr.treefile': 'c568c8a95b9c4f33016b678e6b6e12f8ad826e9dd30b9ae1836fd959e5005b26', 'baseline/cfr.iqtree': '177ac70e3092dc6f057377d1760eeda927a3fcacd4367a2d8d4c4b1ff140721a', 'full_alternative_model/alignment.faa': '8b432e5fc1835cb61d1491f1cde030103c8a95496204968cd7bffa300c414357', 'full_alternative_model/tree.treefile': '32b03264618c990b2f1da14c7e0dbff485197d537c69982ca8621ee5e1d92896', 'full_alternative_model/tree.iqtree': '3a54a7f2032d10000db3739251c67472f5c2a479b483e439c0fb2d21178c99c1', 'full_alternative_model/tree.log': '64dfc0c8d415139adb391626ce9092c8dac0869f3cace0d341ca2230b93bbc03', 'occupancy80_model_selection/alignment.faa': '21821554da72788d63483c6e5e709c37d35c05402f718438a0c6a0337e31e157', 'occupancy80_model_selection/tree.treefile': '8829c562745887796e4cab9996d0960f7ea6ef706ccd1c5062f56039e6c1fb27', 'occupancy80_model_selection/tree.iqtree': '75472d1c3394077ecd1c111f7dea4c7c98c127291424b27c956cb1f8d8f75b58', 'occupancy80_model_selection/tree.log': '8bdc8626155fe52353a55ac76fffa9560aa0c22fea6d3d6dc9fc0129188574f5'}

def main():
 root=Path(__file__).resolve().parent
 sensitivity=root/'study-isolate1/cfr-sensitivity/20261008T190747715606Z'
 for name,expected in VERIFIED_FILES.items():
  p=sensitivity/name
  if hashlib.sha256(p.read_bytes()).hexdigest()!=expected:raise ValueError('Reviewed artifact differs: '+str(p))
 source=root/'study-isolate1/results-deep-anchor-validation/results/isolate1/deepsafe/result.json'
 data=source.read_bytes();record=json.loads(data)
 cfr=next(c for c in record['candidates'] if c.get('protein_id')=='AFFIONLF_01325')
 if cfr.get('query',{}).get('sequence_sha256')!='b79bc2cee954137a5085e9f714233206a04f10272622a7e80718ffbb0626388b':
  raise ValueError('Query differs from reviewed tree follow-up')
 followup={'provenance_verified':True,'budget_exhausted':True,
  'review_basis':'AI-assisted inspection of tree splits, labels, alignment hashes and IQ-TREE diagnostics; not a validated classifier',
  'scope':'Query pairing with ClbA-labelled reference; not whole-family monophyly',
  'runs':[{'execution':'completed','name':'baseline','sh_alrt':24.4,'ufboot':53},
          {'execution':'completed','name':'alternative_model','sh_alrt':20.1,'ufboot':51},
          {'execution':'completed','name':'occupancy80','sh_alrt':9.6,'ufboot':44}],
  'warnings':['RlmN comparator composition test: full p=0.0121; masked p=0.0170',
              'Limited RlmN diversity; most retrieved comparator labels remain unreviewed',
              'Two reviewed Cfr accession records represent one identical sequence'],
  'artifact_hashes':VERIFIED_FILES,'source_directory':str(sensitivity)}
 out=root/'study-isolate1/deep-decision-reports'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 out.mkdir(parents=True,exist_ok=False)
 rows=[]
 for c in record['candidates']:
  if not c.get('selected'):continue
  f=followup if c['candidate_id']==cfr['candidate_id'] else None
  a=assess_candidate(c,f)
  rows.append({'protein_id':c['protein_id'],'candidate_id':c['candidate_id'],'gene':c.get('gene'),
               'assessment':a,'reviewed_followup':f})
  print(c['protein_id'],'| execution:',a['execution'],'| interpretation:',a['interpretation'],'| follow-up:',a['followup'])
 report={'source':str(source),'source_sha256':hashlib.sha256(data).hexdigest(),
         'evidence_basis':'Historical DeepSafe evidence plus hash-verified reviewed follow-up; no native tools run',
         'limitation':'Follow-up actions are recorded decisions. This update does not introduce an automatic sensitivity scheduler.',
         'candidates':rows}
 (out/'decisions.json').write_text(json.dumps(report,indent=2)+'\n')
 esc=lambda v:html.escape(str(v))
 body=''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in [r['protein_id'],r['gene'],r['assessment']['execution'],r['assessment']['interpretation'],r['assessment']['followup']])+'</tr>' for r in rows)
 details=''.join('<details><summary>'+esc(r['protein_id'])+' — evidence and warnings</summary><pre>'+esc(json.dumps(r,indent=2))+'</pre></details>' for r in rows)
 (out/'report.html').write_text('<!doctype html><meta charset="utf-8"><title>BaSIS DeepSafe decision review</title><style>body{font:16px system-ui;margin:2em;max-width:1300px}td,th{padding:.6em;text-align:left;border-bottom:1px solid #ddd}table{border-collapse:collapse}pre{white-space:pre-wrap}details{margin:1em 0}</style><h1>DeepSafe decision review</h1><p>'+esc(report['evidence_basis'])+'</p><p>Execution success does not establish resistance function or organism safety. Deferred candidates remain unassessed.</p><table><tr><th>Protein</th><th>Screen label</th><th>Execution</th><th>Interpretation</th><th>Follow-up decision</th></tr>'+body+'</table>'+details)
 print('Report:',out/'report.html');print('Evidence:',out/'decisions.json')
 print('No native analyses run. Original reports preserved.')
 return 0

if __name__=='__main__':raise SystemExit(main())
