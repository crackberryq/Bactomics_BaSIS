import csv
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from basis.catalog import from_fasta
from basis.cli import compare_reports
from basis.config import load_config
from basis.demo import create_demo
from basis.inputs import validate_sample
from basis.normalize import normalize_result
from basis.pipeline import execute_module, make_plan
from basis.references import load_catalog
from basis.util import atomic_json, read_json


class RootSafeguards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.path = create_demo(self.directory)
        self.config = load_config(self.path)
        self.plan = make_plan(self.config, False, False)
        self.sample = self.plan['samples'][0]
        self.validation = self.plan['validations'][self.sample['sample_id']]

    def tearDown(self):
        self.tmp.cleanup()

    def test_stale_plan_rejected_even_when_cache_exists(self):
        execute_module(self.plan, self.sample, 'amrfinder')
        Path(self.sample['assembly']).write_text(Path(self.sample['assembly']).read_text() + '\n')
        result = execute_module(self.plan, self.sample, 'amrfinder')
        self.assertEqual(result['execution_status'], 'failed')
        self.assertIn('changed after planning', result['error'])

    def test_modified_artifact_invalidates_cache(self):
        first = execute_module(self.plan, self.sample, 'amrfinder')
        self.assertFalse(first['cache_hit'])
        self.assertTrue(execute_module(self.plan, self.sample, 'amrfinder')['cache_hit'])
        log = Path(first['artifacts'][0])
        log.write_text('altered native evidence')
        result = execute_module(self.plan, self.sample, 'amrfinder')
        self.assertFalse(result['cache_hit'])

    def test_unlocalized_wrong_contig_is_rejected(self):
        payload = {'findings': [{'category': 'ice', 'contig': 'another_sample', 'start': None, 'end': None}], 'interpretation': 'detected'}
        with self.assertRaisesRegex(ValueError, 'unresolved contig'):
            normalize_result(payload, 'icescreen', self.sample, self.validation)

    def test_context_incomplete_contig_inventory_is_rejected(self):
        payload = {'findings': [], 'interpretation': 'not_detected', 'metrics': {'contig_ids': []}}
        with self.assertRaisesRegex(ValueError, 'every input contig'):
            normalize_result(payload, 'mobsuite', self.sample, self.validation)

    def test_invalid_imported_pf2_metrics_are_rejected(self):
        payload = {'findings': [], 'interpretation': 'not_detected', 'metrics': {'prediction_mean': 99, 'prediction_std': -1}}
        with self.assertRaisesRegex(ValueError, 'PathogenFinder2'):
            normalize_result(payload, 'pathogenfinder2', self.sample, self.validation)

    def test_pf2_import_ensemble_is_checked(self):
        payload = {'findings': [], 'interpretation': 'not_detected', 'metrics': {'prediction_mean': .5, 'prediction_std': 0., 'ensemble_member_scores': [.1]*4}}
        with self.assertRaisesRegex(ValueError, 'ensemble mean'):
            normalize_result(payload, 'pathogenfinder2', self.sample, self.validation)

    def test_resource_limits_apply_before_run(self):
        self.config['execution']['memory_mb'] = 1
        with self.assertRaisesRegex(ValueError, 'memory'):
            make_plan(self.config, False, False)

    def test_invalid_configuration_types(self):
        value = yaml.safe_load(self.path.read_text())
        value['execution']['cores'] = 0
        self.path.write_text(yaml.safe_dump(value))
        with self.assertRaisesRegex(ValueError, 'positive integer'):
            load_config(self.path)

    def test_same_ids_wrong_protein_sequence_is_rejected(self):
        path = Path(self.sample['proteins'])
        path.write_text(path.read_text().replace('MAAAAA', 'MVAAAA'))
        with self.assertRaises(ValueError):
            validate_sample(self.sample)

    def test_genbank_aliases_are_sequence_verified(self):
        from basis.inputs import read_fasta
        sequence = next(iter(read_fasta(self.sample['assembly']).values()))
        genbank = self.directory/'alias.gbk'
        genbank.write_text('LOCUS       altname\nACCESSION   ALT1\nVERSION     ALT1.1\nORIGIN\n        1 '+sequence.lower()+'\n//\n')
        sample = {**self.sample, 'genbank': str(genbank)}
        validation = validate_sample(sample)
        payload = {'findings': [{'category': 'ice', 'contig': 'ALT1.1', 'start': None, 'end': None}], 'interpretation': 'detected'}
        result = normalize_result(payload, 'icescreen', sample, validation)
        self.assertEqual(result['findings'][0]['contig'], 'DEMO_A_chr')

    def test_report_comparison_exposes_failed_nohit_and_model_changes(self):
        old = {'run': {}, 'records': [{'sample_id': 'A', 'module': 'vfdb', 'execution_status':'completed', 'interpretation':'not_detected', 'findings': []}], 'deep_records': []}
        new = {'run': {}, 'records': [{'sample_id': 'A', 'module': 'vfdb', 'execution_status':'failed', 'interpretation':'inconclusive', 'findings': []}], 'deep_records': []}
        a,b,out = [self.directory/x for x in ('old.json','new.json','change.json')]
        atomic_json(a,old); atomic_json(b,new)
        result=compare_reports(a,b,out)
        self.assertEqual(result['record_changes'],1)
        self.assertEqual(result['added'],0)

    def test_reference_fasta_conversion_never_invents_reviewed_labels(self):
        source = self.directory/'db.faa'
        source.write_text('>sp|P12345|TEST Example GN=demo\nMAAAA\n')
        fasta, metadata = self.directory/'refs.faa',self.directory/'refs.tsv'
        result=from_fasta(source,fasta,metadata,'Example release')
        catalog=load_catalog(fasta,metadata)
        row=catalog.metadata['sp|P12345|TEST']
        self.assertEqual(row['review_status'],'unreviewed')
        self.assertEqual(row['family'],'demo')
        self.assertEqual(row['comparison_group'],'unknown')
        self.assertEqual(result['reference_count'],1)


if __name__ == '__main__':
    unittest.main()
