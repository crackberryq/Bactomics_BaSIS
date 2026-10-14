"""Public preflight reporting behavior with artificial metadata and inputs."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from basis.cli import main
from basis.config import default_config


class PreflightCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for suffix, content in (
            ('fna', '>ctg\nATGAAATAA\n'), ('faa', '>p1\nMK\n'),
            ('gff', '##gff-version 3\nctg\tfixture\tCDS\t1\t9\t.\t+\t0\tID=p1;transl_table=11\n'),
        ):
            (self.root / ('sample.' + suffix)).write_text(content)
        (self.root / 'samples.tsv').write_text(
            'sample_id\tassembly\tproteins\tgff\tinput_mode\n'
            'sample\tsample.fna\tsample.faa\tsample.gff\texternal\n')
        cfg = default_config()
        cfg.update(samples='samples.tsv', outdir='unused-results')
        for value in cfg['modules'].values():
            value['enabled'] = False
        self.config = self.root / 'config.yaml'
        self.config.write_text(yaml.safe_dump(cfg))

    def invoke(self, *args):
        output, error = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {'BASIS_VERBOSE': ''}), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(list(args))
        return code, output.getvalue(), error.getvalue()

    def test_validate_saves_report_and_keeps_json_stdout_and_verbose_stderr(self):
        target = self.root / 'review'
        original = {p: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        code, output, error = self.invoke('validate', '-c', str(self.config), '-v', '--report-dir', str(target))
        self.assertEqual(code, 0, error)
        data = json.loads(output)
        self.assertEqual(data['sample_checks'][0]['input_status'], 'passed')
        self.assertFalse(data['resource_checks_performed'])
        self.assertIn('Write per-sample preflight report', error)
        paths = data['preflight_artifacts']
        self.assertEqual(Path(paths['html']), target / 'index.html')
        self.assertTrue(all(Path(p).is_file() for p in paths.values()))
        saved = json.loads(Path(paths['evidence_json']).read_text())
        self.assertEqual(saved, {k: v for k, v in data.items() if k != 'preflight_artifacts'})
        self.assertEqual(original, {p: p.read_bytes() for p in original})
        self.assertFalse((self.root / 'unused-results').exists())

    def test_failed_validation_still_writes_report_and_returns_two(self):
        (self.root / 'sample.faa').write_text('>absent\nMK\n')
        code, output, error = self.invoke('validate', '-c', str(self.config), '--report-dir', str(self.root / 'failed'))
        self.assertEqual(code, 2, error)
        data = json.loads(output)
        self.assertEqual(data['status'], 'failed')
        self.assertEqual(data['sample_checks'][0]['input_status'], 'failed')
        self.assertTrue(Path(data['preflight_artifacts']['html']).is_file())

    def test_existing_report_rejected_before_checking_inputs(self):
        for kind in ('directory', 'file', 'dangling-symlink'):
            target = self.root / kind
            if kind == 'directory':
                target.mkdir()
                (target / 'preserve').write_text('original')
            elif kind == 'file':
                target.write_text('original')
            else:
                target.symlink_to(self.root / 'does-not-exist')
            with self.subTest(kind=kind), patch('basis.cli.check', side_effect=AssertionError('preflight should not start')):
                code, output, error = self.invoke('validate', '-c', str(self.config), '--report-dir', str(target))
            self.assertEqual(code, 2)
            self.assertFalse(output)
            self.assertIn('Refusing to overwrite', error)
        self.assertEqual((self.root / 'directory/preserve').read_text(), 'original')
        self.assertEqual((self.root / 'file').read_text(), 'original')
        self.assertTrue((self.root / 'dangling-symlink').is_symlink())

    def test_doctor_with_no_requested_tools_never_probes_or_executes(self):
        with patch('basis.cli.shutil.which', side_effect=AssertionError('unexpected tool probe')):
            code, output, error = self.invoke('doctor', '-c', str(self.config), '--report-dir', str(self.root / 'doctor'))
        self.assertEqual(code, 0, error)
        result = json.loads(output)
        self.assertFalse(result['resource_checks_performed'])
        self.assertTrue(all(m['status'] == 'disabled' for m in result['sample_checks'][0]['module_checks'].values()))

    def test_without_report_option_does_not_create_outputs(self):
        before = set(self.root.rglob('*'))
        code, output, error = self.invoke('validate', '-c', str(self.config))
        self.assertEqual(code, 0, error)
        self.assertNotIn('preflight_artifacts', json.loads(output))
        self.assertEqual(before, set(self.root.rglob('*')))


if __name__ == '__main__':
    unittest.main()
