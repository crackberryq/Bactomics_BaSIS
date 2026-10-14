"""Unified command controls and output channels; no native/GPU analyses."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.cli import main, parser, _normalize_run_argv
from basis.config import default_config
from basis.demo import create_demo
from basis.progress import emit, enabled, verbosity


class RunControlTests(unittest.TestCase):
    def invoke(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def controlled_run(self, args, code=0):
        config = default_config()
        plan = {"test_plan": True}
        result = {"summary": {"kind": "run", "sample_count": 2},
                  "artifacts": {"html": "/fixture/report.html"}, "raw": "retained-json-evidence"}
        observed = {}
        def execute(actual_plan, engine, dry_run, force):
            observed.update(plan=actual_plan, engine=engine, dry_run=dry_run, force=force,
                            verbose=enabled())
            emit("fixture pipeline progress")
            return result, code
        with patch("basis.cli.load_config", return_value=config) as load, \
             patch("basis.pipeline.make_plan", return_value=plan) as make, \
             patch("basis.pipeline.run_pipeline", side_effect=execute), \
             patch("basis.run_summary.format_run_summary", return_value="Run complete; report: /fixture/report.html") as formatter:
            response = self.invoke(args)
        observed.update(config=config, load=load.call_args, make=make.call_args, formatter=formatter.call_args)
        return response, observed

    def test_implicit_options_clusters_equals_and_order_preserve_run_controls(self):
        variants = [
            (["-vde", "-c", "fixture.yaml"], "fixture.yaml", True, True, True, "local", False, False, 8),
            (["--config=fixture.yaml", "-ed"], "fixture.yaml", True, True, False, "local", False, False, 8),
            (["--cores", "2", "-cfixture.yaml", "-d"], "fixture.yaml", True, False, False, "local", False, False, 2),
            (["--engine=snakemake", "-e", "-c", "fixture.yaml"], "fixture.yaml", False, True, False, "snakemake", False, False, 8),
            (["--dry-run", "--config", "fixture.yaml"], "fixture.yaml", False, False, False, "local", True, False, 8),
            (["--force", "--config=fixture.yaml"], "fixture.yaml", False, False, False, "local", False, True, 8),
            (["--output", "json", "--config", "fixture.yaml", "--verbose"], "fixture.yaml", False, False, True, "local", False, False, 8),
        ]
        with patch.dict(os.environ, {"BASIS_VERBOSE": "0"}):
            for args, path, deep, extend, verbose, engine, dry_run, force, cores in variants:
                with self.subTest(args=args):
                    (code, output, _), seen = self.controlled_run(args)
                    self.assertEqual(code, 0)
                    self.assertEqual(json.loads(output)["raw"], "retained-json-evidence")
                    self.assertEqual(seen["load"].args, (path,))
                    self.assertEqual(seen["make"].args[1:], (deep, extend))
                    self.assertEqual(seen["config"]["execution"]["cores"], cores)
                    self.assertEqual((seen["engine"], seen["dry_run"], seen["force"]), (engine, dry_run, force))
                    self.assertEqual(seen["verbose"], verbose)
                    self.assertEqual(os.environ["BASIS_VERBOSE"], "0")

    def test_four_deep_extend_combinations_keep_flag_meaning(self):
        with patch.dict(os.environ, {"BASIS_VERBOSE": "0"}):
            for deep, extend in ((False, False), (True, False), (False, True), (True, True)):
                flags = (["-d"] if deep else []) + (["-e"] if extend else [])
                with self.subTest(deep=deep, extend=extend):
                    (code, output, _), seen = self.controlled_run(["run", "-c", "fixture.yaml", *flags])
                    self.assertEqual(code, 0)
                    json.loads(output)
                    self.assertEqual(seen["make"].args[1:], (deep, extend))

    def test_json_stdout_is_one_complete_document_even_with_verbose_progress(self):
        with patch.dict(os.environ, {"BASIS_VERBOSE": "0"}):
            (code, output, error), seen = self.controlled_run(["run", "-c", "fixture.yaml", "-vde"], code=2)
        self.assertEqual(code, 2)  # Incomplete requested scope is never made successful by presentation.
        self.assertEqual(json.loads(output)["summary"]["sample_count"], 2)
        self.assertNotIn("fixture pipeline progress", output)
        self.assertIn("fixture pipeline progress", error)
        self.assertEqual(error.count("Run complete; report:"), 1)
        self.assertEqual(seen["formatter"].kwargs, {"artifacts": {"html": "/fixture/report.html"}})

    def test_summary_output_contains_only_human_summary_and_preserves_exit_code(self):
        with patch.dict(os.environ, {"BASIS_VERBOSE": "0"}):
            (code, output, error), _ = self.controlled_run(["--output=summary", "-c", "fixture.yaml"], code=2)
        self.assertEqual(code, 2)
        self.assertEqual(output, "Run complete; report: /fixture/report.html\n")
        self.assertEqual(error, "")
        self.assertNotIn("retained-json-evidence", output)

    def test_root_help_version_and_unknown_options_remain_parser_owned(self):
        for args in (["--help"], ["--version"]):
            with self.subTest(args=args), contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as error:
                    main(args)
                self.assertEqual(error.exception.code, 0)
                self.assertTrue(output.getvalue())
                self.assertEqual(_normalize_run_argv(args), args)
        for args in (["--unrecognized"], ["-x", "-c", "fixture.yaml"],
                     ["run", "-c", "fixture.yaml", "--output", "invalid"],
                     ["demo", "--outdir", "fixture", "--output", "invalid"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main(args)
                self.assertEqual(error.exception.code, 2)

    def test_verbosity_restores_absent_and_existing_values_after_success_and_error(self):
        with patch.dict(os.environ, {}, clear=True):
            self.controlled_run(["run", "-c", "fixture.yaml", "-v"])
            self.assertNotIn("BASIS_VERBOSE", os.environ)
            with patch("basis.cli.load_config", side_effect=ValueError("fixture failure")):
                code, output, error = self.invoke(["run", "-c", "fixture.yaml", "-v"])
            self.assertEqual(code, 2)
            self.assertEqual(output, "")
            self.assertIn("fixture failure", error)
            self.assertNotIn("BASIS_VERBOSE", os.environ)
        for prior in ("0", "true", "custom"):
            with self.subTest(prior=prior), patch.dict(os.environ, {"BASIS_VERBOSE": prior}):
                self.controlled_run(["run", "-c", "fixture.yaml", "-v"])
                self.assertEqual(os.environ["BASIS_VERBOSE"], prior)
                with patch("basis.cli.load_config", side_effect=RuntimeError("fixture failure")):
                    self.invoke(["run", "-c", "fixture.yaml", "-v"])
                self.assertEqual(os.environ["BASIS_VERBOSE"], prior)

    def test_existing_verbose_environment_is_honored_without_cli_flag(self):
        with patch.dict(os.environ, {"BASIS_VERBOSE": "yes"}):
            (_, output, error), seen = self.controlled_run(["run", "-c", "fixture.yaml"])
            self.assertTrue(seen["verbose"])
            self.assertIn("fixture pipeline progress", error)
            self.assertEqual(os.environ["BASIS_VERBOSE"], "yes")
            json.loads(output)

    def test_all_preflight_and_import_verbose_flags_are_scoped(self):
        for command in ("validate", "doctor"):
            with self.subTest(command=command), patch.dict(os.environ, {"BASIS_VERBOSE": "0"}), \
                 patch("basis.cli.load_config", return_value={}), \
                 patch("basis.cli.check", return_value={"errors": [], "status": "passed"}):
                code, output, error = self.invoke([command, "-c", "fixture.yaml", "-v"])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(output)["status"], "passed")
                self.assertIn("started", error)
                self.assertEqual(os.environ["BASIS_VERBOSE"], "0")
        with patch.dict(os.environ, {"BASIS_VERBOSE": "0"}), \
             patch("basis.cli.import_study", return_value={"status": "ready_for_import"}):
            code, output, error = self.invoke(["import-samples", "--file", "fixture.csv", "--dry-run", "-v"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["status"], "ready_for_import")
            self.assertIn("Preview batch inputs", error)
            self.assertEqual(os.environ["BASIS_VERBOSE"], "0")

    def test_demo_verbose_and_summary_options_are_accepted(self):
        args = parser().parse_args(["demo", "--outdir", "fixture", "-vde", "--output", "summary"])
        self.assertTrue(args.verbose)
        self.assertTrue(args.deep)
        self.assertTrue(args.extend)
        self.assertEqual(args.output, "summary")

    def test_scoped_verbosity_nested_and_exception_cleanup(self):
        with patch.dict(os.environ, {"BASIS_VERBOSE": "0"}):
            with self.assertRaises(ValueError):
                with verbosity(True):
                    self.assertTrue(enabled())
                    with verbosity(False):
                        self.assertTrue(enabled())
                    self.assertEqual(os.environ["BASIS_VERBOSE"], "1")
                    raise ValueError("expected")
            self.assertEqual(os.environ["BASIS_VERBOSE"], "0")


class SyntheticRunControlTests(unittest.TestCase):
    def invoke(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_real_dry_run_builds_plan_but_never_calls_execution(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"BASIS_VERBOSE": "0"}):
            config = create_demo(Path(directory) / "fixture")
            with patch("basis.pipeline.execute_module", side_effect=AssertionError("dry run executed module")), \
                 patch("basis.pipeline.execute_deep", side_effect=AssertionError("dry run executed DeepSafe")), \
                 patch("basis.execution.CommandRunner.run", side_effect=AssertionError("dry run launched native tool")):
                code, output, error = self.invoke(["--dry-run", "--config=" + str(config), "-de"])
            self.assertEqual(code, 0, error)
            result = json.loads(output)
            self.assertEqual(result["summary"]["kind"], "plan")
            self.assertTrue(result["summary"]["options"]["dry_run"])
            self.assertTrue(result["summary"]["options"]["deep"])
            self.assertTrue(result["summary"]["options"]["extend"])
            self.assertTrue(error.strip())

    def test_real_synthetic_demo_verbose_json_and_summary_outputs(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"BASIS_VERBOSE": "0"}):
            for output_mode in ("json", "summary"):
                with self.subTest(output_mode=output_mode), \
                     patch("basis.execution.CommandRunner.run", side_effect=AssertionError("synthetic demo launched a native tool")):
                    code, output, error = self.invoke(["demo", "--outdir", str(Path(directory) / output_mode),
                                                       "-vde", "--output", output_mode])
                    self.assertEqual(code, 0, error)
                    self.assertIn("Pipeline execution", error)
                    if output_mode == "json":
                        result = json.loads(output)
                        self.assertEqual(result["summary"]["kind"], "run")
                        self.assertEqual(result["summary"]["sample_count"], 2)
                        self.assertTrue(Path(result["artifacts"]["html"]).is_file())
                    else:
                        self.assertIn("DEMO_A", output)
                        self.assertIn("DEMO_B", output)
                        self.assertIn("report.html", output)
                        with self.assertRaises(json.JSONDecodeError):
                            json.loads(output)
                    self.assertEqual(os.environ["BASIS_VERBOSE"], "0")


if __name__ == "__main__":
    unittest.main()
