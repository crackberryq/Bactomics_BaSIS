#!/usr/bin/env python3
"""Two bounded sensitivity runs for the reviewed isolate1 Cfr-family alignment."""
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from basis.references import read_fasta, write_fasta
from basis.execution import CommandRunner

EXPECTED_SHA='d86f73b34f534b586abeb7b63b87f505e0ce1467b6aa16cb0b981c34651241a7'

def occupancy_mask(sequences):
    if not sequences or len({len(s) for s in sequences.values()})!=1:
        raise ValueError('Alignment must have equal nonempty rows')
    n=len(sequences)
    # Integer arithmetic: at least 80% nongap occupancy, irrespective of residues or tree.
    keep=[i for i,col in enumerate(zip(*sequences.values())) if 5*sum(x not in '-.' for x in col)>=4*n]
    masked={k:''.join(s[i] for i in keep) for k,s in sequences.items()}
    if not keep or any(not s.strip('-.') for s in masked.values()):
        raise ValueError('Mask removed all usable sites from an alignment row')
    return masked,keep

def main():
    root=Path(__file__).resolve().parent
    baseline=root/'study-isolate1/cfr-exploratory-trees/20261008T185831465804Z'
    source=baseline/'alignment.faa'
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    if digest!=EXPECTED_SHA: raise ValueError('Baseline alignment differs from reviewed input')
    for name in ['cfr.treefile','cfr.iqtree','labels.json','run.json']:
        if not (baseline/name).is_file():raise ValueError('Missing baseline artifact: '+name)
    sequences=read_fasta(source)
    masked,keep=occupancy_mask(sequences)
    if len(sequences)!=18 or len(next(iter(sequences.values())))!=417 or len(keep)!=343:
        raise ValueError('Unexpected dimensions for the reviewed panel')
    out=root/'study-isolate1/cfr-sensitivity'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True,exist_ok=False)
    original=out/'baseline';original.mkdir()
    for name in ['alignment.faa','cfr.treefile','cfr.iqtree','labels.json','run.json','cfr.log']:
        if (baseline/name).is_file():shutil.copy2(baseline/name,original/name)
    report={'baseline':str(baseline),'source_alignment_sha256':digest,'runs':[],
            'mask':{'rule':'nongap occupancy >=80%; fixed before sensitivity results',
                    'retained_columns_1based':[i+1 for i in keep],'original_columns':417,'retained_columns':len(keep)},
            'interpretation':'Post-baseline exploratory sensitivity; no functional verdict. All taxa retained, no outgroup imposed. Do not select only the highest-support result.'}
    (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    prefix=['conda','run','--no-capture-output','-p',str(root/'.basis-envs/basis-deepsafe')]
    failed=False
    for name,seqs,model in [('full_alternative_model',sequences,'LG+I+G4'),('occupancy80_model_selection',masked,'MFP')]:
        work=out/name;work.mkdir();alignment=work/'alignment.faa';write_fasta(seqs,alignment)
        runner=CommandRunner({'command_prefix':prefix,'threads':2,'timeout_seconds':7200},work/'execution.log')
        entry={'name':name,'requested_model':model,'columns':len(next(iter(seqs.values()))),
               'alignment_sha256':hashlib.sha256(alignment.read_bytes()).hexdigest(),'status':'running'}
        report['runs'].append(entry)
        try:
            print(f'Running {name}: {entry["columns"]} columns; model {model}',flush=True)
            runner.run(['iqtree','-s',str(alignment),'-st','AA','-m',model,'-B','1000','-alrt','1000',
                        '-bnni','-nt','2','-seed','42','-pre',str(work/'tree')])
            tree=(work/'tree.treefile').read_text().strip()
            tips=re.findall(r'(?:\(|,)\s*([^\s:(),;]+)\s*:',tree)
            if not tree.endswith(';') or set(tips)!=set(seqs) or len(tips)!=len(seqs):
                raise ValueError('Tree tips differ from submitted alignment')
            iq=(work/'tree.iqtree').read_text()
            selected=re.search(r'Model of substitution:\s*(\S+)',iq)
            entry.update(status='completed',selected_model=selected.group(1) if selected else 'inspect_report',tree=tree)
            print('Completed:',name,'| model:',entry['selected_model'],flush=True)
            print('Tree:',tree,flush=True)
        except (OSError,RuntimeError,ValueError) as exc:
            failed=True;entry.update(status='failed',error=str(exc));print('ERROR:',exc,flush=True)
        finally:
            entry['commands']=runner.commands
            (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    archive=shutil.make_archive(str(out)+'-review','zip',out)
    print('\nUpload ZIP:',archive,flush=True)
    print('Both sensitivity runs completed.' if not failed else 'At least one run failed; retain its diagnostics.',flush=True)
    return 1 if failed else 0

if __name__=='__main__':raise SystemExit(main())
