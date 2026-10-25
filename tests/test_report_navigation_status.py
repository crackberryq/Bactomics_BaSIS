"""Saved navigation status must retain the completion state of its parent."""
from __future__ import annotations

import copy
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/vendor")]

from basis.report_navigation import candidate_entry, render_navigator


class NavigationText(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.badges = []
        self._badge = None
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "span" and "badge" in attrs.get("class", "").split():
            self._badge = []

    def handle_endtag(self, tag):
        if tag == "span" and self._badge is not None:
            self.badges.append(" ".join(self._badge).strip())
            self._badge = None

    def handle_data(self, value):
        self.parts.append(value)
        if self._badge is not None:
            self._badge.append(value)

    @property
    def text(self):
        return " ".join(" ".join(self.parts).split())


class NavigationParentStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.outdir = Path(temporary.name)
        self.candidate = {
            "sample_id": "synthetic", "candidate_id": "candidate-1", "protein_id": "P1",
            "selected": True, "execution": "completed", "selection_queue": "native_screen",
            "analyses": {"domains": {"status": "completed", "hits": []}},
            "assessment": {"interpretation": "unresolved", "reason": "synthetic_review_hold"},
        }

    def parent(self, status="completed"):
        return {"sample_id": "synthetic", "module": "deepsafe",
                "execution_status": status, "candidates": [self.candidate]}

    def render(self, parent):
        before = copy.deepcopy((self.candidate, parent))
        with patch("subprocess.Popen", side_effect=AssertionError("native execution attempted")) as process:
            entry = candidate_entry("synthetic", self.candidate, parent, self.outdir)
            entry_before = copy.deepcopy(entry)
            body = render_navigator([entry])
        process.assert_not_called()
        self.assertEqual((self.candidate, parent), before)
        self.assertEqual(entry, entry_before, "Navigation rendering must preserve its saved entry")
        self.assertEqual(entry["execution"], self.candidate["execution"])
        saved = json.loads((self.outdir / entry["evidence_href"]).read_text(encoding="utf-8"))
        self.assertEqual(saved, self.candidate, "Candidate exports must preserve raw saved evidence")
        return entry, body, NavigationText(body)

    def test_unfinished_parent_marks_retained_completed_child_provisional(self):
        for status in ("failed", "blocked", "not_started", "not_assessed", "not_requested", "unknown"):
            with self.subTest(status=status):
                entry, _, document = self.render(self.parent(status))
                self.assertEqual(entry["parent_execution"], status)
                self.assertIn("provisional", [text.lower() for text in document.badges])
                self.assertNotIn("completed", [text.lower() for text in document.badges])
                self.assertIn("parent", document.text.lower())
                self.assertIn(status.replace("_", " "), document.text.lower())
                self.assertIn("completed", document.text.lower(), "Show the retained child status with its qualification")

    def test_missing_parent_completion_is_not_assumed_successful(self):
        for parent in ({}, {"sample_id": "synthetic", "module": "deepsafe"}):
            with self.subTest(parent=parent):
                _, _, document = self.render(parent)
                self.assertIn("provisional", [text.lower() for text in document.badges])
                self.assertNotIn("completed", [text.lower() for text in document.badges])
                self.assertIn("parent", document.text.lower())
                self.assertIn("completed", document.text.lower())

    def test_completed_parent_preserves_completed_child_badge(self):
        entry, _, document = self.render(self.parent())
        self.assertEqual(entry["parent_execution"], "completed")
        self.assertIn("completed", [text.lower() for text in document.badges])
        self.assertNotIn("provisional", document.text.lower())

    def test_completed_parent_does_not_promote_failed_child(self):
        self.candidate["execution"] = "failed"
        _, _, document = self.render(self.parent())
        self.assertIn("failed", [text.lower() for text in document.badges])
        self.assertNotIn("completed", [text.lower() for text in document.badges])

    def test_parent_execution_alias_retains_recorded_completion(self):
        parent = self.parent()
        del parent["execution_status"]
        parent["execution"] = {"status": "completed"}
        entry, _, document = self.render(parent)
        self.assertEqual(entry["parent_execution"], "completed")
        self.assertIn("completed", [text.lower() for text in document.badges])
        self.assertNotIn("provisional", document.text.lower())

    def test_unknown_parent_status_is_escaped_in_provisional_reason(self):
        status = '<img src=x onerror="synthetic()">'
        entry, body, document = self.render(self.parent(status))
        self.assertEqual(entry["parent_execution"], status)
        self.assertIn("provisional", [text.lower() for text in document.badges])
        self.assertNotIn("<img", body)
        self.assertIn("&lt;img", body)
        self.assertIn(status, document.text)


if __name__ == "__main__":
    unittest.main()
