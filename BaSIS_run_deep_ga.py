#!/usr/bin/env python3
"""Validate GA filtering and partial-domain review without rerunning core tools."""
import json
from pathlib import Path
import yaml
from basis.config import load_config
from basis.pipeline import make_plan, run_pipeline
from BaSIS_run_deep_validation import prepare
from BaSIS_run_context import clean_config


def main():
    project=Path(__file__).resolve().parent
    config=clean_config(prepare(project))
    config['deep'].update(domain_native_cutoff='ga',partial_domain_min_overlap=0.7,phylogeny=False)
    config['outdir']=str(project/'study-isolate1/results-deep-ga-validation')
    path=project/'study-isolate1/config.deep-ga-validation.yaml'
    if path.exists():
        if clean_config(load_config(path))!=config:raise ValueError(f'Existing config differs: {path}')
    else:
        with path.open('x') as handle:yaml.safe_dump(config,handle,sort_keys=False)
    config=load_config(path)
    print('DeepSafe validation: imported core; GA-qualified domains; separate partial-reference review; trees OFF.',flush=True)
    result,code=run_pipeline(make_plan(config,deep=True,extend=False),engine='local',force=False)
    p=Path(config['outdir'])/'results/isolate1/deepsafe/result.json'
    record=json.loads(p.read_text())
    print('Exit code:',code,'| scope:',result['run']['completeness'])
    print('DeepSafe:',record['execution_status'],'| metrics:',record.get('metrics'))
    if record.get('error'):print('ERROR:',record['error'])
    for c in record.get('candidates',[]):
        if not c.get('selected'):continue
        a=c.get('analyses',{});d=a.get('domains',{})
        partial=a.get('partial_references',{}).get('hits',[])
        print(c.get('protein_id'),'|',c.get('execution'),'| GA hits:',len(d.get('hits',[])),
              '| rejected raw hits:',len(d.get('rejected_hits',[])),
              '| whole-protein refs:',len(c.get('references',[])),
              '| partial reference IDs:',len({h['reference_id'] for h in partial}),'|',c.get('reason'))
    print('Evidence:',p);print('Report:',result['artifacts']['html'])
    print('Deferred exploratory candidates remain unassessed; this is not a safety verdict.')
    return code

if __name__=='__main__':raise SystemExit(main())
