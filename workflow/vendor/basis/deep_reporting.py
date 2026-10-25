"""Reporting-only summaries; never infer function from database labels."""


def parent_execution(record):
    """Read recorded aliases without assuming missing parents succeeded."""
    value = record.get('execution_status', record.get('execution', record.get('status')))
    if isinstance(value, dict):
        value = value.get('status')
    return str(value) if value is not None else 'not_recorded'


def effective_execution(recorded, *parents):
    """Qualify retained completion; leave recorded failures and holds intact."""
    if recorded == 'completed' and any(parent != 'completed' for parent in parents):
        return 'provisional'
    return recorded


def qualify_completion_summary(saved, execution):
    """Keep raw child counts beside their parent-qualified completion state."""
    result = dict(saved)
    recorded = saved.get('recorded_selected_execution', saved.get('selected_execution', 'not_recorded'))
    selected = saved.get('selected_candidates')
    completed = saved.get('completed_selected_candidates')
    result.update(parent_execution=execution, recorded_selected_execution=recorded,
                  selected_execution=effective_execution(recorded, execution),
                  accepted_completed_selected_candidates=completed if execution == 'completed' else 0,
                  provisional_completed_selected_candidates=0 if execution == 'completed' else completed)
    if selected == 0 and execution in {'failed', 'blocked', 'not_started'}:
        result['selected_execution'] = execution
    return result

def completion_summary(record):
    candidates = record.get('candidates', [])
    selected = [c for c in candidates if c.get('selected')]
    completed = sum(c.get('execution') == 'completed' for c in selected)
    saved = {'selected_candidates': len(selected), 'completed_selected_candidates': completed,
            'selected_execution': ('not_requested' if not selected else
                                   'completed' if completed == len(selected) else 'incomplete'),
            'deferred_candidates': sum(c.get('execution') == 'deferred' for c in candidates),
            'upstream_incomplete': record.get('upstream_incomplete', []),
            'interpretation': 'Execution completion does not establish function or organism safety.'}
    return qualify_completion_summary(saved, parent_execution(record))


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
