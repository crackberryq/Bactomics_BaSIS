"""Failure collection must not manufacture execution of unscheduled analyses."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.bootstrap import bootstrap_audit, receipt_path
from basis.config import load_config
from basis.demo import create_demo
from basis.pipeline import collect_workflow_evidence, make_plan, record_path, render_workflow_report, run_worker
from basis.run_summary import format_run_summary, write_run_summary
from basis.scheduler_state import mark_started
from basis.util import atomic_json
from basis.workflow_runtime import _write


class SchedulerFailureStatesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.plan = make_plan(load_config(create_demo(self.temp.name)), True, True)
        self.root = Path(self.plan['outdir']) / 'provenance/scheduler/test-run'
        self.run = {'run_id': self.root.name, 'engine': 'snakemake',
                    'options': copy.deepcopy(self.plan['options']),
                    'module_decisions': copy.deepcopy(self.plan['module_decisions'])}

    def tearDown(self):
        self.temp.cleanup()

    def collect(self, error='Scheduler failed'):
        return collect_workflow_evidence(self.plan, copy.deepcopy(self.run), 'snakemake', self.root, error)

    def one(self, bundle, module='amrfinder'):
        rows = bundle['deep_records'] if module == 'deepsafe' else bundle['records']
        return next(r for r in rows if r['sample_id'] == 'DEMO_A' and r['module'] == module)

    def bootstrap_specs(self):
        self.plan['bootstrap_resources'] = [
            {'id': 'card', 'targets': ['rgi'], 'dependencies': [], 'cache_key': 'card-cache'},
            {'id': 'deep_catalogue', 'targets': ['deepsafe'], 'dependencies': ['card'], 'cache_key': 'deep-cache'},
        ]

    def test_conda_solver_failure_before_any_receipt_blocks_dependants_without_false_analysis_failure(self):
        self.bootstrap_specs()
        bundle = self.collect('Conda environment creation failed: solver conflict; see snakemake.log')
        self.assertEqual(bundle['run']['completeness'], 'incomplete')
        self.assertEqual(self.one(bundle, 'rgi')['execution_status'], 'blocked')
        self.assertEqual(self.one(bundle, 'rgi')['reason'], 'resource_preparation_incomplete')
        self.assertIn('solver conflict', self.one(bundle, 'rgi')['error'])
        self.assertEqual(self.one(bundle, 'amrfinder')['execution_status'], 'not_started')
        self.assertEqual(self.one(bundle, 'deepsafe')['execution_status'], 'blocked')
        self.assertEqual(bundle['summary']['samples'][0]['deepsafe']['selected_execution'], 'blocked')
        self.assertTrue(all(r['status'] == 'not_started' for r in bundle['run']['resource_preparation']))
        artifacts = render_workflow_report(bundle, Path(self.temp.name) / 'render')
        html = Path(artifacts['html']).read_text()
        self.assertIn('solver conflict', html)
        self.assertIn('Execution status', html)
        self.assertIn('DeepSafe blocked', html)
        self.assertNotIn('unavailable — DeepSafe execution failed', html)
        self.assertIn('blocked', format_run_summary(bundle['summary']))

    def test_current_independent_completion_and_real_failure_survive_missing_other_jobs(self):
        run_worker(self.plan['plan_path'], 'DEMO_A', 'amrfinder', receipt=str(self.root / 'DEMO_A/amrfinder.json'))
        source = Path(self.plan['config']['modules']['rgi']['result'].replace('{sample_id}', 'DEMO_A'))
        original = json.loads(source.read_text())
        original['sample_id'] = 'WRONG_SAMPLE'
        atomic_json(source, original)
        run_worker(self.plan['plan_path'], 'DEMO_A', 'rgi', receipt=str(self.root / 'DEMO_A/rgi.json'))
        bundle = self.collect()
        self.assertEqual(self.one(bundle)['execution_status'], 'completed')
        self.assertEqual(self.one(bundle, 'rgi')['execution_status'], 'failed')
        self.assertEqual(self.one(bundle, 'vfdb')['execution_status'], 'not_started')
        self.assertEqual(self.one(bundle, 'deepsafe')['execution_status'], 'blocked')
        self.assertNotIn('amrfinder', self.one(bundle, 'deepsafe')['upstream_incomplete'])
        counts = bundle['summary']['samples'][0]['requested_modules']
        self.assertEqual((counts['completed'], counts['failed']), (1, 1))
        exports = write_run_summary(bundle['summary'], Path(self.temp.name) / 'summary')
        tsv = Path(exports['summary_tsv']).read_text()
        self.assertIn('blocked_modules', tsv)
        self.assertIn('not_started_modules', tsv)

    def test_interrupted_worker_start_is_failed_not_blocked(self):
        self.bootstrap_specs()
        mark_started(self.plan, self.root, 'DEMO_A', 'rgi')
        result = self.one(self.collect(), 'rgi')
        self.assertEqual(result['execution_status'], 'failed')
        self.assertEqual(result['reason'], 'scheduler_worker_incomplete')
        self.assertTrue(result['blocked_resources'])

    def test_invalid_final_attestation_is_failed_even_with_resource_blockers(self):
        self.bootstrap_specs()
        atomic_json(self.root / 'DEMO_A/rgi.json', {'schema': 'invalid'})
        result = self.one(self.collect(), 'rgi')
        self.assertEqual(result['execution_status'], 'failed')
        self.assertEqual(result['reason'], 'scheduler_attestation_invalid')

    def test_interrupted_retry_cannot_reuse_previous_same_run_completion(self):
        run_worker(self.plan['plan_path'], 'DEMO_A', 'amrfinder', receipt=str(self.root / 'DEMO_A/amrfinder.json'))
        self.assertEqual(self.one(self.collect())['execution_status'], 'completed')
        mark_started(self.plan, self.root, 'DEMO_A', 'amrfinder')
        result = self.one(self.collect())
        self.assertEqual(result['execution_status'], 'failed')
        self.assertIn('earlier worker attempt', result['error'])

    def test_invalid_start_attestation_is_failed(self):
        mark_started(self.plan, self.root, 'DEMO_A', 'amrfinder')
        path = self.root / 'DEMO_A/started/amrfinder.json'
        value = json.loads(path.read_text())
        value['run_id'] = 'another-run'
        atomic_json(path, value)
        result = self.one(self.collect())
        self.assertEqual(result['execution_status'], 'failed')
        self.assertEqual(result['reason'], 'scheduler_start_attestation_invalid')

    def test_failed_input_gate_blocks_workers_but_invalid_gate_is_not_benign(self):
        path = self.root / 'DEMO_A/input-validation.json'
        value = {'schema': 'basis-workflow-input-check-v1', 'run_id': self.root.name,
                 'plan_hash': self.plan['plan_hash'], 'source_hash': self.plan['source_hash'],
                 'sample_id': 'DEMO_A', 'status': 'failed', 'error': 'inputs changed'}
        _write(path, value)
        result = self.one(self.collect())
        self.assertEqual(result['execution_status'], 'blocked')
        self.assertEqual(result['reason'], 'input_validation_failed')
        atomic_json(path, value)
        result = self.one(self.collect())
        self.assertEqual(result['execution_status'], 'failed')
        self.assertEqual(result['reason'], 'scheduler_gate_attestation_invalid')

    def test_interrupted_resource_is_failed_and_propagates_real_root_cause(self):
        self.bootstrap_specs()
        _write(receipt_path(self.root, 'card').with_suffix('.started.json'), {
            'schema': 'basis-bootstrap-start-v1', 'run_id': self.root.name,
            'plan_hash': self.plan['plan_hash'], 'source_hash': self.plan['source_hash'],
            'resource_id': 'card', 'cache_key': 'card-cache', 'status': 'started', 'started_at': 'test-time',
        })
        audit = {r['resource_id']: r for r in bootstrap_audit(self.plan, self.root)}
        self.assertEqual(audit['card']['status'], 'failed')
        self.assertEqual(audit['deep_catalogue']['status'], 'blocked')
        self.assertIn('did not commit', audit['deep_catalogue']['root_causes'][0]['error'])
        bundle = self.collect()
        self.assertEqual(self.one(bundle, 'rgi')['execution_status'], 'blocked')
        self.assertEqual(self.one(bundle, 'rgi')['reason'], 'resource_preparation_failed')


if __name__ == '__main__':
    unittest.main()
