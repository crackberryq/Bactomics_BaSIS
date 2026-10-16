"""Native single-dash flags must never reach Conda's global pre-parser.

The fixture reproduces the argparse pre-parser in conda/cli/main.py and the
older main_run.py that passes its executable vector unchanged. It executes real subprocesses
but installs no native bioinformatics tools or Conda environments.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from basis import environment_workflow
from basis.execution import CommandRunner
from basis.native_environment import native_command, native_child_environment


class NativeCommandBoundaryTests(unittest.TestCase):
    def test_supported_prefix_gets_one_boundary_and_input_is_unchanged(self):
        for prefix in (["conda", "run", "-p", "/tool env"],
                       ["/opt/conda/bin/conda", "run", "--no-capture-output", "--prefix=/tools"],
                       ["conda.exe", "run", "--name=tools", "--"]):
            with self.subTest(prefix=prefix):
                original = list(prefix)
                argv = ["/tools/bin/java", "-version", "--", "literal trailing value"]
                combined = native_command(prefix, argv)
                routing = original[:-1] if original[-1] == "--" else original
                self.assertEqual(combined[:len(routing)], routing)
                self.assertEqual(combined[len(routing):len(routing)+3], [sys.executable, "-I", "-c"])
                self.assertEqual(combined[-len(argv)-1:], ["--"] + argv)
                self.assertEqual(prefix, original)
                _, audit = native_child_environment({}, routing + ["--"])
                self.assertTrue(audit["applied"])

    def test_unknown_wrappers_and_ambiguous_targets_are_not_rewritten(self):
        for prefix in ([], ["bash", "-c", "arbitrary wrapper"], ["mamba", "run", "-p", "/tools"],
                       ["conda", "run", "-p", "/a", "-n", "b"],
                       ["conda", "run", "-p", "/a", "python"]):
            self.assertEqual(native_command(prefix, ["java", "-version"]), prefix + ["java", "-version"])


@unittest.skipUnless(os.name == "posix", "Executable fixtures use POSIX shebangs")
class CondaPreParserRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis conda flags ")
        self.root = Path(self.temp.name)
        self.prefix = self.root / "selected environment"
        (self.prefix / "bin").mkdir(parents=True)
        self.manager = self.root / "conda"
        self.manager.write_text("#!" + sys.executable + "\n" + r'''
import argparse,os,sys
from pathlib import Path
args=sys.argv[1:]
# Conda's logging pre-parser runs before dispatch and must stop at --.
parser=argparse.ArgumentParser(prog='conda',add_help=False)
parser.add_argument('-v','--verbose',action='count',default=0)
namespace,_=parser.parse_known_args(args[:args.index('--')] if '--' in args else args)
# Python 3.11 errors on -version; Python 3.12 instead consumes -v and leaves
# -ersion. Either behavior means native arguments reached the wrong parser.
if namespace.verbose:
    parser.error("argument -v/--verbose: ignored explicit argument 'ersion'")
assert args[:2]==['run','--prefix']
prefix=args[2]
command=args[3:]
# Conda 25.9.1 main_run passes the executable vector unchanged.
if os.environ.get('BASIS_TEST_CONDA_NEW')=='1' and command and command[0]=='--':
    command=command[1:]
os.environ['CONDA_PREFIX']=prefix
os.environ['PATH']=str(Path(prefix)/'bin')
os.execvpe(command[0],command,os.environ)
''')
        self.manager.chmod(0o755)
        for name in ("java", "vmatch", "blastp"):
            path = self.prefix / "bin" / name
            path.write_text("#!" + sys.executable + "\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n")
            path.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def test_baseline_reproduces_uploaded_conda_error(self):
        result = subprocess.run([str(self.manager), "run", "--prefix", str(self.prefix),
                                 str(self.prefix / "bin/java"), "-version"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("ignored explicit argument 'ersion'", result.stderr)

    def test_native_runner_preserves_target_flags(self):
        prefix = [str(self.manager), "run", "--prefix", str(self.prefix)]
        for newer_conda in (False, True):
            for tool in ("java", "vmatch", "blastp"):
                with self.subTest(tool=tool, newer_conda=newer_conda), patch.dict(
                        os.environ, {"BASIS_TEST_CONDA_NEW": str(int(newer_conda))}):
                    runner = CommandRunner({"command_prefix": prefix}, self.root / (tool + ".log"))
                    result = runner.run([tool, "-version"])
                    self.assertEqual(json.loads(result.stdout), ["-version"])
                    self.assertEqual(runner.commands[0]["argv"], native_command(prefix, [tool, "-version"]))
                    self.assertTrue(runner.commands[0]["environment_isolation"]["applied"])
                    self.assertIn("os.execvpe", runner.logfile.read_text())

    def test_environment_startup_probes_use_the_same_boundary(self):
        for tool in ("java", "vmatch", "blastp"):
            with self.subTest(tool=tool), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = environment_workflow._run_prefix(
                    {"manager_executable": str(self.manager)}, self.prefix,
                    [str(self.prefix / "bin" / tool), "-version"], self.root / (tool + "-verify.log"))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout), ["-version"])

    def test_existing_boundary_is_not_delivered_to_tool_and_isolation_still_applies(self):
        prefix = [str(self.manager), "run", "--prefix", str(self.prefix), "--"]
        runner = CommandRunner({"command_prefix": prefix}, self.root / "already-delimited.log")
        with patch.dict(os.environ, {"PYTHONPATH": "SENTINEL-private-pythonpath"}):
            result = runner.run(["java", "-version", "--", "literal argument"])
        self.assertEqual(json.loads(result.stdout), ["-version", "--", "literal argument"])
        self.assertTrue(runner.commands[0]["environment_isolation"]["applied"])
        self.assertNotIn("SENTINEL", runner.logfile.read_text())


if __name__ == "__main__":
    unittest.main()
