"""Offline reporting for the internal, source-audited seed-panel benchmark.

Reports summarize recorded outputs only.  This module never runs native tools,
changes the input bundle, or presents placement mass as a functional probability.
"""
from __future__ import annotations

import csv
import html
import hashlib
import json
import math
from collections import Counter
from copy import deepcopy
from pathlib import Path

from .report_placement import diagnostic_summary, DIAGNOSTIC_CSS


SCHEMA = "basis-placement-seed-benchmark-run-v1"
METHODS = ("placement", "blastp")
METHOD_LABELS = {"placement": "Reference-tree placement", "blastp": "BLASTP nearest reference"}
CSS = """
:root{color-scheme:light;--ink:#203c50;--muted:#577080;--line:#dce6ec;--teal:#087970;--blue:#244e7a;--amber:#926015}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:var(--ink);background:#eef3f7;font:14px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}header{background:linear-gradient(120deg,#12374b,#164e5a);color:white;padding:38px max(26px,calc((100vw - 1280px)/2)) 30px}header h1{font-size:32px;line-height:1.15;letter-spacing:-.03em;margin:10px 0 13px}header p{color:#dbe9ee;max-width:820px;margin:6px 0}.eyebrow{font-size:11px;font-weight:800;letter-spacing:.18em;text-transform:uppercase}.pill{display:inline-block;padding:3px 10px;border-radius:20px;background:#e8f3f1;color:#12655f;font-size:11px;font-weight:750;letter-spacing:.04em}.pill.incomplete,.pill.held{background:#fff1d9;color:#835b15}.pill.failed,.pill.incorrect{background:#fdecea;color:#a34037}.pill.abstained{background:#edf0f6;color:#596a80}.pill.correct{background:#e5f5ee;color:#246746}.pill.completed{background:#e5f3f1;color:#17665f}nav{display:flex;gap:8px;flex-wrap:wrap;margin-top:22px}nav a{color:#eefaff;border:1px solid #567989;border-radius:7px;padding:6px 11px;text-decoration:none;font-size:12px}main{max-width:1332px;margin:24px auto;padding:0 26px 50px}.notice{border-left:4px solid #ccaa5d;padding:17px 20px;background:#fff9ed;border-radius:0 10px 10px 0;margin-bottom:22px}.notice strong{display:block;font-size:15px;margin-bottom:4px}.notice p{margin:0;color:#745e38}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:22px 0}.card,.section{background:white;border:1px solid var(--line);border-radius:12px;box-shadow:0 2px 9px #16304405}.card{padding:19px}.card .value{font-size:29px;font-weight:760;letter-spacing:-.03em}.card .label{font-size:12px;color:var(--muted);margin-bottom:5px}.card small{display:block;color:var(--muted);font-size:11px;margin-top:4px}.section{padding:23px;margin:20px 0;scroll-margin-top:18px}.section h2{font-size:20px;letter-spacing:-.02em;margin:0 0 7px}.section h3{font-size:15px;margin:15px 0 8px}.section>p{color:var(--muted);margin:5px 0 16px}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:8px}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:11px 13px;border-bottom:1px solid #e4edf1;text-align:left;vertical-align:top}th{background:#f1f6f8;font-weight:680;white-space:nowrap}tr:last-child td{border-bottom:0}tbody tr:hover{background:#f9fbfc}td.num{font-variant-numeric:tabular-nums;white-space:nowrap}.metric-note{display:block;font-size:10px;color:var(--muted);font-weight:400;margin-top:2px}.score-bar{height:5px;background:#e9eef2;border-radius:8px;width:100%;max-width:135px;margin:6px 0}.score-bar span{display:block;background:var(--teal);height:100%;border-radius:8px}.method-blastp .score-bar span{background:var(--blue)}.muted,.note{color:var(--muted);font-size:12px}.note{margin-top:13px}.downloads,.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.button{display:inline-block;padding:8px 13px;border:1px solid #b6cdd8;background:#f7fbfc;border-radius:7px;color:#185a68;text-decoration:none;font-size:12px;font-weight:650}.button.primary{background:#087970;border-color:#087970;color:white}.toolbar{margin:16px 0 12px}.toolbar label{font-size:12px;color:var(--muted)}input,select{font:inherit;font-size:12px;border:1px solid #bdd0da;border-radius:6px;background:white;padding:8px 10px;color:var(--ink)}input{min-width:245px;max-width:100%}code{font-size:11px;overflow-wrap:anywhere}.reason{max-width:390px;min-width:130px;overflow-wrap:anywhere}.result-label{font-size:11px;margin:3px 0}.status-note{font-size:11px;color:var(--muted)}details{margin:10px 0}summary{cursor:pointer;font-weight:650;font-size:13px}pre{background:#f3f7fa;border:1px solid var(--line);padding:14px;border-radius:7px;overflow:auto;max-height:440px;font:11px/1.6 ui-monospace,monospace;white-space:pre-wrap;word-break:break-word}.empty{padding:23px;color:var(--muted);text-align:center}.group-label{display:block;color:var(--muted);font-size:11px}.footer{font-size:11px;color:var(--muted);margin:25px 0}a{color:#087970}.placement-tree-launch{border:1px solid #bad7d5;background:#f0f8f6;border-radius:10px;padding:15px;margin:14px 0}.placement-tree-launch h4{margin:0 0 5px}.placement-tree-launch p{font-size:12px;color:var(--muted)}.placement-tree-links{display:flex;flex-wrap:wrap;gap:7px}[hidden]{display:none!important}@media(max-width:800px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}header{padding:27px 20px}header h1{font-size:27px}main{padding:0 14px 35px}.section{padding:17px}input{min-width:200px}}@media print{body{background:white}header{background:#173f51!important;-webkit-print-color-adjust:exact}nav,.toolbar{display:none}.card,.section{box-shadow:none;break-inside:avoid}.table-wrap{overflow:visible}main{padding:0}.notice{break-inside:avoid}}
"""
JS = """
(()=>{const search=document.getElementById('query-filter');const method=document.getElementById('method-filter');const outcome=document.getElementById('outcome-filter');const rows=Array.from(document.querySelectorAll('[data-result]'));const count=document.getElementById('visible-count');function apply(){const q=search.value.toLowerCase();let visible=0;rows.forEach(row=>{const show=(!q||row.textContent.toLowerCase().includes(q))&&(!method.value||row.dataset.method===method.value)&&(!outcome.value||row.dataset.outcome===outcome.value);row.hidden=!show;if(show)visible++;});count.textContent=visible+' of '+rows.length+' method–query results shown';}search.addEventListener('input',apply);method.addEventListener('change',apply);outcome.addEventListener('change',apply);apply();})();
"""
CSS += DIAGNOSTIC_CSS


def _esc(value):
    return html.escape(str(value if value is not None else "—"), quote=True)


def _json(value):
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return "—"
    return f"{value:,.2f}"


def _ratio(numerator, denominator):
    if not denominator:
        return '—<span class="metric-note">No eligible denominator</span>'
    fraction = numerator / denominator
    return (f'{100 * fraction:.1f}%<span class="metric-note">{numerator} / {denominator}</span>'
            f'<div class="score-bar"><span style="width:{100 * fraction:.6f}%"></span></div>')


def _outcome(row):
    if row.get("status") in ("held", "failed"):
        return row["status"]
    if row.get("abstained") or row.get("predicted_group") is None:
        return "abstained"
    return "correct" if row.get("predicted_group") == row.get("true_group") else "incorrect"


def _validated_rows(bundle):
    if bundle.get("schema") != SCHEMA:
        raise ValueError("Expected internal seed-panel benchmark run schema")
    if bundle.get("status") not in ("prepared", "completed", "incomplete"):
        raise ValueError("Benchmark status must be prepared, completed or incomplete")
    rows = bundle.get("results")
    if not isinstance(rows, list):
        raise ValueError("Benchmark results must be a list")
    if bundle["status"] == "prepared" and rows:
        raise ValueError("A prepared-only benchmark cannot contain prediction results")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("method") not in METHODS:
            raise ValueError("Unknown benchmark result method")
        query = row.get("query_id")
        if not isinstance(query, str) or not query:
            raise ValueError("Benchmark result requires a query ID")
        key = (row["method"], query)
        if key in seen:
            raise ValueError("Duplicate benchmark method/query result")
        seen.add(key)
        if row.get("status") not in ("completed", "held", "failed"):
            raise ValueError("Unknown benchmark query execution status")
        if row["status"] == "failed":
            if row.get("predicted_group") is not None or (row.get("abstained") is not None and row.get("abstained") is not False):
                raise ValueError("An execution failure is neither an assignment nor a biological abstention")
        else:
            if type(row.get("abstained")) is not bool:
                raise ValueError("Benchmark result requires explicit abstention status")
            if row.get("abstained") != (row.get("predicted_group") is None):
                raise ValueError("Benchmark abstention and predicted group disagree")
            if row["status"] == "held" and not row["abstained"]:
                raise ValueError("Held queries cannot have accepted assignments")
        if not isinstance(row.get("true_group"), str) or not row["true_group"]:
            raise ValueError("Benchmark result requires a scoring-only true group")
        elapsed = row.get("elapsed_seconds")
        if elapsed is not None and (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0):
            raise ValueError("Recorded elapsed seconds must be finite and nonnegative")
    return rows


def _query_metadata(protocol, rows):
    queries = protocol.get("queries")
    if queries is None:
        return {row["query_id"]: row for row in rows}
    if not isinstance(queries, list):
        raise ValueError("Protocol queries must be a list")
    lookup = {}
    for query in queries:
        if not isinstance(query, dict) or not isinstance(query.get("query_id"), str) or not query["query_id"]:
            raise ValueError("Protocol query requires a query ID")
        if query["query_id"] in lookup:
            raise ValueError("Duplicate protocol query ID")
        lookup[query["query_id"]] = query
    for row in rows:
        if row["query_id"] not in lookup:
            raise ValueError("Result query is absent from the recorded protocol")
        if any(lookup[row["query_id"]].get(key) != row.get(key) for key in ("true_group", "cluster_id", "fold_id")):
            raise ValueError("Result labels or holdout membership disagree with the protocol")
    return lookup


def _method_table(rows, total):
    body = []
    for method in METHODS:
        subset = [r for r in rows if r["method"] == method]
        outcomes = Counter(_outcome(r) for r in subset)
        accepted = outcomes["correct"] + outcomes["incorrect"]
        body.append(f'<tr class="method-{method}"><td><strong>{METHOD_LABELS[method]}</strong>'
                    f'<span class="metric-note">{len(subset)} recorded results</span></td>'
                    f'<td class="num">{_ratio(accepted,total)}</td>'
                    f'<td class="num">{_ratio(outcomes["correct"],accepted)}</td>'
                    f'<td class="num">{_ratio(outcomes["correct"],total)}</td>'
                    f'<td class="num">{outcomes["incorrect"]}</td>'
                    f'<td class="num">{outcomes["abstained"]}</td>'
                    f'<td class="num">{outcomes["held"]}</td>'
                    f'<td class="num">{outcomes["failed"]}</td>'
                    f'<td class="num">{total-len(subset)}</td>'
                    f'<td class="num">{_number(sum(float(r.get("elapsed_seconds") or 0) for r in subset))}</td></tr>')
    return ('<div class="table-wrap"><table><thead><tr><th>Method</th><th>Assignment coverage</th>'
            '<th>Accuracy among accepted</th><th>Correct / all queries</th><th>Wrong</th>'
            '<th>Abstained</th><th>Held</th><th>Failed</th><th>Missing</th><th>Recorded seconds</th>'
            '</tr></thead><tbody>'+''.join(body)+'</tbody></table></div>')


def _result_table(rows, query_metadata, tree_links=None, prepared=False, diagnostics=None):
    tree_links = tree_links or {}
    diagnostics = diagnostics or {}
    body = []
    for row in sorted(rows, key=lambda r: (r["query_id"], METHODS.index(r["method"]))):
        outcome = _outcome(row)
        detail = _esc(_json(row.get("metrics") or {}))
        accessions = query_metadata.get(row["query_id"], {}).get("accessions") or []
        accessions_html = ('<span class="group-label">'+_esc(" · ".join(accessions))+'</span>') if accessions else ''
        tree_href = tree_links.get(row["query_id"]) if row["method"] == "placement" else None
        tree_html = (f'<p><a class="button" href="{_esc(tree_href)}" target="_blank" rel="noopener">Open placement tree</a></p>') if tree_href else ''
        diagnostic_html = ''
        if row['method'] == 'placement' and row['query_id'] in diagnostics:
            diagnostic = diagnostic_summary(diagnostics[row['query_id']])
            if diagnostic['status'] == 'completed':
                mass = diagnostic['categories']['competing_group_boundary']
                maximum = diagnostic['max_split_lwr']
                maximum = 'not available' if maximum is None else f'{maximum:.6g}'
                diagnostic_html = ('<div class="fp-localization-summary"><strong>Localization diagnostic</strong><br>'
                    +f'Competing-boundary mass: {mass:.6g}<br>Strongest biological split: {maximum}<br>'
                    +'95% split-set size: '+str(diagnostic['split_set']['selected_split_count'])+'<br>'
                    +_esc(str(diagnostic.get('reason') or '').replace('_',' '))
                    +'. Location concentration does not establish function; the original decision is unchanged.</div>')
            else:
                diagnostic_html = '<p class="fp-localization-summary">Localization diagnostics unavailable; recorded decision unchanged.</p>'
        body.append(f'<tr data-result="true" data-method="{row["method"]}" data-outcome="{outcome}">'
                    f'<td><code>{_esc(row["query_id"])}</code>{accessions_html}<span class="group-label">Fold: {_esc(row.get("fold_id"))}</span>'
                    f'<span class="group-label">Cluster: {_esc(row.get("cluster_id"))}</span></td>'
                    f'<td>{METHOD_LABELS[row["method"]]}</td><td>{_esc(row["true_group"])}</td>'
                    f'<td>{_esc(row.get("predicted_group"))}</td><td><span class="pill {outcome}">{outcome}</span>'
                    f'<div class="status-note">Execution: {_esc(row["status"])}</div></td>'
                    f'<td class="reason">{_esc(row.get("reason") or "—")}<details><summary>Recorded metrics</summary>'
                    f'<pre>{detail}</pre></details>{diagnostic_html}{tree_html}</td><td class="num">{_number(row.get("elapsed_seconds"))}</td></tr>')
    if not body:
        message = 'Predictions have not been run. Planned queries appear in the fold table below.' if prepared else 'No query results were recorded.'
        body.append('<tr><td class="empty" colspan="7">'+message+'</td></tr>')
    return ('<div class="toolbar"><label>Find query <input id="query-filter" type="search" placeholder="Query, group, fold or reason"></label>'
            '<label>Method <select id="method-filter"><option value="">All methods</option><option value="placement">Placement</option>'
            '<option value="blastp">BLASTP</option></select></label><label>Outcome <select id="outcome-filter">'
            '<option value="">All outcomes</option>'+''.join(f'<option value="{x}">{x.title()}</option>' for x in
            ['correct','incorrect','abstained','held','failed'])+'</select></label><span class="muted" id="visible-count"></span></div>'
            '<div class="table-wrap"><table><thead><tr><th>Query</th><th>Method</th><th>Recorded source label</th>'
            '<th>Assigned group</th><th>Outcome</th><th>Reason / metrics</th><th>Seconds</th></tr></thead>'
            '<tbody>'+''.join(body)+'</tbody></table></div>')


def _fold_table(protocol):
    folds = protocol.get("folds") or []
    if not folds:
        return '<p class="note">No folds were recorded. See the full protocol audit.</p>'
    rows = []
    for fold in folds:
        counts = fold.get("training_group_counts") or {}
        rows.append('<tr><td><code>'+_esc(fold.get("fold_id"))+'</code><span class="group-label">'+_esc(fold.get("cluster_id"))+'</span></td>'
                    '<td>'+_esc(", ".join(fold.get("query_ids") or []))+'</td>'
                    '<td>'+_esc(", ".join(fold.get("held_out_ids") or []))+'</td>'
                    '<td>'+_esc(len(fold.get("reference_ids") or []))+'<span class="group-label">'+_esc("; ".join(f"{k}: {v}" for k,v in sorted(counts.items())))+'</span></td>'
                    '<td>'+_esc(fold.get("status"))+'<span class="group-label">'+_esc("; ".join(fold.get("reasons") or []))+'</span></td></tr>')
    return ('<div class="table-wrap"><table><thead><tr><th>Fold / cluster</th><th>Scored queries</th><th>Excluded reference IDs</th>'
            '<th>Remaining references</th><th>Panel gate</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>')


def _policy_table(protocol):
    labels = {"cluster_identity": "Similarity clustering: identity (%)", "cluster_coverage": "Similarity clustering: bilateral span coverage (%)",
              "placement_min_mass": "Placement: minimum group likelihood-weight mass", "placement_min_margin": "Placement: minimum between-group mass margin",
              "blast_min_identity": "BLASTP: minimum identity (%)", "blast_min_query_coverage": "BLASTP: minimum query coverage (%)",
              "blast_min_reference_coverage": "BLASTP: minimum reference coverage (%)", "min_anchors_per_group": "Minimum remaining anchors per group",
              "blast_max_evalue": "BLASTP: maximum E-value",
              "min_training_references": "Minimum remaining unique references"}
    policy = protocol.get("policy") or {}
    if not policy:
        return '<p class="note">No decision policy was recorded.</p>'
    return ('<div class="table-wrap"><table><thead><tr><th>Predeclared research setting</th><th>Value</th></tr></thead><tbody>'+''.join(
        '<tr><td>'+_esc(labels.get(key,key))+'</td><td class="num">'+_esc(value)+'</td></tr>' for key,value in policy.items())+'</tbody></table></div>')


def _tsv_text(value):
    text = str(value if value is not None else "")
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def write_benchmark_report(bundle, outdir):
    """Write portable HTML and exact evidence downloads without mutating bundle."""
    rows = _validated_rows(bundle)
    protocol, evaluation = bundle.get("protocol") or {}, bundle.get("evaluation") or {}
    query_metadata = _query_metadata(protocol, rows)
    prepared = bundle["status"] == "prepared"
    serialized = _json(bundle)  # Reject nonfinite/unserializable evidence before writing.
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    paths = {"html": outdir / "report.html", "benchmark_json": outdir / "benchmark.json",
             "results_tsv": outdir / "results.tsv", "protocol_json": outdir / "protocol.json",
             "evaluation_json": outdir / "evaluation.json",
             "placement_diagnostics_json": outdir / "placement-diagnostics.json"}
    paths["benchmark_json"].write_text(serialized, encoding="utf-8")
    paths["protocol_json"].write_text(_json(protocol), encoding="utf-8")
    paths["evaluation_json"].write_text(_json(evaluation), encoding="utf-8")
    columns = ["method", "query_id", "cluster_id", "fold_id", "true_group", "status",
               "predicted_group", "abstained", "outcome", "reason", "elapsed_seconds", "metrics_json"]
    with paths["results_tsv"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            flat = dict(row, outcome=_outcome(row), metrics_json=json.dumps(row.get("metrics") or {}, sort_keys=True, allow_nan=False))
            writer.writerow([_tsv_text(flat.get(name)) for name in columns])
    queries = set(query_metadata)
    clusters = {row.get("cluster_id") for row in query_metadata.values() if row.get("cluster_id")}
    groups = {row["true_group"] for row in query_metadata.values()}
    failed = sum(row["status"] == "failed" for row in rows)
    views, tree_links, diagnostics, diagnostic_audit = [], {}, {}, []
    for item in ([] if prepared else bundle.get("placement_candidates") or []):
        from .report_placement_tree import render_placement_tree, exported_tree_href, _validated
        candidate, record = deepcopy(item.get("candidate") or {}), item.get("deep_record") or {}
        query_id = item.get("query_id") or (candidate.get("query") or {}).get("id")
        accessions = query_metadata.get(query_id, {}).get("accessions") or []
        if accessions:
            candidate["protein_id"] = " · ".join(accessions)
        views.append(render_placement_tree(candidate, record, outdir))
        href = exported_tree_href(candidate, record, outdir)
        if query_id in query_metadata and href:
            tree_links[query_id] = href
        try:
            verified = _validated(candidate, record)
            diagnostic = verified['diagnostics']
            artifact_sha256 = {name: hashlib.sha256(content).hexdigest() for name,content in verified['contents'].items()}
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            diagnostic = {'schema':'basis-placement-diagnostics-v1','status':'not_assessed',
                          'reason':'verified_native_artifacts_unavailable','error':str(exc),
                          'function_probability':None,'classification_policy_changed':False}
            artifact_sha256 = {}
        diagnostics[query_id] = diagnostic
        diagnostic_audit.append({'query_id':query_id,'diagnostics':diagnostic,'verified_native_artifact_sha256':artifact_sha256})
    paths['placement_diagnostics_json'].write_text(_json({
        'schema':'basis-placement-diagnostic-report-v1',
        'source_benchmark_sha256':hashlib.sha256(serialized.encode('utf-8')).hexdigest(),
        'scope':'report_only_recomputed_from_verified_native_artifacts',
        'source_bundle_and_decisions_unchanged':True, 'candidates':diagnostic_audit}),encoding='utf-8')
    comparison = ('<p class="empty">Predictions have not been run. No method evaluation, accuracy or coverage is available.</p>'
                  if prepared else _method_table(rows, len(queries)))
    evaluation_details = ('<p class="note">Evaluation has not been run.</p>' if prepared else
                          '<details><summary>Evaluation and audit details</summary><pre>'+_esc(_json(evaluation))+'</pre></details>')
    prepare_banner = ('<aside class="notice"><strong>Preparation complete · predictions not run</strong>'
                      '<p>The protocol and reference exclusions are ready. No native predictions or method evaluation are reported.</p></aside>') if prepared else ''
    header = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
              '<title>BaSIS · DeepSafe internal seed-panel benchmark</title><style>'+CSS+'</style></head><body><header>'
              '<div class="eyebrow">BaSIS / DeepSafe · Method evaluation</div><h1>Does placement improve family assignment?</h1>'
              '<p>An auditable comparison of reference-tree placement and BLASTP nearest-reference assignment under shared cluster holdouts.</p>'
              f'<p><span class="pill {_esc(bundle["status"])}">Run: {_esc(bundle["status"])}</span></p>'
              '<nav aria-label="Report sections"><a href="#comparison">Method comparison</a><a href="#queries">Query results</a>'
              '<a href="#protocol">Holdouts &amp; policy</a><a href="#downloads">Evidence downloads</a></nav></header><main>')
    content = (prepare_banner+'<aside class="notice"><strong>Internal seed-panel benchmark — not independent validation or calibrated probabilities</strong>'
               '<p>These queries come from the source-audited reference panel. Small related families limit generalization. '
               'A correct family assignment does not establish resistance expression, isolate susceptibility, or organism safety.</p></aside>'
               '<div class="cards">'+''.join(f'<div class="card"><div class="label">{label}</div><div class="value">{value}</div><small>{note}</small></div>'
               for label,value,note in [('Unique test queries',len(queries),'Unique queries in the frozen protocol'),
                                        ('Holdout clusters',len(clusters),'Queries in one cluster are not independent'),
                                        ('Recorded source groups',len(groups),'Functional group labels used for scoring'),
                                        ('Execution failures','—' if prepared else failed,'Predictions not run' if prepared else 'Counted separately from abstentions')])+'</div>'
               '<section class="section" id="comparison"><h2>Method comparison</h2><p>Every held, failed or unassigned query remains in the all-query denominator.</p>'
               +comparison+'<p class="note">Assignment coverage = accepted assignments / all queries. Accuracy among accepted excludes abstentions, holds and failures; '
               'read it alongside coverage and correct / all queries. Raw counts are shown because this seed panel is small. '
               'Recorded seconds are run-specific; cache use and shared preparation affect timing.</p></section>'
               '<section class="section" id="queries"><h2>Inspect each query</h2><p>Placement likelihood weights measure conditional phylogenetic association. '
               'BLASTP identity and coverage describe sequence matches. Neither is a calibrated probability of function.</p>'+_result_table(rows,query_metadata,tree_links,prepared,diagnostics)+'</section>'
               '<section class="section" id="protocol"><h2>Holdouts, frozen thresholds and source audit</h2>'
               '<p>Inspect the exact recorded protocol below. Query source labels are for scoring; held-out sequences and their similarity-cluster companions must be excluded from the corresponding reference panel.</p>'
               +_fold_table(protocol)+'<h3>Frozen decision settings</h3>'+_policy_table(protocol)+
               '<p class="note">Similarity-connected components are held out in full, including context sequences. Similarity blocking does not establish clade disjointness. '
               'Thresholds are predeclared research settings; they are not calibrated probability cutoffs.</p>'
               '<details><summary>Complete protocol, reference labels and source audit</summary><pre>'+_esc(_json(protocol))+'</pre></details>'
               +evaluation_details+'</section>')
    if any(views):
        content += '<section class="section"><h2>Inspect saved placement trees</h2><p>These views use verified native artifacts. Query attachments are placements onto a reference-only tree.</p>'+''.join(views)+'</section>'
    content += ('<section class="section" id="downloads"><h2>Evidence downloads</h2><p>Keep this report folder together when sharing. All views and downloads work offline.</p>'
                '<div class="downloads"><a class="button primary" download href="benchmark.json">Complete benchmark · JSON</a>'
                '<a class="button" download href="results.tsv">Query results · TSV</a><a class="button" download href="protocol.json">Frozen protocol · JSON</a>'
                '<a class="button" download href="placement-diagnostics.json">Localization diagnostics · JSON</a>'
                +('' if prepared else '<a class="button" download href="evaluation.json">Evaluation · JSON</a>')+'</div></section>'
                f'<p class="footer">Started: {_esc(bundle.get("started_at"))} · Finished: {_esc(bundle.get("finished_at"))}<br>'
                'BaSIS DeepSafe · Internal seed-panel benchmark · Reporting does not rerun native analyses.</p>')
    paths["html"].write_text(header+content+'</main><script>'+JS+'</script></body></html>', encoding="utf-8")
    return {key: str(path.resolve()) for key,path in paths.items()}
