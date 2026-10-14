"""Local official PROSITE pattern scan. Matches remain exploratory evidence."""
import hashlib,json,re
from pathlib import Path
from .references import read_fasta,write_fasta,sequence_hash
from .deep_alignment_review import normalize_alignment_sequence
from .util import sha256_file

FILES=('prosite.dat','prosite.aux','prosite.doc','ps_reldt.txt','ps_scan.pl')
LIMITATION=('Pattern-only scan: profiles, miniprofile validation and ProRule/post-processing are not run. '
            'A curated pattern match does not establish query function, pathogenicity or resistance. '
            'No match is not evidence of safety or loss of function.')

def entries(text):
    for block in text.split('\n//'):
        match=re.search(r'^AC   (PS\d+);',block,re.M)
        if match:yield match.group(1),block

def load_snapshot(directory):
    directory=Path(directory).resolve();manifest=json.loads((directory/'provenance.json').read_text())
    if manifest.get('schema')!='basis-prosite-snapshot-v1':raise ValueError('Invalid PROSITE manifest')
    texts={}
    for name in FILES:
        path=directory/name
        if sha256_file(path)!=manifest.get('sha256',{}).get(name):raise ValueError('PROSITE checksum mismatch: '+name)
        texts[name]=path.read_text(encoding='utf-8')
    for name in FILES[:4]:
        m=re.search(r'Release (\d{4}_\d+)',texts[name])
        if not m or m.group(1)!=manifest.get('release'):raise ValueError('PROSITE release mismatch: '+name)
    auxiliary={ac:b for ac,b in entries(texts['prosite.aux'])}
    patterns={};profiles=0;skipped=0
    for ac,b in entries(texts['prosite.dat']):
        if not re.search(r'^ID   .*; PATTERN\.',b,re.M):profiles+=1;continue
        skip='/SKIP-FLAG=TRUE' in b
        skipped+=int(skip)
        doc=re.search(r'^DO   (PDOC\d+);',b,re.M)
        if not doc or '{'+doc.group(1)+'}' not in texts['prosite.doc']:
            raise ValueError('Pattern documentation missing: '+ac)
        aux=auxiliary.get(ac,'')
        tax=re.search(r'/TAXO-RANGE=([^;]+)',aux or b)
        patterns[ac]={'accession':ac,'name':re.search(r'^ID   ([^;]+)',b,re.M).group(1),
            'description':' '.join(re.findall(r'^DE   (.*)',b,re.M)),
            'pattern':''.join(re.findall(r'^PA   (.*)',b,re.M)),
            'skip_flag':skip,'documentation_id':doc.group(1),
            'documentation_url':'https://prosite.expasy.org/'+doc.group(1),
            'source_url':'https://prosite.expasy.org/'+ac,
            'taxonomy_range_as_recorded':tax.group(1) if tax else None,
            'database_validation_statistics':re.findall(r'^NR   (.*)',aux,re.M),
            'database_validation_scope':'PROSITE reference-database statistics, not BaSIS query confidence',
            'auxiliary_record_available':bool(aux)}
    if not patterns:raise ValueError('No PROSITE patterns')
    provenance={'directory':str(directory),'release':manifest['release'],'sha256':manifest['sha256'],
                'eligible_patterns':len(patterns)-skipped,'excluded_frequent_patterns':skipped,
                'excluded_profiles':profiles,'pattern_only':True,'limitation':LIMITATION}
    return patterns,provenance

def parse_gff(path,queries,patterns):
    hits={q:[] for q in queries}
    for number,line in enumerate(Path(path).read_text().splitlines(),1):
        if not line.strip() or line.startswith('#'):continue
        fields=line.split('\t')
        if len(fields)!=9:raise ValueError(f'Invalid ps_scan GFF row {number}')
        q,tool,ac,start,end,score,strand,frame,attributes=fields
        if q not in queries or ac not in patterns or not tool.startswith('ps_scan|v'):
            raise ValueError('Unknown query, pattern or tool in PROSITE GFF')
        if patterns[ac]['skip_flag']:raise ValueError('Excluded frequent pattern returned')
        start,end=int(start),int(end)
        if not 1<=start<=end<=len(queries[q]):raise ValueError('PROSITE coordinates outside query')
        native=re.search(r'(?:^| ; )Sequence "([^"]*)"',attributes)
        sequence=queries[q][start-1:end]
        if not native or native.group(1).replace('.', '').upper()!=sequence:raise ValueError('PROSITE matched sequence mismatch')
        hits[q].append({**patterns[ac],'start':start,'end':end,'sequence':sequence,'native_tool':tool,
                        'native_attributes':attributes,'contains_ambiguous_residues':any(a not in 'ACDEFGHIKLMNPQRSTVWY' for a in sequence),
                        'interpretation':'exploratory_pattern_match','function_established':False})
    return hits

def run_prosite(candidates,config,outdir,runner):
    for c in candidates:
        c.setdefault('analyses',{})['prosite_patterns']={'status':'not_assessed',
            'reason':'candidate_deferred' if not c.get('selected') else 'prosite_snapshot_not_configured',
            'function_established':False}
    snapshot=config.get('prosite_snapshot')
    if not snapshot:return []
    patterns,provenance=load_snapshot(snapshot)
    queries={};owners={};normalization={}
    for c in candidates:
        if not c.get('selected'):continue
        result=c['analyses']['prosite_patterns']
        if c.get('execution') in {'failed','not_assessed'}:
            result['reason']='candidate_not_ready';continue
        query=c.get('query',{})
        if not query.get('path') or not query.get('id'):
            result['reason']='query_sequence_unavailable';continue
        records=read_fasta(Path(query['path']))
        if set(records)!={query['id']}:raise ValueError('PROSITE query IDs differ from saved evidence')
        sequence=records[query['id']]
        if sequence_hash(sequence)!=query.get('sequence_sha256'):raise ValueError('PROSITE query checksum mismatch')
        if query['id'] in queries:raise ValueError('Duplicate PROSITE query ID')
        sequence,norm=normalize_alignment_sequence(sequence)
        queries[query['id']]=sequence;owners[query['id']]=c;normalization[query['id']]=norm
    if not queries:return []
    work=Path(outdir).resolve()/'prosite_patterns';work.mkdir(parents=True,exist_ok=False)
    fasta=work/'queries.faa';gff=work/'patterns.gff';write_fasta(queries,fasta)
    argv=[config.get('executables',{}).get('perl','perl'),str(Path(snapshot).resolve()/'ps_scan.pl'),
          '-r','-s','--nopp','-x','0','-o','gff','-d',str(Path(snapshot).resolve()/'prosite.dat'),str(fasta.resolve())]
    runner.run(argv,stdout_path=gff,cwd=work)
    hits=parse_gff(gff,queries,patterns)
    for q,c in owners.items():
        domain_hits=c.get('analyses',{}).get('domains',{}).get('hits',[])
        for h in hits[q]:
            h['overlapping_GA_domains']=[{'accession':d.get('domain_accession'),'name':d.get('domain_name')}
                for d in domain_hits if d.get('passes_ga') is True and d.get('query_start',10**9)<=h['end'] and d.get('query_end',0)>=h['start']]
        c['analyses']['prosite_patterns']={'status':'completed','reason':'patterns_detected' if hits[q] else 'no_pattern_detected',
            'hits':hits[q],'hit_count':len(hits[q]),'provenance':provenance,'normalization':normalization[q],
            'taxonomy_applicability':'not_automatically_validated','function_established':False,'limitation':LIMITATION}
    result=work/'scan.json';result.write_text(json.dumps({'provenance':provenance,'command':argv,'hits':hits,'normalization':normalization},indent=2)+'\n')
    return [str(fasta),str(gff),str(result)]
