"""Reporting-only summaries; never infer function from database labels."""

def completion_summary(record):
    candidates = record.get('candidates', [])
    selected = [c for c in candidates if c.get('selected')]
    completed = sum(c.get('execution') == 'completed' for c in selected)
    return {'selected_candidates': len(selected), 'completed_selected_candidates': completed,
            'selected_execution': (record.get('execution_status') if not selected and record.get('execution_status') in {'failed', 'blocked', 'not_started'} else
                                   'not_requested' if not selected else
                                   'completed' if completed == len(selected) else 'incomplete'),
            'deferred_candidates': sum(c.get('execution') == 'deferred' for c in candidates),
            'upstream_incomplete': record.get('upstream_incomplete', []),
            'interpretation': 'Execution completion does not establish function or organism safety.'}


def tree_reference_summary(candidate):
    tree = candidate.get('analyses', {}).get('phylogeny', {})
    labels = tree.get('reference_labels')
    basis = 'recorded_native_label_map'
    if labels is None:
        labels = {}; seen = {}; basis = 'reconstructed_from_recorded_normalized_hashes'
        if tree.get('policy') != 'bounded_phylogeny_v1':
            return {'mapping_basis': 'unavailable_for_historical_review', 'runs': []}
        for ref in candidate.get('references', []):
            norm = tree.get('normalization', {}).get(ref.get('reference_id'), {})
            sha = norm.get('alignment_sequence_sha256')
            if not sha or norm.get('source_sha256') != ref.get('sequence_sha256'):
                return {'mapping_basis': 'unavailable_incomplete_hash_metadata', 'runs': []}
            if sha not in seen:
                seen[sha] = f'R{len(seen)+1:05d}'
            labels.setdefault(seen[sha], {'references': []})['references'].append(ref)
    runs = []
    for run in tree.get('runs', []):
        refs = []
        for tip in run.get('query_side', []):
            if tip == candidate.get('query', {}).get('id'): continue
            for ref in labels.get(tip, {}).get('references', []):
                refs.append({'tip': tip, 'reference_id': ref.get('reference_id'),
                             'accession': ref.get('accession'),
                             'database_label': ref.get('source_description') or ref.get('family'),
                             'review_status': ref.get('review_status'),
                             'comparison_group': ref.get('comparison_group')})
        runs.append({**run, 'query_side_references': refs})
    sides = [tuple(sorted(r.get('query_side', []))) for r in runs if r.get('execution') == 'completed']
    return {'mapping_basis': basis, 'split_consistent': len(set(sides)) == 1 if sides else None,
            'runs': runs, 'limitation': 'Database labels are annotations, not established query functions. Mapping does not independently verify tree files.'}
