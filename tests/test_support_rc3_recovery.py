"""Read-only fixtures for the study-specific support helper; no processes stopped."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

import yaml

from basis.run_control import Process

PATH = Path(__file__).resolve().parents[1] / 'support/recover_isolate2_rc3.py'
spec = importlib.util.spec_from_file_location('isolate2_support', PATH)
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def model(self):
        root = self.root / 'prott5'
        model = root / support.REVISION
        model.mkdir(parents=True)
        for name in support.REQUIRED_FILES:
            (model / name).write_bytes(b'fixture')
        (model / 'config.json').write_text(json.dumps({'model_type': 't5', 'd_model': 1024}))
        manifest = {'repository': support.REPOSITORY, 'revision': support.REVISION,
                    'sha256': {name: support.sha256(model / name) for name in support.REQUIRED_FILES}}
        (root / 'provenance.json').write_text(json.dumps(manifest))
        return root, model, manifest

    def chain(self):
        stage = self.root / 'resources/staging' / support.STAGING_NAME
        stage.mkdir(parents=True)
        argv = ('python', '-c', 'snapshot_download fixture', str(stage), support.REPOSITORY, support.REVISION)
        one = Process(support.ROOT_PID, 1, os.getuid(), '11', 'S', argv, str(stage))
        two = replace(one, pid=support.SHELL_PID, ppid=one.pid, start_time='12', argv=('bash', '/tmp/native.sh'))
        three = replace(one, pid=support.WORKER_PID, ppid=two.pid, start_time='13')
        return {item.pid: item for item in (one, two, three)}

    def test_verified_model_needs_no_invented_manifest_path_field(self):
        root, expected, _ = self.model()
        actual, audit = support.verify_model(root)
        self.assertEqual(actual, expected)
        self.assertEqual(set(audit['verified_sha256']), support.REQUIRED_FILES)

    def test_corruption_and_unrecorded_shadow_checkpoint_rejected(self):
        root, model, manifest = self.model()
        (model / 'model.safetensors').write_bytes(b'unchecked')
        with self.assertRaisesRegex(ValueError, 'Unrecorded'):
            support.verify_model(root)
        (model / 'model.safetensors').unlink()
        (model / 'pytorch_model.bin').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            support.verify_model(root)

    def test_manifest_path_escape_rejected(self):
        root, model, manifest = self.model()
        manifest['sha256']['../outside.bin'] = '0' * 64
        (root / 'provenance.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'Unsafe relative'):
            support.verify_model(root)

    def test_symlink_escape_rejected(self):
        root, model, manifest = self.model()
        outside = self.root / 'outside'
        outside.write_bytes((model / 'pytorch_model.bin').read_bytes())
        (model / 'pytorch_model.bin').unlink()
        (model / 'pytorch_model.bin').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'escaping'):
            support.verify_model(root)

    def test_exact_inspected_chain_and_already_absent(self):
        table = self.chain()
        selected, owned = support.verify_orphans(table, self.root)
        self.assertEqual(selected.pid, support.ROOT_PID)
        self.assertEqual(set(owned), set(support.EXPECTED_PIDS))
        self.assertEqual(support.verify_orphans({}, self.root), (None, {}))

    def test_partial_reused_or_changed_parent_chain_is_not_owned(self):
        table = self.chain()
        for changed in ({support.ROOT_PID: table[support.ROOT_PID]},
                        {**table, support.WORKER_PID: replace(table[support.WORKER_PID], ppid=1)},
                        {**table, support.ROOT_PID: replace(table[support.ROOT_PID], argv=('python', '-c', 'unrelated'))}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                support.verify_orphans(changed, self.root)

    def test_config_stays_beside_original_and_keeps_existing_relative_paths(self):
        study = self.root / 'study-isolate2'
        study.mkdir()
        cfg = study / 'config.retry-rc3.yaml'
        raw = {'samples': 'samples.tsv', 'resources': {'directory': '../resources', 'policy': 'auto'},
               'modules': {'pathogenfinder2': {'weights_model': '../weights', 'attention_top_n': 20}}}
        cfg.write_text(yaml.safe_dump(raw))
        source = cfg.read_bytes()
        target, audit, outdir = support.prepare_config(self.root, cfg, raw, source, self.root / 'model', {}, {'status': 'stopped'})
        self.assertEqual(cfg.read_bytes(), source)
        updated = yaml.safe_load(target.read_text())
        self.assertEqual(target.parent, cfg.parent)
        self.assertEqual(updated['samples'], 'samples.tsv')
        self.assertEqual(updated['resources'], raw['resources'])
        self.assertEqual(updated['modules']['pathogenfinder2']['weights_model'], '../weights')
        self.assertFalse(outdir.exists())
        self.assertTrue(audit.is_file())

    def test_readonly_does_not_signal_write_or_launch(self):
        study = self.root / 'study-isolate2'
        study.mkdir()
        cfg = study / 'config.retry-rc3.yaml'
        cfg.write_text('{}')
        model_root, _, _ = self.model()
        with patch('basis.__version__', '0.3.0rc5'), \
             patch('basis.run_control.process_table', return_value={}), \
             patch('basis.run_control.stop_invocation') as stop, \
             patch.object(support, 'MODEL_ROOT', model_root), \
             patch.object(support, 'prepare_config') as write, \
             patch.object(support.os, 'execv') as launch:
            self.assertEqual(support.main(['--project', str(self.root)]), 0)
        stop.assert_not_called()
        write.assert_not_called()
        launch.assert_not_called()

    def test_apply_accepts_orphan_finishing_during_model_verification(self):
        study = self.root / 'study-isolate2'
        study.mkdir()
        (study / 'config.retry-rc3.yaml').write_text('{}')
        model_root, _, _ = self.model()
        initial = self.chain()
        with patch('basis.__version__', '0.3.0rc5'), \
             patch('basis.run_control.process_table', side_effect=[initial, {}, {}]), \
             patch('basis.run_control.stop_invocation') as stop, \
             patch.object(support, 'MODEL_ROOT', model_root), \
             patch.object(support.os, 'chdir'), \
             patch.object(support.os, 'execv') as launch:
            self.assertEqual(support.main(['--project', str(self.root), '--apply']), 0)
        stop.assert_not_called()
        command = launch.call_args.args[1]
        self.assertEqual(command[1:5], ['-I', '-m', 'basis', 'run'])
        self.assertEqual(command[-7:], ['-d', '-e', '-v', '--cores', '4', '--output', 'summary'])
        self.assertEqual(len(list(study.glob('config.retry-rc5-*.yaml'))), 1)
