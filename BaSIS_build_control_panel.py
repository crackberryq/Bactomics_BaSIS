#!/usr/bin/env python3
"""Build a read-only navigation hub for saved BaSIS reports and run artifacts.

Run from the BaSIS project after the pilot finishes, or take a labelled snapshot
while it is running. No BaSIS import, native analysis or network request occurs.
The default selects the newest timestamped study-pilot456 directory.
Use --pilot-dir PATH to select a pilot explicitly; --results PATH can be repeated
to add other existing BaSIS output directories. Keep the linked folders in place.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote
import uuid


def say(message):
    print(message, flush=True)


def load_json(path, warnings):
    try:
        if path.stat().st_size > 256 * 1024 * 1024:
            raise ValueError("JSON exceeds the 256 MiB review limit")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected an object")
        return data
    except (OSError, ValueError) as exc:
        warnings.append(f"Could not read {path}: {exc}")
        return {}


def safe_file(path, root):
    try:
        return path.resolve(strict=True).is_relative_to(root.resolve(strict=True)) and path.is_file()
    except OSError:
        return False


def href(path, output, root):
    if not safe_file(path, root):
        return None
    return quote(Path(os.path.relpath(path.resolve(), output.resolve())).as_posix(), safe="/")


def anchor(sample, protein):
    value = json.dumps([str(sample), str(protein)], ensure_ascii=False, separators=(",", ":"))
    return "candidate-" + hashlib.sha256(value.encode()).hexdigest()[:20]


def classify(path):
    suffix = path.suffix.lower()
    name = path.name.lower()
    if "citation" in name or suffix in {".bib", ".ris", ".cff"}:
        return "Citations"
    if ("tree-view" in name or "placement-tree" in name) and suffix in {".svg", ".html"}:
        return "Trees & placement"
    if "alignment" in name or suffix in {".aln", ".afa", ".a3m"}:
        return "Alignments"
    if suffix in {".html", ".htm"}:
        return "Reports"
    if suffix in {".treefile", ".contree", ".nwk", ".newick", ".jplace", ".nex", ".ufboot", ".iqtree"}:
        return "Trees & placement"
    if suffix in {".fasta", ".fa", ".faa", ".fna", ".gbk", ".gbff", ".gff", ".gff3"}:
        return "Sequences & annotation"
    if suffix in {".svg", ".png", ".jpg", ".jpeg", ".pdf"}:
        return "Figures & documents"
    if suffix == ".log" or "stderr" in name or "stdout" in name:
        return "Logs"
    if suffix in {".json", ".tsv", ".csv", ".yaml", ".yml", ".txt", ".domtblout", ".tblout"}:
        return "Evidence & tables"
    return "Other files"


def collect_run(root, run_id, output, pilot_item=None):
    root = root.resolve()
    warnings = []
    if not root.is_dir():
        return {"id": run_id, "label": (pilot_item or {}).get("sample_id", root.name),
                "root": str(root), "scope": (pilot_item or {}).get("scope", "not recorded"),
                "status": "output not yet available", "report": None,
                "modules": [], "candidates": [], "files": [], "warnings": ["Run output directory not yet present"]}
    run_path = root / "run.json"
    run = load_json(run_path, warnings) if safe_file(run_path, root) else {}
    main_report = href(root / "report.html", output, root)
    modules, candidates, current_attempts = [], [], set()
    result_root = root / "results"
    paths = sorted(result_root.glob("*/*/result.json")) if result_root.is_dir() else []
    for path in paths:
        if not safe_file(path, root):
            warnings.append(f"Excluded result outside run directory: {path}")
            continue
        rec = load_json(path, warnings)
        sid, module = path.parent.parent.name, path.parent.name
        if pilot_item and sid != pilot_item.get("sample_id"):
            warnings.append(f"Result sample differs from pilot manifest: {path}")
            continue
        if rec and (rec.get("sample_id") != sid or rec.get("module") != module):
            warnings.append(f"Result identity mismatch: {path}")
            continue
        recorded_artifacts = rec.get("artifacts", [])
        if not isinstance(recorded_artifacts, list):
            recorded_artifacts = []
        for artifact in recorded_artifacts:
            if not isinstance(artifact, str):
                continue
            candidate_path = Path(artifact)
            if not candidate_path.is_absolute():
                candidate_path = root / candidate_path
            try:
                rel = candidate_path.resolve().relative_to(root)
            except (OSError, ValueError):
                continue
            parts = rel.parts
            if "attempts" in parts:
                i = parts.index("attempts")
                if len(parts) > i + 1:
                    current_attempts.add(Path(*parts[:i+2]).as_posix())
        modules.append({"sample": sid, "name": module,
                        "status": rec.get("execution_status", "not recorded"),
                        "reason": rec.get("error") or rec.get("reason") or "",
                        "href": href(path, output, root)})
        if module == "deepsafe":
            rows = rec.get("candidates", [])
            if not isinstance(rows, list):
                warnings.append(f"Unexpected candidate inventory: {path}")
                continue
            for c in rows:
                if not isinstance(c, dict) or not c.get("selected"):
                    continue
                if c.get("sample_id") not in (None, "", sid):
                    warnings.append(f"Excluded candidate with conflicting sample identity: {path}")
                    continue
                protein = c.get("protein_id") or c.get("candidate_id") or "unrecorded"
                assessment = c.get("assessment") or {}
                layers = c.get("analyses") or {}
                candidates.append({"sample": sid, "protein": str(protein), "gene": str(c.get("gene") or ""),
                    "execution": c.get("execution", "not recorded"),
                    "interpretation": assessment.get("interpretation", "not recorded"),
                    "followup": assessment.get("followup", "not recorded"),
                    "layers": {name: value.get("status", "not recorded")
                               for name, value in layers.items() if isinstance(value, dict)},
                    "href": main_report + "#" + anchor(sid, protein) if main_report else None,
                    "evidence": href(path, output, root)})
    inventory = []
    excluded = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            if name.startswith("."):
                continue
            path = Path(directory) / name
            if not safe_file(path, root):
                excluded += 1
                continue
            relative = path.relative_to(root)
            parts = relative.parts
            status = "run artifact"
            if "history" in parts:
                status = "historical snapshot"
            elif "attempts" in parts:
                i = parts.index("attempts")
                prefix = Path(*parts[:i+2]).as_posix()
                status = "referenced by current result" if prefix in current_attempts else "other attempt; not current evidence"
            sample, module = "", ""
            if len(parts) >= 3 and parts[0] == "results":
                sample, module = parts[1], parts[2]
            try:
                size = path.stat().st_size
            except OSError:
                warnings.append(f"File disappeared while indexing: {path}")
                continue
            inventory.append({"path": relative.as_posix(), "href": href(path, output, root),
                              "sample": sample, "module": module, "kind": classify(path),
                              "status": status, "bytes": size})
            if len(inventory) > 200000:
                raise ValueError(f"More than 200,000 files in {root}; select a narrower output directory")
    if excluded:
        warnings.append(f"Excluded {excluded} broken or out-of-root file links")
    return {"id": run_id, "label": (pilot_item or {}).get("sample_id", root.name), "root": str(root),
            "scope": (pilot_item or {}).get("scope", "scope recorded in source report"),
            "status": run.get("completeness") or (pilot_item or {}).get("status", "not recorded"),
            "report": main_report, "modules": modules, "candidates": candidates,
            "files": inventory, "warnings": warnings}


HTML = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>BaSIS · Study control panel</title><style>
:root{--ink:#173347;--muted:#597083;--line:#dae4ec;--teal:#097f83;--nav:#102b3f;--bg:#f2f6f9}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
button,input,select{font:inherit}button,a{touch-action:manipulation}a{color:#086e83;text-decoration:none}a:hover{text-decoration:underline}
button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #ce9a30;outline-offset:3px}
.rail{position:fixed;inset:0 auto 0 0;width:234px;background:var(--nav);color:#e6f1f4;padding:30px 20px;overflow:auto}
.brand{font-size:31px;font-weight:780;letter-spacing:-1px}.rail small{color:#9db8c7}.rail nav{display:grid;gap:8px;margin:35px 0}
.nav{background:none;border:0;color:#c6d7e0;text-align:left;border-radius:8px;padding:12px;cursor:pointer}.nav.active{background:#20495c;color:#fff;font-weight:700}
.rail footer{font-size:12px;color:#a5becc;margin-top:40px}.main{margin-left:234px;padding:32px;max-width:1800px}
.top{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:25px}.eyebrow{color:var(--teal);text-transform:uppercase;letter-spacing:1.7px;font-size:11px;font-weight:800}
h1{font-size:32px;line-height:1.15;letter-spacing:-1px;margin:7px 0 12px}h2{font-size:22px;margin:0 0 10px}h3{margin:0 0 8px}.muted{color:var(--muted)}
.tag{display:inline-block;padding:5px 10px;background:#e4eef3;border-radius:7px;font-size:12px;font-weight:650}.tag.completed{background:#dcefeb;color:#195d52}.tag.failed{background:#fbe2e2;color:#922e36}
.note{padding:16px 20px;border-left:4px solid #c69a40;background:#fff7e8;border-radius:6px;margin:16px 0 24px;font-size:14px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:18px}.card,.panel{background:white;border:1px solid var(--line);border-radius:12px;padding:22px;margin-bottom:18px;min-width:0}
.card p{margin:8px 0}.metrics{display:flex;gap:24px;margin:22px 0}.metric strong{display:block;font-size:28px;line-height:1.1}.metric span{font-size:12px;color:var(--muted)}
.button{display:inline-block;background:var(--teal);color:white;padding:10px 15px;border-radius:7px;font-weight:650;border:0;cursor:pointer;margin:8px 8px 0 0}.button:hover{background:#066a6c;text-decoration:none}.button.secondary{background:#eaf1f5;color:var(--ink)}
.filters{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0}.filters label{display:grid;gap:6px;font-size:12px;color:var(--muted);min-width:170px}.filters .search{flex:1;min-width:240px}
input,select{padding:10px 12px;border:1px solid #bacbd8;border-radius:7px;background:white;color:var(--ink);max-width:100%}
.scroll{overflow:auto;border:1px solid var(--line);border-radius:9px}table{width:100%;border-collapse:collapse;background:#fff;font-size:13px}th{text-align:left;background:#eaf1f6;color:#416074;font-size:11px;text-transform:uppercase;letter-spacing:.6px}td,th{padding:13px 15px;border-bottom:1px solid var(--line);vertical-align:top}td.path{min-width:260px;max-width:620px;overflow-wrap:anywhere}tr:last-child td{border:0}.small{font-size:12px}.pager{display:flex;gap:12px;align-items:center;justify-content:flex-end;margin:16px 0}
details{margin-top:12px;font-size:12px}summary{cursor:pointer;color:var(--teal);font-weight:650}code{overflow-wrap:anywhere}ul{padding-left:20px}.empty{padding:26px;color:var(--muted);text-align:center}
@media(max-width:950px){.rail{position:static;width:auto;padding:18px 22px}.rail nav{display:flex;flex-wrap:wrap;margin:14px 0 0;gap:4px}.rail footer{display:none}.main{margin:0;padding:22px}.top{align-items:flex-start;flex-direction:column}h1{font-size:28px}.nav{padding:9px}}
@media print{.rail,.filters,.pager{display:none}.main{margin:0;padding:0}.card,.panel{break-inside:avoid}a{color:var(--ink)}}
</style></head><body><aside class="rail"><div class="brand">BaSIS<span style="color:#5dc7c0">.</span></div><small>Study control panel</small><nav id="nav" aria-label="Report sections"></nav><footer>Saved evidence, connected.<br><br>This panel navigates existing outputs. It does not run analyses or issue a safety verdict.</footer></aside>
<main class="main"><header class="top"><div><div class="eyebrow">Bactromics environment</div><h1 id="title">Study overview</h1><div id="timestamp" class="small muted"></div></div><a class="button secondary" href="inventory.json" target="_blank" rel="noopener">Download inventory</a></header>
<div class="note">Execution status and scientific interpretation are separate. AMR evidence does not establish pathogenicity. Unresolved findings and limited-input samples retain their recorded assessment limits.</div>
<section id="content" aria-live="polite"></section></main><script id="data" type="application/json">__DATA__</script><script>
'use strict';
const data=JSON.parse(document.getElementById('data').textContent);
const state={view:'Overview',run:'all',kind:'all',attempt:'all',query:'',page:0};
const views=['Overview','Sample reports','Candidate evidence','Trees & alignments','All run files','Execution & provenance'];
const el=(tag,text,cls)=>{const x=document.createElement(tag);if(text!==undefined)x.textContent=String(text);if(cls)x.className=cls;return x};
const human=x=>String(x??'not recorded').replaceAll('_',' ');
const link=(url,label,cls)=>{if(!url)return el('span','Not yet available','muted');const a=el('a',label,cls);a.href=url;a.target='_blank';a.rel='noopener';return a};
const button=(label,action,cls='button secondary')=>{const b=el('button',label,cls);b.type='button';b.onclick=action;return b};
const selected=()=>data.runs.filter(r=>state.run==='all'||r.id===state.run);
const badge=s=>el('span',human(s),'tag '+(s==='completed'?'completed':s==='failed'?'failed':''));
const bytes=n=>n<1024?n+' B':n<1048576?(n/1024).toFixed(1)+' KiB':(n/1048576).toFixed(1)+' MiB';
document.getElementById('timestamp').textContent='Snapshot: '+data.generated_at_utc+' · Local report navigation';
views.forEach(v=>document.getElementById('nav').append(button(v,()=>{state.view=v;state.kind='all';state.query='';state.page=0;render()},'nav')));
function filters(host,files=false){const row=el('div',undefined,'filters');function select(label,values,key){const lab=el('label',label);const s=el('select');s.setAttribute('aria-label',label);values.forEach(([v,t])=>{const o=el('option',t);o.value=v;s.append(o)});s.value=state[key];s.onchange=()=>{state[key]=s.value;state.page=0;render()};lab.append(s);row.append(lab)}
 select('Run',[['all','All runs'],...data.runs.map(r=>[r.id,r.label])],'run');
 if(files){select('File category',[['all','All categories'],...Array.from(new Set(data.runs.flatMap(r=>r.files.map(f=>f.kind)))).sort().map(x=>[x,x])],'kind');select('Evidence status',[['all','All recorded files'],['current','Exclude historical / other attempts'],['history','Historical / other attempts']],'attempt')}
 const lab=el('label','Search','search');const input=el('input');input.type='search';input.placeholder=files?'Filename, module, sample…':'Protein ID, gene, interpretation…';input.value=state.query;input.oninput=()=>{state.query=input.value;state.page=0;drawRows()};lab.append(input);row.append(lab);host.append(row);
}
let drawRows=()=>{};
function table(host,heads,rows){if(!rows.length){host.append(el('p','No matching records in this snapshot.','empty'));return}const wrap=el('div',undefined,'scroll'),t=el('table'),head=el('thead'),tr=el('tr');heads.forEach(h=>tr.append(el('th',h)));head.append(tr);t.append(head);const body=el('tbody');rows.forEach(cells=>{const r=el('tr');cells.forEach(cell=>{const td=el('td',undefined,'path');if(cell instanceof Node)td.append(cell);else td.textContent=String(cell??'');r.append(td)});body.append(r)});t.append(body);wrap.append(t);host.append(wrap)}
function cards(host){const grid=el('div',undefined,'cards');selected().forEach(r=>{const card=el('article',undefined,'card');card.append(el('div',r.scope,'eyebrow'),el('h2',r.label),badge(r.status));const metrics=el('div',undefined,'metrics');[[r.modules.length,'module records'],[r.candidates.length,'selected candidates'],[r.files.length,'run files']].forEach(([n,l])=>{const m=el('div',undefined,'metric');m.append(el('strong',n),el('span',l));metrics.append(m)});card.append(metrics,link(r.report,'Open full report','button'),button('Browse run files',()=>{state.run=r.id;state.view='All run files';state.page=0;render()}));const d=el('details');d.append(el('summary','Recorded location'),el('code',r.root));card.append(d);r.warnings.forEach(w=>card.append(el('p',w,'small muted')));grid.append(card)});host.append(grid)}
function filesView(host){filters(host,true);const target=el('div');host.append(target);drawRows=()=>{target.replaceChildren();const q=state.query.toLowerCase();let rows=selected().flatMap(r=>r.files.map(f=>({...f,run:r.label}))).filter(f=>(state.kind==='all'||f.kind===state.kind)&&[f.path,f.sample,f.module,f.run].join(' ').toLowerCase().includes(q));
 if(state.view==='Trees & alignments')rows=rows.filter(f=>['Trees & placement','Alignments'].includes(f.kind));
 if(state.view==='Sample reports')rows=rows.filter(f=>/\.html?$/i.test(f.path));
 rows=rows.filter(f=>state.attempt==='all'||(state.attempt==='history')===['historical snapshot','other attempt; not current evidence'].includes(f.status));
 target.append(el('p',rows.length.toLocaleString()+' matching files · Open preserves native report assets; download saves the individual file.','small muted'));const start=state.page*100;
 table(target,['Run / module','File','Category / status','Size','Actions'],rows.slice(start,start+100).map(f=>{const actions=el('div');actions.append(link(f.href,'Open'));const a=link(f.href,'Download');a.download=f.path.split('/').pop();actions.append(document.createTextNode(' · '),a);return [f.run+' / '+(f.module||'run'),f.path,f.kind+' · '+f.status,bytes(f.bytes),actions]}));
 const pager=el('div',undefined,'pager');const prev=button('Previous',()=>{state.page--;drawRows()});prev.disabled=state.page===0;const next=button('Next',()=>{state.page++;drawRows()});next.disabled=start+100>=rows.length;pager.append(prev,el('span','Page '+(state.page+1)+' / '+Math.max(1,Math.ceil(rows.length/100))),next);target.append(pager)};drawRows()}
function candidatesView(host){filters(host);const target=el('div');host.append(target);drawRows=()=>{target.replaceChildren();const q=state.query.toLowerCase();const rows=selected().flatMap(r=>r.candidates.map(c=>({...c,run:r.label}))).filter(c=>[c.sample,c.protein,c.gene,c.interpretation,c.followup].join(' ').toLowerCase().includes(q));target.append(el('p','Recorded selected candidates only. Labels and interpretations are copied without reassessment. The full report contains deferred candidates and assessment detail.','small muted'));table(target,['Sample / protein','Screen label','Execution / interpretation','Recorded follow-up','Evidence'],rows.map(c=>{const box=el('div');box.append(link(c.href,'Open candidate'),document.createTextNode(' · '),link(c.evidence,'Source JSON'));const d=el('details');d.append(el('summary','Analysis layers'));Object.entries(c.layers).forEach(([k,v])=>d.append(el('p',human(k)+': '+human(v))));box.append(d);return [c.sample+' / '+c.protein,c.gene,human(c.execution)+' / '+human(c.interpretation),human(c.followup),box]}))};drawRows()}
function render(){document.getElementById('title').textContent=state.view;document.querySelectorAll('.nav').forEach((b,i)=>b.classList.toggle('active',views[i]===state.view));const host=document.getElementById('content');host.replaceChildren();drawRows=()=>{};
 if(state.view==='Overview'){cards(host);const p=el('div',undefined,'panel');p.append(el('h2','One entry point to the study'),el('p','Open a sample report, inspect a selected candidate, or browse trees, alignments, tables, sequences and command logs. Native reports open in a new tab so their existing interactive assets remain available.'));p.append(el('p','This is a saved snapshot. Rebuild after analyses finish to include new artifacts. Keep this panel beside its linked study folders; the HTML alone is not a portable report bundle.','muted'));host.append(p)}
 else if(state.view==='Candidate evidence')candidatesView(host);
 else if(state.view==='Execution & provenance'){filters(host);const target=el('div');host.append(target);drawRows=()=>{target.replaceChildren();const q=state.query.toLowerCase();table(target,['Sample','Module','Recorded execution','Reason','Evidence'],selected().flatMap(r=>r.modules).filter(m=>[m.sample,m.name,m.status,m.reason].join(' ').toLowerCase().includes(q)).map(m=>[m.sample,m.name,human(m.status),m.reason,link(m.href,'Open JSON')]));data.warnings.forEach(w=>target.append(el('p',w,'note')))};drawRows()}
 else filesView(host);
}
render();
</script></body></html>'''


def render(data):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return HTML.replace("__DATA__", payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-dir", type=Path)
    parser.add_argument("--results", type=Path, action="append", default=[])
    args = parser.parse_args()
    project = Path.cwd().resolve()
    pilot_dir = args.pilot_dir.resolve() if args.pilot_dir else None
    if not pilot_dir and not args.results:
        pilots = sorted((project / "study-pilot456").glob("*/pilot.json"), key=lambda p: p.parent.name)
        if not pilots:
            raise ValueError("No pilot manifest found; use --pilot-dir PATH or --results OUTPUT_DIRECTORY")
        pilot_dir = pilots[-1].parent.resolve()
    warnings, sources = [], []
    if pilot_dir:
        manifest_path = pilot_dir / "pilot.json"
        if not safe_file(manifest_path, pilot_dir):
            raise ValueError("Pilot manifest missing or outside the selected pilot")
        pilot = load_json(manifest_path, warnings)
        if pilot.get("schema") != "basis-pilot456-runner-v1":
            raise ValueError("Unrecognized pilot manifest schema")
        for item in pilot.get("samples", []):
            root = Path(item["outdir"]).resolve()
            if not root.is_relative_to(pilot_dir):
                raise ValueError(f"Pilot output escapes selected study: {root}")
            sources.append((root, item))
        say(f"Selected pilot: {pilot_dir}")
    for root in args.results:
        root = root.resolve(strict=True)
        if not root.is_dir() or not ((root / "results").is_dir() or (root / "report.html").is_file()):
            raise ValueError(f"Not a BaSIS output directory: {root}")
        sources.append((root, None))
    if not sources:
        raise ValueError("No run outputs selected")
    base = pilot_dir if pilot_dir else project
    output = base / "control-panels" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:6])
    for root, _ in sources:
        if output.is_relative_to(root):
            raise ValueError("Run the helper from outside the selected output directories")
    output.mkdir(parents=True, exist_ok=False)
    data = {"schema": "basis-report-control-panel-v1", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "runs": [], "warnings": warnings,
            "scope": "Existing in-run files; external databases, raw-input directories and out-of-root symlinks are not inventoried.",
            "limitations": "No analyses, biological reassessment, checksum validation or independent scientific validation performed."}
    seen = set()
    for root, item in sources:
        if root in seen:
            continue
        seen.add(root)
        say(f"Indexing saved outputs: {root}")
        data["runs"].append(collect_run(root, f"run{len(data['runs'])+1}", output, item))
    (output / "inventory.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "index.html").write_text(render(data), encoding="utf-8")
    say(f"CONTROL PANEL: {output / 'index.html'}")
    say(f"Runs: {len(data['runs'])} | files linked: {sum(len(r['files']) for r in data['runs']):,}")
    say("Report navigation only. Original results and scientific interpretations unchanged.")
    try:
        import subprocess
        converted = subprocess.run(["wslpath", "-w", str(output / "index.html")], capture_output=True, text=True)
        if converted.returncode == 0:
            say("WINDOWS PATH: " + converted.stdout.strip())
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(f"Control-panel error: {exc}", file=sys.stderr)
        raise SystemExit(1)
