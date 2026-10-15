"""Streamlit's actual AppTest runtime; optional dependency stays optional."""
import importlib.util
import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Install the ui extra for interface tests")
class GuiAppTests(unittest.TestCase):
    def test_pages_render_without_launching_jobs(self):
        from streamlit.testing.v1 import AppTest
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp):
            app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30)
            for page in ("Overview", "Setup", "Resources", "Samples", "Run", "Jobs", "Results & export"):
                app.session_state["page"] = page
                app.run(timeout=20)
                self.assertEqual(list(app.exception), [], page)
            self.assertFalse((Path(tmp) / "jobs").exists())

    def test_button_submits_once_and_rerun_does_not_submit(self):
        from streamlit.testing.v1 import AppTest
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp), \
             patch("basis.jobs.submit", return_value={"id": "a"*24, "state": "queued"}) as submit:
            app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30).run()
            app.button(key="run_demo").click().run()
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(submit.call_args.args[1]["engine"], "snakemake")
            app.run()
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(list(app.exception), [])

    def test_results_include_separate_deepsafe_records(self):
        from streamlit.testing.v1 import AppTest
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp):
            results = Path(tmp) / "results"
            results.mkdir()
            (results / "evidence.json").write_text(json.dumps({"records": [], "deep_records": [{
                "module": "deepsafe", "sample_id": "A", "execution_status": "completed",
                "metrics": {"selected_candidates": 14, "completed_candidates": 14, "deferred_candidates": 3663}}]}))
            app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30)
            app.session_state["page"] = "Results & export"
            app.run()
            app.text_input[0].set_value(str(results)).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("selected 14" in c.value and "deferred 3663" in c.value for c in app.caption))

    def test_guided_navigation_does_not_launch_analysis(self):
        from streamlit.testing.v1 import AppTest
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp), \
             patch("basis.jobs.submit") as submit:
            app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30).run()
            app.button(key="start_01").click().run()
            self.assertEqual(app.session_state["page"], "Samples")
            app.button(key="samples_next").click().run()
            self.assertEqual(app.session_state["page"], "Run")
            self.assertFalse(app.exception)
            submit.assert_not_called()

    def test_setup_uses_snakemake_and_requires_download_selection(self):
        from streamlit.testing.v1 import AppTest
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp), \
             patch("basis.jobs.submit", return_value={"id": "a"*24, "state": "queued"}) as submit:
            app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30)
            app.session_state["page"] = "Setup"
            app.run()
            button = next(b for b in app.button if b.label == "Install missing tool environments")
            self.assertTrue(button.disabled)
            self.assertFalse(any(c.key == "retry_failed" for c in app.checkbox))
            next(b for b in app.button if b.label == "Preview setup plan").click().run()
            request = submit.call_args.args[1]
            self.assertEqual(request["engine"], "snakemake")
            self.assertFalse(request["create"])
            self.assertNotIn("retry_failed", request)
            app.checkbox(key="allow_packages").check().run()
            next(b for b in app.button if b.label == "Install missing tool environments").click().run()
            request = submit.call_args.args[1]
            self.assertEqual(request["engine"], "snakemake")
            self.assertTrue(request["create"])
            self.assertNotIn("retry_failed", request)
            self.assertFalse(app.exception)
            self.assertTrue(any("This step prepares tool environments only" in info.value for info in app.info))

    def test_existing_environment_folder_is_not_claimed_ready(self):
        from streamlit.testing.v1 import AppTest
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp):
            prefix = Path(tmp) / ".basis-envs" / "basis-core-amr"
            prefix.mkdir(parents=True)
            app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30)
            app.session_state["page"] = "Setup"
            app.run()
            frame = app.dataframe[0].value
            status = frame.loc[frame.Environment == "core-amr", "Installation record"].iloc[0]
            self.assertEqual(status, "Folder exists · unverified")
            summary = Path(tmp) / ".basis-setup" / "attempts" / "check" / "summary.json"
            summary.parent.mkdir(parents=True)
            summary.write_text(json.dumps({"schema": "basis-environment-setup-summary-v1",
                "environments": [{"name": "core-amr", "prefix": str(prefix), "status": "ready"}]}))
            app.run()
            frame = app.dataframe[0].value
            status = frame.loc[frame.Environment == "core-amr", "Installation record"].iloc[0]
            self.assertEqual(status, "Verified during setup")

    def test_managed_environment_receipt_uses_actual_hashed_prefix(self):
        from streamlit.testing.v1 import AppTest
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp):
            prefix = Path(tmp) / ".basis-envs" / "1234567890_"
            prefix.mkdir(parents=True)
            summary = Path(tmp) / "jobs" / ("a"*24) / "setup" / "summary.json"
            summary.parent.mkdir(parents=True)
            summary.write_text(json.dumps({"schema": "basis-environment-setup-summary-v1",
                "environments": [{"name": "core-amr", "recipe_name": "core-amr", "backend": "managed",
                    "prefix": str(prefix), "requested_prefix": str(prefix.parent / "basis-core-amr"), "status": "ready"}]}))
            app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30)
            app.session_state["page"] = "Setup"
            app.run()
            frame = app.dataframe[0].value
            row = frame.loc[frame.Environment == "core-amr"].iloc[0]
            self.assertEqual(row["Installation record"], "Verified during setup")
            self.assertEqual(row["Deployment"], "managed")
            self.assertEqual(row["Environment path"], str(prefix))
            self.assertFalse(app.exception)

    def test_run_uses_only_snakemake_and_resumes_same_study(self):
        from streamlit.testing.v1 import AppTest
        from basis.config import default_config
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp), \
             patch("basis.config.load_config", return_value=default_config()), \
             patch("basis.jobs.submit", return_value={"id": "a"*24, "state": "queued"}) as submit:
            app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30)
            app.session_state["page"] = "Run"
            app.run()
            app.text_input(key="run_config").set_value("/study/config.yaml").run()
            app.button(key="run_study").click().run()
            self.assertEqual(submit.call_args.args[1]["engine"], "snakemake")
            self.assertEqual(submit.call_args.args[1]["environment_backend"], "auto")
            app.selectbox(key="run_environment_backend").select("external").run()
            app.button(key="run_study").click().run()
            self.assertEqual(submit.call_args.args[1]["environment_backend"], "external")
            self.assertTrue(submit.call_args.kwargs["new_attempt"])
            self.assertFalse(any("local" in getattr(widget, "options", []) for widget in app.selectbox))
            self.assertEqual(submit.call_args.args[1]["config"], "/study/config.yaml")
            next(b for b in app.button if b.label == "Check readiness").click().run()
            self.assertNotIn("engine", submit.call_args.args[1])
            self.assertFalse(app.exception)

    def test_resource_preparation_requires_explicit_selection_and_consent(self):
        from streamlit.testing.v1 import AppTest
        import basis
        resource_data = {"resources": {"reviewed_files": {"backend": "pinned_files", "version": "1", "destination": "/db/pinned"}}}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp), \
             patch("basis.resource_setup.load_resource_config", return_value=resource_data), \
             patch("basis.jobs.submit", return_value={"id": "a"*24, "state": "queued"}) as submit:
            app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30)
            app.session_state["page"] = "Resources"
            app.run()
            app.text_input(key="resource_config").set_value("/db/resources.yaml").run()
            self.assertTrue(next(b for b in app.button if b.label == "Prepare selected resources").disabled)
            app.multiselect(key="selected_resources").select("reviewed_files").run()
            next(b for b in app.button if b.label == "Preview resource plan").click().run()
            self.assertEqual(submit.call_args.args[1]["kind"], "resources")
            self.assertFalse(submit.call_args.args[1]["execute"])
            self.assertTrue(next(b for b in app.button if b.label == "Prepare selected resources").disabled)
            app.checkbox(key="allow_resources").check().run()
            next(b for b in app.button if b.label == "Prepare selected resources").click().run()
            self.assertTrue(submit.call_args.args[1]["execute"])
            self.assertEqual(submit.call_args.args[1]["resources"], ["reviewed_files"])
            self.assertTrue(any("Pinned files alone may still need indexing" in item.value for item in app.info))
            self.assertFalse(app.exception)

    def test_resource_yaml_error_is_actionable_without_submitting_a_job(self):
        from streamlit.testing.v1 import AppTest
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp), \
             patch("basis.jobs.submit") as submit:
            config = Path(tmp) / "broken-resources.yaml"
            config.write_text("schema_version: [}\n")
            app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30)
            app.session_state["page"] = "Resources"
            app.run()
            app.text_input(key="resource_config").set_value(str(config)).run()
            self.assertFalse(app.exception)
            self.assertTrue(app.error)
            self.assertTrue(next(b for b in app.button if b.label == "Prepare selected resources").disabled)
            submit.assert_not_called()

    def test_setup_reuses_explicit_existing_environment_directory(self):
        from streamlit.testing.v1 import AppTest
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=str(Path(tmp) / "new-workspace")), \
             patch("basis.jobs.submit", return_value={"id": "a"*24, "state": "queued"}) as submit:
            existing = Path(tmp) / "old-install" / ".basis-envs"
            (existing / "basis-core-amr").mkdir(parents=True)
            app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30)
            app.session_state["page"] = "Setup"
            app.run()
            app.text_input(key="setup_prefix_root").set_value(str(existing)).run()
            frame = app.dataframe[0].value
            status = frame.loc[frame.Environment == "core-amr", "Installation record"].iloc[0]
            self.assertEqual(status, "Folder exists · unverified")
            next(b for b in app.button if b.label == "Preview setup plan").click().run()
            self.assertEqual(submit.call_args.args[1]["prefix_root"], str(existing))
            app.checkbox(key="allow_packages").check().run()
            next(b for b in app.button if b.label == "Install missing tool environments").click().run()
            self.assertEqual(submit.call_args.args[1]["prefix_root"], str(existing))
            self.assertTrue(submit.call_args.args[1]["create"])
            self.assertFalse((Path(tmp) / "new-workspace" / ".basis-envs").exists())
            self.assertFalse(app.exception)


    def test_hybas_initialization_is_explicit_and_does_not_run_samples(self):
        from streamlit.testing.v1 import AppTest
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp), \
             patch("basis.jobs.submit", return_value={"id": "a"*24, "state": "queued"}) as submit:
            app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30)
            app.session_state["page"] = "Samples"
            app.run()
            self.assertTrue(app.button(key="init_hybas").disabled)
            app.text_input(key="hybas_dir").set_value("/home/research/isolate2").run()
            submit.assert_not_called()
            app.button(key="init_hybas").click().run()
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(submit.call_args.args[1], {"kind": "init", "hybas_dir": "/home/research/isolate2", "preset": "full"})
            app.run()
            self.assertEqual(submit.call_count, 1)
            self.assertFalse(app.exception)

    def test_saved_study_picker_populates_config_without_launching(self):
        from streamlit.testing.v1 import AppTest
        from basis.config import default_config
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp), \
             patch("basis.config.load_config", return_value=default_config()), patch("basis.jobs.submit") as submit:
            study = Path(tmp) / "studies" / "isolate2" / "config.yaml"
            study.parent.mkdir(parents=True)
            study.write_text("project: isolate2")
            app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30)
            app.session_state["page"] = "Run"
            app.run()
            app.selectbox(key="saved_study").select(str(study)).run()
            self.assertEqual(app.text_input(key="run_config").value, str(study))
            self.assertEqual(app.session_state["active_study"], str(study))
            submit.assert_not_called()
            self.assertFalse(app.exception)

    def test_progress_displays_observed_scheduler_state_only(self):
        from streamlit.testing.v1 import AppTest
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp):
            logfile = Path(tmp) / "console.log"
            logfile.write_text("localrule bootstrap_resource:\n3 of 12 steps (25%) done\n")
            job = {"id": "a"*24, "kind": "setup", "state": "running", "created_at": "2026-10-15T00:00:00+00:00",
                   "command": ["basis", "setup-environments"], "directory": tmp, "log": str(logfile), "outdir": None}
            with patch("basis.jobs.list_jobs", return_value=[job]), patch("basis.jobs.read_job", return_value=job):
                app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30)
                app.session_state["page"] = "Jobs"
                app.run()
                self.assertFalse(app.exception)
                self.assertTrue(any("bootstrap resource" in item.value for item in app.markdown))
                self.assertEqual(app.metric[1].value, "running")
                self.assertFalse(any("completed" == item.value for item in app.metric))

    def test_theme_helpers_escape_user_content_and_keep_discovery_shallow(self):
        from basis.gui_theme import hero, state_badge, study_configurations
        self.assertIn("&lt;script&gt;", hero("<script>", "quoted & text", "test"))
        self.assertNotIn("<script>", state_badge("<script>"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "study" / "config.yaml"
            first.parent.mkdir()
            first.write_text("schema_version: 1")
            deep = root / "study" / "results" / "nested" / "config.yaml"
            deep.parent.mkdir(parents=True)
            deep.write_text("schema_version: 1")
            self.assertEqual(study_configurations(root, []), [str(first)])



    def test_stop_control_calls_verified_job_api_only_on_click(self):
        from streamlit.testing.v1 import AppTest
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp):
            logfile = Path(tmp) / "console.log"
            logfile.write_text("rule prepare_resources:\n")
            job = {"id": "a"*24, "kind": "run", "state": "running", "created_at": "2026-10-15T00:00:00+00:00",
                   "command": ["basis", "run"], "directory": tmp, "log": str(logfile), "outdir": None}
            with patch("basis.jobs.list_jobs", return_value=[job]), patch("basis.jobs.read_job", return_value=job), \
                 patch("basis.jobs.stop_job", return_value={"state": "cancelled", "message": "Verified launcher stopped", "stop_result": {"status": "stopped"}}) as stop:
                app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30)
                app.session_state["page"] = "Jobs"
                app.run()
                stop.assert_not_called()
                app.button(key="stop_" + job["id"]).click().run()
                stop.assert_called_once_with(Path(tmp), job["id"])
                self.assertFalse(app.exception)
                self.assertTrue(any("Verified launcher stopped" in x.value for x in app.info))

    def test_stop_refusal_is_visible_and_does_not_claim_cancellation(self):
        from streamlit.testing.v1 import AppTest
        import basis
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BASIS_GUI_WORKSPACE=tmp):
            logfile = Path(tmp) / "console.log"
            logfile.write_text("rule prepare_resources:\n")
            job = {"id": "a"*24, "kind": "run", "state": "running", "created_at": "2026-10-15T00:00:00+00:00",
                   "command": ["basis", "run"], "directory": tmp, "log": str(logfile), "outdir": None}
            with patch("basis.jobs.list_jobs", return_value=[job]), patch("basis.jobs.read_job", return_value=job), \
                 patch("basis.jobs.stop_job", side_effect=RuntimeError("Launcher identity changed; no signals sent")):
                app = AppTest.from_file(str(Path(basis.__file__).with_name("gui_app.py")), default_timeout=30)
                app.session_state["page"] = "Jobs"
                app.run()
                app.button(key="stop_" + job["id"]).click().run()
                self.assertFalse(app.exception)
                self.assertTrue(any("no signals sent" in x.value for x in app.error))
                self.assertEqual(app.metric[1].value, "running")


if __name__ == "__main__":
    unittest.main()
