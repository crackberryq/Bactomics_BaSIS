#!/usr/bin/env python3
"""Run focused DeepSafe with explicitly imported, input-matched core evidence."""
import json
from pathlib import Path
import yaml
from basis.config import CORE, load_config
from basis.inputs import load_samples, validate_sample
from basis.pipeline import make_plan, run_pipeline
from basis.util import sha256_file
from BaSIS_run_context import clean_config


def prepare(project):
    source=project/'study-isolate1/config.deep-resources.yaml'
    target=source.with_name('config.deep-imported-core.yaml')
    config=clean_config(load_config(source))
    samples=load_samples(config['samples'])
    if len(samples)!=1 or samples[0]['sample_id']!='isolate1':
        raise ValueError('This validation launcher expects only isolate1')
    validation=validate_sample(samples[0])
    if validation.get('errors'):
        raise ValueError(str(validation['errors']))
    original=Path(config['outdir'])
    destination=project/'study-isolate1/results-deep-validation'
    snapshots=destination/'imported-core';snapshots.mkdir(parents=True,exist_ok=True)
    for module in CORE:
        path=original/'results/isolate1'/module/'result.json'
        raw=path.read_bytes(); record=json.loads(raw)
        if record.get('execution_status')!='completed' or record.get('sample_id')!='isolate1' or record.get('module')!=module:
            raise ValueError(f'Invalid core evidence: {module}')
        if record.get('input_hashes')!=validation['input_hashes']:
            raise ValueError(f'Inputs no longer match recorded {module} evidence')
        for artifact, expected in record.get('artifact_checksums',{}).items():
            if not Path(artifact).is_file() or sha256_file(artifact)!=expected:
                raise ValueError(f'Core artifact missing or changed: {artifact}')
        snapshot=snapshots/f'{module}.json'
        if snapshot.exists():
            if snapshot.read_bytes()!=raw:
                raise ValueError(f'Historical evidence changed: {snapshot}; refusing overwrite')
        else:
            with snapshot.open('xb') as handle:handle.write(raw)
        config['modules'][module]={'enabled':True,'mode':'import','result':str(snapshot),
                                    'threads':1,'memory_mb':1000,'cache':True}
    config['outdir']=str(destination)
    config['deep'].update(include_exploratory=False,maximum_exploratory_candidates=0,phylogeny=False)
    if target.exists():
        if clean_config(load_config(target))!=clean_config_from_mapping(config, target):
            raise ValueError(f'Existing configuration differs: {target}')
    else:
        with target.open('x') as handle:yaml.safe_dump(config,handle,sort_keys=False)
    return load_config(target)


def clean_config_from_mapping(config, target):
    # load_config merges default settings; compare using the same normalization.
    from tempfile import NamedTemporaryFile
    with NamedTemporaryFile(mode='w',suffix='.yaml',dir=target.parent) as handle:
        yaml.safe_dump(config,handle,sort_keys=False);handle.flush()
        return clean_config(load_config(handle.name))


def main():
    project=Path(__file__).resolve().parent
    config=prepare(project)
    print('Core results are IMPORTED historical evidence, not re-executed or claimed as current-source caches.',flush=True)
    print('Running 14-candidate validation: extraction, context, Pfam and reference retrieval; trees disabled.',flush=True)
    plan=make_plan(config,deep=True,extend=False)
    result,code=run_pipeline(plan,engine='local',force=False)
    print('Pipeline exit code:',code)
    print('Run status:',result['run']['completeness'])
    record=json.loads((Path(config['outdir'])/'results/isolate1/deepsafe/result.json').read_text())
    print('DeepSafe execution:',record['execution_status'])
    print('Metrics:',json.dumps(record.get('metrics',{})))
    for c in record.get('candidates',[]):
        if not c.get('selected'):continue
        a=c.get('analyses',{})
        print(c.get('protein_id'),'|',c.get('execution'),'| references:',len(c.get('references',[])),
              '| domains:',len(a.get('domains',{}).get('hits',[])),'|',c.get('reason'))
    if record.get('error'):print('ERROR:',record['error'])
    print('Evidence:',Path(config['outdir'])/'results/isolate1/deepsafe/result.json')
    print('Report:',result['artifacts']['html'])
    print('Deferred exploratory candidates may make overall scope incomplete; check selected-candidate statuses above.')
    return code


if __name__=='__main__':
    raise SystemExit(main())
