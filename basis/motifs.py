"""Bounded, domain-gated motif observations; never a functional classifier."""
import hashlib
import json
import re
from pathlib import Path

AA = frozenset('ACDEFGHIKLMNPQRSTVWY')
DEFAULT_RULES = Path(__file__).parent / 'resources/motifs/rules-v1.json'

def compile_pattern(pattern):
    """A deliberately small syntax: residues, [sets], x, fixed repeats (n)."""
    if not isinstance(pattern, str) or len(pattern) > 1000:
        raise ValueError('Invalid motif pattern')
    positions = []
    for token in pattern.split('-'):
        match = re.fullmatch(r'(x|[ACDEFGHIKLMNPQRSTVWY]|\[[ACDEFGHIKLMNPQRSTVWY]+\])(?:\(([1-9][0-9]?)\))?', token)
        if not match: raise ValueError('Unsupported motif token: '+token)
        base, repeat = match.groups(); n = int(repeat or 1)
        allowed = AA if base == 'x' else frozenset(base.strip('[]'))
        positions.extend([allowed] * n)
    if not positions or len(positions) > 200: raise ValueError('Motif length must be 1..200')
    return positions

def load_rules(path=None):
    p = Path(path) if path else DEFAULT_RULES
    raw = p.read_bytes(); data = json.loads(raw)
    if data.get('schema_version') != 1 or not isinstance(data.get('rules'), list):
        raise ValueError('Unsupported motif registry schema')
    seen = set()
    for rule in data['rules']:
        key = rule.get('rule_id')
        if not isinstance(key,str) or not key or key in seen: raise ValueError('Duplicate/invalid motif rule ID')
        seen.add(key); compile_pattern(rule.get('pattern'))
        for field in ('domain_accessions','profile_ids','sources','limitations'):
            values = rule.get(field)
            if not isinstance(values,list) or any(not isinstance(v,str) or not v for v in values):
                raise ValueError('Invalid motif field: '+field)
        if not rule['domain_accessions'] or not rule['sources'] or not rule.get('evidence_basis'):
            raise ValueError('Rules require domain selectors, evidence basis and sources')
        if rule.get('function_established') is not False:
            raise ValueError('Motif rules cannot establish function')
        coverage = rule.get('minimum_model_coverage_for_absence')
        if isinstance(coverage,bool) or not isinstance(coverage,(float,int)) or not 0 < coverage <= 1:
            raise ValueError('Invalid domain completeness threshold')
    return data['rules'], {'path':str(p.resolve()), 'sha256':hashlib.sha256(raw).hexdigest(),
                           'registry_id':data.get('registry_id'), 'schema_version':1}

def assess_motifs(candidate, sequence, rules, registry, enabled=True):
    result = {'status':'not_assessed','reason':'candidate_deferred','rules':[],
              'registry':registry,'function_established':False,
              'interpretation':'Supporting sequence evidence only; no safety or resistance verdict.'}
    if not enabled: result['reason']='motifs_disabled'; return result
    if not candidate.get('selected'): return result
    analyses = candidate.get('analyses',{})
    domains = analyses.get('domains',{})
    hits = [h for h in domains.get('hits',[]) if h.get('passes_ga') is True]
    accessions = {str(h.get('domain_accession','')).split('.')[0] for h in hits}
    profile_id = candidate.get('profile',{}).get('profile_id')
    applicable = [r for r in rules if accessions.intersection(r['domain_accessions']) or profile_id in r['profile_ids']]
    if not applicable: result.update(status='no_applicable_rule',reason='registry_has_no_matching_rule'); return result
    if sequence is None: result['reason']='query_sequence_unavailable'; return result
    expected = candidate.get('query',{}).get('sequence_sha256')
    if not expected or hashlib.sha256(sequence.encode()).hexdigest()!=expected:
        result.update(status='failed',reason='query_hash_mismatch'); return result
    seq = sequence[:-1] if sequence.endswith('*') else sequence
    integrity = analyses.get('integrity',{})
    for rule in applicable:
        row = {k:rule[k] for k in ('rule_id','name','pattern','sources','evidence_basis','limitations') if k in rule}
        row.update(status='not_assessable',matches=[],domain_checks=[],function_established=False)
        positions = compile_pattern(rule['pattern'])
        targets = [h for h in hits if str(h.get('domain_accession','')).split('.')[0] in rule['domain_accessions']]
        for h in targets:
            start,end = h.get('query_start'),h.get('query_end')
            if not isinstance(start,int) or not isinstance(end,int) or not 1<=start<=end<=len(seq):
                row['domain_checks'].append({'status':'invalid_domain_coordinates'});continue
            region = seq[start-1:end]
            matches=[]
            for i in range(len(region)-len(positions)+1):
                word=region[i:i+len(positions)]
                if all(a in allowed for a,allowed in zip(word,positions)):
                    matches.append({'start':start+i,'end':start+i+len(positions)-1,'sequence':word,
                                    'domain_accession':h['domain_accession']})
            row['matches'].extend(matches)
            hs,he,ml = h.get('hmm_start'),h.get('hmm_end'),h.get('model_length')
            coverage = (he-hs+1)/ml if all(isinstance(x,int) for x in (hs,he,ml)) and 1<=hs<=he<=ml else None
            assessable = (coverage is not None and coverage>=rule['minimum_model_coverage_for_absence']
                          and all(a in AA for a in region) and '*' not in seq
                          and integrity.get('status')=='completed' and not integrity.get('flags'))
            row['domain_checks'].append({'start':start,'end':end,'model_coverage':coverage,
                'status':'present' if matches else 'not_detected_in_assessed_region' if assessable else 'not_assessable',
                'absence_assessable':assessable})
        if row['matches']: row['status']='present'
        elif row['domain_checks'] and all(d['status']=='not_detected_in_assessed_region' for d in row['domain_checks']):
            row['status']='not_detected_in_assessed_region'
        row['reason'] = 'domain_gated_sequence_observation' if targets else 'required_GA_domain_unavailable'
        result['rules'].append(row)
    result.update(status='completed',reason='applicable_rules_evaluated')
    return result
