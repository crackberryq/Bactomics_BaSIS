"""Regression checks for the compact offline report's scientific presentation."""
import copy
import csv
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest

from basis.report import write_report


class Node:
    def __init__(self, tag="document", attrs=(), parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []

    @property
    def text(self):
        return "".join(child.text if isinstance(child, Node) else child for child in self.children)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def find(self, tag=None, cls=None, ident=None):
        return [n for n in self.walk()
                if (tag is None or n.tag == tag)
                and (cls is None or cls in n.attrs.get("class", "").split())
                and (ident is None or n.attrs.get("id") == ident)]

    def has_ancestor(self, tag):
        p = self.parent
        while p is not None:
            if p.tag == tag:
                return True
            p = p.parent
        return False


class Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def pf2(status="completed"):
    return {"sample_id": "isolate1", "module": "pathogenfinder2", "execution_status": status,
            "interpretation": "detected", "findings": [],
            "effective_config": {"enabled": True, "mode": "native"},
            "metrics": {"prediction_mean": 0.1628570556640625,
                        "prediction_std": 0.042204792730342805,
                        "native_phenotype": "Human Non Pathogenic",
                        "ensemble_member_scores": [0.11102294921875, 0.174560546875, 0.2247314453125, 0.14111328125],
                        "input_protein_count": 4511, "attention_protein_count": 4511,
                        "attention_selected_count": 20, "embeddings_reused": False}}


def deep(candidates):
    return {"sample_id": "isolate1", "module": "deepsafe", "execution_status": "completed",
            "interpretation": "inconclusive", "findings": [], "candidates": candidates}


def selected(protein="AFFIONLF_03648"):
    return {"sample_id": "isolate1", "candidate_id": "selected1", "protein_id": protein,
            "selected": True, "execution": "completed", "selection_queue": "ml_attention",
            "attention_rank": 1, "gene": "misleading loose resistance label",
            "assessment": {"interpretation": "unresolved", "followup": "review_existing_evidence"},
            "query": {"length": 210}, "source_evidence": [{"finding_id": "weak1", "category": "amr",
                "evidence_level": "exploratory", "gene": "misleading loose resistance label"}],
            "analyses": {"domains": {"status": "completed", "hits": []}}}


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.run = {"version": "0.1.0", "run_id": "dashboard-test", "completeness": "incomplete",
                    "enabled_modules": ["pathogenfinder2"], "options": {"deep": True}}
        self.samples = [{"sample_id": "isolate1", "taxon": "Lysinibacillus irui"}]

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, records, deeps=None):
        paths = write_report(self.run, self.samples, records, deeps or [], self.out)
        html = Path(paths["html"]).read_text()
        return html, Document(html).root, paths

    def test_completed_model_mean_label_and_coverage_are_prominent(self):
        html, doc, _ = self.render([pf2()])
        models = doc.find("article", "model-panel")
        self.assertEqual(len(models), 1)
        model = models[0]
        score = model.find(cls="model-score")[0]
        label = model.find(cls="model-label")[0]
        self.assertIn("0.16286", score.text)
        self.assertIn("0.04220", score.text)
        self.assertEqual(label.text, "Human Non Pathogenic")
        self.assertFalse(score.has_ancestor("details"))
        self.assertFalse(label.has_ancestor("details"))
        self.assertIn("4,511", model.text)
        self.assertIn("DeepSafe nominations20", model.text)
        self.assertEqual(len(model.find(cls="model-bar")), 4)
        self.assertIn("not a confidence interval or exposure-risk estimate", model.text)
        self.assertIn('href="#extend"', html)

    def test_failed_model_cannot_display_retained_scores_as_a_completed_prediction(self):
        record = pf2("failed")
        record["error"] = "parsing failed after inference"
        html, doc, paths = self.render([record])
        model = doc.find("article", "model-panel")[0]
        self.assertFalse(model.find(cls="model-score"))
        self.assertFalse(model.find(cls="model-label"))
        self.assertFalse(model.find(cls="model-bar"))
        self.assertIn("No completed model prediction is available", model.text)
        self.assertNotIn("Human Non Pathogenic", model.text)
        self.assertNotIn("0.16286", model.text)
        evidence = json.loads(Path(paths["evidence_json"]).read_text())
        self.assertEqual(evidence["records"][0]["metrics"], record["metrics"])

    def test_requested_denominator_excludes_disabled_but_includes_requested_failure_and_pending(self):
        completed = {"sample_id": "isolate1", "module": "amrfinder", "execution_status": "completed",
                     "interpretation": "not_detected", "findings": []}
        omitted = {"sample_id": "isolate1", "module": "vfdb", "execution_status": "not_assessed",
                   "effective_config": {"enabled": False}, "findings": []}
        pending = {"sample_id": "isolate1", "module": "rgi", "execution_status": "not_assessed",
                   "effective_config": {"enabled": True}, "findings": []}
        self.run["enabled_modules"] = ["amrfinder", "pathogenfinder2", "rgi"]
        _, doc, _ = self.render([completed, pf2("failed"), omitted, pending])
        stats = doc.find(ident="overview")[0].find(cls="stat")
        requested = next(n for n in stats if n.find(cls="stat-label")[0].text == "Requested modules")
        self.assertEqual(requested.find(cls="stat-value")[0].text, "1 / 3")
        failures = next(n for n in stats if n.find(cls="stat-label")[0].text == "Execution failures")
        self.assertEqual(failures.find(cls="stat-value")[0].text, "1")
        self.assertIn("1 optional module records not assessed", doc.text)

    def test_explicit_deferred_candidates_only_in_export_not_candidate_cards(self):
        deferred = {"candidate_id": "deferred9", "protein_id": "DEFERRED_PROTEIN_9",
                    "selected": False, "execution": "deferred", "selection_queue": "exploratory",
                    "reason": "exploratory_budget_zero"}
        html, doc, _ = self.render([pf2()], [deep([selected(), deferred])])
        cards = doc.find("details", "candidate")
        self.assertEqual(len(cards), 1)
        self.assertNotIn("DEFERRED_PROTEIN_9", html)
        self.assertIn('href="deferred-candidates.tsv"', html)
        with (self.out / "deferred-candidates.tsv").open() as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["protein_id"], "DEFERRED_PROTEIN_9")
        self.assertEqual(rows[0]["reason"], "exploratory_budget_zero")

    def test_citation_downloads_and_disabled_scope_preserve_recorded_evidence(self):
        disabled = [{"sample_id": "isolate1", "module": name,
                     "execution_status": "not_assessed", "reason": "module_disabled"}
                    for name in ["bacmet", "isescan", "icescreen", "crisprcasfinder", "minced", "mlst"]]
        html, doc, paths = self.render(disabled)
        citation = doc.find(ident="citation")[0]
        self.assertIn("Jimi Goldstein", citation.text)
        self.assertIn("Version 0.1.0", citation.text)
        for key in ("cite_cff", "cite_bib", "cite_ris", "cite_txt"):
            path = Path(paths[key])
            self.assertTrue(path.is_file())
            self.assertIn('href="' + path.name + '" download', html)
        optional = doc.find(ident="optional-modules")[0]
        self.assertEqual(optional.text.count("Disabled in this run’s configuration"), 6)
        self.assertIn("Native adapter available", optional.text)
        self.assertNotIn("Import-only in this BaSIS release", optional.text)
        self.assertIn("It does not mean a negative result", optional.text)
        saved = json.loads(Path(paths["evidence_json"]).read_text())
        self.assertEqual(saved["records"], disabled)

    def test_attention_headline_uses_protein_identity_not_loose_annotation(self):
        _, doc, _ = self.render([pf2()], [deep([selected()])])
        summary = doc.find("details", "candidate")[0].find("summary")[0]
        self.assertIn("AFFIONLF_03648", summary.text)
        self.assertIn("Attention rank 1", summary.text)
        self.assertNotIn("misleading loose resistance label", summary.text)
        self.assertIn("Exploratory source labels", doc.text)

    def test_cross_sample_candidate_is_not_linked_into_another_genome(self):
        c = selected("shared-protein")
        c["sample_id"] = "different-sample"
        _, doc, paths = self.render([pf2()], [deep([c])])
        self.assertFalse(doc.find("details", "candidate"))
        self.assertIn("Candidate sample mismatch", doc.text)
        evidence = json.loads(Path(paths["evidence_json"]).read_text())
        self.assertEqual(evidence["deep_records"][0]["candidates"][0], c)

    def test_large_deferred_and_weak_payload_stays_in_exports_without_raw_duplication_in_html(self):
        payload = "SOURCE_SEQUENCE_SHOULD_REMAIN_IN_JSON_" + "A" * 2048
        findings = [{"finding_id": "weak" + str(i), "protein_id": "weakprotein" + str(i),
                     "category": "amr", "gene": "exploratory annotation", "evidence_level": "exploratory",
                     "attributes": {"raw_protein_sequence": payload, "arbitrary_raw_payload": payload}}
                    for i in range(1100)]
        records = [{"sample_id": "isolate1", "module": "rgi", "execution_status": "completed",
                    "interpretation": "inconclusive", "findings": findings}]
        candidates = [{"candidate_id": "deferred" + str(i), "protein_id": "DEFERRED_" + str(i),
                       "selected": False, "execution": "deferred", "selection_queue": "exploratory",
                       "source_evidence": [{"raw_sequence": payload}]} for i in range(1100)]
        deeps = [deep([selected()] + candidates)]
        unchanged = copy.deepcopy((records, deeps))
        html, doc, paths = self.render(records, deeps)
        self.assertLess(len(html.encode()), 250_000)
        self.assertNotIn("SOURCE_SEQUENCE_SHOULD_REMAIN_IN_JSON_", html)
        self.assertNotIn("DEFERRED_1099", html)
        self.assertEqual(len(doc.find("details", "candidate")), 1)
        self.assertEqual((records, deeps), unchanged)
        evidence = json.loads(Path(paths["evidence_json"]).read_text())
        self.assertEqual(evidence["records"], records)
        self.assertEqual(evidence["deep_records"], deeps)
        self.assertEqual(evidence["records"][0]["findings"][1099]["attributes"]["raw_protein_sequence"], payload)
        with (self.out / "deferred-candidates.tsv").open() as handle:
            self.assertEqual(len(list(csv.DictReader(handle, delimiter="\t"))), 1100)

    def test_untrusted_long_labels_and_keys_are_escaped_without_new_markup(self):
        attack = '<img src=x onerror="alert(1)">'
        long_label = attack + " verylonglabel" * 400
        self.samples[0]["phenotypes"] = [{"assay": attack, "result": {attack: "pending review"}}]
        record = pf2()
        record["metrics"]["native_phenotype"] = long_label
        c = selected(protein=attack)
        c["analyses"]["domains"]["hits"] = [{"passes_ga": True, "domain_name": long_label,
                                                "query_start": 5, "query_end": 100}]
        c["assessment"]["warnings"] = [attack]
        html, doc, _ = self.render([record], [deep([c])])
        self.assertFalse(doc.find("img"))
        self.assertNotIn(attack, html)
        self.assertIn("&lt;img", html)
        self.assertEqual(doc.find(cls="model-label")[0].text, long_label)
        self.assertTrue(any(long_label in n.text for n in doc.find("title")))
        self.assertFalse(any("onerror" in n.attrs for n in doc.walk()))


if __name__ == "__main__":
    unittest.main()
