#!/usr/bin/env python3
"""Prepare native combined execution and a saved-evidence integration validation."""
import copy,json
from collections import Counter
from pathlib import Path
import yaml
from basis.config import CORE,load_config
from basis.inputs import load_samples,validate_sample
from basis.pipeline import _import_result
from basis.deep import select_candidates
from basis.util import sha256_file

def save(path,cfg):
    text=yaml.safe_dump(cfg,sort_keys=False)
    if path.exists() and path.read_text()!=text:raise ValueError('Existing configuration differs; preserved: '+str(path))
    if not path.exists():path.write_text(text)

def main():
    study=Path('study-isolate1').resolve();cfg=load_config(study/'config.final.yaml');cfg.pop('config_path',None)
    samples=load_samples(cfg['samples'])
    if len(samples)!=1 or samples[0]['sample_id']!='isolate1':raise ValueError('This study helper expects isolate1 only; pipeline remains multi-sample capable')
    sample=samples[0];validation=validate_sample(sample)
    if validation.get('errors'):raise ValueError('Input validation errors: '+str(validation['errors']))
    paths={m:study/'results-final/results/isolate1'/m/'result.json' for m in CORE}
    recoveries=list((study/'results-extend-validation/recovery').glob('*/result.json'))
    if not recoveries:raise ValueError('Recover Extend output first')
    paths['pathogenfinder2']=max(recoveries,key=lambda p:p.stat().st_mtime_ns)
    records=[]
    for m,p in paths.items():
        raw=json.loads(p.read_text())
        if not raw.get('artifact_checksums'):raise ValueError('Missing artifact checksums: '+str(p))
        for name,digest in raw['artifact_checksums'].items():
            if sha256_file(name)!=digest:raise ValueError('Artifact changed: '+name)
        payload=_import_result(str(p),sample,m,validation)
        records.append({**payload,'module':m,'execution_status':'completed','record_id':'isolate1:'+m})
    recovered=json.loads(paths['pathogenfinder2'].read_text())
    module=copy.deepcopy(recovered['effective_config']);module.update(enabled=True,mode='native',cache=False)
    for k in ('result','embeddings','embeddings_manifest'):module.pop(k,None)
    cfg['modules']['pathogenfinder2']=module;cfg['execution']['gpus']=max(1,cfg['execution'].get('gpus',0))
    for m in CORE:
        if not cfg['modules'][m].get('enabled') or cfg['modules'][m].get('mode')!='native':raise ValueError('Final core must be native and enabled: '+m)
    cfg['deep'].update(attention_queue_for_mixed_evidence=True,maximum_attention_candidates=20)
    preliminary=select_candidates(records,cfg['deep'],sample)
    native_count=sum(c['selection_queue']=='native_screen' for c in preliminary)
    cfg['deep']['maximum_candidates']=max(cfg['deep'].get('maximum_candidates',100),native_count+20)
    cfg['outdir']=str(study/'results-combined')
    native=study/'config.combined.yaml';save(native,cfg)
    imported=copy.deepcopy(cfg);imported['outdir']=str(study/'results-combined-validation')
    for m in paths:
        imported['modules'][m]={'enabled':True,'mode':'import','result':str(paths[m]),'cache':True,'threads':1,'memory_mb':1000,'gpus':0}
    review=study/'config.combined-validation.yaml';save(review,imported)
    candidates=select_candidates(records,cfg['deep'],sample)
    print('Verified saved core and recovered Extend evidence.')
    print('Queues:',dict(Counter((c['selection_queue'],bool(c['selected'])) for c in candidates)))
    for c in candidates:
        if 'ml_attention_followup' in c['selection_reasons']:
            print(c.get('protein_id'),'| attention rank:',c.get('attention_rank'),'| queue:',c['selection_queue'],'| selected:',c['selected'])
    print('Native combined config:',native)
    print('Saved-evidence validation config:',review)
    print('Validation imports core/Extend evidence and runs DeepSafe; it does not repeat GPU inference.')
    print('Run: basis run -c study-isolate1/config.combined-validation.yaml -d -e -v')
    print('Future native run: basis run -c study-isolate1/config.combined.yaml -d -e -v')
if __name__=='__main__':main()
