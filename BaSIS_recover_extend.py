#!/usr/bin/env python3
"""Recover a header-mapping failure from saved native outputs, without inference."""
import argparse,copy,html,json
from datetime import datetime,timezone
from pathlib import Path
from basis.adapters.extend import collect_outputs,_validate_reuse,_sha256
from basis.config import load_config
from basis.inputs import load_samples
from basis.execution import CommandRunner


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='study-isolate1/config.extend-validation.yaml')
    parser.add_argument('--sample',default='isolate1')
    args=parser.parse_args();cfg=load_config(args.config)
    sample=next(s for s in load_samples(cfg['samples']) if s['sample_id']==args.sample)
    root=Path(cfg['outdir'])/'results'/args.sample/'pathogenfinder2'
    source=root/'result.json';original=json.loads(source.read_text())
    if original.get('execution_status')!='failed' or not str(original.get('error','')).startswith('Attention protein is absent from input proteome:'):
        raise ValueError('Recovery is restricted to the observed attention-header mapping failure')
    mc=original['effective_config'];proteins=Path(sample['proteins']).resolve()
    if _sha256(proteins)!=original.get('input_hashes',{}).get('proteins'):
        raise ValueError('Input proteome changed since native execution')
    commands=original.get('provenance',{}).get('commands',[])
    matches=[c for c in commands if 'predict' in c.get('argv',[]) and c.get('returncode')==0]
    if len(matches)!=1:raise ValueError('Expected exactly one successful recorded native prediction')
    argv=matches[0]['argv']
    if Path(argv[argv.index('-i')+1]).resolve()!=proteins:raise ValueError('Recorded inference input differs')
    native=Path(argv[argv.index('-o')+1]).resolve();attempt=native.parent
    if not native.is_relative_to((root/'attempts').resolve()) or native.name!='native':
        raise ValueError('Native outputs are outside the recorded attempt tree')
    embeddings=native/'predictionPF2/preprocess_PF2/ProteinEmbeddings.h5'
    _validate_reuse(embeddings,attempt/'embeddings_manifest.json',proteins,mc['prot_t5_identity'])
    inputs=[source,attempt/'embeddings_manifest.json',embeddings,
            native/'predictionPF2/results_PF2/predictions.tsv',native/'predictionPF2/results_PF2/attentions.npz']
    hashes={str(p):_sha256(p) for p in inputs}
    out=Path(cfg['outdir'])/'recovery'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True,exist_ok=False)
    print('Verified original proteome and embedding checksums. Parsing saved predictions and attention; no GPU inference.',flush=True)
    runner=CommandRunner(mc,out/'commands.log')
    payload=collect_outputs(sample,mc,out,runner,native=native)
    if any(_sha256(Path(p))!=h for p,h in hashes.items()):raise ValueError('Source outputs changed during recovery')
    record=copy.deepcopy(original);record.pop('error',None);record.pop('cache_key',None);record.pop('artifact_checksums',None)
    record.update(payload,execution_status='completed',cache_hit=False,finished_at=datetime.now(timezone.utc).isoformat())
    record['recovery']={'source_result':str(source),'input_checksums':hashes,'method':'reparse_saved_native_outputs',
                        'inference_rerun':False,'parser_commands':runner.commands}
    record['artifacts']=payload['artifacts']+[str(out/'commands.log')]
    record['artifact_checksums']={p:_sha256(Path(p)) for p in record['artifacts'] if Path(p).is_file()}
    record['notes'].append('Recovered using corrected header normalization. Original failed result and native outputs preserved; no native-cache claim.')
    target=out/'result.json';target.write_text(json.dumps(record,indent=2)+'\n')
    esc=lambda x:html.escape(str(x))
    metrics=payload['metrics']
    rows=''.join('<tr><td>'+esc(f['protein_id'])+'</td><td>'+esc(f['attributes']['rank'])+'</td><td>'+esc(f['attributes']['attention_max'])+'</td></tr>' for f in payload['findings'])
    (out/'report.html').write_text('<!doctype html><meta charset="utf-8"><title>BaSIS Extend recovery</title><style>body{font:16px system-ui;margin:2em}td,th{padding:.5em;border:1px solid #ccc}pre{white-space:pre-wrap}</style><h1>Extend: recovered native results</h1><p>No inference rerun. Original outputs preserved. Model scores and attention rankings do not establish organism safety or protein virulence.</p><pre>'+esc(json.dumps(metrics,indent=2))+'</pre><table><tr><th>Protein</th><th>Attention rank</th><th>Maximum model attention</th></tr>'+rows+'</table><p><a href="result.json">Evidence</a></p>')
    print('Status: completed (recovered)')
    print('Model mean:',metrics['prediction_mean'],'| between-model SD:',metrics['prediction_std'])
    print('Model scores:',metrics['ensemble_member_scores'])
    print('Attention coverage:',metrics.get('attention_protein_count'),'/',metrics['input_protein_count'])
    print('Evidence:',target);print('Report:',out/'report.html')
if __name__=='__main__':main()
