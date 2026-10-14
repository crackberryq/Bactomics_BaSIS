"""Version probes use each tool's native CLI without overriding configured flags."""
import subprocess
import unittest

from basis.execution import probe_version


class ProbeRunner:
    def __init__(self):
        self.calls = []

    def run(self, argv):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "synthetic version\n", "")


class VersionProbeTests(unittest.TestCase):
    def test_rgi_default_uses_main_subcommand(self):
        runner = ProbeRunner()
        result = probe_version("rgi", {"executable": "/tools/rgi", "version": "6.0.8"}, runner)
        self.assertEqual(runner.calls, [["/tools/rgi", "main", "--version"]])
        self.assertEqual(result, {"status": "completed", "output": "synthetic version",
                                  "declared_version": "6.0.8"})

    def test_amrfinder_probe_uses_configured_database(self):
        runner = ProbeRunner()
        result = probe_version("amrfinder", {"executable": "/tools/amrfinder",
            "database": "/data/custom amrfinder database"}, runner)
        self.assertEqual(runner.calls, [["/tools/amrfinder", "-V", "--database",
                                        "/data/custom amrfinder database"]])
        self.assertEqual(result["status"], "completed")

    def test_amrfinder_without_database_keeps_native_version_flag(self):
        runner = ProbeRunner()
        probe_version("amrfinder", {"executable": "amrfinder", "database": None}, runner)
        self.assertEqual(runner.calls, [["amrfinder", "-V"]])

    def test_explicit_version_args_override_tool_defaults(self):
        for module in ("rgi", "amrfinder"):
            with self.subTest(module=module):
                runner = ProbeRunner()
                config = {"executable": module, "database": "/data/custom-db",
                          "version_args": ["--custom-version", "literal argument"]}
                probe_version(module, config, runner)
                self.assertEqual(runner.calls, [[module, "--custom-version", "literal argument"]])
                self.assertEqual(config["version_args"], ["--custom-version", "literal argument"])

    def test_explicit_empty_version_args_disable_probe(self):
        for module in ("rgi", "amrfinder"):
            with self.subTest(module=module):
                runner = ProbeRunner()
                result = probe_version(module, {"executable": module, "database": "/data/custom-db",
                    "version_args": [], "version": "declared"}, runner)
                self.assertEqual(runner.calls, [])
                self.assertEqual(result, {"status": "not_assessed", "declared_version": "declared"})

    def test_other_tool_default_remains_version_flag(self):
        runner = ProbeRunner()
        probe_version("blastp", {"executable": "blastp"}, runner)
        self.assertEqual(runner.calls, [["blastp", "--version"]])


if __name__ == "__main__":
    unittest.main()
