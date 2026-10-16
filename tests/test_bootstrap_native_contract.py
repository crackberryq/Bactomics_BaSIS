"""First-run contracts using the real planner, runner and provider dispatch.

Native binaries/downloads are local fixtures. Package discovery itself executes
in an isolated Python environment with both ordinary and .pth/egg layouts.
"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import venv

import yaml
from basis import bootstrap, bootstrap_providers, bootstrap_typing_defense
from basis.config import default_config
from basis.util import sha256_file


class NativeBootstrapContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, provider, prefix):
        cfg = default_config()
        cfg['outdir'] = str(self.root / 'results')
        cfg['resources'] = {'policy': 'auto', 'directory': str(self.root / 'cache')}
        cfg['execution']['conda_prefix'] = str(self.root / 'environments')
        for name, section in cfg['modules'].items():
            section['enabled'] = name == provider
        cfg['modules'][provider]['command_prefix'] = prefix
        config, specs = bootstrap.plan_auto_resources(cfg)
        return {'plan_hash': 'fixture-plan', 'source_hash': 'fixture-source',
                'config': config, 'options': {'deep': False, 'extend': False},
                'outdir': cfg['outdir'], 'bootstrap_resources': specs}

    def write_wrapper(self, code):
        path = self.root / 'native_fixture.py'
        path.write_text(code)
        return [sys.executable, str(path)]

    def test_mlst_planned_provider_publishes_through_real_runner_and_dispatch(self):
        database = self.root / 'mlst-db'
        (database / 'pubmlst/test').mkdir(parents=True)
        (database / 'pubmlst/test/test.txt').write_text('ST\tx\n1\t1\n')
        (database / 'blast').mkdir()
        for suffix in ('nhr', 'nin', 'nsq'):
            (database / ('blast/mlst.fa.' + suffix)).write_bytes(b'native index fixture')
        prefix = self.write_wrapper(
            'import sys\n'
            'if sys.argv[1:] == ["mlst", "--version"]: print("mlst 2.35.0")\n'
            'elif sys.argv[1] == "perl": print(' + repr(str(database)) + ')\n'
            'elif sys.argv[1] == "blastdbcmd": print("Database: PubMLST\\n1 sequences; 4 total bases")\n'
            'else: raise SystemExit("Unexpected fixture command")\n')
        plan = self.plan('mlst', prefix)
        spec = plan['bootstrap_resources'][0]
        self.assertEqual(spec['provider'], 'mlst')
        self.assertNotIn('backend', spec)
        receipt = bootstrap.run_resource(plan, self.root / 'run', 'mlst')
        self.assertEqual(receipt['status'], 'ready', receipt)
        manifest = bootstrap.verify_snapshot(spec)
        prepared = Path(manifest['result']['module_updates']['mlst']['database'])
        self.assertFalse((prepared / 'blast/mlst.fa').exists())
        for suffix in ('nhr', 'nin', 'nsq'):
            self.assertEqual((prepared / ('blast/mlst.fa.' + suffix)).read_bytes(),
                             (database / ('blast/mlst.fa.' + suffix)).read_bytes())
        self.assertTrue(manifest['commands'])

    def archive(self, name, entries):
        path = self.root / (name + '.tar.gz')
        with tarfile.open(path, 'w:gz') as archive:
            for relative, value in entries.items():
                data = value.encode()
                member = tarfile.TarInfo('source/' + relative)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return path

    def test_ccf_planned_provider_publishes_through_real_runner_and_dispatch(self):
        source = self.archive('ccf', {'CRISPRCasFinder.pl': '# fixture', 'sel392v2.so': 'fixture',
                                      'COPYING': 'GPL-3.0-or-later'})
        models = self.archive('cas', {'LICENSE': 'CC BY-NC-SA 4.0', 'metadata.yml': 'vers: 3.1.0\n'})
        mapping = {'fixture:ccf': str(source), 'fixture:cas': str(models)}
        sources = {name: {'url': url, 'sha256': sha256_file(path), 'repository': url}
                   for name, url, path in [('crisprcasfinder', 'fixture:ccf', source),
                                           ('casfinder_models', 'fixture:cas', models)]}
        prefix = self.write_wrapper('import shutil, sys\n'
            'mapping = ' + repr(mapping) + '\n'
            'if sys.argv[1] == "curl": shutil.copy2(mapping[sys.argv[-1]], sys.argv[sys.argv.index("--output") + 1])\n'
            'elif sys.argv[1] == "vmatch": print(">29 0 D 29 65\\nGTTCACTGCCGTACAGGCAGCTTAGAAA")\n'
            'elif sys.argv[1] not in {"perl", "mkvtree"}: raise SystemExit("Unexpected fixture command")\n')
        plan = self.plan('crisprcasfinder', prefix)
        with patch.object(bootstrap_typing_defense, 'SOURCES', sources), \
             patch.object(bootstrap_typing_defense.platform, 'system', return_value='Linux'), \
             patch.object(bootstrap_typing_defense.platform, 'machine', return_value='x86_64'):
            receipt = bootstrap.run_resource(plan, self.root / 'run', 'crisprcasfinder')
        self.assertEqual(receipt['status'], 'ready', receipt)
        manifest = bootstrap.verify_snapshot(plan['bootstrap_resources'][0])
        self.assertEqual(manifest['result']['provenance']['runtime_checks']['vmatch_plugin'],
                         'synthetic_repeat_detected')

    def native_environment(self):
        prefix = self.root / 'environments' / 'native'
        venv.EnvBuilder(with_pip=False, symlinks=False).create(prefix)
        (prefix / 'conda-meta').mkdir()
        (prefix / 'conda-meta/history').write_text('Synthetic transaction for routing test\n')
        package_root = Path(subprocess.check_output([str(prefix / 'bin/python'), '-I', '-c',
            'import sysconfig; print(sysconfig.get_paths()["purelib"])'], text=True).strip())
        conda = self.root / 'conda'
        conda.write_text('#!' + sys.executable + '\nimport os, sys\n'
            'args=sys.argv[1:]\n'
            'prefix=args[args.index("--prefix")+1]\n'
            'command=args[args.index("--prefix")+2:]\n'
            'if not command or command[0] != "python": raise SystemExit("Unexpected fixture command")\n'
            'os.execv(prefix+"/bin/python", [prefix+"/bin/python"]+command[1:])\n')
        conda.chmod(0o755)
        return prefix, package_root, conda

    def runner_for_managed(self, provider, prefix, conda):
        plan = self.plan(provider, [])
        spec = plan['bootstrap_resources'][0]
        # Activation and routing are real; package verification probes are native
        # fixture boundaries and are covered separately by environment tests.
        with patch.dict(os.environ, {'CONDA_PREFIX': str(prefix), 'CONDA_EXE': str(conda)}), \
             patch('basis.environment_workflow.verify', return_value={'metadata_sha256': {'fixture': 'digest'}}):
            runner, environment = bootstrap._runner(plan, spec, self.root / 'native.log')
        self.assertEqual(environment['prefix'], str(prefix))
        return runner

    def test_managed_runner_discovers_egg_package_and_ignores_other_python_layouts(self):
        prefix, site, conda = self.native_environment()
        egg = site / 'antismash-8.0.4-py3.11.egg'
        package = egg / 'antismash'
        (package / 'data').mkdir(parents=True)
        (package / '__init__.py').write_text('raise RuntimeError("Package import is not needed")\n')
        (package / 'data/model.hmm').write_text('fixture profile')
        (site / 'antismash.pth').write_text(str(egg) + '\n')
        # A stale directory must not create ambiguity when it is not importable.
        (prefix / 'lib/python9.99/site-packages/antismash').mkdir(parents=True)
        runner = self.runner_for_managed('antismash', prefix, conda)
        assets = bootstrap_providers._antismash_environment_assets(runner)
        self.assertEqual(list(assets['source_sha256']), [str(package / 'data/model.hmm')])
        self.assertIn('-I', runner.commands[0]['argv'])

    def test_integron_models_resolved_from_selected_interpreter_import_path(self):
        prefix, site, conda = self.native_environment()
        package = site / 'integron_finder-2.0.6-py3.11.egg/integron_finder'
        source = package / 'data/Models'
        source.mkdir(parents=True)
        (package / '__init__.py').write_text('')
        (source / 'model.hmm').write_text('fixture model')
        (site / 'integron.pth').write_text(str(package.parent) + '\n')
        runner = self.runner_for_managed('integronfinder', prefix, conda)
        destination = self.root / 'prepared'
        result = bootstrap_providers.prepare_resource({'provider': 'integronfinder'}, destination, runner)
        self.assertEqual(result['module_updates']['integronfinder']['database'], str(source))
        self.assertEqual((destination / 'bundled/model.hmm').read_text(), 'fixture model')

    def test_external_package_escape_is_rejected_even_when_importable(self):
        prefix, site, conda = self.native_environment()
        outside = self.root / 'outside'
        (outside / 'antismash').mkdir(parents=True)
        (outside / 'antismash/__init__.py').write_text('')
        (site / 'outside.pth').write_text(str(outside) + '\n')
        runner = self.runner_for_managed('antismash', prefix, conda)
        with self.assertRaisesRegex(ValueError, 'outside its environment'):
            bootstrap_providers._antismash_environment_assets(runner)

    def test_ccf_recipe_hmmer_satisfies_macsyfinder_20_constraint(self):
        path = Path(bootstrap.__file__).parent / 'resources/envs/crisprcasfinder.yml'
        dependencies = yaml.safe_load(path.read_text())['dependencies']
        self.assertIn('macsyfinder=2.0', dependencies)
        self.assertIn('hmmer=3.3.2', dependencies)


if __name__ == '__main__':
    unittest.main()
