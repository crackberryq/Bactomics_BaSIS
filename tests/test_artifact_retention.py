"""Storage-only retention contracts; no biological tools or data."""
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety.artifact_retention import archive_paths, verified_snakemake_version
from basis.retention import preserve_existing, new_attempt, publish_link


class ControllerVersionTests(unittest.TestCase):
    def checked(self, runtime, metadata):
        module = SimpleNamespace(__version__=runtime)
        with patch.dict(sys.modules, {"snakemake": module}), patch(
                "safety.artifact_retention.importlib.metadata.version",
                side_effect=metadata if isinstance(metadata, Exception) else None,
                return_value=metadata):
            return verified_snakemake_version()

    def test_supported_runtime_accepts_conda_placeholder_metadata(self):
        for version in ("9.6.2", "9.27.1"):
            with self.subTest(version=version):
                self.assertEqual(self.checked(version, "0.0.0"), version)

    def test_supported_runtime_accepts_matching_or_absent_distribution(self):
        self.assertEqual(self.checked("9.6.2", "9.6.2"), "9.6.2")
        self.assertEqual(self.checked("9.27.1", importlib.metadata.PackageNotFoundError("snakemake")), "9.27.1")

    def test_distribution_metadata_never_blesses_unknown_runtime(self):
        for runtime in (None, "", "0.0.0", "9.6.3", "10.0.0"):
            with self.subTest(runtime=runtime), self.assertRaisesRegex(RuntimeError, "runtime"):
                self.checked(runtime, "9.6.2")

    def test_conflicting_or_unrecognized_metadata_fails_closed(self):
        for metadata in ("9.27.1", "9.6.3", "", "unknown"):
            with self.subTest(metadata=metadata), self.assertRaisesRegex(RuntimeError, "conflicts"):
                self.checked("9.6.2", metadata)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="basis retention ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_moves_native_directory_logs_and_sidecars_without_losing_bytes(self):
        output = self.root / "native"
        output.mkdir()
        (output / "partial.bin").write_bytes(b"partial\x00output")
        log = self.root / "commands.log"
        log.write_text("native failure")
        archive = archive_paths(self.root, [output, output / "partial.bin", log], rule="test")
        self.assertEqual((archive / "files/native/partial.bin").read_bytes(), b"partial\x00output")
        self.assertEqual((archive / "files/commands.log").read_text(), "native failure")
        self.assertFalse(output.exists())
        manifest = json.loads((archive / "manifest.json").read_text())
        self.assertEqual(manifest["state"], "complete")
        self.assertEqual(len(manifest["entries"]), 2)

    def test_never_moves_inputs_external_paths_or_symlink_ancestor(self):
        output = self.root / "native"
        output.mkdir()
        source = output / "input.txt"
        source.write_text("unchanged")
        with self.assertRaisesRegex(ValueError, "current input"):
            archive_paths(self.root, [output], rule="test", inputs=[source])
        with self.assertRaisesRegex(ValueError, "unowned"):
            archive_paths(output, [source.parent.parent], rule="test")
        alias = self.root / "alias"
        alias.symlink_to(output, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked ancestors"):
            archive_paths(self.root, [alias / "input.txt"], rule="test")
        self.assertEqual(source.read_text(), "unchanged")

    def test_unique_attempts_and_replaced_links_keep_prior_payloads(self):
        destination = self.root / "results"
        previous = []
        for label in ("first", "second", "failed third"):
            attempt = new_attempt(destination)
            payload = attempt / "payload.txt"
            payload.write_text(label)
            previous.append(payload)
            publish_link(destination, payload)
        self.assertEqual([p.read_text() for p in previous], ["first", "second", "failed third"])
        self.assertEqual(destination.read_text(), "failed third")
        self.assertEqual(len(list((self.root / ".previous-results").glob("*/retention.json"))), 2)
        retained = preserve_existing(destination)
        self.assertEqual(retained.read_text(), "failed third")
        self.assertTrue(previous[-1].is_file())

    def test_direct_command_stdout_replacement_retains_previous_bytes(self):
        from safety.runtime import DirectRunner
        target = self.root / "stdout.bin"
        runner = DirectRunner({"timeout_seconds": 5}, self.root / "commands.log")
        runner.run([sys.executable, "-c", "print('first')"], stdout_path=target)
        runner.run([sys.executable, "-c", "print('second')"], stdout_path=target)
        self.assertEqual(target.read_bytes(), b"second\n")
        retained = list((self.root / "retained").glob("*/files/stdout.bin"))
        self.assertEqual([p.read_bytes() for p in retained], [b"first\n"])

    @unittest.skipUnless(importlib.util.find_spec("snakemake"), "Snakemake not installed")
    def test_scheduler_retry_preserves_failed_files_logs_and_directory_sidecars(self):
        source = self.root / "input.txt"
        source.write_text("input untouched")
        script = self.root / "worker.py"
        script.write_text('''from pathlib import Path
import sys
counter=Path("calls.txt")
n=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(n))
out=Path("results");out.mkdir(exist_ok=True)
(out/"native.txt").write_text("attempt "+str(n))
(out/"extra.bin").write_bytes(bytes([n])*19)
Path("worker.log").write_text("log "+str(n))
raise SystemExit(1 if n == 1 else 0)
''')
        snake = self.root / "Snakefile"
        snake.write_text(f'''import sys
sys.path.insert(0, {str(ROOT / "workflow/lib")!r})
from safety.artifact_retention import install
install(workflow, [{str(self.root)!r}])
rule example:
    input: "input.txt"
    output: directory("results")
    log: "worker.log"
    shell: "{{sys.executable}} worker.py"
''')
        result = subprocess.run([sys.executable, "-m", "snakemake", "--snakefile", str(snake),
            "--directory", str(self.root), "--cores", "1", "--retries", "1"],
            text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / "results/native.txt").read_text(), "attempt 2")
        retained = list((self.root / "retained").glob("*/files/results/native.txt"))
        self.assertEqual([p.read_text() for p in retained], ["attempt 1"])
        self.assertEqual((retained[0].parent / "extra.bin").read_bytes(), bytes([1])*19)
        self.assertEqual((retained[0].parents[1] / "worker.log").read_text(), "log 1")
        self.assertEqual(source.read_text(), "input untouched")
        again = subprocess.run([sys.executable, "-m", "snakemake", "--snakefile", str(snake),
            "--directory", str(self.root), "--cores", "1", "--forceall"], text=True, capture_output=True)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(sorted(p.read_text() for p in (self.root / "retained").glob("*/files/results/native.txt")),
                         ["attempt 1", "attempt 2"])


if __name__ == "__main__":
    unittest.main()
