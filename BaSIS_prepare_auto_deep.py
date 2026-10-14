#!/usr/bin/env python3
"""Prepare normal basis run -d configuration, with verified completed follow-up imports."""
import hashlib,json
from pathlib import Path
import yaml
from basis.config import load_config,CORE
from BaSIS_run_context import clean_config

def save_once(path,text):
 if path.exists():
  if path.read_text()!=text:raise ValueError('Existing file differs: '+str(path))
 else:
  path.parent.mkdir(parents=True,exist_ok=True)
  with path.open('x') as handle:handle.write(text)

def main():
 root=Path(__file__).resolve().parent;study=root/'study-isolate1'
 cfg=clean_config(load_config(study/'config.deep-anchor-validation.yaml'))
 if any(cfg['modules'][m].get('mode')!='import' for m in CORE):raise ValueError('Expected imported core evidence')
 source=Path(cfg['outdir'])/'results/isolate1/deepsafe/result.json';raw=source.read_bytes();record=json.loads(raw)
 reviews=sorted((study/'deep-decision-reports').glob('*/decisions.json'))
 if not reviews:raise ValueError('Run update11 decision report first')
 review=json.loads(reviews[-1].read_text())
 if hashlib.sha256(raw).hexdigest()!=review['source_sha256']:raise ValueError('Historical evidence differs from reviewed record')
 registry=[]
 for row in review['candidates']:
  followup=row.get('reviewed_followup')
  if not followup:continue
  c=next(c for c in record['candidates'] if c['candidate_id']==row['candidate_id'])
  artifact_paths={str(Path(followup['source_directory'])/name):digest for name,digest in followup['artifact_hashes'].items()}
  for name,digest in artifact_paths.items():
   if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=digest:raise ValueError('Reviewed artifact changed: '+name)
  registry.append({'sample_id':c.get('sample_id','isolate1'),'protein_id':c['protein_id'],
   'query_sha256':c['query']['sequence_sha256'],
   'references':sorted((r['reference_id'],r['sequence_sha256']) for r in c['references']),
   'required_groups':sorted(c['profile'].get('required_comparison_groups',[])),
   'artifacts':artifact_paths,'followup':followup})
 registry_path=study/'auto-deep-resources/reviewed-followups.json'
 save_once(registry_path,json.dumps({'records':registry},indent=2)+'\n')
 profiles=study/'auto-deep-resources/profiles'
 original=Path(cfg['deep']['profiles_dir'])
 for p in sorted(original.glob('*.yaml')):
  profile=yaml.safe_load(p.read_text())
  if profile.get('validation_status')=='reviewed' and 'references' in profile.get('analyses',[]):
   profile['analyses']=list(dict.fromkeys(profile['analyses']+['phylogeny']))
   profile['notes']=[n for n in profile.get('notes',[]) if 'Trees disabled' not in n]
   profile['notes'].append('Bounded exploratory trees enabled by auto-deep configuration; runtime evidence gates apply. No classifier validation asserted.')
  save_once(profiles/p.name,yaml.safe_dump(profile,sort_keys=False))
 cfg['deep'].update(auto_phylogeny=True,phylogeny=True,domain_native_cutoff='ga',maximum_tree_candidates=6,
                    profiles_dir=str(profiles),tree_review_registry=str(registry_path),threads=2)
 cfg['outdir']=str(study/'results-deep-auto')
 path=study/'config.deep-auto.yaml'
 if path.exists():
  if clean_config(load_config(path))!=cfg:raise ValueError('Existing auto config differs')
 else:save_once(path,yaml.safe_dump(cfg,sort_keys=False))
 print('Config:',path)
 print('Verified completed follow-up imports:',len(registry))
 print('Native tree budget: at most 6 candidates, at most 3 trees each per invocation.')
 print('Historical core imported. Reference retrieval and domains execute in normal DeepSafe.')
 print('Run: basis run -c study-isolate1/config.deep-auto.yaml -d')
if __name__=='__main__':main()
