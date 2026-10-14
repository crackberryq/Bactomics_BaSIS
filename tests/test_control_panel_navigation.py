"""Navigation-snapshot checks using tiny saved report fixtures only."""
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

from basis import control_panel as hub
from basis.report_links import candidate_anchor


def digest(value):
    return hashlib.sha256(value).hexdigest()


class ControlPanelNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "saved run"
        self.root.mkdir()
        self.out = self.base / "panel"
        self.out.mkdir()

    def put(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, (dict, list)) else value,
                        encoding="utf-8")
        return path

    def fixture(self):
        from basis.report_navigation import candidate_fingerprint
        candidates = []
        for sid in ("A", "B"):
            candidates.append({"sample_id": sid, "protein_id": "shared", "candidate_id": "c" + sid,
                "selected": True, "execution": "completed", "selection_queue": "ml_attention",
                "gene": "loose source name", "assessment": {"interpretation": "unresolved",
                "followup": "hold_for_comparator_review", "reason": "missing_reviewed_comparison_groups",
                "missing_comparison_groups": ["Comparator control"]},
                "analyses": {"motifs": {"status": "no_applicable_rule"},
                             "phylogeny": {"status": "not_assessed"}}})
        records = [{"sample_id": c["sample_id"], "module": "deepsafe", "execution_status": "completed",
                    "candidates": [c]} for c in candidates]
        records[0]["candidates"].append({"sample_id": "A", "protein_id": "deferred", "candidate_id": "d",
                                           "selected": False, "execution": "deferred"})
        bundle = {"schema_version": "basis-report-1", "run": {"run_id": "report-run", "completeness": "incomplete"},
                  "records": [], "deep_records": records}
        evidence = self.put("evidence.json", bundle)
        html = ["<!doctype html><html><body>"]
        entries = []
        for c in candidates:
            sid, protein = c["sample_id"], c["protein_id"]
            outer = candidate_anchor(sid, protein)
            sections = {key: outer + "-" + key for key in
                        ("overview", "domains", "motifs", "trees", "placement", "references", "files")}
            html.append('<details id="' + outer + '">' + ''.join('<section id="' + x + '"></section>' for x in sections.values()) + '</details>')
            raw = "report-data/candidate-" + sid + ".json"
            self.put(raw, c)
            entries.append({"sample_id": sid, "protein_id": protein, "candidate_id": c["candidate_id"],
                "candidate_sha256": candidate_fingerprint(c), "anchor": outer, "sections": sections,
                "evidence_href": raw, "label": "ML attention · rank 1", "search_text": "PROSITE pattern PS_TEST",
                "execution": c["execution"], **c["assessment"],
                "layer_statuses": {key: value["status"] for key, value in c["analyses"].items()}})
        html.append("</body></html>")
        report = self.put("report.html", ''.join(html))
        index = {"schema": "basis-report-navigation-v1", "run_id": "report-run",
                 "report_sha256": digest(report.read_bytes()), "evidence_sha256": digest(evidence.read_bytes()),
                 "candidates": entries}
        self.put("report-navigation.json", index)
        return index, bundle

    def collect(self):
        return hub.collect_run(self.root, "r1", self.out)

    def test_valid_index_matches_current_report_even_with_stale_result_tree(self):
        index, _ = self.fixture()
        self.put("results/OLD/deepsafe/result.json", {"sample_id": "OLD", "module": "deepsafe",
            "execution_status": "failed", "candidates": [{"selected": True, "protein_id": "STALE"}]})
        self.put("run.json", {"run_id": "old", "completeness": "completed_requested_scope"})
        before = {str(p.relative_to(self.root)): digest(p.read_bytes()) for p in self.root.rglob('*') if p.is_file()}
        run = self.collect()
        self.assertEqual(run["navigation_source"], "verified_report_snapshot")
        self.assertEqual(run["status"], "incomplete")
        self.assertEqual([c["sample"] for c in run["candidates"]], ["A", "B"])
        self.assertFalse(run["warnings"])
        self.assertNotEqual(run["candidates"][0]["href"], run["candidates"][1]["href"])
        for c, entry in zip(run["candidates"], index["candidates"]):
            self.assertEqual(c["gene"], "ML attention · rank 1")
            self.assertEqual(c["reason"], "missing_reviewed_comparison_groups")
            self.assertEqual(c["missing_comparison_groups"], ["Comparator control"])
            self.assertEqual(c["search_text"], "PROSITE pattern PS_TEST")
            self.assertEqual(c["navigation_source"], "verified_report_snapshot")
            self.assertEqual(set(c["sections"]), set(entry["sections"]))
            for key, url in c["sections"].items():
                parsed = urlsplit(url)
                self.assertEqual((self.out / unquote(parsed.path)).resolve(), self.root / "report.html")
                self.assertEqual(parsed.fragment, entry["sections"][key])
            self.assertEqual((self.out / unquote(c["evidence"])).resolve(), self.root / entry["evidence_href"])
        after = {str(p.relative_to(self.root)): digest(p.read_bytes()) for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_missing_index_keeps_legacy_candidates_and_links(self):
        self.fixture()
        (self.root / "report-navigation.json").unlink()
        run = self.collect()
        self.assertEqual(run["navigation_source"], "legacy_result_records")
        self.assertEqual(len(run["candidates"]), 2)
        self.assertFalse(run["warnings"])
        self.assertTrue(all("sections" not in c for c in run["candidates"]))
        self.assertTrue(all(c["href"].endswith("#" + candidate_anchor(c["sample"], "shared")) for c in run["candidates"]))

    def test_legacy_result_without_matching_report_card_keeps_only_source_json(self):
        self.put("report.html", "<!doctype html><html><body>Older report</body></html>")
        self.put("results/A/deepsafe/result.json", {"sample_id": "A", "module": "deepsafe",
            "execution_status": "completed", "candidates": [
                {"selected": True, "protein_id": "not-in-report", "execution": "completed"}]})
        run = self.collect()
        self.assertEqual(run["navigation_source"], "legacy_result_records")
        self.assertIsNone(run["candidates"][0]["href"])
        self.assertIsNotNone(run["candidates"][0]["evidence"])
        self.assertTrue(any("card links unavailable" in w for w in run["warnings"]))

    def test_stale_report_or_evidence_falls_back_with_warning(self):
        for filename in ("report.html", "evidence.json"):
            with self.subTest(filename=filename):
                self.fixture()
                with (self.root / filename).open("a") as handle:
                    handle.write("\n")
                run = self.collect()
                self.assertEqual(run["navigation_source"], "legacy_result_records")
                self.assertTrue(any("checksum differs" in w for w in run["warnings"]))
                self.assertTrue(all("sections" not in c for c in run["candidates"]))

    def test_unsafe_evidence_links_do_not_become_shortcuts(self):
        for value in ("../outside.json", "/tmp/outside.json", "https://example.org/a.json", "//example.org/a.json",
                      "javascript:alert(1)", "report-data/%2e%2e/%2e%2e/outside.json", "report-data/candidate-A.json#x"):
            with self.subTest(value=value):
                index, _ = self.fixture()
                index["candidates"][0]["evidence_href"] = value
                self.put("report-navigation.json", index)
                run = self.collect()
                self.assertEqual(run["navigation_source"], "legacy_result_records")
                self.assertTrue(any("Detailed report navigation unavailable" in w for w in run["warnings"]))
        index, _ = self.fixture()
        outside = self.base / "outside.json"
        outside.write_text('{}')
        link = self.root / "report-data" / "outside-link.json"
        link.symlink_to(outside)
        index["candidates"][0]["evidence_href"] = "report-data/outside-link.json"
        self.put("report-navigation.json", index)
        self.assertEqual(self.collect()["navigation_source"], "legacy_result_records")

    def test_wrong_anchor_or_candidate_export_withholds_details(self):
        for alteration in ("anchor", "section", "candidate", "missing_entry", "duplicate"):
            with self.subTest(alteration=alteration):
                index, _ = self.fixture()
                if alteration == "anchor":
                    index["candidates"][0]["anchor"] = index["candidates"][1]["anchor"]
                elif alteration == "section":
                    index["candidates"][0]["sections"]["trees"] = index["candidates"][1]["sections"]["trees"]
                elif alteration == "candidate":
                    self.put("report-data/candidate-A.json", {"replaced": True})
                elif alteration == "missing_entry":
                    index["candidates"].pop()
                else:
                    index["candidates"].append(copy.deepcopy(index["candidates"][0]))
                self.put("report-navigation.json", index)
                run = self.collect()
                self.assertEqual(run["navigation_source"], "legacy_result_records")
                self.assertTrue(run["warnings"])

    def test_pilot_filter_uses_sample_identity_after_snapshot_validation(self):
        self.fixture()
        run = hub.collect_run(self.root, "r1", self.out, {"sample_id": "B"})
        self.assertEqual(run["navigation_source"], "verified_report_snapshot")
        self.assertEqual([c["sample"] for c in run["candidates"]], ["B"])

    def test_generated_ui_uses_text_labels_and_searches_motifs_reasons(self):
        index, _ = self.fixture()
        hostile = '</script><img src=x onerror=alert(1)>'
        index["candidates"][0]["label"] = hostile
        self.put("report-navigation.json", index)
        run = self.collect()
        data = {"runs": [run], "warnings": [], "generated_at_utc": "fixture"}
        html = hub.render(data)
        self.assertNotIn(hostile, html)
        self.assertIn('\\u003c/script\\u003e', html)
        self.assertIn("x.textContent=String(text)", html)
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js required for generated candidate navigation checks")
        code = re.search(r"<script>\n('use strict';.*?)</script>", html, re.S).group(1)
        stub = r'''
class Node{constructor(tag,text=''){this.tag=tag;this.children=[];this.textContent=text;this.attrs={};this.classList={toggle:()=>{}}}append(...c){this.children.push(...c)}replaceChildren(...c){this.children=[...c]}setAttribute(k,v){this.attrs[k]=v}}
const ids=Object.fromEntries(['data','timestamp','nav','title','content'].map(k=>[k,new Node('div')]));ids.data.textContent=JSON.stringify(fixture);
const document={getElementById:k=>ids[k],createElement:tag=>new Node(tag),createTextNode:text=>new Node('#text',text),querySelectorAll:()=>ids.nav.children};
const descendants=(r,tag)=>r.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...descendants(c,tag)]);
const textOf=r=>r.textContent+r.children.map(textOf).join(' ');
'''
        assertions = r'''
setView('Candidate evidence');
assert(textOf(ids.content).includes(hostile));
assert(!descendants(ids.content,'img').length);
assert.equal(descendants(ids.content,'a').filter(a=>a.textContent==='Motifs & profiles').length,2);
assert.equal(descendants(ids.content,'a').filter(a=>a.textContent==='Trees & alignments').length,2);
assert(textOf(ids.content).includes('Comparator gaps: Comparator control'));
state.query='PS_TEST';drawRows();assert.equal(descendants(ids.content,'tbody')[0].children.length,2);
state.query='missing_reviewed_comparison_groups';drawRows();assert.equal(descendants(ids.content,'tbody')[0].children.length,2);
state.query='not-present';drawRows();assert(textOf(ids.content).includes('No matching records'));
'''
        script = "const assert=require('node:assert/strict');const fixture=" + json.dumps(data) + ";const hostile=" + json.dumps(hostile) + ";\n" + stub + code + assertions
        result = subprocess.run([node, "-"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
