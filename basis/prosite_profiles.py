"""PROSITE matrix scanning and conservative ProRule residue observations.

This is NOT a complete ProRule interpreter. It maps simple single-position
residue conditions; it never executes functional annotation or case logic.
"""
import json,math,re
from pathlib import Path
from .prosite import entries,load_snapshot
from .references import read_fasta,write_fasta,sequence_hash
from .deep_alignment_review import normalize_alignment_sequence
from .util import sha256_file

LIMITATION=('Profile matches and residue-condition observations are exploratory. Full ProRule case logic, '
 'taxonomic scope, fusion/repeat/size requirements and functional annotation are not evaluated. '
 'Neither matched nor mismatched residues establish activity, resistance or safety.')

def load_profile_snapshot(directory):
    directory=Path(directory).resolve();_,provenance=load_snapshot(directory)
    manifest=json.loads((directory/'provenance.json').read_text())
    rulepath=directory/'prorule.dat'
    if sha256_file(rulepath)!=manifest.get('sha256',{}).get('prorule.dat'):raise ValueError('ProRule checksum mismatch')
    rules_text=rulepath.read_text()
    release=re.search(r'Release (\d{4}_\d+)',rules_text)
    if not release or release.group(1)!=provenance['release']:raise ValueError('ProRule release mismatch')
    profiles={}
    for ac,block in entries((directory/'prosite.dat').read_text()):
        if not re.search(r'^ID   .*; MATRIX\.',block,re.M):continue
        length=re.search(r'^MA   /GENERAL_SPEC:.*LENGTH=(\d+);',block,re.M)
        if not length:raise ValueError('Profile length unavailable: '+ac)
        profiles[ac]={'accession':ac,'name':re.search(r'^ID   ([^;]+)',block,re.M).group(1),
          'length':int(length.group(1)),'description':' '.join(re.findall(r'^DE   (.*)',block,re.M)),
          'documentation_ids':re.findall(r'^DO   (PDOC\d+);',block,re.M),
          'rule_ids':re.findall(r'PRU\d+', ' '.join(re.findall(r'^PR   (.*)',block,re.M))),'source_url':'https://prosite.expasy.org/'+ac}
    rules={}
    for block in rules_text.split('\n//'):
        ac=re.search(r'^AC   (PRU\d+);',block,re.M)
        if not ac:continue
        predictors=re.findall(r'^TR   PROSITE; (PS\d+);',block,re.M)
        features=[];current=None;origin=None
        for line in block.splitlines():
            if line.startswith('FT   From:'):
                if current:features.append(current);current=None
                origin=line.split(':',1)[1].strip();continue
            feature=re.match(r'^FT   ([A-Z][A-Z_]*)\s+(\S+)\s*$',line)
            if feature:
                if current:features.append(current)
                current={'feature':feature.group(1),'position_expression':feature.group(2),'profile':origin,'raw':[line], 'conditions':[],'groups':[]}
            elif current and line.startswith('FT'):
                current['raw'].append(line)
                cond=re.search(r'Condition: (.*)',line)
                if cond:current['conditions'].append(cond.group(1).strip())
                group=re.search(r'Group: ([^;]+)',line)
                if group:current['groups'].append(group.group(1).strip())
        if current:features.append(current)
        rules[ac.group(1)]={'rule_id':ac.group(1),'predictors':predictors,'features':features,
          'version':(re.search(r'^# Version: (.*)',block,re.M).group(1) if re.search(r'^# Version: (.*)',block,re.M) else None),
          'source_url':'https://prosite.expasy.org/rule/'+ac.group(1)}
    provenance.pop('excluded_profiles',None)
    provenance.update(pattern_only=False,profile_count=len(profiles),prorule_count=len(rules),limitation=LIMITATION)
    return profiles,rules,provenance

def alignment_map(alignment,query,start,end,profile_length,motif_start,motif_end):
    """PSA uppercase=match, lowercase=insertion, -=deletion; full length required."""
    if not re.fullmatch(r'[A-Za-z-]+',alignment):raise ValueError('Unsupported PSA alignment characters')
    if not 1<=start<=end<=len(query):raise ValueError('Query coordinates out of range')
    if re.sub('[^A-Za-z]','',alignment).upper()!=query[start-1:end]:raise ValueError('PSA/query sequence mismatch')
    if sum(ch.isupper() or ch=='-' for ch in alignment)!=profile_length:raise ValueError('PSA does not span full profile coordinate system')
    last=profile_length+motif_end+1
    if not (motif_end<0 and 1<=motif_start<=last<=profile_length):raise ValueError('Invalid profile bounds')
    pp=0;qp=start-1;mapping={}
    for ch in alignment:
        if ch.isalpha():qp+=1
        if ch.isupper() or ch=='-':
            pp+=1
            mapping[pp]={'query_position':qp if ch!='-' else None,'residue':ch if ch!='-' else None,
                         'within_reported_profile_span':motif_start<=pp<=last}
    if qp!=end:raise ValueError('PSA query span mismatch')
    return mapping

def parse_xpsa(path,queries,profiles):
    blocks=[];header=None;seq=[]
    for line in Path(path).read_text().splitlines():
        if not line.strip():continue
        if line.startswith('>'):
            if header:blocks.append((header,''.join(seq)))
            header=line;seq=[]
        elif header:seq.append(line.strip())
        else:raise ValueError('Unexpected profile output before header')
    if header:blocks.append((header,''.join(seq)))
    hits={q:[] for q in queries}
    for header,alignment in blocks:
        match=re.match(r'^>(\S+)/(\d+)-(\d+)\s+',header)
        if not match:raise ValueError('Invalid profile output header')
        q,start,end=match.group(1),int(match.group(2)),int(match.group(3))
        fields=dict(re.findall(r'(\w+)=(\S+)',header));ac=fields.get('motif','').split('|')[0]
        if q not in queries or ac not in profiles:raise ValueError('Unknown profile or query')
        level=int(fields['level']);score=float(fields['norm_score']);raw=float(fields['raw_score'])
        if level<0 or not all(math.isfinite(x) for x in (score,raw)):raise ValueError('Invalid or below-threshold profile score')
        if fields.get('motif_rev')!='F' or fields.get('strand')!='+':raise ValueError('Unexpected profile orientation')
        hit={**profiles[ac],'start':start,'end':end,'normalized_score':score,'raw_score':raw,'level':level,
             'alignment':alignment,'native_header':header,'function_established':False}
        try:
            hit['position_map']=alignment_map(alignment,queries[q],start,end,profiles[ac]['length'],int(fields['motif_start']),int(fields['motif_end']))
            hit['mapping_status']='completed'
        except (ValueError,KeyError) as exc:
            hit.update(mapping_status='not_assessable',mapping_reason=str(exc),position_map={})
        hits[q].append(hit)
    return hits

def evaluate_sites(hit,rules):
    results=[]
    for rid in hit['rule_ids']:
        rule=rules.get(rid)
        if not rule:
            results.append({'rule_id':rid,'status':'rule_unavailable','sites':[]});continue
        sites=[];unsupported=[]
        for feature in rule['features']:
            if not feature['conditions']:continue
            # No implicit 'any' profile mapping or ranged condition inference.
            valid=(feature['profile']==hit['accession'] and re.fullmatch(r'[1-9]\d*',feature['position_expression'])
                and len(feature['conditions'])==1 and re.fullmatch(r'[ACDEFGHIKLMNPQRSTVWY]|\[[ACDEFGHIKLMNPQRSTVWY]+\]',feature['conditions'][0]))
            if not valid:
                unsupported.append(feature);continue
            pos=int(feature['position_expression']);allowed=feature['conditions'][0].strip('[]')
            mapped=hit['position_map'].get(pos,{})
            residue=mapped.get('residue')
            if not mapped or not mapped.get('within_reported_profile_span'):status='position_not_assessable'
            elif residue is None:status='deleted_in_query'
            elif residue not in 'ACDEFGHIKLMNPQRSTVWY':status='ambiguous_residue'
            elif residue in allowed:status='condition_satisfied'
            else:status='condition_not_satisfied'
            sites.append({'feature':feature['feature'],'profile_position':pos,'expected_residues':allowed,
                'query_position':mapped.get('query_position'),'observed_residue':residue,'status':status,
                'groups':feature['groups'],'source_feature':feature['raw']})
        results.append({'rule_id':rid,'rule_version':rule['version'],'source_url':rule['source_url'],
          'status':'partial_supported_conditions_assessed' if unsupported else 'supported_conditions_assessed' if sites else 'no_supported_residue_conditions',
          'sites':sites,'unsupported_conditions':unsupported,'full_rule_evaluated':False,'function_established':False})
    return results

def run_profiles(candidates,config,outdir,runner):
    for c in candidates:
        c.setdefault('analyses',{})['prosite_profiles']={'status':'not_assessed','reason':'candidate_deferred' if not c.get('selected') else 'profiles_not_enabled','function_established':False}
    if not config.get('prosite_profiles'):return []
    profiles,rules,provenance=load_profile_snapshot(config['prosite_snapshot'])
    queries={};owners={};normalization={}
    for c in candidates:
        if not c.get('selected'):continue
        query=c.get('query',{})
        if c.get('execution') in {'failed','not_assessed'} or not query.get('path'):
            c['analyses']['prosite_profiles']['reason']='candidate_not_ready';continue
        data=read_fasta(Path(query['path']))
        if set(data)!={query['id']} or sequence_hash(data[query['id']])!=query['sequence_sha256']:raise ValueError('Profile query integrity mismatch')
        if query['id'] in queries:raise ValueError('Duplicate profile query ID')
        sequence,norm=normalize_alignment_sequence(data[query['id']]);queries[query['id']]=sequence;normalization[query['id']]=norm;owners[query['id']]=c
    if not queries:return []
    work=Path(outdir).resolve()/'prosite_profiles';work.mkdir(parents=True,exist_ok=False)
    fasta=work/'queries.faa';output=work/'profiles.xpsa';write_fasta(queries,fasta)
    argv=[config.get('executables',{}).get('pfscan','pfscanV3'),'--matrix-only','-L','0','-t',str(int(config.get('threads',1))),'-o','6',str(Path(config['prosite_snapshot']).resolve()/'prosite.dat'),str(fasta)]
    runner.run(argv,stdout_path=output,cwd=work)
    hits=parse_xpsa(output,queries,profiles)
    for q,c in owners.items():
        for hit in hits[q]:hit['residue_rules']=evaluate_sites(hit,rules)
        c['analyses']['prosite_profiles']={'status':'completed','reason':'profiles_detected' if hits[q] else 'no_profile_detected',
            'hits':hits[q],'hit_count':len(hits[q]),'provenance':provenance,'normalization':normalization[q],
            'function_established':False,'limitation':LIMITATION}
    result=work/'scan.json';result.write_text(json.dumps({'provenance':provenance,'command':argv,'hits':hits,'normalization':normalization},indent=2)+'\n')
    return [str(fasta),str(output),str(result)]
