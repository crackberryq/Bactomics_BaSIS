"""Check the extracted sharing copy with the original directory unavailable."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety.share_bundle import build_plan, export_bundle
from safety.reporting import render
from test_report_screening import synthetic_finding


@unittest.skipUnless(os.environ.get("BASIS_TEST_REPORT_BROWSER") == "1"
                     and importlib.util.find_spec("playwright"), "Dedicated report-browser checks")
class SharedReportBrowserTests(unittest.TestCase):
    def test_extracted_report_search_download_and_navigation_work_without_originals(self):
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory(prefix="basis-share-browser-") as temporary:
            base = Path(temporary)
            source = base / "source/safety"
            (source / "metadata").mkdir(parents=True)
            (source / "evidence").mkdir()
            sample = {"sample_id": "synthetic-sample", "input_mode": "external"}
            config = {"modules": {"amrfinder": {"enabled": True}}, "deep": {"enabled": False}}
            evidence = {"sample_id": "synthetic-sample", "module": "amrfinder", "execution_status": "completed",
                        "interpretation": "detected", "findings": [synthetic_finding()], "artifacts": []}
            for relative, value in (("metadata/sample.json", sample), ("metadata/config.json", config),
                                    ("evidence/amrfinder.json", evidence)):
                (source / relative).write_text(json.dumps(value))
            render(source / "metadata/sample.json", source / "metadata/config.json",
                   [source / "evidence/amrfinder.json"], source, view="screening", evidence_root=source)
            output = base / "sample.zip"
            export_bundle(build_plan(source, output))
            extracted = base / "moved/s"
            with zipfile.ZipFile(output) as archive:
                archive.extractall(extracted)
            source.rename(base / "original-unavailable")
            with sync_playwright() as playwright:
                opts = {"headless": True, "args": ["--no-sandbox"]}
                if os.environ.get("BASIS_CHROMIUM_EXECUTABLE"):
                    opts["executable_path"] = os.environ["BASIS_CHROMIUM_EXECUTABLE"]
                browser = playwright.chromium.launch(**opts)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    page.context.set_offline(True)
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto((extracted / "index.html").as_uri())
                    page.get_by_role("link", name="Screening report", exact=True).click()
                    page.locator("#finding-search").fill("azithromycin")
                    self.assertEqual(page.locator("#finding-table tbody tr:visible").count(), 1)
                    row = page.locator("#finding-table tbody tr:visible")
                    row.locator("summary").click()
                    self.assertIn("Element name", row.inner_text())
                    link = row.get_by_role("link", name="Full saved module record · JSON")
                    target = Path(link.evaluate("a => new URL(a.href).pathname"))
                    self.assertTrue(target.is_relative_to(extracted))
                    self.assertEqual(json.loads(target.read_text())["module"], "amrfinder")
                    page.get_by_role("link", name="Run files", exact=True).click()
                    self.assertTrue(page.url.endswith("/index.html"))
                    page.locator("#search").fill("evidence/amrfinder.json")
                    self.assertEqual(page.locator("tbody tr:visible").count(), 1)
                    with page.expect_download() as download:
                        page.locator("tbody tr:visible a").click()
                    saved = base / "download.json"
                    download.value.save_as(saved)
                    self.assertEqual(json.loads(saved.read_text()), evidence)
                    self.assertEqual(errors, [])
                    if os.environ.get("BASIS_REPORT_SCREENSHOT_DIR"):
                        out = Path(os.environ["BASIS_REPORT_SCREENSHOT_DIR"])
                        out.mkdir(exist_ok=True)
                        page.screenshot(path=str(out / "teams-share-index.png"))
                finally:
                    browser.close()


if __name__ == "__main__":
    unittest.main()
