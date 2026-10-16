"""Real subprocess identity checks with Pythonless and legacy-Python tool prefixes."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from basis.execution import CommandRunner
from basis.scheduler_persistence import capture_tool_identity, file_inventory
from basis.util import hash_object, sha256_file


@unittest.skipUnless(os.name == "posix", "Conda activation simulation uses POSIX executables")
class ToolIdentityRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis identity spaces ")
        self.root = Path(self.temp.name)
        self.target = self.root / "native environment"
        (self.target / "bin").mkdir(parents=True)
        (self.target / "conda-meta").mkdir()
        (self.target / "conda-meta/history").write_text("synthetic installed environment\n")
        self.calls = self.root / "manager-arguments.json"
        self.manager = self.root / "conda"
        self.manager.write_text("#!" + sys.executable + "\n" + r'''
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
assert args.pop(0)=='run'
while args[0] in ('--no-capture-output', '--live-stream'):
    args.pop(0)
option=args.pop(0)
if option.startswith('--prefix='):
    target=option.split('=',1)[1]
elif option in ('-p','--prefix'):
    target=args.pop(0)
elif option in ('-n','--name'):
    assert args.pop(0)=='fixture'
    target=TARGET
else:
    raise SystemExit('unsupported fixture selector')
if args and args[0]=='--':
    args.pop(0)
Path(CALLS).write_text(json.dumps(args))
os.environ['CONDA_PREFIX']=os.environ.get('BASIS_TEST_WRONG_PREFIX',target)
os.environ['PATH']=str(Path(target)/'bin')
os.execvpe(args[0],args,os.environ)
'''.replace("TARGET", repr(str(self.target))).replace("CALLS", repr(str(self.calls))))
        self.manager.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def executable(self, name, folder=None):
        path = (folder or self.target / "bin") / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
        return path

    def probe(self, executable, selector=None):
        prefix = [str(self.manager), "run", "--no-capture-output", *(selector or ["--prefix", str(self.target)])]
        cfg = {"executable": str(executable), "command_prefix": prefix,
               "python_executable": str(self.target / "bin/python"), "timeout_seconds": 10}
        runner = CommandRunner(cfg, self.root / "probe.log")
        identity = capture_tool_identity("vfdb", cfg, runner)
        return identity, runner

    def test_pythonless_diamond_hashes_selected_prefix_not_controller(self):
        diamond = self.executable("diamond")
        self.assertFalse((self.target / "bin/python").exists())
        identity, runner = self.probe("diamond")
        self.assertEqual(identity["prefix"], str(self.target))
        self.assertEqual(identity["executables"], {str(diamond): sha256_file(diamond)})
        self.assertEqual(identity["environment_sha256"], hash_object(file_inventory(self.target)))
        self.assertTrue(identity["reuse_supported"])
        self.assertTrue(runner.commands[0]["environment_isolation"]["applied"])
        command = json.loads(self.calls.read_text())
        self.assertEqual(command[:3], [sys.executable, "-I", "-c"])
        dependency = self.target / "native_dependency.dat"
        dependency.write_text("dependency content changed")
        changed, _ = self.probe("diamond")
        self.assertNotEqual(identity["environment_sha256"], changed["environment_sha256"])

    def test_isescan_python310_is_never_used_for_controller_hashing(self):
        isescan = self.executable("isescan.py")
        marker = self.root / "legacy-python-was-run"
        legacy = self.target / "bin/python"
        legacy.write_text("#!/bin/sh\nprintf 'called' > " + "'" + str(marker) + "'\n" +
                          "printf '%s\\n' \"AttributeError: module 'hashlib' has no attribute 'file_digest'\" >&2\nexit 1\n")
        legacy.chmod(0o755)
        (self.target / "conda-meta/python-3.10.18.json").write_text(json.dumps({"name": "python", "version": "3.10.18"}))
        identity, _ = self.probe("isescan.py")
        self.assertFalse(marker.exists())
        self.assertEqual(identity["prefix"], str(self.target))
        self.assertEqual(identity["executables"], {str(isescan): sha256_file(isescan)})
        self.assertTrue(identity["reuse_supported"])

    def test_absolute_tool_and_explicit_prefix_forms(self):
        tool = self.executable("tool with spaces")
        for selector in (["-p", str(self.target)], ["--prefix=" + str(self.target)], ["-n", "fixture"]):
            with self.subTest(selector=selector):
                identity, _ = self.probe(tool, selector)
                self.assertEqual(identity["prefix"], str(self.target))
                self.assertEqual(set(identity["executables"]), {str(tool)})
                self.assertTrue(identity["reuse_supported"])

    def test_external_and_symlinked_outside_tools_cannot_authorize_reuse(self):
        outside = self.executable("external-tool", self.root)
        link = self.target / "bin/external-link"
        link.symlink_to(outside)
        for name in (outside, "external-link"):
            with self.subTest(name=name):
                identity, _ = self.probe(name)
                self.assertEqual(set(identity["executables"]), {str(outside)})
                self.assertFalse(identity["reuse_supported"])

    def test_incorrect_activation_cannot_claim_selected_environment(self):
        self.executable("diamond")
        with patch.dict(os.environ, {"BASIS_TEST_WRONG_PREFIX": str(self.root / "other-env")}):
            with self.assertRaisesRegex(RuntimeError, "exit code 1"):
                self.probe("diamond")
        self.assertIn("Activated Conda prefix differs", (self.root / "probe.log").read_text())


if __name__ == "__main__":
    unittest.main()
