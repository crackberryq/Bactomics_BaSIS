"""Installation-path contracts; no native tools, downloads or biology required."""
import contextlib
import io
from importlib.resources import as_file, files
import json
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import basis
from basis import environment_setup
from basis import optional_setup


class ReleasePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_version_metadata_uses_single_authority(self):
        project = Path(__file__).resolve().parent.parent
        metadata = tomllib.loads((project / "pyproject.toml").read_text())
        self.assertIn("version", metadata["project"]["dynamic"])
        self.assertNotIn("version", metadata["project"])
        self.assertEqual(metadata["tool"]["setuptools"]["dynamic"]["version"]["attr"], "basis.__version__")
        self.assertEqual(basis.__version__, "0.3.0rc6")

    def test_packaged_recipes_match_captured_recipes(self):
        project = Path(__file__).resolve().parent.parent
        captured = {p.stem: p.read_bytes() for p in (project / "envs").glob("*.yml")}
        packaged = {name: environment_setup.recipe_resource(name).read_bytes()
                    for name in environment_setup.recipe_names()}
        self.assertTrue(captured)
        self.assertEqual(packaged, captured)

    def test_runtime_assets_are_available_through_package_resources(self):
        root = files("basis").joinpath("resources")
        for name in ("Snakefile", "SetupSnakefile", "rules/setup.smk", "rules/bootstrap.smk", "rules/deep_candidates.smk", "motifs/rules-v1.json", "placement/registry-v2.json", "profiles/cfr_family.yaml", "comparators/reviewed-v1/references.tsv"):
            self.assertTrue(root.joinpath(name).is_file(), name)

    def test_plan_is_read_only_and_defaults_prefix_to_explicit_workspace(self):
        output = io.StringIO()
        project = self.root / "new workspace"
        with patch("basis.environment_setup.subprocess.run") as run, contextlib.redirect_stdout(output):
            code = environment_setup.main(["--project", str(project), "--env", "minced"])
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertFalse(project.exists())
        self.assertIn(str(project / ".basis-envs/basis-minced"), output.getvalue())

    def test_create_requires_explicit_selection(self):
        with patch("basis.environment_setup.subprocess.run") as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:
                environment_setup.main(["--project", str(self.root), "--create"])
        self.assertEqual(exc.exception.code, 2)
        run.assert_not_called()

    def test_existing_prefix_is_preserved_and_never_installed_over(self):
        prefix = self.root / ".basis-envs/basis-minced"
        prefix.mkdir(parents=True)
        sentinel = prefix / "keep.txt"
        sentinel.write_text("preserved")
        from basis.environment_workflow import run_setup
        with patch("basis.environment_setup.shutil.which", return_value="/usr/bin/conda"), \
             patch("basis.environment_workflow._command") as run, contextlib.redirect_stdout(io.StringIO()):
            _, code = run_setup(self.root, self.root / ".basis-envs", ("minced",), engine="local")
        self.assertEqual(code, 2)
        run.assert_not_called()
        self.assertEqual(sentinel.read_text(), "preserved")
        summary = json.loads(next((self.root / ".basis-setup/attempts").glob("*/summary.json")).read_text())
        self.assertEqual(summary["status"], "incomplete")
        self.assertEqual(summary["environments"][0]["status"], "failed")

    def test_creation_delegates_to_verified_workflow_with_explicit_workspace(self):
        summary = {"summary_path": str(self.root / "summary.json"), "status": "completed"}
        with patch("basis.environment_setup.shutil.which", return_value="/usr/bin/conda"), \
             patch("basis.environment_workflow.run_setup", return_value=(summary, 0)) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            code = environment_setup.main(["--project", str(self.root), "--env", "minced", "--create"])
        self.assertEqual(code, 0)
        args, kwargs = run.call_args
        self.assertEqual(args, (self.root, self.root / ".basis-envs", ("minced",)))
        self.assertEqual(kwargs["manager"], "conda")
        self.assertEqual(kwargs["engine"], "snakemake")

    def test_installed_package_and_site_packages_destinations_are_rejected(self):
        package_dir = Path(basis.__file__).resolve().parent
        with self.assertRaisesRegex(ValueError, "outside"):
            environment_setup.workspace_path(package_dir / "new-study")
        fake_site = self.root / "site-packages"
        with patch("basis.environment_setup.sysconfig.get_path", return_value=str(fake_site)):
            with self.assertRaisesRegex(ValueError, "outside"):
                environment_setup.workspace_path(fake_site / "other")

    def test_optional_install_uses_packaged_recipe_outside_source_checkout(self):
        seen = []
        packages = [{"name": k, "version": v} for k, v in optional_setup.PINS["minced"].items()]
        def fake_run(command, **kwargs):
            seen.append(command)
            recipe = Path(command[command.index("--file") + 1])
            self.assertEqual(recipe.read_bytes(), environment_setup.recipe_resource("minced").read_bytes())
        with patch("basis.optional_setup.run", side_effect=fake_run), \
             patch("basis.optional_setup.package_list", return_value=packages):
            prefix, result = optional_setup.ensure_environment(self.root, "minced", True)
        self.assertEqual(prefix, self.root / ".basis-envs/basis-minced")
        self.assertEqual(result, packages)
        self.assertEqual(len(seen), 1)
        self.assertFalse((self.root / "envs").exists())

    def test_shell_wrapper_plan_supports_external_working_directory(self):
        script = Path(__file__).resolve().parent.parent / "scripts/setup_envs.sh"
        completed = subprocess.run(["bash", str(script), "--env", "minced"], cwd=self.root,
                                   check=True, capture_output=True, text=True)
        self.assertIn(str(self.root / ".basis-envs/basis-minced"), completed.stdout)
        self.assertFalse((self.root / ".basis-envs").exists())


if __name__ == "__main__":
    unittest.main()
