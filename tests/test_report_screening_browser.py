"""Offline Chromium checks over synthetic report rows; opt in with the CI flag."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

from test_report_screening import synthetic_finding
from basis.report import write_report


@unittest.skipUnless(os.environ.get("BASIS_TEST_REPORT_BROWSER") == "1"
                     and importlib.util.find_spec("playwright"), "Dedicated report-browser checks")
class ScreeningBrowserTests(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright

        temporary = tempfile.TemporaryDirectory(prefix="basis-report-browser-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        findings = [synthetic_finding(finding_id=f"synthetic-{i}", gene=f"synthetic_gene_{i}") for i in range(301)]
        findings[-1]["gene"] = "last_page_target"
        findings[-1]["attributes"]["raw"]["Element name"] = "Synthetic description containing the unique term farawaydrug"
        records = [{"sample_id": "synthetic-sample", "module": "amrfinder", "execution_status": "completed",
                    "interpretation": "detected", "findings": findings, "artifacts": []},
                   {"sample_id": "synthetic-sample", "module": "rgi", "execution_status": "completed",
                    "interpretation": "detected", "findings": [synthetic_finding(module="rgi", gene="synthetic_exploratory",
                    evidence_level="exploratory", attributes={"cutoff": "loose", "raw": {"Drug Class": "synthetic class"}})], "artifacts": []},
                   {"sample_id": "synthetic-sample", "module": "vfdb", "execution_status": "failed",
                    "findings": [synthetic_finding(module="vfdb", gene="synthetic_provisional")], "artifacts": []}]
        write_report({"run_id": "synthetic-browser", "version": "test", "report_view": "screening",
                      "options": {"deep": False}, "validation_mode": "synthetic_fixture", "completeness": "incomplete"},
                     [{"sample_id": "synthetic-sample", "input_mode": "external"}], records, [], self.root)
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        opts = {"headless": True, "args": ["--no-sandbox"]}
        if os.environ.get("BASIS_CHROMIUM_EXECUTABLE"):
            opts["executable_path"] = os.environ["BASIS_CHROMIUM_EXECUTABLE"]
        self.browser = self.playwright.chromium.launch(**opts)
        self.addCleanup(self.browser.close)

    def page(self, **options):
        page = self.browser.new_page(**options)
        page.context.set_offline(True)
        self.errors = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto((self.root / "report.html").as_uri())
        return page

    def screenshot(self, page, name):
        if os.environ.get("BASIS_REPORT_SCREENSHOT_DIR"):
            out = Path(os.environ["BASIS_REPORT_SCREENSHOT_DIR"])
            out.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out / name), full_page=False)

    def test_search_pagination_and_evidence_groups_cover_every_row(self):
        page = self.page(viewport={"width": 1440, "height": 1000})
        visible = page.locator("#finding-table tbody tr:visible")
        self.assertEqual(visible.count(), 50)
        self.assertIn("301 matching", page.locator("#finding-count").inner_text())
        page.locator("#finding-next").click()
        self.assertIn("51–100", page.locator("#finding-count").inner_text())
        page.locator("#finding-search").fill("farawaydrug")
        self.assertEqual(visible.count(), 1)
        self.assertIn("last_page_target", visible.inner_text())
        self.assertIn("Page 1 of 1", page.locator("#finding-page").text_content())
        page.locator("#finding-search").fill("")
        page.locator("#finding-module").select_option("rgi")
        self.assertEqual(visible.count(), 0)
        self.assertTrue(page.locator("#finding-empty").is_visible())
        page.locator("#finding-group").select_option("exploratory")
        self.assertEqual(visible.count(), 1)
        self.assertIn("Exploratory", visible.inner_text())
        page.locator("#finding-module").select_option("vfdb")
        page.locator("#finding-group").select_option("provisional")
        self.assertEqual(visible.count(), 1)
        self.assertIn("source module did not complete", visible.inner_text())
        page.locator("#finding-module").select_option("")
        page.locator("#finding-group").select_option("")
        self.assertIn("303 matching", page.locator("#finding-count").inner_text())
        page.locator("#finding-search").fill("synthetic_gene_0")
        page.locator("#screening-findings").scroll_into_view_if_needed()
        self.screenshot(page, "screening-desktop.png")
        self.assertEqual(self.errors, [])

    def test_source_fields_and_mobile_view_are_readable_offline(self):
        page = self.page(viewport={"width": 390, "height": 844})
        page.locator("#finding-search").fill("synthetic_gene_0")
        row = page.locator("#finding-table tbody tr:visible")
        row.locator("summary").click()
        self.assertTrue(row.locator("details").evaluate("element => element.open"))
        self.assertIn("Element name", row.inner_text())
        self.assertIn("AZITHROMYCIN", row.inner_text())
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 391)
        page.locator("#finding-table").scroll_into_view_if_needed()
        self.screenshot(page, "screening-mobile.png")
        self.assertEqual(self.errors, [])

    def test_no_javascript_and_print_keep_the_complete_record_set(self):
        page = self.page(java_script_enabled=False, viewport={"width": 1440, "height": 1000})
        self.assertEqual(page.locator("#finding-table tbody tr:visible").count(), 303)
        self.assertIn("farawaydrug", page.locator("#finding-table").inner_text())
        page.close()
        page = self.page(viewport={"width": 1440, "height": 1000})
        page.locator("#finding-search").fill("farawaydrug")
        self.assertEqual(page.locator("#finding-table tbody tr:visible").count(), 1)
        page.emulate_media(media="print")
        self.assertEqual(page.locator("#finding-table tbody tr:visible").count(), 303)


if __name__ == "__main__":
    unittest.main()
