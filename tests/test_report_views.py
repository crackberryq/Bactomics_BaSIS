"""Separate saved-evidence views preserve inputs and expose only their own UI."""
from __future__ import annotations

import copy
from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/vendor")]

from basis.report import write_report


class ReportHTML(HTMLParser):
    """Inspect actual document elements, excluding embedded scripts and CSS."""

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.section_ids = []
        self.fragment_targets = []
        self.nav_targets = []
        self.visible_parts = []
        self.genome_parts = []
        self.title_parts = []
        self._in_title = False
        self._in_head = False
        self._raw_tag = None
        self._genome_script = False
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "head":
            self._in_head = True
        if tag == "title" and self._in_head:
            self._in_title = True
        if attrs.get("id"):
            self.ids.append(attrs["id"])
            if tag == "section":
                self.section_ids.append(attrs["id"])
        href = attrs.get("href", "")
        if tag == "a" and href.startswith("#") and len(href) > 1:
            target = unquote(href[1:])
            self.fragment_targets.append(target)
            if "nav-link" in attrs.get("class", "").split():
                self.nav_targets.append(target)
        if tag in {"script", "style"}:
            self._raw_tag = tag
            self._genome_script = attrs.get("id") == "basis-genome-data"

    def handle_endtag(self, tag):
        if tag == "head":
            self._in_head = False
        if tag == "title":
            self._in_title = False
        if tag == self._raw_tag:
            self._raw_tag = None
            self._genome_script = False

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        if self._genome_script:
            self.genome_parts.append(data)
        elif self._raw_tag is None:
            self.visible_parts.append(data)

    @property
    def visible_text(self):
        return " ".join(" ".join(self.visible_parts).split())

    @property
    def genome(self):
        return json.loads("".join(self.genome_parts))


def synthetic_inputs(view):
    """No native files, sequences, external references, or analyses are needed."""
    run = {
        "run_id": "synthetic-split-report", "version": "test",
        "validation_mode": "synthetic_fixture", "completeness": "complete",
        "options": {"deep": view != "screening"},
        "enabled_modules": ["amrfinder", "pathogenfinder2"],
        "input_validations": {"sample1": {
            "contig_lengths": {"synthetic_contig": 1000},
            "protein_coordinates": {"P1": {
                "contig": "synthetic_contig", "start": 101, "end": 400,
                "strand": "+",
            }},
        }},
    }
    if view is not None:
        run["report_view"] = view
    samples = [{"sample_id": "sample1", "input_mode": "external", "taxon": ""}]
    finding = {
        "finding_id": "synthetic-source-1", "protein_id": "P1",
        "category": "amr", "gene": "synthetic_marker", "family": "synthetic_family",
        "contig": "synthetic_contig", "start": 101, "end": 400, "strand": "+",
        "evidence_level": "native", "interpretation": "screening_call",
        "attributes": {"native_method": "RECORDED"},
    }
    attention = {
        "finding_id": "synthetic-attention-1", "protein_id": "P1",
        "category": "ml_attention", "interpretation": "attention_priority",
        "contig": "synthetic_contig", "start": 101, "end": 400, "strand": "+",
        "attributes": {"rank": 1, "attention_mean": 0.375, "attention_max": 0.5,
                       "model_attention_sums": [0.25, 0.5]},
    }
    records = [
        {"sample_id": "sample1", "module": "amrfinder",
         "execution_status": "completed", "interpretation": "detected",
         "findings": [finding], "metrics": {}, "artifacts": []},
        {"sample_id": "sample1", "module": "pathogenfinder2",
         "execution_status": "completed", "interpretation": "model_output",
         "findings": [attention], "artifacts": [],
         "metrics": {"prediction_mean": 0.125, "prediction_std": 0.025,
                     "native_phenotype": "synthetic_native_label",
                     "ensemble_member_scores": [0.1, 0.15], "input_protein_count": 4,
                     "attention_protein_count": 4, "attention_selected_count": 1}},
    ]
    candidate = {
        "sample_id": "sample1", "candidate_id": "synthetic-candidate-1",
        "protein_id": "P1", "selected": True, "selection_queue": "native_screen",
        "execution": "completed", "contig": "synthetic_contig",
        "start": 101, "end": 400, "strand": "+", "query": {"length": 100},
        "finding_ids": [finding["finding_id"]],
        "source_evidence": [dict(copy.deepcopy(finding), tool="amrfinder")],
        "analyses": {
            "domains": {"status": "completed", "hits": [{"id": "synthetic_domain"}]},
            "motifs": {"status": "completed", "rules": [{"status": "present"}]},
            "prosite_patterns": {"status": "completed", "hits": []},
            "prosite_profiles": {"status": "completed", "hits": []},
            "references": {"status": "completed", "selected_count": 1},
            "phylogeny": {"status": "not_assessed", "reason": "synthetic fixture"},
        },
        "references": [{"accession": "synthetic_comparator", "review_status": "unreviewed",
                        "selection_reason": "saved synthetic reference"}],
        "assessment": {"interpretation": "unresolved", "reason": "synthetic_review_hold",
                       "followup": "review_existing_evidence",
                       "missing_comparison_groups": ["synthetic_comparison_gap"]},
    }
    deferred = {
        "sample_id": "sample1", "candidate_id": "synthetic-deferred-1",
        "protein_id": "P2", "selected": False, "selection_queue": "exploratory",
        "execution": "not_started", "reason": "synthetic deferred record",
    }
    deep = [] if view == "screening" else [{
        "sample_id": "sample1", "module": "deepsafe", "execution_status": "completed",
        "candidates": [candidate, deferred], "findings": [], "artifacts": [],
    }]
    return run, samples, records, deep


class SeparateReportViewTests(unittest.TestCase):
    def render(self, view, *, inputs=None):
        inputs = synthetic_inputs(view) if inputs is None else inputs
        before = copy.deepcopy(inputs)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with patch("subprocess.Popen", side_effect=AssertionError("report attempted native execution")) as process:
            artifacts = write_report(*inputs, Path(temporary.name))
        process.assert_not_called()
        self.assertEqual(inputs, before, "Rendering must preserve all saved input records")
        body = Path(artifacts["html"]).read_text(encoding="utf-8")
        evidence = json.loads(Path(artifacts["evidence_json"]).read_text(encoding="utf-8"))
        for key, value in zip(("run", "samples", "records", "deep_records"), before):
            self.assertEqual(evidence[key], value, "Source evidence changed in export: " + key)
        navigation = json.loads(Path(artifacts["report_navigation_json"]).read_text(encoding="utf-8"))
        return ReportHTML(body), evidence, navigation

    def assert_navigation_matches(self, document, expected_sections):
        duplicates = {ident: count for ident, count in Counter(document.ids).items() if count > 1}
        self.assertEqual(duplicates, {}, "Document IDs must be unique")
        self.assertEqual(set(document.section_ids), set(expected_sections))
        self.assertEqual(set(document.nav_targets), set(expected_sections))
        self.assertEqual(len(document.nav_targets), len(expected_sections))
        self.assertEqual(set(document.fragment_targets) - set(document.ids), set(),
                         "Every in-page navigation link must reach an existing element")

    def test_screening_omits_deep_sections_counters_and_navigation(self):
        document, _, navigation = self.render("screening")
        self.assert_navigation_matches(document, {
            "overview", "inputs", "genome", "screens", "extend", "recommendations", "provenance",
        })
        for ident in ("deep", "deep-navigator", "evidence-matrix", "candidate-search", "candidate-count"):
            self.assertNotIn(ident, document.ids)
        for text in ("DeepSafe nominations", "Not selected for DeepSafe", "How candidates reached DeepSafe",
                     "Selected candidates", "Deferred candidates", "Deep family assessment"):
            with self.subTest(text=text):
                self.assertNotIn(text, document.visible_text)
        self.assertEqual(navigation["candidates"], [])
        for sample in document.genome["samples"]:
            self.assertNotIn("selected", {track["id"] for track in sample["tracks"]})
            for contig in sample["contigs"]:
                self.assertNotIn("selected", {region["track"] for region in contig["regions"]})

    def test_screening_title_and_intro_identify_screening_scope(self):
        document, _, _ = self.render("screening")
        self.assertEqual("".join(document.title_parts), "BaSIS · Screening report")
        self.assertIn("SCREENING REPORT", document.visible_text)
        self.assertIn("Inspect native screening calls and model outputs", document.visible_text)
        self.assertNotIn("DEEP EVIDENCE", document.visible_text)
        self.assertNotIn("recorded DeepSafe holds", document.visible_text)

    def test_deep_title_and_intro_identify_followup_scope(self):
        document, _, _ = self.render("deep")
        self.assertEqual("".join(document.title_parts), "BaSIS · DeepSafe follow-up report")
        self.assertIn("DEEPSAFE FOLLOW-UP REPORT", document.visible_text)
        self.assertIn("Inspect saved follow-up analyses and source context", document.visible_text)
        self.assertNotIn("CORE SCREENING · DEEP EVIDENCE · MODEL INSIGHT", document.visible_text)

    def test_screening_retains_native_core_and_extend_attention_evidence(self):
        document, evidence, _ = self.render("screening")
        for text in ("Core screening evidence", "synthetic_marker", "RECORDED", "Extend",
                     "synthetic_native_label", "0.12500", "0.02500", "0.10000", "0.15000",
                     "Attention coverage", "ML attention explorer", "P1",
                     "Maximum member attention", "Mean member attention", "synthetic-attention-1"):
            with self.subTest(text=text):
                self.assertIn(text, document.visible_text)
        tracks = {track["id"]: track for track in document.genome["samples"][0]["tracks"]}
        self.assertEqual(tracks["amr"]["count"], 1)
        self.assertEqual(tracks["ml_attention"]["count"], 1)
        self.assertEqual(evidence["records"][1]["metrics"]["attention_selected_count"], 1,
                         "Hiding nomination UI must not erase recorded native metrics")

    def test_deep_retains_candidate_evidence_and_context_without_duplicate_screens(self):
        document, evidence, navigation = self.render("deep")
        self.assert_navigation_matches(document, {
            "overview", "inputs", "genome", "evidence-matrix", "deep", "recommendations", "provenance",
        })
        for ident in ("screens", "extend", "finding-search", "finding-table"):
            self.assertNotIn(ident, document.ids)
        for text in ("Core screening evidence", "Screening calls for review", "Extend · model insight",
                     "Native model prediction", "ML attention explorer"):
            self.assertNotIn(text, document.visible_text)
        for text in ("Candidate evidence matrix", "Deep family assessment", "Evidence navigator",
                     "RECORDED: synthetic_marker", "synthetic_comparator", "synthetic_comparison_gap",
                     "Show genomic neighborhood", "Analysis coverage", "Protein feature map",
                     "Provenance", "Run record", "synthetic-split-report"):
            with self.subTest(text=text):
                self.assertIn(text, document.visible_text)
        tracks = {track["id"]: track for track in document.genome["samples"][0]["tracks"]}
        for name in ("amr", "ml_attention", "selected"):
            self.assertEqual(tracks[name]["count"], 1, name)
        self.assertEqual(len(evidence["records"]), 2, "Deep views retain native evidence as source context")
        self.assertEqual(len(evidence["deep_records"][0]["candidates"]), 2)
        self.assertEqual(len(navigation["candidates"]), 1)
        entry, = navigation["candidates"]
        self.assertEqual(entry["candidate_id"], "synthetic-candidate-1")
        self.assertIn(entry["anchor"], document.ids)
        for anchor in entry["sections"].values():
            self.assertIn(anchor, document.ids)

    def test_absent_view_preserves_legacy_combined_report(self):
        document, evidence, navigation = self.render(None)
        self.assertEqual("".join(document.title_parts), "BaSIS · Genomic evidence report")
        self.assertIn("CORE SCREENING · DEEP EVIDENCE · MODEL INSIGHT", document.visible_text)
        self.assert_navigation_matches(document, {
            "overview", "inputs", "genome", "evidence-matrix", "screens", "extend",
            "deep", "recommendations", "provenance",
        })
        self.assertNotIn("report_view", evidence["run"])
        for text in ("Core screening evidence", "Extend · model insight", "Native model prediction",
                     "ML attention explorer", "Candidate evidence matrix", "Deep family assessment"):
            with self.subTest(text=text):
                self.assertIn(text, document.visible_text)
        self.assertEqual(len(navigation["candidates"]), 1)

    def test_completed_children_require_completed_parent_for_overview_totals(self):
        for status in ("failed", "blocked", "not_started", "unknown", None):
            with self.subTest(status=status):
                inputs = synthetic_inputs("deep")
                if status is None:
                    inputs[3][0].pop("execution_status")
                else:
                    inputs[3][0]["execution_status"] = status
                document, _, _ = self.render("deep", inputs=inputs)
                self.assertIn("Selected candidates 0 / 1", document.visible_text)
                self.assertNotIn("Selected analyses completed", document.visible_text)
        completed, _, _ = self.render("deep")
        self.assertIn("Selected candidates 1 / 1", completed.visible_text)

    def test_legacy_no_deep_view_has_no_followup_navigation_or_track(self):
        inputs = synthetic_inputs("screening")
        inputs[0].pop("report_view")
        document, _, _ = self.render(None, inputs=inputs)
        self.assert_navigation_matches(document, {
            "overview", "inputs", "genome", "screens", "extend", "recommendations", "provenance",
        })
        self.assertNotIn("DeepSafe nominations", document.visible_text)
        self.assertNotIn("selected", {track["id"] for track in document.genome["samples"][0]["tracks"]})


if __name__ == "__main__":
    unittest.main()
