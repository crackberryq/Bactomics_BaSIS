"""Deterministic reporting groups; no voting, functional inference or reclassification."""
import html,json
from .deep_reporting import tree_reference_summary

POLICY='Intervals are connected overlap groups, not independent evidence or physical-domain counts. Residues are grouped by query position and observed residue; all source conditions remain attached. No functional or safety verdict.'

def consolidate(candidate):
    analyses=candidate.get('analyses',{});observations=[];sites={};unmapped=[];unsupported=[]
    def add(source,label,start,end,status='detected'):
        if isinstance(start,int) and isinstance(end,int) and 1<=start<=end:
            observations.append(dict(source=source,label=label,start=start,end=end,status=status))
    for h in analyses.get('domains',{}).get('hits',[]):
        if h.get('passes_ga') is True:add('Pfam',h.get('domain_name'),h.get('query_start'),h.get('query_end'),'GA_accepted')
    for r in analyses.get('motifs',{}).get('rules',[]):
        for h in r.get('matches',[]):add('custom_motif',r.get('rule_id'),h.get('start'),h.get('end'),r.get('status'))
    for h in analyses.get('prosite_patterns',{}).get('hits',[]):add('PROSITE_pattern',h.get('accession'),h.get('start'),h.get('end'))
    for h in analyses.get('prosite_profiles',{}).get('hits',[]):
        add('PROSITE_profile',h.get('accession'),h.get('start'),h.get('end'),h.get('mapping_status'))
        for r in h.get('residue_rules',[]):
            for s in r.get('sites',[]):
                item={**s,'profile':h.get('accession'),'rule':r.get('rule_id'),'profile_interval':[h.get('start'),h.get('end')]}
                pos=s.get('query_position')
                if not isinstance(pos,int):unmapped.append(item);continue
                key=(pos,s.get('observed_residue'))
                row=sites.setdefault(key,{'query_position':pos,'observed_residue':s.get('observed_residue'),'conditions':[]})
                if item not in row['conditions']:row['conditions'].append(item)
            for condition in r.get('unsupported_conditions',[]):unsupported.append({'rule':r.get('rule_id'),'profile':h.get('accession'),'condition':condition})
    # Collapse identical observations only; repeat copies at different coordinates stay separate.
    unique={json.dumps(o,sort_keys=True):o for o in observations};groups=[]
    for o in sorted(unique.values(),key=lambda x:(x['start'],x['end'],str(x['label']),x['source'])):
        if not groups or o['start']>groups[-1]['end']:groups.append({'start':o['start'],'end':o['end'],'annotations':[]})
        groups[-1]['end']=max(groups[-1]['end'],o['end']);groups[-1]['annotations'].append(o)
    layers=[]
    for name in ('integrity','context','domains','motifs','prosite_patterns','prosite_profiles','phylogeny','family_placement'):
        a=analyses.get(name,{})
        layers.append({'layer':name,'status':a.get('status','not_assessed'),'reason':a.get('reason','not_recorded' if a else 'no_recorded_analysis')})
    refs=candidate.get('references',[])
    return {'schema':'basis-consolidated-v1','protein_id':candidate.get('protein_id'),
      'query_sha256':candidate.get('query',{}).get('sequence_sha256'),'selected':candidate.get('selected',False),
      'selection_queue':candidate.get('selection_queue'),'attention_rank':candidate.get('attention_rank'),
      'attention_max':candidate.get('attention_max'),'attention_routing_policy':candidate.get('attention_routing_policy'),
      'execution':candidate.get('execution'),'assessment':candidate.get('assessment',{}),'layers':layers,
      'overlap_groups':groups,'residue_observations':sorted(sites.values(),key=lambda x:(x['query_position'],str(x['observed_residue']))),
      'unmapped_residue_conditions':unmapped,'unsupported_conditions':unsupported,
      'reference_records':len(refs),'unique_reference_sequences':len({r['sequence_sha256'] for r in refs if isinstance(r,dict) and r.get('sequence_sha256')}),
      'review_notes':candidate.get('notes',[]),'tree_summary':tree_reference_summary(candidate),
      'family_placement':analyses.get('family_placement',{}),
      'full_prorule_evaluated':False,'function_established':False,'policy':POLICY}

def render_summary(summary):
    esc=lambda x:html.escape(str(x))
    def table(headers,rows):
        return '<table><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</table>'
    result='<h4>Consolidated candidate evidence</h4><p>'+esc(POLICY)+'</p>'
    result+=table(['Layer','Status','Reason'],[(r['layer'],r['status'],r['reason']) for r in summary['layers']])
    result+=table(['Query interval','Overlapping annotations'],[(str(g['start'])+'–'+str(g['end']),'; '.join(f"{a['source']}: {a['label']} ({a['start']}–{a['end']})" for a in g['annotations'])) for g in summary['overlap_groups']])
    result+=table(['Query position','Residue','Source conditions'],[(r['query_position'],r['observed_residue'],'; '.join(f"{c['rule']} / {c['profile']}: expected {c['expected_residues']}, {c['status']} ({c['feature']})" for c in r['conditions'])) for r in summary['residue_observations']])
    result+='<p>Unsupported conditions: '+str(len(summary['unsupported_conditions']))+'; unmapped residue conditions: '+str(len(summary['unmapped_residue_conditions']))+'. Full ProRule logic remains unevaluated.</p>'
    result+='<details><summary>Consolidated evidence and limitations</summary><pre>'+esc(json.dumps(summary,indent=2))+'</pre></details>'
    return result
