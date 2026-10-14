"""Release navigation checks on synthetic files; no native analysis is run."""
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from urllib.parse import unquote


from basis import control_panel as hub

DOM = r"""
class Node {
 constructor(tag, text=''){this.tag=tag;this.children=[];this.textContent=text;this.attrs={};this.classList={toggle:()=>{}}}
 append(...children){this.children.push(...children)}
 replaceChildren(...children){this.children=[...children]}
 setAttribute(name,value){this.attrs[name]=value}
}
const ids=Object.fromEntries(['data','timestamp','nav','title','content'].map(id=>[id,new Node('div')]));
ids.data.textContent=JSON.stringify(fixture);
const document={getElementById:id=>ids[id],createElement:tag=>new Node(tag),createTextNode:text=>new Node('#text',text),querySelectorAll:()=>ids.nav.children};
const descendants=(root,tag)=>root.children.flatMap(child=>[...(child.tag===tag?[child]:[]),...descendants(child,tag)]);
const textOf=root=>root.textContent+root.children.map(textOf).join(' ');
const clickNav=label=>ids.nav.children.find(node=>node.textContent===label).onclick();
const paths=()=>descendants(ids.content,'tbody').flatMap(body=>body.children.map(row=>row.children[1].textContent));
"""


class ReleaseNavigationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "saved run"
        self.root.mkdir()
        self.output = self.base / "panel"
        self.output.mkdir()

    def put(self, relative, content="saved"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content) if isinstance(content, dict) else content, encoding="utf-8")
        return path

    def fixture(self):
        current = self.put("results/sample/antismash/attempts/current/native/index.html")
        self.put("results/sample/antismash/attempts/old/native/report.html")
        self.put("results/sample/antismash/native/index.html")
        self.put("results/sample/antismash/native/details.html")
        self.put("results/sample/antismash/native/assets/index.html")
        self.put("report-assets/alignment-view-a1.html")
        self.put("notes.html")
        self.put("report.html")
        self.put("run.json", {"completeness": "incomplete"})
        self.put("results/sample/antismash/result.json", {
            "sample_id": "sample", "module": "antismash", "execution_status": "failed",
            "reason": "saved failure", "artifacts": [str(current)]})
        first = hub.collect_run(self.root, "r1", self.output)
        first["label"] = "First"
        second = {**first, "id": "r2", "label": "Second", "files": [], "modules": [], "candidates": []}
        third = {**second, "id": "r3", "label": "Third"}
        return {"runs": [first, second, third], "warnings": [], "generated_at_utc": "snapshot"}

    def javascript(self, data, assertions):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for generated-page interaction checks")
        page = hub.render(data)
        code = re.search(r"<script>\n('use strict';.*?)</script>", page, re.S).group(1)
        script = "const assert=require('node:assert/strict');const fixture=" + json.dumps(data) + ";\n" + DOM + code + assertions
        result = subprocess.run([node, "-"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_entrypoint_allowlist_is_narrow(self):
        accepted = {
            "report.html": "Consolidated run report",
            "results/s/antismash/native/index.html": "Native tool report",
            "results/s/tool/native/report.htm": "Native tool report",
            "results/s/tool/attempts/a/native/index.html": "Native tool report",
        }
        for path, role in accepted.items():
            self.assertEqual(hub.report_role(path), role, path)
        for path in (
            "notes.html", "history/old/report.html", "report-assets/alignment-view-123.html",
            "results/s/tool/native/alignment-view.html", "results/s/tool/native/assets/index.html",
            "results/s/tool/index.html", "results/s/tool/native/region001.html",
            "results/s/tool/attempts/a/native/readme.html",
        ):
            self.assertIsNone(hub.report_role(path), path)

    def test_roles_do_not_change_status_or_url_safety(self):
        data = self.fixture()
        run = data["runs"][0]
        entries = {f["path"]: f for f in run["files"]}
        self.assertEqual(run["status"], "incomplete")
        self.assertEqual(run["modules"][0]["status"], "failed")
        current = entries["results/sample/antismash/attempts/current/native/index.html"]
        old = entries["results/sample/antismash/attempts/old/native/report.html"]
        self.assertEqual(current["status"], "referenced by current result")
        self.assertEqual(old["status"], "other attempt; not current evidence")
        self.assertEqual(current["report_role"], old["report_role"])
        hostile = self.put('results/s # & " <tag>/tool/native/index.html')
        entry = next(f for f in hub.collect_run(self.root, "r1", self.output)["files"] if f["path"] == hostile.relative_to(self.root).as_posix())
        self.assertEqual(entry["report_role"], "Native tool report")
        self.assertNotRegex(entry["href"], r'["#<>]')
        self.assertEqual((self.output / unquote(entry["href"])).resolve(), hostile)

    def test_overview_clears_run_filter_from_every_filtered_view(self):
        self.javascript(self.fixture(), r"""
for(const view of views.filter(view=>view!=='Overview')){
 clickNav(view);
 const runSelect=descendants(ids.content,'select').find(node=>node.attrs['aria-label']==='Run');
 runSelect.value='r2';runSelect.onchange();
 assert.equal(state.run,'r2');
 clickNav('Overview');
 assert.equal(state.run,'all');
 assert.equal(descendants(ids.content,'article').length,3);
 clickNav(view);
 assert.equal(descendants(ids.content,'select').find(node=>node.attrs['aria-label']==='Run').value,'all');
}
clickNav('Overview');
descendants(ids.content,'button').find(node=>node.textContent==='Browse run files').onclick();
assert.equal(state.run,'r1');
clickNav('Overview');
assert.equal(state.run,'all');
assert.equal(descendants(ids.content,'article').length,3);
""")

    def test_report_list_prioritizes_entrypoints_and_retains_other_html(self):
        self.javascript(self.fixture(), r"""
clickNav('Sample reports');
assert.equal(paths().length,4);
assert.equal(paths()[0],'report.html');
assert(paths().slice(1).every(path=>path.includes('/native/')));
assert(!paths().some(path=>path.includes('alignment')||path.includes('details')||path.includes('assets')||path==='notes.html'));
assert(textOf(ids.content).includes('other attempt; not current evidence'));
const evidence=descendants(ids.content,'select').find(node=>node.attrs['aria-label']==='Evidence status');
evidence.value='current';evidence.onchange();
assert.equal(paths().length,3);
assert(!paths().some(path=>path.includes('/old/')));
clickNav('Overview');
clickNav('All run files');
assert(paths().includes('report-assets/alignment-view-a1.html'));
assert(paths().includes('notes.html'));
assert(paths().includes('results/sample/antismash/native/assets/index.html'));
clickNav('Trees & alignments');
assert.deepEqual(paths(),['report-assets/alignment-view-a1.html']);
""")


if __name__ == "__main__":
    unittest.main()
