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
            for page in ("Overview", "Setup", "Samples", "Run", "Jobs", "Results & export"):
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
            self.assertEqual(app.session_state["page"], "Setup")
            app.button(key="setup_next").click().run()
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
            app.checkbox(key="retry_failed").check().run()
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
            self.assertTrue(request["retry_failed"])
            self.assertFalse(app.exception)
            self.assertTrue(any("Database and model downloads are not yet automated" in info.value for info in app.info))

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

    def test_run_uses_snakemake_with_explicit_local_alternative(self):
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
            next(b for b in app.button if b.label == "Start analysis").click().run()
            self.assertEqual(submit.call_args.args[1]["engine"], "snakemake")
            app.selectbox[0].select("local").run()
            next(b for b in app.button if b.label == "Start analysis").click().run()
            self.assertEqual(submit.call_args.args[1]["engine"], "local")
            next(b for b in app.button if b.label == "Check readiness").click().run()
            self.assertNotIn("engine", submit.call_args.args[1])
            self.assertFalse(app.exception)

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


if __name__ == "__main__":
    unittest.main()
