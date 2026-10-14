#!/usr/bin/env python3
"""Assess motifs against hash-verified existing query files; no native tools."""
import argparse,hashlib,html,json
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from basis.motifs import load_rules,assess_motifs
from basis.references import read_fasta

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--result',default='study-isolate1/results-deep-auto/results/isolate1/deepsafe/result.json')
    p.add_argument('--rules',default=None)
    args=p.parse_args();source=Path(args.result).resolve();raw=source.read_bytes();record=json.loads(raw)
    if record.get('module')!='deepsafe':raise ValueError('Expected DeepSafe evidence')
    rules,registry=load_rules(args.rules)
    rows=[]
    for c in record['candidates']:
        seq=None;error=None
        if c.get('selected') and c.get('query',{}).get('path'):
            try:
                query=read_fasta(Path(c['query']['path']))
                if set(query)!={c['query']['id']}:raise ValueError('Query FASTA identifiers differ')
                seq=query[c['query']['id']]
                if hashlib.sha256(seq.encode()).hexdigest()!=c['query']['sequence_sha256']:raise ValueError('Query hash mismatch')
            except (OSError,ValueError) as exc:error=str(exc)
        assessment=assess_motifs(c,seq,rules,registry)
        if error:assessment.update(status='failed',reason=error)
        rows.append({'protein_id':c.get('protein_id'),'candidate_id':c['candidate_id'],'selected':c.get('selected'),
                     'query_sha256':c.get('query',{}).get('sequence_sha256'),'motifs':assessment})
        if c.get('selected'):
            print(c.get('protein_id'),'|',assessment['status'],'|','; '.join(x['rule_id']+': '+x['status'] for x in assessment['rules']) or assessment['reason'])
            for rule in assessment['rules']:
                for match in rule['matches']:print(' ',match['start'],'-',match['end'],match['sequence'])
    out=source.parents[3]/'motif-reviews'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True,exist_ok=False)
    counts=dict(Counter(r['motifs']['status'] for r in rows if r['selected']))
    evidence={'source':str(source),'source_sha256':hashlib.sha256(raw).hexdigest(),'registry':registry,
              'selected_counts':counts,'deferred_candidates':sum(not r['selected'] for r in rows),
              'native_analyses_run':False,'candidates':rows}
    (out/'motifs.json').write_text(json.dumps(evidence,indent=2)+'\n')
    esc=lambda x:html.escape(str(x))
    body=''.join('<details><summary>'+esc(r['protein_id'])+' — '+esc(r['motifs']['status'])+'</summary><pre>'+esc(json.dumps(r,indent=2))+'</pre></details>' for r in rows if r['selected'])
    (out/'report.html').write_text('<!doctype html><meta charset="utf-8"><title>BaSIS motif review</title><style>body{font:16px system-ui;margin:2em;max-width:1200px}pre{white-space:pre-wrap}details{margin:1em 0}</style><h1>Motif review</h1><p>Existing query hashes verified. No native analyses run. Original evidence and decisions preserved. Motifs do not establish function; absent matches do not establish inactivity.</p><pre>'+esc(json.dumps(counts,indent=2))+'</pre><p><a href="motifs.json">Full motif evidence</a></p>'+body)
    print('Report:',out/'report.html');print('Evidence:',out/'motifs.json')
    return 2 if counts.get('failed') else 0
if __name__=='__main__':raise SystemExit(main())
