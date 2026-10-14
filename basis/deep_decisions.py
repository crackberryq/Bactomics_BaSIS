"""Conservative reporting decisions, separate from native execution success."""
import math

def assess_candidate(candidate, followup=None):
    execution=candidate.get('execution','not_assessed')
    refs=candidate.get('references',[])
    groups={r.get('comparison_group') for r in refs if r.get('review_status')=='reviewed'}
    missing=sorted(set(candidate.get('profile',{}).get('required_comparison_groups',[]))-groups)
    warnings=list(candidate.get('notes',[]))
    taxon=candidate.get('taxonomy_review',{})
    if taxon.get('status')=='review_required':warnings.append('taxonomic_scope_review_required')
    warnings += ['missing_reviewed_comparison_group:'+g for g in missing]
    interpretation='not_assessed';action='not_scheduled';reason=execution
    if execution=='failed':action='review_execution_failure'
    elif execution=='completed':
        interpretation='unresolved';action='review_existing_evidence';reason='no_validated_functional_classifier'
        if missing:action='hold_for_comparator_review';reason='missing_reviewed_comparison_groups'
        if followup:
            runs=followup.get('runs',[])
            if not followup.get('provenance_verified'):
                reason='unverified_followup';action='review_followup_provenance'
            else:
                warnings.extend(followup.get('warnings',[]))
                def weak(r):
                    values=[r.get('sh_alrt'),r.get('ufboot')]
                    return (all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and 0<=x<=100 for x in values)
                            and (values[0]<80 or values[1]<95))
                if runs and all(r.get('execution')=='completed' and weak(r) for r in runs):
                    interpretation='subgroup_unresolved';reason='weak_support_across_recorded_runs'
                    if followup.get('budget_exhausted') is True:
                        action='stop_automatic_tree_followup'
                elif followup.get('budget_exhausted') is True and runs and all(r.get('execution')=='completed' and all(isinstance(r.get(k),(int,float)) and math.isfinite(r[k]) and 0<=r[k]<=100 for k in ['sh_alrt','ufboot']) for r in runs):
                    interpretation='subgroup_unresolved' if followup.get('split_consistent') is False else 'phylogenetic_evidence_requires_review'
                    reason='bounded_followup_requires_interpretation'
                    action='stop_automatic_tree_followup'
                else:
                    reason='tree_interpretation_requires_review'
                    # Strong support alone never establishes function.
                    action='review_existing_evidence'
                if missing:action='hold_for_comparator_review'
        elif candidate.get('analyses',{}).get('phylogeny',{}).get('status')=='completed':
            reason='tree_support_and_sensitivity_not_assessed'
    phy=candidate.get('analyses',{}).get('phylogeny',{})
    if execution=='completed' and not followup and phy.get('reason'):
        reason=phy['reason']
        if reason=='taxonomic_scope_review_required':action='hold_for_taxonomic_review'
        elif reason=='tree_candidate_budget_exhausted':action='deferred_by_tree_budget'
        elif reason in {'sequence_integrity_review_required','overlapping_domain_models_require_review','no_GA_qualified_domain_evidence','profile_not_source_reviewed','comparison_groups_not_defined'}:action='hold_for_evidence_review'
    return {'execution':execution,'interpretation':interpretation,'followup':action,'reason':reason,
            'missing_comparison_groups':missing,'warnings':list(dict.fromkeys(warnings)),
            'function_established':False,
            'policy':'deep_decisions_v1; support thresholds are reporting heuristics, not a validated functional classifier'}
