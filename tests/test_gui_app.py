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
            app.button[0].click().run()
            self.assertEqual(submit.call_count, 1)
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


if __name__ == "__main__":
    unittest.main()
