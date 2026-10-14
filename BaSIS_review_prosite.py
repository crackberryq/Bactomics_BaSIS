#!/usr/bin/env python3
"""Scan selected saved queries locally; preserve previous result files."""
import argparse,copy,hashlib,html,json
from datetime import datetime,timezone
from pathlib import Path
from basis.config import load_config
from basis.execution import CommandRunner
from basis.prosite import run_prosite

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    parser.add_argument('--result',default='study-isolate1/results-deep-auto/results/isolate1/deepsafe/result.json')
    args=parser.parse_args()
    if args.config:config_path=Path(args.config)
    else:
        files=list(Path('study-isolate1').glob('config.deep-prosite-*.yaml'))
        if not files:raise ValueError('Run BaSIS_prepare_prosite.py first')
        config_path=max(files,key=lambda p:p.stat().st_mtime_ns)
    cfg=load_config(config_path);source=Path(args.result).resolve();raw=source.read_bytes();record=json.loads(raw)
    if record.get('module')!='deepsafe':raise ValueError('Expected DeepSafe evidence')
    out=source.parents[3]/'prosite-reviews'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True,exist_ok=False)
    runner=CommandRunner(cfg['deep'],out/'commands.log')
    candidates=copy.deepcopy(record['candidates'])
    print('Local PROSITE pattern scan only; previous domain, reference and tree analyses are not rerun.',flush=True)
    artifacts=run_prosite(candidates,cfg['deep'],out,runner)
    rows=[]
    for c in candidates:
        if not c.get('selected'):continue
        a=c['analyses']['prosite_patterns'];rows.append({'protein_id':c.get('protein_id'),'assessment':a})
        print(c.get('protein_id'),'|',a['status'],'| matches:',a.get('hit_count',0))
        for hit in a.get('hits',[]):print(' ',hit['accession'],hit['name'],str(hit['start'])+'-'+str(hit['end']))
    review={'source':str(source),'source_sha256':hashlib.sha256(raw).hexdigest(),'config':str(config_path.resolve()),
        'config_sha256':hashlib.sha256(config_path.read_bytes()).hexdigest(),'candidate_results':rows,
        'deferred_candidates':sum(not c.get('selected') for c in candidates),'artifacts':artifacts,'commands':runner.commands}
    (out/'review.json').write_text(json.dumps(review,indent=2)+'\n')
    esc=lambda s:html.escape(str(s))
    content=''.join('<details><summary>'+esc(r['protein_id'])+' — '+esc(r['assessment']['status'])+'</summary><pre>'+esc(json.dumps(r['assessment'],indent=2))+'</pre></details>' for r in rows)
    (out/'report.html').write_text('<!doctype html><meta charset="utf-8"><title>BaSIS PROSITE review</title><style>body{font:16px system-ui;margin:2em}pre{white-space:pre-wrap}details{margin:1em 0}</style><h1>PROSITE pattern review</h1><p>Curated pattern matches are exploratory sequence evidence. No functional or safety verdict. Profiles, miniprofiles and ProRule were not run. Original results preserved.</p><p><a href="review.json">Full review evidence</a></p>'+content)
    print('Report:',out/'report.html');print('Evidence:',out/'review.json')
if __name__=='__main__':main()
