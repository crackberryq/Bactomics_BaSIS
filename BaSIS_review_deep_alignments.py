#!/usr/bin/env python3
"""Read existing DeepSafe output; align panels with represented comparison groups. No trees."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from basis.deep_alignment_review import anchor_summary, comparison_gaps, alignment_metrics, normalize_alignment_sequence
from basis.references import load_catalog, read_fasta, write_fasta, sequence_hash
from basis.config import load_config
from basis.execution import CommandRunner

def main():
    root=Path(__file__).resolve().parent
    config=load_config(root/'study-isolate1/config.deep-anchor-validation.yaml')
    source=Path(config['outdir'])/'results/isolate1/deepsafe/result.json'
    content=source.read_bytes(); record=json.loads(content)
    if record.get('execution_status')!='completed': raise ValueError('Source DeepSafe execution is not completed')
    catalog=load_catalog(Path(config['deep']['reference_fasta']),Path(config['deep']['reference_metadata']))
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out=root/'study-isolate1/deep-alignment-review'/stamp; out.mkdir(parents=True,exist_ok=False)
    prefix=['conda','run','--no-capture-output','-p',str(root/'.basis-envs/basis-deepsafe')]
    runner=CommandRunner({'command_prefix':prefix,'threads':2,'timeout_seconds':3600},out/'mafft.log')
    report={'source':str(source),'source_sha256':hashlib.sha256(content).hexdigest(),
            'trees_run':False,'candidates':[],
            'scope':'Existing evidence imported. MAFFT only; no core, domain or reference searches rerun. Panel coverage does not validate family classification.'}
    runner.run(['mafft','--version'],stdout_path=out/'mafft-version.txt')
    failed=False
    for c in record.get('candidates',[]):
        if not c.get('selected'): continue
        print('\n'+c['protein_id'],flush=True)
        for a in c.get('analyses',{}).get('anchor_review',{}).get('anchors',[]): print(' ',anchor_summary(a),flush=True)
        gaps=comparison_gaps(c)
        item={'protein_id':c['protein_id'],'candidate_id':c['candidate_id'],'missing_groups':gaps,
              'taxonomy_review':c.get('taxonomy_review',{}),'status':'not_assessed'}
        report['candidates'].append(item)
        try:
            if c.get('execution')!='completed': raise ValueError('Candidate source execution incomplete')
            if gaps:
                item['reason']='missing_reviewed_comparison_groups'
                print(' HOLD: missing groups:',', '.join(gaps),flush=True)
                continue
            query=c['query']; qid=query['id']; qs=read_fasta(Path(query['path']))[qid]
            if sequence_hash(qs)!=query['sequence_sha256']: raise ValueError('Query hash mismatch')
            qs,query_normalization=normalize_alignment_sequence(qs)
            normalization={qid:query_normalization}
            seqs={qid:qs}; labels={qid:{'role':'query'}}; seen={}
            for ref in c['references']:
                rid=ref['reference_id']; seq=catalog.sequences[rid]
                if sequence_hash(seq)!=ref['sequence_sha256']: raise ValueError('Reference hash mismatch: '+rid)
                seq,normalization[rid]=normalize_alignment_sequence(seq)
                if seq in seen:
                    labels[seen[seq]]['references'].append(ref); continue
                label=f'R{len(seen)+1:05d}'; seen[seq]=label
                seqs[label]=seq; labels[label]={'references':[ref]}
            item['normalization']=normalization
            print(' Terminal stop markers normalized:',sum(x['terminal_stop_removed'] for x in normalization.values()),flush=True)
            item['distinct_sequences']=len(set(seqs.values()))
            if item['distinct_sequences']<4:
                item['reason']='fewer_than_four_distinct_sequences'; print(' HOLD:',item['reason'],flush=True); continue
            work=out/c['candidate_id']; work.mkdir()
            fasta=work/'input.faa'; aligned=work/'alignment.faa'
            write_fasta(seqs,fasta)
            (work/'labels.json').write_text(json.dumps(labels,indent=2)+'\n')
            print(' MAFFT: comparative alignment only; no tree.',flush=True)
            runner.run(['mafft','--auto','--thread','2',str(fasta)],stdout_path=aligned)
            metrics=alignment_metrics(seqs,read_fasta(aligned),qid)
            item.update(status='alignment_prepared_for_review',metrics=metrics,alignment=str(aligned),
                        alignment_sha256=hashlib.sha256(aligned.read_bytes()).hexdigest(),
                        input_sha256=hashlib.sha256(fasta.read_bytes()).hexdigest())
            print(' Columns:',metrics['alignment_columns'],'| gaps:',round(100*metrics['gap_fraction'],1),'%',
                  '| informative columns:',metrics['parsimony_informative_columns_canonical_only'],flush=True)
            print(' Taxon review:',item['taxonomy_review'].get('status','unknown'),flush=True)
        except (ValueError,RuntimeError,OSError,KeyError) as exc:
            failed=True;item.update(status='failed',reason=str(exc));print(' ERROR:',exc,flush=True)
        finally:
            report['commands']=runner.commands
            (out/'review.json').write_text(json.dumps(report,indent=2)+'\n')
    print('\nReview:',out/'review.json')
    print('Trees remain OFF. Alignment and reference interpretation still require review.')
    return 1 if failed else 0

if __name__=='__main__': raise SystemExit(main())
