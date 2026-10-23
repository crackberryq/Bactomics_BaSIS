"""Native-wrapper integration with synthetic executables, never biological tools."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from safety.native_execution import execute
from safety.resources import SCHEMA as RESOURCE_SCHEMA
from safety.result_store import sha256, verify_receipt


FIXTURE_TOOL = r'''import json, pathlib, sys
root = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
with (root / "invocations.jsonl").open("a") as log:
    log.write(json.dumps({"args": args, "cwd": str(pathlib.Path.cwd())}) + "\n")
if args[0] == "-gffFull":
    pathlib.Path(args[-2]).write_text("synthetic arrays output\n")
    pathlib.Path(args[-1]).write_text("synthetic gff output\n")
    if "FAIL" in pathlib.Path(args[1]).read_text():
        print("synthetic failure diagnostics", file=sys.stderr)
        raise SystemExit(17)
elif args[0] == "load":
    folder = pathlib.Path("localDB")
    folder.mkdir()
    (folder / "index.txt").write_text("synthetic local database index\n")
elif args[0] == "main":
    if not pathlib.Path("localDB/index.txt").is_file():
        raise SystemExit("synthetic tool did not start in its local database directory")
    pathlib.Path("localDB/generated-index.txt").write_text("synthetic generated index\n")
    pathlib.Path("localDB/index.txt").write_text("synthetic modified local database index\n")
    output = pathlib.Path(args[args.index("--output_file") + 1])
    output.with_suffix(".txt").write_text("synthetic result table\n")
    output.with_suffix(".json").write_text("{}\n")
    (output.parent / "observed.json").write_text(json.dumps({"cwd": str(pathlib.Path.cwd()),
        "output_directory": str(output.parent), "local_database_present": True}))
    source = pathlib.Path(args[args.index("--input_sequence") + 1])
    if "FAIL" in source.read_text():
        print("synthetic analysis failure diagnostics", file=sys.stderr)
        raise SystemExit(17)
else:
    raise SystemExit("unexpected synthetic command")
'''


class NativeExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis-native execution-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tool = self.root / "fixture-tool"
        self.tool.write_text("#!" + sys.executable + "\n" + FIXTURE_TOOL)
        self.tool.chmod(0o755)
        self.input = self.root / "input.txt"
        self.input.write_text("synthetic input, no sequence analysis")
        self.sample = {"sample_id": "fixture", "assembly": str(self.input), "proteins": str(self.input)}
        self.source = self.root / "source.py"
        self.source.write_text("# synthetic parser and command identity\n")
        self.prefix = self.root / "environment"
        self.prefix.mkdir()
        self.environment = self.root / "environment.json"
        self.environment.write_text(json.dumps({"schema": "basis-snake-environment-v1",
            "status": "completed", "prefix": str(self.prefix),
            "packages": [{"name": "synthetic-fixture", "version": "1"}],
            "recipe_sha256": "fixture", "post_deploy_sha256": "fixture"}))
        self.database = self.root / "database.json"
        self.database.write_text('{"synthetic":true}\n')
        self.resource = self.root / "resource.json"
        self.resource.write_text(json.dumps({"schema": RESOURCE_SCHEMA, "status": "completed",
            "resource": "fixture", "artifacts": {str(self.database): {
                "bytes": self.database.stat().st_size, "sha256": sha256(self.database)}},
            "result": {"version": "synthetic-v1"}}))

    def configuration(self, module):
        return {"modules": {module: {"executable": str(self.tool), "database": str(self.database),
                    "threads": 1, "timeout_seconds": 5, "cache": True}},
                "environment_receipts": [str(self.environment)],
                "resource_receipts": [{"path": str(self.resource), "sha256": sha256(self.resource)}]}

    def calls(self):
        path = self.root / "invocations.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def run_job(self, module="minced", config=None, *, sample=None, load_database=False,
                database_receipt=None):
        destination = self.root / "native" / module
        if module == "rgi":
            destination /= "database" if load_database else "results"
        label = module + ("_database" if load_database else "")
        receipt = self.root / "completion" / (label + ".json")
        store = self.root / "result-store" / label
        with contextlib.redirect_stdout(io.StringIO()):
            return execute(module, sample or self.sample, config or self.configuration(module),
                destination, receipt, store, threads=1, sources=[self.source],
                load_database=load_database, database_receipt=database_receipt)

    @staticmethod
    def verify(module, sample, settings, payload):
        # This patch stands in only for scientific parsing; execution/storage are real.
        payload = Path(payload)
        expected = "rgi.txt" if module == "rgi" else "arrays.txt"
        if not (payload / expected).is_file():
            raise ValueError("synthetic output missing")
        (payload / "parsed.json").write_text('{"synthetic_verification":"passed"}\n')

    def test_completed_result_reuse_survives_missing_scheduler_receipt(self):
        with patch("safety.native_execution.native.verify", side_effect=self.verify) as verify:
            first, reused = self.run_job()
            self.assertFalse(reused)
            receipt = self.root / "completion/minced.json"
            saved = receipt.read_bytes()
            receipt.unlink()
            second, reused = self.run_job()
        self.assertTrue(reused)
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(receipt.read_bytes(), saved)
        destination = self.root / "native/minced"
        self.assertEqual(verify_receipt(receipt, destination), first)
        record = json.loads((Path(first["payload_root"]).parent / "commands.json").read_text())
        self.assertEqual(record[0]["status"], "completed")
        self.assertEqual(record[0]["returncode"], 0)

    def test_cache_false_executes_and_preserves_previous_success(self):
        config = self.configuration("minced")
        with patch("safety.native_execution.native.verify", side_effect=self.verify):
            first, _ = self.run_job(config=config)
            config["modules"]["minced"]["cache"] = False
            second, reused = self.run_job(config=config)
        self.assertFalse(reused)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(first["identity"], second["identity"])
        self.assertNotEqual(first["payload_root"], second["payload_root"])
        self.assertTrue(Path(first["payload_root"]).is_dir())
        self.assertEqual((self.root / "native/minced").resolve(), Path(second["payload_root"]))

    def test_native_failure_preserves_diagnostics_and_prior_success(self):
        with patch("safety.native_execution.native.verify", side_effect=self.verify) as verify:
            first, _ = self.run_job()
            receipt = self.root / "completion/minced.json"
            saved = receipt.read_bytes()
            self.input.write_text("FAIL synthetic native command")
            with self.assertRaisesRegex(RuntimeError, "17"):
                self.run_job()
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(receipt.read_bytes(), saved)
        self.assertEqual((self.root / "native/minced").resolve(), Path(first["payload_root"]))
        failures = list((self.root / "result-store/minced").glob("*/attempt-*/failure.json"))
        self.assertEqual(len(failures), 1)
        attempt = failures[0].parent
        self.assertTrue((attempt / "data/arrays.txt").is_file())
        self.assertIn("synthetic failure diagnostics", (attempt / "data/stderr.txt").read_text())
        self.assertIn("synthetic failure diagnostics", (attempt / "commands.log").read_text())
        record = json.loads((attempt / "commands.json").read_text())
        self.assertEqual(record[0]["status"], "failed")
        self.assertEqual(record[0]["returncode"], 17)
        self.assertFalse((attempt / "completed.json").exists())

    def test_failed_verification_does_not_publish_completion(self):
        with patch("safety.native_execution.native.verify", side_effect=ValueError("synthetic parser rejected output")):
            with self.assertRaisesRegex(ValueError, "synthetic parser rejected"):
                self.run_job()
        self.assertFalse((self.root / "completion/minced.json").exists())
        self.assertFalse((self.root / "native/minced").exists())
        failures = list((self.root / "result-store/minced").glob("*/attempt-*/failure.json"))
        self.assertEqual(len(failures), 1)
        self.assertTrue((failures[0].parent / "data/arrays.txt").is_file())

    def test_rgi_loader_reuse_and_private_analysis_database(self):
        config = self.configuration("rgi")
        database, reused = self.run_job("rgi", config, load_database=True)
        self.assertFalse(reused)
        self.assertEqual(self.calls()[0]["args"][0], "load")
        self.assertTrue((Path(database["payload_root"]) / "localDB/index.txt").is_file())
        changed_sample = copy.deepcopy(self.sample)
        changed_sample["sample_id"] = "other-sample"
        alternate = self.root / "other-input.txt"
        alternate.write_text("different synthetic input")
        changed_sample["assembly"] = changed_sample["proteins"] = str(alternate)
        reused_database, reused = self.run_job("rgi", config, load_database=True, sample=changed_sample)
        self.assertTrue(reused)
        self.assertEqual(reused_database, database)
        self.assertEqual(len(self.calls()), 1)
        receipt = self.root / "completion/rgi_database.json"
        with patch("safety.native_execution.native.verify", side_effect=self.verify) as verify, \
                patch("safety.native_execution.shutil.copytree", wraps=shutil.copytree) as copytree:
            analysis, reused = self.run_job("rgi", config, database_receipt=receipt)
            self.assertFalse(reused)
            copies_after_first = copytree.call_count
            self.assertGreater(copies_after_first, 0)
            (self.root / "completion/rgi.json").unlink()
            reused_analysis, reused = self.run_job("rgi", config, database_receipt=receipt)
            self.assertEqual(copytree.call_count, copies_after_first)
        self.assertTrue(reused)
        self.assertEqual(reused_analysis, analysis)
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(len(self.calls()), 2)
        observed = json.loads((Path(analysis["payload_root"]) / "observed.json").read_text())
        workspace = Path(analysis["payload_root"]).parent / "database-work"
        self.assertEqual(Path(observed["cwd"]), workspace)
        self.assertNotEqual(workspace, Path(database["payload_root"]))
        self.assertFalse(workspace.exists())
        self.assertEqual(Path(observed["output_directory"]), Path(analysis["payload_root"]))
        self.assertNotEqual(Path(analysis["payload_root"]), Path(database["payload_root"]))
        self.assertTrue(observed["local_database_present"])
        self.assertEqual(verify_receipt(receipt, self.root / "native/rgi/database"), database)
        self.assertEqual((Path(database["payload_root"]) / "localDB/index.txt").read_text(),
                         "synthetic local database index\n")
        self.assertFalse((Path(database["payload_root"]) / "localDB/generated-index.txt").exists())
        self.assertEqual(verify_receipt(self.root / "completion/rgi.json", self.root / "native/rgi/results"),
                         analysis)

    def test_rgi_failed_verification_preserves_private_workspace_and_loader(self):
        config = self.configuration("rgi")
        database, _ = self.run_job("rgi", config, load_database=True)
        receipt = self.root / "completion/rgi_database.json"
        with patch("safety.native_execution.native.verify", side_effect=ValueError("synthetic parser rejected output")):
            with self.assertRaisesRegex(ValueError, "synthetic parser rejected"):
                self.run_job("rgi", config, database_receipt=receipt)
        failures = list((self.root / "result-store/rgi").glob("*/attempt-*/failure.json"))
        self.assertEqual(len(failures), 1)
        workspace = failures[0].parent / "database-work"
        self.assertEqual((workspace / "localDB/index.txt").read_text(),
                         "synthetic modified local database index\n")
        self.assertTrue((workspace / "localDB/generated-index.txt").is_file())
        self.assertFalse((self.root / "completion/rgi.json").exists())
        self.assertEqual(verify_receipt(receipt, self.root / "native/rgi/database"), database)

    def test_rgi_failed_command_preserves_private_workspace_and_loader(self):
        config = self.configuration("rgi")
        database, _ = self.run_job("rgi", config, load_database=True)
        receipt = self.root / "completion/rgi_database.json"
        self.input.write_text("FAIL synthetic native analysis")
        with patch("safety.native_execution.native.verify", side_effect=self.verify) as verify:
            with self.assertRaisesRegex(RuntimeError, "17"):
                self.run_job("rgi", config, database_receipt=receipt)
        verify.assert_not_called()
        failures = list((self.root / "result-store/rgi").glob("*/attempt-*/failure.json"))
        self.assertEqual(len(failures), 1)
        attempt = failures[0].parent
        self.assertTrue((attempt / "database-work/localDB/generated-index.txt").is_file())
        self.assertIn("synthetic analysis failure diagnostics", (attempt / "commands.log").read_text())
        self.assertFalse((self.root / "completion/rgi.json").exists())
        self.assertEqual(verify_receipt(receipt, self.root / "native/rgi/database"), database)

    def test_rgi_analysis_rejects_changed_loaded_database_before_launch(self):
        config = self.configuration("rgi")
        database, _ = self.run_job("rgi", config, load_database=True)
        (Path(database["payload_root"]) / "localDB/index.txt").write_text("changed synthetic database index")
        with patch("safety.native_execution.native.verify", side_effect=self.verify) as verify:
            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                self.run_job("rgi", config, database_receipt=self.root / "completion/rgi_database.json")
        self.assertEqual(len(self.calls()), 1)
        verify.assert_not_called()
        self.assertFalse((self.root / "completion/rgi.json").exists())


if __name__ == "__main__":
    unittest.main()
