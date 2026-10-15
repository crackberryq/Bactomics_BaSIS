"""Generic subprocess routing regressions; no biological tools or GPU calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from basis.execution import CommandRunner
from basis.native_environment import native_child_environment, is_explicit_conda_run_prefix


CONDA = ["conda", "run", "--no-capture-output", "-p", "/native/tool"]


class EnvironmentPolicyTests(unittest.TestCase):
    def test_explicit_prefix_forms(self):
        for prefix in (
            CONDA, ["/opt/conda/bin/conda", "run", "--prefix=/native/tool"],
            ["conda", "run", "-n", "tool"], ["conda.exe", "run", "--name=tool"],
            ["conda", "run", "--cwd", "/work", "-vv", "-p", "/native/tool"],
        ):
            with self.subTest(prefix=prefix):
                self.assertTrue(is_explicit_conda_run_prefix(prefix))

    def test_unsupported_and_ambiguous_prefixes_do_not_trigger_cleanup(self):
        for prefix in (
            [], ["python"], ["bash", "-c", "conda run -p /native/tool"],
            ["mamba", "run", "-p", "/native/tool"], ["conda", "run"],
            ["conda", "run", "--prefix="], ["conda", "run", "-p"],
            ["conda", "run", "-p", "/one", "-n", "two"],
            ["conda", "run", "-p", "/one", "python"],
        ):
            with self.subTest(prefix=prefix):
                self.assertFalse(is_explicit_conda_run_prefix(prefix))

    def test_launcher_removed_conda_cuda_offline_and_other_settings_preserved(self):
        env = {
            "PATH": "/app/.venv/bin:/provider/bin:/usr/bin",
            "VIRTUAL_ENV": "/app/.venv", "PYTHONPATH": "PRIVATE_SENTINEL",
            "PYTHONHOME": "PRIVATE_HOME", "CONDA_PREFIX": "/provider",
            "CONDA_PREFIX_1": "/base", "CONDA_SHLVL": "2",
            "CUDA_VISIBLE_DEVICES": "0", "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1", "OMP_NUM_THREADS": "4",
            "LD_LIBRARY_PATH": "/preserved", "HF_TOKEN": "SECRET_TOKEN",
        }
        original = dict(env)
        clean, audit = native_child_environment(env, CONDA, launcher_prefix="/app/.venv", base_prefix="/provider")
        self.assertEqual(env, original)
        self.assertEqual(clean["PATH"], "/provider/bin:/usr/bin")
        for key in ("CONDA_PREFIX", "CONDA_PREFIX_1", "CONDA_SHLVL", "CUDA_VISIBLE_DEVICES",
                    "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "OMP_NUM_THREADS", "LD_LIBRARY_PATH", "HF_TOKEN"):
            self.assertEqual(clean[key], env[key])
        for key in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"):
            self.assertNotIn(key, clean)
        self.assertEqual(clean["PYTHONNOUSERSITE"], "1")
        self.assertTrue(audit["applied"])
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(audit))
        self.assertNotIn("PRIVATE_HOME", json.dumps(audit))
        self.assertNotIn("SECRET_TOKEN", json.dumps(audit))

    def test_all_supported_python_override_names_removed(self):
        names = ("VIRTUAL_ENV_PROMPT", "_OLD_VIRTUAL_PATH", "PYTHONUSERBASE",
                 "PYTHONSTARTUP", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__")
        clean, audit = native_child_environment({name: "PRIVATE" for name in names}, CONDA)
        self.assertFalse(set(names) & clean.keys())
        self.assertEqual(set(audit["removed_variable_names"]), set(names))
        self.assertNotIn("PRIVATE", json.dumps(audit))

    def test_unactivated_virtualenv_detected_by_runtime_prefix(self):
        clean, _ = native_child_environment({"PATH": "/app/.venv/bin:/provider/bin"}, CONDA,
                                            launcher_prefix="/app/.venv", base_prefix="/provider")
        self.assertEqual(clean["PATH"], "/provider/bin")

    def test_conda_only_environment_keeps_bin_path(self):
        env = {"PATH": "/provider/bin:/usr/bin", "CONDA_PREFIX": "/provider"}
        clean, audit = native_child_environment(env, CONDA, launcher_prefix="/provider", base_prefix="/provider")
        self.assertEqual(clean["PATH"], env["PATH"])
        self.assertEqual(audit["removed_path_directories"], [])

    def test_symlinked_launcher_duplicate_entries_and_empty_path_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "launcher"
            (launcher / "bin").mkdir(parents=True)
            alias = Path(tmp) / "alias"
            alias.symlink_to(launcher, target_is_directory=True)
            env = {"PATH": os.pathsep.join([str(alias / "bin"), str(launcher / "bin"), "/different/bin", ""])}
            clean, audit = native_child_environment(env, CONDA, launcher_prefix=str(launcher), base_prefix="/provider")
            self.assertEqual(clean["PATH"], "/different/bin" + os.pathsep)
            self.assertEqual(len(audit["removed_path_directories"]), 2)

    def test_absent_path_is_not_created(self):
        clean, _ = native_child_environment({}, CONDA)
        self.assertNotIn("PATH", clean)

    def test_unprefixed_environment_is_an_unmodified_copy(self):
        env = {"PATH": "/app/.venv/bin:/usr/bin", "VIRTUAL_ENV": "/app/.venv",
               "PYTHONPATH": "/needed", "PYTHONNOUSERSITE": "0"}
        clean, audit = native_child_environment(env, [], launcher_prefix="/app/.venv", base_prefix="/provider")
        self.assertEqual(clean, env)
        self.assertIsNot(clean, env)
        self.assertFalse(audit["applied"])
        self.assertEqual(audit["removed_variable_names"], [])


class CommandRunnerEnvironmentTests(unittest.TestCase):
    def _mock_run(self, prefix, gpus=None):
        with tempfile.TemporaryDirectory() as tmp:
            runner = CommandRunner({"command_prefix": prefix, "threads": 3, "gpus": gpus}, Path(tmp) / "commands.log")
            with patch("basis.execution.subprocess.Popen") as popen:
                import io
                popen.return_value.stdout = io.BytesIO(b"ok")
                popen.return_value.stderr = io.BytesIO(b"")
                popen.return_value.wait.return_value = 0
                popen.return_value.pid = 12345
                popen.return_value.returncode = 0
                runner.run(["python", "-c", "pass"])
            return popen.call_args.kwargs["env"], runner.commands[0], runner.logfile.read_text()

    def test_runner_conda_isolation_is_recorded_and_parent_is_unmodified(self):
        with patch.dict(os.environ, {"PATH": "/app/.venv/bin:/provider/bin", "VIRTUAL_ENV": "/app/.venv",
                                     "PYTHONPATH": "SECRET_OVERRIDE", "CUDA_VISIBLE_DEVICES": "0",
                                     "HF_HUB_OFFLINE": "1"}, clear=True):
            before = dict(os.environ)
            env, command, log = self._mock_run(CONDA)
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(env["PATH"], "/provider/bin")
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "0")
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")
        self.assertEqual(env["OMP_NUM_THREADS"], "3")
        self.assertTrue(command["environment_isolation"]["applied"])
        self.assertIn("Environment isolation:", log)
        self.assertNotIn("SECRET_OVERRIDE", log)
        self.assertNotIn("SECRET_OVERRIDE", json.dumps(command))

    def test_runner_unprefixed_python_overrides_are_retained(self):
        with patch.dict(os.environ, {"PATH": "/app/.venv/bin:/usr/bin", "VIRTUAL_ENV": "/app/.venv",
                                     "PYTHONPATH": "/needed", "PYTHONNOUSERSITE": "0"}, clear=True):
            env, command, _ = self._mock_run([])
        self.assertEqual(env["PATH"], "/app/.venv/bin:/usr/bin")
        self.assertEqual(env["VIRTUAL_ENV"], "/app/.venv")
        self.assertEqual(env["PYTHONPATH"], "/needed")
        self.assertEqual(env["PYTHONNOUSERSITE"], "0")
        self.assertFalse(command["environment_isolation"]["applied"])

    def test_gpu_zero_policy_still_applies(self):
        with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "0"}, clear=True):
            env, _, _ = self._mock_run(CONDA, gpus=0)
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "")

    def test_subprocess_failure_keeps_audit_and_parent_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = CommandRunner({"command_prefix": CONDA}, Path(tmp) / "commands.log")
            before = dict(os.environ)
            with patch("basis.execution.subprocess.Popen", side_effect=OSError("synthetic failure")):
                with self.assertRaisesRegex(OSError, "synthetic failure"):
                    runner.run(["python"])
            self.assertEqual(dict(os.environ), before)
            self.assertTrue(runner.commands[0]["environment_isolation"]["applied"])
            self.assertIn("error", runner.commands[0])

    @unittest.skipUnless(os.name == "posix", "Executable-script PATH simulation uses POSIX shebangs")
    def test_target_python_wins_after_simulated_conda_activation(self):
        """A launcher bin before the active Conda bin reproduces the routing bug."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher, provider, target = [root / name for name in ("launcher", "provider", "target")]
            for folder in (launcher, provider, target):
                (folder / "bin").mkdir(parents=True)
            def executable(path, body):
                path.write_text("#!" + sys.executable + "\n" + body)
                path.chmod(0o755)
            for folder, marker in ((launcher, "launcher"), (target, "target")):
                executable(folder / "bin/python", "print(" + repr(marker) + ")\n")
            conda = root / "conda"
            executable(conda, "import os, sys\n"
                "assert sys.argv[1:3] == ['run', '-p']\n"
                "target = sys.argv[3]\n"
                "old = os.environ['CONDA_PREFIX'] + '/bin'\n"
                "os.environ['PATH'] = os.pathsep.join(target + '/bin' if p == old else p "
                "for p in os.environ['PATH'].split(os.pathsep))\n"
                "os.environ['CONDA_PREFIX'] = target\n"
                "os.execvpe(sys.argv[4], sys.argv[4:], os.environ)\n")
            prefix = [str(conda), "run", "-p", str(target)]
            env = dict(os.environ)
            for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__"):
                env.pop(key, None)
            env.update(PATH=os.pathsep.join([str(launcher / "bin"), str(provider / "bin"), "/usr/bin"]),
                       VIRTUAL_ENV=str(launcher), CONDA_PREFIX=str(provider))
            baseline = subprocess.run(prefix + ["python"], env=env, text=True, capture_output=True, check=True)
            self.assertEqual(baseline.stdout.strip(), "launcher")
            with patch.dict(os.environ, env, clear=True):
                before = dict(os.environ)
                runner = CommandRunner({"command_prefix": prefix}, root / "commands.log")
                result = runner.run(["python"])
                self.assertEqual(dict(os.environ), before)
            self.assertEqual(result.stdout.strip(), "target")
            self.assertTrue(runner.commands[0]["environment_isolation"]["applied"])


if __name__ == "__main__":
    unittest.main()
