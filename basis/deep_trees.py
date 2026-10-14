"""Bounded exploratory tree workflow. No functional classifications are inferred."""
import hashlib
import json
import math
import re
from pathlib import Path
from .references import read_fasta,write_fasta
from .deep_alignment_review import normalize_alignment_sequence,alignment_metrics,comparison_gaps

POLICY='bounded_phylogeny_v1'

def parse_splits(text, expected, query):
    """Parse IQ-TREE Newick with opaque BaSIS IDs; evaluate unrooted splits."""
    tokens=re.findall(r'[(),:;]|[^\s(),:;]+',text)
    position=0;nodes=[];tips=[]
    def node():
        nonlocal position
        children=[];name=''
        if tokens[position]=='(':
            position+=1;children.append(node())
            while tokens[position]==',':position+=1;children.append(node())
            if tokens[position]!=')':raise ValueError('Malformed tree')
            position+=1
            if tokens[position] not in [',',')',':',';']:name=tokens[position];position+=1
        else:
            name=tokens[position];position+=1;tips.append(name)
        if tokens[position]==':':
            position+=1;length=float(tokens[position]);position+=1
            if not math.isfinite(length) or length<0:raise ValueError('Invalid branch length')
        leaves=set().union(*(x[0] for x in children)) if children else {name}
        if children:nodes.append((leaves,name))
        return leaves,name
    try:
        node()
        if tokens[position:]!=[';'] or len(tips)!=len(set(tips)) or set(tips)!=set(expected):
            raise ValueError('Tree tip set differs from alignment')
    except (IndexError,TypeError) as exc:raise ValueError('Malformed tree') from exc
    all_tips=set(tips);splits=[]
    for side,label in nodes:
        if query not in side:side=all_tips-side
        if not 2<=len(side)<=len(all_tips)-2:continue
        values=label.split('/')
        support={}
        if len(values)==2:
            try:
                a,b=map(float,values)
                if all(math.isfinite(x) and 0<=x<=100 for x in [a,b]):support={'sh_alrt':a,'ufboot':b}
            except ValueError:pass
        splits.append({'query_side':sorted(side),**support})
    # Smallest nontrivial query-containing split; no rooting or nearest-tip claim.
    return min(splits,key=lambda x:(len(x['query_side']),x['query_side'])) if splits else {'query_side':[]}

def mask_alignment(sequences):
    n=len(sequences)
    keep=[i for i,col in enumerate(zip(*sequences.values())) if 5*sum(x not in '-.' for x in col)>=4*n]
    return {k:''.join(seq[i] for i in keep) for k,seq in sequences.items()},keep

def eligible(candidate):
    if not candidate['analysis_plan']['analyses'].get('phylogeny'):return 'disabled_by_config_or_profile'
    if candidate.get('profile',{}).get('validation_status')!='reviewed':return 'profile_not_source_reviewed'
    if not candidate['profile'].get('required_comparison_groups'):return 'comparison_groups_not_defined'
    if comparison_gaps(candidate):return 'missing_reviewed_comparison_groups'
    if candidate.get('taxonomy_review',{}).get('status')=='review_required':return 'taxonomic_scope_review_required'
    integrity=candidate.get('analyses',{}).get('integrity',{})
    if integrity.get('flags'):return 'sequence_integrity_review_required'
    domains=candidate.get('analyses',{}).get('domains',{})
    if not any(h.get('passes_ga') for h in domains.get('hits',[])):return 'no_GA_qualified_domain_evidence'
    if domains.get('architecture',{}).get('ambiguous_region_count',0):return 'overlapping_domain_models_require_review'
    return None

def run_bounded(candidate,catalog,config,directory,runner,budget):
    result={'status':'not_assessed','policy':POLICY,'runs':[],'artifacts':[],
            'note':'All trees are exploratory and unrooted; support is not functional evidence.'}
    reason=eligible(candidate)
    if reason:result['reason']=reason;return result
    maximum=int(config.get('maximum_tree_candidates',6))
    if maximum<0:raise ValueError('maximum_tree_candidates must be >=0')
    if budget['used']>=maximum:result['reason']='tree_candidate_budget_exhausted';return result
    query=candidate['query'];qid=query['id']
    original=read_fasta(Path(query['path']))[qid]
    if hashlib.sha256(original.encode()).hexdigest()!=query['sequence_sha256']:raise ValueError('Query changed before alignment')
    qs,qn=normalize_alignment_sequence(original)
    sequences={qid:qs};labels={qid:{'role':'query'}};normalization={qid:qn};seen={}
    for ref in candidate['references']:
        rid=ref['reference_id'];raw=catalog.sequences[rid]
        if hashlib.sha256(raw.encode()).hexdigest()!=ref['sequence_sha256']:raise ValueError('Reference changed before alignment')
        seq,normalization[rid]=normalize_alignment_sequence(raw)
        if seq in seen:labels[seen[seq]]['references'].append(ref);continue
        key=f'R{len(seen)+1:05d}';seen[seq]=key;sequences[key]=seq;labels[key]={'references':[ref]}
    if len(set(sequences.values()))<4:result['reason']='fewer_than_four_distinct_sequences';return result
    budget['used']+=1
    work=directory/'bounded_trees';work.mkdir(exist_ok=True)
    result.update(input=str(work/'input.faa'),labels=str(work/'labels.json'),normalization=normalization)
    write_fasta(sequences,Path(result['input']))
    result['reference_labels'] = labels
    Path(result['labels']).write_text(json.dumps(labels,indent=2)+'\n')
    artifacts=result['artifacts'];artifacts.extend([result['input'],result['labels']])
    executables=config.get('executables',{});threads=str(int(config.get('threads',1)))
    followup={'provenance_verified':True,'budget_exhausted':False,'runs':result['runs'],'warnings':[],
              'scope':'Smallest nontrivial query-containing unrooted split; no family label inferred.'}
    result['followup_evidence']=followup
    try:
        alignment=work/'alignment.faa'
        runner.run([executables.get('mafft','mafft'),'--auto','--thread',threads,result['input']],stdout_path=alignment)
        aligned=read_fasta(alignment);result['alignment_metrics']=alignment_metrics(sequences,aligned,qid)
        result['alignment']=str(alignment);artifacts.append(str(alignment))
        def tree_run(name,seqs,model):
            folder=work/name;folder.mkdir(exist_ok=True);fasta=folder/'alignment.faa';write_fasta(seqs,fasta)
            prefix=folder/'tree'
            row={'name':name,'execution':'running','requested_model':model,'columns':len(next(iter(seqs.values()))),
                 'alignment_sha256':hashlib.sha256(fasta.read_bytes()).hexdigest()}
            result['runs'].append(row)
            try:
                runner.run([executables.get('iqtree','iqtree'),'-s',str(fasta),'-st','AA','-m',model,
                            '-B','1000','-alrt','1000','-bnni','-nt',threads,'-seed',str(int(config.get('phylogeny_seed',42))),'-pre',str(prefix)])
                tree=Path(str(prefix)+'.treefile');report=Path(str(prefix)+'.iqtree');log=Path(str(prefix)+'.log')
                focal=parse_splits(tree.read_text(),seqs,qid)
                row.update(execution='completed',**focal,tree=str(tree),tree_sha256=hashlib.sha256(tree.read_bytes()).hexdigest())
                row['reference_groups_on_query_side']=sorted({ref.get('comparison_group','unassigned')
                     for key in focal['query_side'] if key!=qid for ref in labels[key]['references']})
                text=report.read_text();match=re.search(r'Model of substitution:\s*(\S+)',text)
                row['selected_model']=match.group(1) if match else None
                warnings=[line.strip() for line in log.read_text().splitlines() if ('failed' in line.lower() or 'WARNING:' in line) and 'Normalizing state frequencies' not in line]
                row['warnings']=warnings;followup['warnings'].extend(name+': '+x for x in warnings)
                artifacts.extend(str(p) for p in [fasta,tree,report,log])
                return text
            except (OSError,ValueError,RuntimeError) as exc:
                row.update(execution='failed',error=str(exc));raise
        report=tree_run('baseline',aligned,'MFP');result['tree']=result['runs'][0]['tree']
        models=[]
        for line in report.splitlines():
            fields=line.split()
            if len(fields)!=11:continue
            try:bic=float(fields[8])
            except ValueError:continue
            if re.fullmatch(r'[A-Za-z0-9.+_-]+',fields[0]):models.append((fields[0],bic))
        models.sort(key=lambda x:x[1]);selected=result['runs'][0]['selected_model']
        alt=next((model for model,bic in models if model!=selected and bic-models[0][1]<=2),None) if models else None
        result['alternative_model_policy']='One alternative within 2 BIC units; at most one run'
        if alt:tree_run('alternative_model',aligned,alt)
        else:result['alternative_model_skipped']='no_near_tied_model_identified'
        masked,keep=mask_alignment(aligned)
        result['mask']={'rule':'nongap occupancy >=80%; fixed before sensitivity trees','retained_columns_1based':[i+1 for i in keep]}
        if 50<=len(keep)<len(next(iter(aligned.values()))) and len(set(masked.values()))>=4 and all(s.strip('-.') for s in masked.values()):
            tree_run('occupancy80',masked,'MFP')
        else:result['mask_skipped']='unchanged_or_insufficient_alignment_after_mask'
        followup['budget_exhausted']=True
        followup['split_consistent']=len({tuple(r.get('query_side',[])) for r in result['runs']})==1
        result.update(status='completed',reason='bounded_followup_completed')
    except (ValueError,RuntimeError,OSError) as exc:
        result.update(status='failed',reason=str(exc));followup['provenance_verified']=False
    manifest=work/'followup.json';artifacts.append(str(manifest))
    manifest.write_text(json.dumps(result,indent=2)+'\n')
    return result

def reuse_review(candidate, config):
    registry=config.get('tree_review_registry')
    if not registry:return None
    records=json.loads(Path(registry).read_text()).get('records',[])
    for row in records:
        if row.get('sample_id')!=candidate.get('sample_id') or row.get('protein_id')!=candidate.get('protein_id'):continue
        if row['query_sha256']!=candidate['query']['sequence_sha256']:continue
        current=sorted((r['reference_id'],r['sequence_sha256']) for r in candidate['references'])
        if current!=[tuple(x) for x in row['references']]:continue
        if sorted(candidate['profile'].get('required_comparison_groups',[]))!=row['required_groups']:continue
        for path,digest in row['artifacts'].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest:
                raise ValueError('Previously reviewed follow-up artifact changed: '+path)
        evidence=row['followup']
        if evidence.get('provenance_verified') is not True or evidence.get('budget_exhausted') is not True:continue
        return {'status':'completed','reason':'reused_verified_review','review_source':'historical_reviewed_evidence',
                'followup_evidence':evidence,'artifacts':list(row['artifacts']),
                'note':'Imported reviewed follow-up, not a current-source native cache.'}
    return None
