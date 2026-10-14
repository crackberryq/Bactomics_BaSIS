#!/usr/bin/env python3
"""Validate reviewed anchor retrieval; preserve previous results and import core evidence."""
import json
from pathlib import Path
import yaml
from basis.config import CORE, load_config
from basis.deep_alignment_review import anchor_summary
from basis.pipeline import make_plan, run_pipeline
from BaSIS_run_context import clean_config

def main():
    project=Path(__file__).resolve().parent
    config=clean_config(load_config(project/'study-isolate1/config.deep-reviewed.yaml'))
    for module in CORE:
        if config['modules'][module].get('mode')!='import':
            raise ValueError(f'Expected historical core import for {module}; refusing native rerun.')
    config['deep']['phylogeny']=False
    config['outdir']=str(project/'study-isolate1/results-deep-anchor-validation')
    path=project/'study-isolate1/config.deep-anchor-validation.yaml'
    if path.exists():
        if clean_config(load_config(path))!=config: raise ValueError(f'Existing config differs: {path}')
    else:
        with path.open('x') as handle: yaml.safe_dump(config,handle,sort_keys=False)
    config=load_config(path)
    print('DeepSafe: separate reviewed-anchor search; unchanged retrieval filters; trees OFF.',flush=True)
    print('Core evidence imported. Deep analyses run in a separate output directory.',flush=True)
    result,code=run_pipeline(make_plan(config,deep=True,extend=False),engine='local',force=False)
    p=Path(config['outdir'])/'results/isolate1/deepsafe/result.json'
    print('Exit:',code,'| scope:',result['run']['completeness'])
    if not p.exists():
        print('DeepSafe result missing. Report:',result['artifacts']['html']); return code or 1
    record=json.loads(p.read_text())
    print('DeepSafe:',record['execution_status'],'| metrics:',record.get('metrics'))
    if record.get('error'): print('ERROR:',record['error'])
    for c in record.get('candidates',[]):
        if not c.get('selected'): continue
        print(c.get('protein_id'),'|',c.get('execution'),'| references:',len(c.get('references',[])))
        for a in c.get('analyses',{}).get('anchor_review',{}).get('anchors',[]):
            print(' ',anchor_summary(a))
        print(' Notes:', '; '.join(c.get('notes',[])))
    print('Evidence:',p); print('Report:',result['artifacts']['html'])
    print('Deferred candidates remain unassessed; overall incomplete status can persist.')
    return code

if __name__=='__main__': raise SystemExit(main())
