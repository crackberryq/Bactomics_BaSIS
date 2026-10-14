"""Public configuration and CLI contracts for the workflow deployment policy."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml
from basis.config import load_config
from basis.cli import main


class WorkflowConfigTests(unittest.TestCase):
    def test_deployment_paths_resolve_relative_to_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.yaml'
            path.write_text(yaml.safe_dump({'execution': {'conda_prefix': 'tool-envs'}}))
            cfg = load_config(path)
            self.assertEqual(cfg['execution']['environment_backend'], 'auto')
            self.assertEqual(cfg['execution']['conda_prefix'], str(Path(tmp) / 'tool-envs'))

    def test_invalid_environment_policy_fails_early(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.yaml'
            for supplied in ({'execution': {'environment_backend': 'automatic'}},
                             {'execution': {'conda_prefix': ''}},
                             {'deep': {'environment_backend': 'unknown'}}):
                path.write_text(yaml.safe_dump(supplied))
                with self.subTest(supplied=supplied), self.assertRaises(ValueError):
                    load_config(path)

    def test_resource_cli_delegates_explicit_arguments(self):
        with patch('basis.resource_setup.main', return_value=0) as prepare:
            self.assertEqual(main(['prepare-resources', '-c', 'resource.yaml', '--resource', 'db']), 0)
        prepare.assert_called_once_with(['-c', 'resource.yaml', '--resource', 'db'])

    def test_resource_preview_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'resources.yaml'
            config.write_text(yaml.safe_dump({'schema_version': 1, 'resources': {'fixture': {
                'backend': 'pinned_files', 'version': 'test-1', 'destination': 'snapshot',
                'files': [{'path': 'data.txt', 'source': 'missing.txt', 'sha256': '0' * 64}]}}}))
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(['prepare-resources', '-c', str(config), '--resource', 'fixture'])
            self.assertEqual(code, 0)
            self.assertEqual(list(root.iterdir()), [config])


if __name__ == '__main__':
    unittest.main()
