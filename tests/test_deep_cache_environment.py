"""Native cache identity is separate from synthetic fixture execution."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import venv

from basis.deep_cache import _PROBE, engine_identity
from basis.execution import CommandRunner
from basis.pipeline import execute_deep


class EngineIdentityTests(unittest.TestCase):
    def test_real_plain_venv_is_unavailable_without_failed_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prefix = root / 'venv'
            venv.EnvBuilder(with_pip=False, symlinks=True).create(prefix)
            python = prefix / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            config = {'python_executable': str(python)}
            runner = CommandRunner(config, root / 'commands.log')
            identity = engine_identity(config, runner)
            self.assertEqual(identity['status'], 'unavailable')
            self.assertEqual(identity['environment_type'], 'python_venv')
            self.assertEqual(identity['reason'], 'non_conda_environment')
            self.assertNotIn('environment_sha256', identity)
            self.assertEqual(runner.commands[0]['returncode'], 0)
            self.assertNotIn('error', runner.commands[0])

    def _probe_fake_prefix(self, root, names, path):
        # Only Python runs; executable files are inert fixtures, never launched.
        script = 'import sys; sys.prefix=sys.argv.pop(1); sys.base_prefix=sys.prefix; exec(' + repr(_PROBE) + ')'
        return subprocess.run([sys.executable, '-c', script, str(root), json.dumps(names)],
                              env={**os.environ, 'PATH': str(path)}, text=True, capture_output=True)

    def test_conda_guard_still_hashes_engine_and_detects_file_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'conda-meta').mkdir()
            metadata = root / 'conda-meta/package.json'
            metadata.write_text('{"version": "1"}')
            bindir = root / 'bin'
            bindir.mkdir()
            tool = bindir / 'fixture-tool'
            tool.write_text('inert executable fixture')
            tool.chmod(0o755)
            first = self._probe_fake_prefix(root, ['fixture-tool'], bindir)
            self.assertEqual(first.returncode, 0, first.stderr)
            identity = json.loads(first.stdout)
            self.assertEqual(identity['status'], 'available')
            self.assertEqual(identity['file_count'], 2)
            self.assertEqual(identity['executables'], {'fixture-tool': str(tool)})
            metadata.write_text('{"version": "2"}')
            second = self._probe_fake_prefix(root, ['fixture-tool'], bindir)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertNotEqual(identity['environment_sha256'], json.loads(second.stdout)['environment_sha256'])

    def test_conda_probe_rejects_executable_outside_fingerprinted_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'engine'
            (root / 'conda-meta').mkdir(parents=True)
            outside = Path(td) / 'outside'
            outside.mkdir()
            tool = outside / 'fixture-tool'
            tool.write_text('never executed')
            tool.chmod(0o755)
            result = self._probe_fake_prefix(root, ['fixture-tool'], outside)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Executable outside fingerprinted environment', result.stderr)

    def test_identity_uses_supplied_runner_and_configured_native_python(self):
        names = ['hmmscan', 'mafft', 'iqtree', 'epa-ng', 'perl', 'diamond', 'pfscanV3']
        identity = {'status': 'available', 'prefix': '/native/conda', 'environment_sha256': 'a' * 64,
                    'file_count': 123, 'executables': {n: '/native/conda/bin/' + n for n in names}}
        runner = Mock()
        runner.run.return_value = subprocess.CompletedProcess([], 0, json.dumps(identity), '')
        config = {'family_placement': True, 'prosite_snapshot': 'snapshot', 'prosite_profiles': True,
                  'reference_search_tool': 'diamond', 'python_executable': '/native/conda/bin/python'}
        self.assertEqual(engine_identity(config, runner), identity)
        argv = runner.run.call_args.args[0]
        self.assertEqual(argv[:2], ['/native/conda/bin/python', '-c'])
        self.assertEqual(json.loads(argv[3]), names)
        self.assertEqual(runner.run.call_count, 1)

    def test_malformed_or_incomplete_identity_never_enables_cache(self):
        valid = {'status': 'available', 'prefix': '/native', 'environment_sha256': 'a' * 64,
                 'file_count': 3, 'executables': {n: '/native/bin/' + n for n in ['hmmscan', 'mafft', 'iqtree', 'blastp']}}
        invalid = [None, [], {}, {'hash': 'legacy'}, {'status': 'available'},
                   {**valid, 'environment_sha256': 'wrong'}, {**valid, 'file_count': True},
                   {**valid, 'executables': {}}, {**valid, 'prefix': 'relative'},
                   {**valid, 'executables': {**valid['executables'], 'mafft': '/outside/bin/mafft'}},
                   {**valid, 'executables': {**valid['executables'], 'mafft': '/native/../outside/mafft'}}]
        for value in invalid:
            with self.subTest(value=value):
                runner = Mock()
                runner.run.return_value = subprocess.CompletedProcess([], 0, json.dumps(value), '')
                with self.assertRaisesRegex(ValueError, 'Incomplete or malformed'):
                    engine_identity({}, runner)


class PipelineCacheEnvironmentTests(unittest.TestCase):
    def _plan(self, td, validation_mode):
        profiles = Path(td) / 'profiles'
        profiles.mkdir()
        return {'outdir': td, 'config': {'outdir': td, 'validation_mode': validation_mode,
                'deep': {'profiles_dir': str(profiles)}, 'execution': {'cores': 1}},
                'options': {'deep': True}, 'validations': {'s': {'input_hashes': {}}},
                'source_hash': 'code', 'enabled_modules': []}

    @staticmethod
    def _payload(*args):
        return {'candidates': [], 'artifacts': [], 'metrics': {'failed_candidates': 0}, 'complete': True}

    def test_synthetic_fixture_never_probes_or_seals_native_environment(self):
        with tempfile.TemporaryDirectory() as td:
            plan = self._plan(td, 'synthetic_fixture')
            with patch('basis.deep_cache.engine_identity') as identity, \
                    patch('basis.execution.CommandRunner.run') as native, \
                    patch('basis.deep.run_deep', side_effect=self._payload):
                record = execute_deep(plan, {'sample_id': 's'})
            identity.assert_not_called()
            native.assert_not_called()
            self.assertEqual(record['execution_status'], 'completed')
            self.assertEqual(record['cache_status'], {'status': 'not_applicable', 'reason': 'synthetic_fixture'})
            self.assertIsNone(record['provenance']['engine_identity'])
            self.assertEqual(record['provenance']['commands'], [])
            self.assertNotIn('deep_cache', record)

    def test_native_unsupported_environment_disables_reuse_without_stage_failure(self):
        with tempfile.TemporaryDirectory() as td:
            plan = self._plan(td, 'research')
            with patch('basis.deep_cache.engine_identity', return_value={
                    'status': 'unavailable', 'reason': 'non_conda_environment'}), \
                    patch('basis.deep.run_deep', side_effect=self._payload) as run:
                first = execute_deep(plan, {'sample_id': 's'})
                second = execute_deep(plan, {'sample_id': 's'})
            self.assertEqual(run.call_count, 2)
            for record in (first, second):
                self.assertEqual(record['execution_status'], 'completed')
                self.assertFalse(record['cache_hit'])
                self.assertNotIn('deep_cache', record)
                self.assertEqual(record['cache_status'], {'status': 'unavailable', 'reason': 'non_conda_environment'})

    def test_failed_fingerprint_keeps_native_recomputation_and_no_seal(self):
        with tempfile.TemporaryDirectory() as td:
            plan = self._plan(td, 'research')
            with patch('basis.deep_cache.engine_identity', side_effect=ValueError('malformed identity')), \
                    patch('basis.deep.run_deep', side_effect=self._payload):
                record = execute_deep(plan, {'sample_id': 's'})
            self.assertEqual(record['execution_status'], 'completed')
            self.assertEqual(record['cache_status']['reason'], 'engine_identity_failed')
            self.assertNotIn('deep_cache', record)
            self.assertIn('Deep cache disabled: malformed identity', record['notes'])


if __name__ == '__main__':
    unittest.main()
