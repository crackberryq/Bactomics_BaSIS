#!/usr/bin/env python3
"""Consolidate saved DeepSafe evidence and prepare the automated final run."""
import argparse,copy,hashlib,html,json,os
from datetime import datetime,timezone
from pathlib import Path
import yaml
from basis.config import load_config,CORE
from basis.execution import CommandRunner
from basis.prosite import run_prosite
from basis.prosite_profiles import run_profiles,load_profile_snapshot
from basis.motifs import load_rules,assess_motifs
from basis.references import read_fasta
from basis.deep_consolidation import consolidate
from basis.deep_reporting import completion_summary
from basis.report import _render_candidate

def final_config(core,deep_config,study):
    if core['samples']!=deep_config['samples']:raise ValueError('Core and DeepSafe sample manifests differ')
    for m in CORE:
        if not core['modules'][m].get('enabled') or core['modules'][m].get('mode')!='native':
            raise ValueError('Final run requires enabled native core configuration: '+m)
    cfg=copy.deepcopy(core);cfg.pop('config_path',None)
    cfg['deep']=copy.deepcopy(deep_config['deep'])
    cfg['deep'].update(motifs=True,prosite_profiles=True)
    cfg['outdir']=str(study/'results-final')
    return cfg

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-v','--verbose',action='store_true')
    parser.add_argument('--config');parser.add_argument('--core-config',default='study-isolate1/config.core.yaml')
    parser.add_argument('--result',default='study-isolate1/results-deep-auto/results/isolate1/deepsafe/result.json')
    args=parser.parse_args();study=Path('study-isolate1').resolve()
    if args.verbose:os.environ['BASIS_VERBOSE']='1'
    configs=list(study.glob('config.deep-profiles-*.yaml'))
    if not args.config and not configs:raise ValueError('Prepare PROSITE profiles first')
    path=Path(args.config) if args.config else max(configs,key=lambda p:p.stat().st_mtime_ns)
    cfg=load_config(path);load_profile_snapshot(cfg['deep']['prosite_snapshot'])
    cfg['deep'].update(motifs=True,prosite_profiles=True)
    final=final_config(load_config(args.core_config),cfg,study)
    target=study/'config.final.yaml';text=yaml.safe_dump(final,sort_keys=False)
    if target.exists() and target.read_text()!=text:raise ValueError('Existing config.final.yaml differs; preserved. Review it before replacement.')
    if not target.exists():target.write_text(text)
    source=Path(args.result).resolve();raw=source.read_bytes();record=json.loads(raw)
    if record.get('module')!='deepsafe':raise ValueError('Expected DeepSafe evidence')
    out=source.parents[3]/'consolidated-reviews'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True,exist_ok=False)
    candidates=copy.deepcopy(record['candidates']);runner=CommandRunner(cfg['deep'],out/'commands.log')
    print('Refreshing local patterns, profiles and motifs; preserving existing domains, references and trees.',flush=True)
    artifacts=run_prosite(candidates,cfg['deep'],out,runner)+run_profiles(candidates,cfg['deep'],out,runner)
    rules,registry=load_rules(cfg['deep'].get('motif_rules'))
    for c in candidates:
        seq=None
        if c.get('selected') and c.get('query',{}).get('path'):
            seq=read_fasta(Path(c['query']['path']))[c['query']['id']]
        c.setdefault('analyses',{})['motifs']=assess_motifs(c,seq,rules,registry,True)
        c['consolidated_evidence']=consolidate(c)
    selected=[c for c in candidates if c.get('selected')]
    review={'source':str(source),'source_sha256':hashlib.sha256(raw).hexdigest(),
      'config':str(path.resolve()),'config_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
      'completion':completion_summary({**record,'candidates':candidates}),
      'native_refresh_scope':['prosite_patterns','prosite_profiles'],
      'preserved_historical_evidence':['domains','references','phylogeny','context','integrity'],
      'candidates':selected,'artifacts':artifacts,'commands':runner.commands}
    (out/'review.json').write_text(json.dumps(review,indent=2)+'\n')
    body=''.join(_render_candidate(c,out) for c in selected)
    (out/'report.html').write_text('<!doctype html><meta charset="utf-8"><title>BaSIS consolidated DeepSafe evidence</title><style>body{font:16px system-ui;margin:2em}table{border-collapse:collapse;width:100%}th,td{border:1px solid #bbb;padding:.5em;overflow-wrap:anywhere}pre{white-space:pre-wrap}details{margin:1em 0}</style><h1>Consolidated DeepSafe evidence</h1><p>Patterns and profiles refreshed. Earlier domains, references and trees preserved as historical evidence. No safety verdict.</p><pre>'+html.escape(json.dumps(review['completion'],indent=2))+'</pre>'+body)
    for c in selected:
        s=c['consolidated_evidence']
        print(c['protein_id'],'| overlap groups:',len(s['overlap_groups']),'| distinct mapped residue observations:',len(s['residue_observations']),'| unsupported conditions:',len(s['unsupported_conditions']))
    print('Report:',out/'report.html');print('Evidence:',out/'review.json')
    print('Final config:',target)
    print('Automated final run: basis run -c study-isolate1/config.final.yaml -d')
    print('Final run uses native core screens (or verified caches), then DeepSafe and the consolidated report. Extend is not enabled.')
if __name__=='__main__':main()
