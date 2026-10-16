"""Regress the Conda 26.1.1 precomputed-activation/shell-hook ordering.

This POSIX subprocess fixture models the relevant upstream contract, not a Conda
installation: utils.wrap_subprocess_call writes the shell hook before activation
code computed from the parent, while activate.build_reactivate omits CONDA_PREFIX.
Sources: conda/conda tag 26.1.1, conda/utils.py and conda/activate.py. Alias collision
handling follows conda/common/configuration.py's ParameterLoader.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from basis.execution import CommandRunner
from basis.native_environment import native_child_environment, native_command
from basis.scheduler_persistence import capture_tool_identity


_MANAGER = r'''
import json, os, shlex, subprocess, sys, tempfile
from pathlib import Path

target = os.environ['BASIS_FIXTURE_TARGET']
base = os.environ['BASIS_FIXTURE_BASE']

def export(name, value):
    return 'export ' + name + '=' + shlex.quote(str(value)) + '\n'

def hooks(prefix, phase):
    paths = sorted((Path(prefix) / 'etc/conda' / (phase + '.d')).glob('*.sh'),
                   reverse=phase == 'deactivate')
    return ''.join('. ' + shlex.quote(str(path)) + '\n' for path in paths)

def activation(prefix):
    old = os.environ.get('CONDA_PREFIX')
    level = int(os.environ.get('CONDA_SHLVL', '0'))
    path = os.environ['PATH'].split(os.pathsep)
    old_bin = str(Path(old) / 'bin') if old else None
    path = [str(Path(prefix) / 'bin') if entry == old_bin else entry for entry in path]
    if old_bin not in os.environ['PATH'].split(os.pathsep):
        path.insert(0, str(Path(prefix) / 'bin'))
    code = hooks(old, 'deactivate') if old else ''
    code += export('PATH', os.pathsep.join(path))
    # Conda's same-prefix branch returns build_reactivate: no prefix/default-env export.
    code += export('CONDA_SHLVL', level if old == prefix and level else level + 1)
    if old != prefix or not level:
        code += export('CONDA_PREFIX', prefix)
        code += export('CONDA_DEFAULT_ENV', prefix)
    return code + hooks(prefix, 'activate')

args = sys.argv[1:]
if args == ['shell.posix', 'hook']:
    keys = [key for key in ('CONDA_AUTO_ACTIVATE', 'CONDA_AUTO_ACTIVATE_BASE')
            if key in os.environ]
    if len(keys) > 1:
        raise SystemExit('Multiple aliased keys in environment: auto_activate, auto_activate_base')
    default = json.loads(Path(os.environ['CONDARC']).read_text())['auto_activate']
    automatic = os.environ[keys[0]].lower() not in ('false', '0', 'no') if keys else default
    print(activation(base) if automatic else '')
    raise SystemExit(0)

assert args.pop(0) == 'run'
while args[0] in ('--no-capture-output', '--live-stream'):
    args.pop(0)
option = args.pop(0)
if option in ('--prefix', '-p'):
    selected = args.pop(0)
elif option.startswith('--prefix='):
    selected = option.split('=', 1)[1]
elif option in ('--name', '-n'):
    assert args.pop(0) == 'fixture'
    selected = target
else:
    raise SystemExit('unsupported fixture selector')
assert selected == target
if args and args[0] == '--':
    args.pop(0)

# Crucially compute activation now, before the separate shell executes its hook.
precomputed = activation(selected)
with tempfile.TemporaryDirectory() as temp:
    script = Path(temp) / 'wrapper.sh'
    hook = shlex.join([sys.executable, __file__, 'shell.posix', 'hook'])
    script.write_text('set -e\neval "$(' + hook + ')"\n' + precomputed + shlex.join(args) + '\n')
    raise SystemExit(subprocess.run(['/bin/sh', str(script)]).returncode)
'''

_PROBE = '''import json,os,shutil
print(json.dumps({
    "prefix": os.environ.get("CONDA_PREFIX"),
    "level": os.environ.get("CONDA_SHLVL"),
    "default": os.environ.get("CONDA_DEFAULT_ENV"),
    "tool": shutil.which("diamond"),
    "target_hook": os.environ.get("BASIS_TARGET_HOOK"),
    "base_hook": os.environ.get("BASIS_BASE_HOOK"),
}))
'''


@unittest.skipUnless(os.name == "posix", "The upstream regression is a POSIX shell sequence")
class CondaHookActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis conda hook ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target, self.base = self.root / "target", self.root / "base"
        for prefix, marker in ((self.target, "BASIS_TARGET_HOOK"), (self.base, "BASIS_BASE_HOOK")):
            (prefix / "bin").mkdir(parents=True)
            (prefix / "conda-meta").mkdir()
            (prefix / "conda-meta/history").write_text("fixture environment\n")
            hooks = prefix / "etc/conda/activate.d"
            hooks.mkdir(parents=True)
            (hooks / "resource.sh").write_text("export " + marker + "=loaded\n")
            tool = prefix / "bin/diamond"
            tool.write_text("#!/bin/sh\nexit 0\n")
            tool.chmod(0o755)
        self.manager = self.root / "conda"
        self.manager.write_text("#!" + sys.executable + "\n" + _MANAGER)
        self.manager.chmod(0o755)
        self.condarc = self.root / "condarc"
        self.condarc.write_text('{"auto_activate": true}\n')
        self.env = dict(os.environ)
        for name in ("CONDA_AUTO_ACTIVATE", "CONDA_AUTO_ACTIVATE_BASE", "PYTHONHOME",
                     "PYTHONPATH", "BASIS_TARGET_HOOK", "BASIS_BASE_HOOK"):
            self.env.pop(name, None)
        self.env.update(
            PATH=os.pathsep.join([str(self.target / "bin"), "/usr/bin", "/bin"]),
            CONDA_PREFIX=str(self.target), CONDA_SHLVL="3", CONDA_DEFAULT_ENV=str(self.target),
            CONDA_PREFIX_1=str(self.base), CONDA_PREFIX_2=str(self.root / "controller"),
            BASIS_FIXTURE_TARGET=str(self.target), BASIS_FIXTURE_BASE=str(self.base),
            CONDARC=str(self.condarc),
        )

    def prefix(self, selector=None):
        return [str(self.manager), "run", *(selector or ["--prefix", str(self.target)])]

    def probe(self, env):
        command = native_command(self.prefix(), [sys.executable, "-I", "-c", _PROBE])
        result = subprocess.run(command, env=env, check=True, text=True, capture_output=True)
        return json.loads(result.stdout)

    def test_precomputed_reactivation_reproduces_then_prevents_split_identity(self):
        baseline = self.probe(self.env)
        self.assertEqual(baseline["prefix"], str(self.base))
        self.assertEqual(baseline["tool"], str(self.target / "bin/diamond"))
        self.assertEqual(baseline["level"], "3")
        self.assertEqual(baseline["base_hook"], "loaded")
        before, config_before = dict(self.env), self.condarc.read_bytes()
        clean, audit = native_child_environment(self.env, self.prefix())
        fixed = self.probe(clean)
        self.assertEqual(fixed["prefix"], str(self.target))
        self.assertEqual(fixed["default"], str(self.target))
        self.assertEqual(fixed["tool"], str(self.target / "bin/diamond"))
        self.assertEqual(fixed["level"], "3")
        self.assertEqual(fixed["target_hook"], "loaded")
        self.assertIsNone(fixed["base_hook"])
        self.assertEqual(self.env, before)
        self.assertEqual(self.condarc.read_bytes(), config_before)
        self.assertIn("CONDA_AUTO_ACTIVATE_BASE", audit["set_variable_names"])

    def test_inherited_aliases_cannot_reenable_hook_or_conflict(self):
        for overrides in ({"CONDA_AUTO_ACTIVATE": "true"},
                          {"CONDA_AUTO_ACTIVATE_BASE": "true"},
                          {"CONDA_AUTO_ACTIVATE": "true", "CONDA_AUTO_ACTIVATE_BASE": "true"}):
            with self.subTest(overrides=overrides):
                env = {**self.env, **overrides}
                before = dict(env)
                clean, audit = native_child_environment(env, self.prefix())
                self.assertNotIn("CONDA_AUTO_ACTIVATE", clean)
                self.assertEqual(clean["CONDA_AUTO_ACTIVATE_BASE"], "false")
                self.assertEqual(self.probe(clean)["prefix"], str(self.target))
                self.assertEqual(env, before)
                if "CONDA_AUTO_ACTIVATE" in env:
                    self.assertIn("CONDA_AUTO_ACTIVATE", audit["removed_variable_names"])
                for name in ("CONDA_PREFIX", "CONDA_PREFIX_1", "CONDA_PREFIX_2", "CONDA_SHLVL"):
                    self.assertEqual(clean[name], env[name])

    def test_real_identity_probe_accepts_target_for_prefix_and_name(self):
        for selector in (["--prefix", str(self.target)], ["--prefix=" + str(self.target)],
                         ["--name", "fixture"]):
            with self.subTest(selector=selector), patch.dict(os.environ, self.env, clear=True):
                before = dict(os.environ)
                cfg = {"command_prefix": self.prefix(selector), "executable": "diamond"}
                runner = CommandRunner(cfg, self.root / "commands.log")
                identity = capture_tool_identity("vfdb", cfg, runner)
                self.assertEqual(identity["prefix"], str(self.target))
                self.assertEqual(set(identity["executables"]), {str(self.target / "bin/diamond")})
                self.assertTrue(identity["reuse_supported"])
                self.assertEqual(dict(os.environ), before)

    def test_unselected_commands_preserve_user_auto_activation_settings(self):
        env = {**self.env, "CONDA_AUTO_ACTIVATE": "true", "CONDA_AUTO_ACTIVATE_BASE": "true"}
        for prefix in ([], ["conda", "run"], ["mamba", "run", "-n", "fixture"]):
            with self.subTest(prefix=prefix):
                clean, audit = native_child_environment(env, prefix)
                self.assertEqual(clean, env)
                self.assertFalse(audit["applied"])


if __name__ == "__main__":
    unittest.main()
