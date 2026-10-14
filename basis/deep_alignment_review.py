"""Descriptive alignment checks, not a validated functional classifier."""
from collections import Counter

def anchor_summary(anchor):
    rows=anchor.get('alignment_rows', [])
    passed=[r for r in rows if r.get('passes_retrieval_filters')]
    rejected=[r for r in rows if not r.get('passes_retrieval_filters')]
    reasons=sorted({reason for r in rejected for reason in r.get('filter_reasons',[])})
    text=f"{anchor['reference_id']} | {anchor['status']} | selected: {anchor.get('selected',False)}"
    text+=f" | passing alignments: {len(passed)} | rejected alignments: {len(rejected)}"
    if reasons: text+=' | rejected-alignment reasons: '+','.join(reasons)
    return text

def comparison_gaps(candidate):
    groups={r.get('comparison_group') for r in candidate.get('references',[]) if r.get('review_status')=='reviewed'}
    return sorted(set(candidate['profile'].get('required_comparison_groups',[]))-groups)

def alignment_metrics(submitted, aligned, query_id):
    if not submitted or set(submitted)!=set(aligned): raise ValueError('Alignment identifiers changed')
    lengths={len(s) for s in aligned.values()}
    if len(lengths)!=1 or not next(iter(lengths)): raise ValueError('Invalid alignment row lengths')
    for key,seq in submitted.items():
        if aligned[key].replace('-','').replace('.','')!=seq: raise ValueError('Alignment altered sequence: '+key)
    gap={'-','.'}; canonical=set('ACDEFGHIKLMNPQRSTVWY')
    columns=list(zip(*aligned.values())); n=len(aligned)
    occupancy=[sum(x not in gap for x in col)/n for col in columns]
    informative=0
    for col in columns:
        counts=Counter(x for x in col if x in canonical)
        informative+=sum(v>=2 for v in counts.values())>=2
    qcols=[i for i,x in enumerate(aligned[query_id]) if x not in gap]
    per_ref={key:sum(seq[i] not in gap for i in qcols)/len(qcols)
             for key,seq in aligned.items() if key!=query_id}
    return {'sequence_count':n,'distinct_sequences':len(set(submitted.values())),
            'alignment_columns':len(columns),'gap_fraction':1-sum(occupancy)/len(columns),
            'columns_occupancy_ge_80pct':sum(x>=0.8 for x in occupancy),
            'parsimony_informative_columns_canonical_only':informative,
            'reference_coverage_of_query_residues':per_ref,
            'note':'Descriptive metrics on an untrimmed alignment; 80% occupancy is a reporting band, not a pass threshold. No tree-readiness or functional verdict.'}


def normalize_alignment_sequence(sequence):
    """Remove one terminal stop marker only; refuse internal/repeated stops."""
    import hashlib
    normalized=sequence[:-1] if sequence.endswith('*') else sequence
    if not normalized or '*' in normalized:
        raise ValueError('Empty protein or internal/repeated stop marker; alignment held')
    if any(x not in 'ACDEFGHIKLMNPQRSTVWYBXZJUO' for x in normalized):
        raise ValueError('Invalid unaligned protein character; alignment held')
    sha=lambda s:hashlib.sha256(s.encode('ascii')).hexdigest()
    return normalized, {'policy':'remove_single_terminal_stop_marker_only',
        'terminal_stop_removed':sequence.endswith('*'), 'source_length':len(sequence),
        'alignment_length':len(normalized),'source_sha256':sha(sequence),
        'alignment_sequence_sha256':sha(normalized)}
