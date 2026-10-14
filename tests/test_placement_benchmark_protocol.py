import copy
import hashlib
import math
import unittest
from basis.placement_benchmark_protocol import ABSTAIN, FAILED, aggregate, blast_call, build_protocol, normalize_policy, placement_call, validate_pairwise_rows

def member(tip, sequence, group='A', role='amr'):
    return {'tip_id': tip, 'sequence': sequence, 'sequence_sha256': hashlib.sha256(sequence.encode()).hexdigest(), 'group': group, 'role': role, 'accessions': [tip]}

def panel():
    return {'panel_id': 'seed', 'panel_sha256': 'test-panel', 'members': [member('A1', 'A' * 100), member('A2', 'C' * 100), member('A3', 'D' * 100), member('A4', 'E' * 100), member('B1', 'F' * 100, 'B', 'alternative'), member('B2', 'G' * 100, 'B', 'alternative'), member('B3', 'H' * 100, 'B', 'alternative'), member('C1', 'I' * 100, None, 'context')]}

def hit(q='A1', r='A2', identity=80, qstart=1, qend=100, rstart=1, rend=100, qlen=100, rlen=100, bitscore=200):
    return {'query_id': q, 'reference_id': r, 'identity': identity, 'alignment_length': max(qend - qstart + 1, rend - rstart + 1), 'query_length': qlen, 'reference_length': rlen, 'query_start': qstart, 'query_end': qend, 'reference_start': rstart, 'reference_end': rend, 'evalue': 1e-20, 'bitscore': bitscore}

def training():
    return [m for m in panel()['members'] if m['tip_id'] != 'A1']

def result_row(query, method='placement', status='completed', group='A', truth='A', cluster=None):
    return {'query_id': query, 'method': method, 'status': status, 'predicted_group': group, 'true_group': truth, 'cluster_id': cluster or query, 'abstained': None if status == 'failed' else group is None}

class PlacementBenchmarkProtocolTests(unittest.TestCase):

    def test_label_blind_transitive_components_include_context_and_whole_holdout(self):
        p = panel()
        result = build_protocol(p, [hit('A1', 'C1', 72), hit('C1', 'A2', 72)])
        fold = next((f for f in result['folds'] if 'A1' in f['held_out_ids']))
        assert set(fold['held_out_ids']) == {'A1', 'A2', 'C1'}
        assert fold['query_ids'] == ['A1', 'A2']
        assert fold['status'] == 'ready'
        assert fold['training_group_counts'] == {'A': 2, 'B': 3}
        assert not set(fold['reference_ids']) & set(fold['held_out_ids'])
        assert result['audit']['clustering_uses_function_labels'] is False
        assert result['audit']['clade_disjointness'] == 'not_assessed'
        assert len(result['queries']) == 7
        assert 'C1' not in {q['query_id'] for q in result['queries']}
        assert all(('sequence' not in m for m in result['members']))

    def test_cluster_identity_independent_of_labels_order_and_tip_aliases(self):
        p = panel()
        rows = [hit('A1', 'C1', 72), hit('C1', 'A2', 72)]
        original = build_protocol(p, rows)
        swapped = copy.deepcopy(p)
        swapped['members'].reverse()
        for m in swapped['members']:
            if m['role'] == 'amr':
                m['group'], m['role'] = ('B', 'alternative')
            elif m['role'] == 'alternative':
                m['group'], m['role'] = ('A', 'amr')
        changed = build_protocol(swapped, list(reversed(rows)))
        assert [c['cluster_id'] for c in original['clusters']] == [c['cluster_id'] for c in changed['clusters']]
        assert original['policy_sha256'] == changed['policy_sha256']
        assert [f['fold_id'] for f in original['folds']] == [f['fold_id'] for f in changed['folds']]
        assert original['audit']['pairwise_rows_sha256'] == changed['audit']['pairwise_rows_sha256']

    def test_bilateral_coordinate_span_not_alignment_length_drives_clusters(self):
        row = hit(qend=79, rend=79)
        row['alignment_length'] = 100
        result = build_protocol(panel(), [row])
        assert result['audit']['qualifying_similarity_edges'] == []
        row['query_end'] = row['reference_end'] = 80
        assert build_protocol(panel(), [row])['audit']['qualifying_similarity_edges'] == [['A1', 'A2']]
        row['reference_end'] = 79
        assert build_protocol(panel(), [row])['audit']['qualifying_similarity_edges'] == []

    def test_primary_cluster_policy_and_explicit_gates_are_frozen(self):
        assert normalize_policy()['cluster_identity'] == 70
        assert normalize_policy()['cluster_coverage'] == 80
        assert build_protocol(panel(), [hit(identity=70)])['audit']['qualifying_similarity_edges']
        assert not build_protocol(panel(), [hit(identity=69.99)])['audit']['qualifying_similarity_edges']
        for policy in ({'min_anchors_per_group': 1}, {'min_training_references': 3}, {'placement_min_mass': float('nan')}, {'unknown': 3}, {'cluster_identity': True}, {'min_anchors_per_group': 2.5}):
            with self.assertRaises(ValueError):
                normalize_policy(policy)

    def test_connected_component_mixed_truth_and_insufficient_training_kept_as_hold(self):
        result = build_protocol(panel(), [hit('A1', 'A2'), hit('A2', 'B1'), hit('B1', 'B2')])
        fold = next((f for f in result['folds'] if 'A1' in f['held_out_ids']))
        assert fold['status'] == 'held'
        assert fold['reasons'] == ['insufficient_remaining_anchors:B']
        assert set(fold['query_ids']) == {'A1', 'A2', 'B1', 'B2'}
        assert fold['adequacy']['eligible'] is False
        assert len(result['queries']) == 7

    def test_duplicate_sequence_even_different_accession_or_conflicting_label_rejected(self):
        p = panel()
        p['members'].append(member('alias', 'A' * 100, 'B', 'alternative'))
        with self.assertRaisesRegex(ValueError, 'Duplicate panel sequence'):
            build_protocol(p, [])
        p = panel()
        p['members'][0]['sequence_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'checksum'):
            build_protocol(p, [])
        p = panel()
        p['members'][0]['label_conflict'] = True
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            build_protocol(p, [])

    def test_invalid_alignment_rows_are_not_silently_dropped(self):
        for key, value in [('query_id', 'missing'), ('reference_id', 'missing'), ('identity', 101), ('identity', float('nan')), ('identity', True), ('query_length', 101), ('reference_length', 99), ('query_start', 0), ('reference_end', 101), ('alignment_length', 99), ('bitscore', -1), ('evalue', float('inf')), ('query_end', 3.5)]:
            row = hit()
            row[key] = value
            with self.assertRaises(ValueError):
                build_protocol(panel(), [row])

    def test_reverse_coordinates_rejected_for_protein_hsp(self):
        row = hit()
        row['query_start'], row['query_end'] = (80, 20)
        with self.assertRaises(ValueError):
            validate_pairwise_rows([row], panel()['members'])

    def test_placement_research_rule_and_no_renormalization(self):
        call = placement_call({'group_support': {'A': 0.8, 'B': 0.1}, 'missing_mass': 0.1})
        assert call['predicted_group'] == 'A'
        assert call['function_probability'] is None
        assert placement_call({'group_support': {'A': 0.79, 'B': 0}})['abstained']
        assert placement_call({'group_support': {'A': 0.5, 'B': 0.5}})['reason'] == 'tied_placement_groups'
        assert placement_call({'group_support': {'A': 1}})['reason'] == 'fewer_than_two_scored_groups'
        result = placement_call({'group_support': {'A': 0.55, 'B': 0.45}}, {'placement_min_mass': 0.5})
        assert result['reason'] == 'placement_margin_below_threshold'
        with self.assertRaisesRegex(ValueError, 'exceeds one'):
            placement_call({'group_support': {'A': 0.8, 'B': 0.8}})
        with self.assertRaises(ValueError):
            placement_call({'group_support': {'A': float('nan')}})

    def test_blast_baseline_selects_best_eligible_training_hit_only(self):
        result = blast_call('A1', training(), [hit('A1', 'A1', 100, bitscore=1000), hit('A1', 'A2', 31, bitscore=100), hit('A1', 'B1', 90, bitscore=99)])
        assert result['predicted_group'] == 'A'
        assert result['metrics']['best_reference_ids'] == ['A2']
        row = hit('A1', 'A2', 90, rstart=32)
        row['alignment_length'] = 100
        assert blast_call('A1', training(), [row])['reason'] == 'no_eligible_blast_alignment'
        with self.assertRaisesRegex(ValueError, 'own training'):
            blast_call('A1', panel()['members'], [])

    def test_blast_tie_and_context_abstentions(self):
        result = blast_call('A1', training(), [hit('A1', 'A2'), hit('A1', 'A3')])
        assert result['predicted_group'] == 'A'
        assert len(result['metrics']['best_reference_ids']) == 2
        result = blast_call('A1', training(), [hit('A1', 'A2'), hit('A1', 'B1')])
        assert result['reason'] == 'best_bitscore_tie_across_groups_or_context'
        result = blast_call('A1', training(), [hit('A1', 'A2'), hit('A1', 'C1')])
        assert result['reason'] == 'best_bitscore_tie_across_groups_or_context'
        assert blast_call('A1', training(), [hit('A1', 'C1')])['reason'] == 'best_blast_reference_is_unlabelled_context'

    def test_blast_loose_discovery_hits_do_not_bypass_frozen_evalue_threshold(self):
        self.assertEqual(normalize_policy()["blast_max_evalue"], 1e-5)
        row = hit("A1", "A2", identity=90)
        row["evalue"] = 1e-4
        self.assertEqual(blast_call("A1", training(), [row])["reason"], "no_eligible_blast_alignment")
        row["evalue"] = 1e-5
        self.assertEqual(blast_call("A1", training(), [row])["predicted_group"], "A")
        row["evalue"] = float("nan")
        with self.assertRaises(ValueError):
            blast_call("A1", training(), [row])

    def test_execution_failure_is_not_a_biological_abstention(self):
        row = result_row("q1", status="failed", group=None)
        self.assertIsNone(row["abstained"])
        self.assertEqual(aggregate([row])["methods"]["placement"]["biological_abstained_count"], 0)
        row["abstained"] = True
        with self.assertRaisesRegex(ValueError, "not a biological abstention"):
            aggregate([row])

    def test_placement_rounding_tolerance_preserves_raw_weight_and_failed_score_refused(self):
        result = placement_call({"group_support": {"A": 1.00000001, "B": 0}})
        self.assertEqual(result["metrics"]["best_group_mass"], 1.00000001)
        with self.assertRaisesRegex(ValueError, "Only completed"):
            placement_call({"status": "failed", "group_support": {"A": .9, "B": .1}})

    def test_all_denominators_and_paired_subset_retain_abstentions_errors_and_holds(self):
        rows = [result_row('q1'), result_row('q2', group=None), result_row('q3', status='failed', group=None, truth='B'), result_row('q4', status='held', group=None, truth='B'), result_row('q1', 'blastp', group='B'), result_row('q2', 'blastp'), result_row('q3', 'blastp', group='B', truth='B'), result_row('q4', 'blastp', status='held', group=None, truth='B')]
        evaluation = aggregate(rows)
        m = evaluation['methods']['placement']
        assert (m['planned_count'], m['completed_count'], m['accepted_count']) == (4, 2, 1)
        assert (m['failed_count'], m['held_count'], m['completed_abstained_count']) == (1, 1, 1)
        assert m['biological_abstained_count'] == 2
        assert m['correct_fraction_all_planned'] == 0.25
        assert m['coverage_all_planned'] == 0.25
        assert m['accepted_accuracy'] == 1
        assert m['confusion']['B'] == {ABSTAIN: 1, FAILED: 1}
        assert m['per_group']['A']['recall_all_planned'] == 0.5
        pair = evaluation['paired_comparisons'][0]
        assert pair['query_ids'] == ['q1', 'q2']
        assert pair['metrics']['placement']['planned_count'] == 2
        assert pair['metrics']['placement']['completed_abstained_count'] == 1
        assert pair['excluded_planned_count'] == 2

    def test_aggregate_missing_duplicates_truth_drift_and_nonfinite_time_rejected(self):
        with self.assertRaisesRegex(ValueError, 'every planned'):
            aggregate([result_row('q1'), result_row('q2'), result_row('q1', 'blastp')])
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            aggregate([result_row('q1'), result_row('q1')])
        with self.assertRaisesRegex(ValueError, 'disagree'):
            aggregate([result_row('q1'), result_row('q1', 'blastp', truth='B')])
        row = result_row('q1')
        row['elapsed_seconds'] = math.inf
        with self.assertRaises(ValueError):
            aggregate([row])
        row = result_row('q1', status='failed')
        with self.assertRaisesRegex(ValueError, 'Only completed'):
            aggregate([row])

    def test_no_accepted_calls_yields_null_selective_accuracy_and_per_cluster_counts(self):
        result = aggregate([result_row('q1', group=None, cluster='c'), result_row('q2', group=None, cluster='c')])
        m = result['methods']['placement']
        assert m['accepted_accuracy'] is None
        assert m['correct_fraction_all_planned'] == 0
        assert m['per_cluster'] == {'c': {'completed_abstained': 2, 'planned': 2}}
        assert result['cluster_count'] == 1
        assert result['function_probability'] is None

    def test_empty_shared_completed_subset_is_explicit(self):
        result = aggregate([result_row('q1', status='failed', group=None), result_row('q1', 'blastp')])
        pair = result['paired_comparisons'][0]
        assert pair['query_count'] == 0
        assert pair['metrics']['placement']['correct_fraction_all_planned'] is None
