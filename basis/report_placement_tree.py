"""Portable views of verified reference trees and their query placements.

Rendering never runs native tools, rewrites evidence, or treats a likelihood
weight as a calibrated probability of resistance.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

from .placement_scoring import _Newick, analyze_jplace
from .placement_diagnostics import diagnose_jplace
from .report_placement import (render_placement_diagnostics, diagnostic_side_text,
                               DIAGNOSTIC_CSS)
from .report_tree_views import (_esc, _sha, _verified, _parse_alignment,
                                _parse_tree, _export, MAX_TIPS)

CSS = """
.placement-tree-launch{margin:14px 0;padding:16px;border:1px solid #bed8d8;border-radius:12px;background:#f0f8f7}
.placement-tree-launch h4{margin:0 0 6px}.placement-tree-launch p{font-size:13px;color:#466075}
.placement-tree-links{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.placement-tree-launch .placement-tree-primary{background:#075f60;color:white;border-color:#075f60}
"""
JS = ""
_PAGE_CSS = """
*{box-sizing:border-box}body{margin:0;background:#f2f6f8;color:#223e52;font:14px/1.5 system-ui,sans-serif}main{max-width:1500px;margin:30px auto;padding:0 24px}header{background:#123d50;color:white;padding:25px 30px;border-radius:15px}h1{margin:3px 0 9px;font-size:27px}header p{margin:5px 0;color:#d6e8ee}.eyebrow{letter-spacing:.15em;font-size:11px;font-weight:700;text-transform:uppercase}.card{background:white;border:1px solid #dce6ec;border-radius:12px;margin:18px 0;padding:20px}.toolbar,.legend,.links{display:flex;flex-wrap:wrap;gap:10px;align-items:center}select,button,.button{font:inherit;padding:8px 12px;border:1px solid #bdcdd5;border-radius:7px;background:white;color:#164557;cursor:pointer}.button{text-decoration:none;display:inline-block}.scroll{overflow:auto}.placement-svg{display:block;width:100%;min-width:1100px;background:white}.placement-svg text{font:12px system-ui;fill:#355065}.placement-svg .branch{stroke:#7e97a5;stroke-width:1.6;fill:none}.placement-svg .query-branch{stroke:#047a74;stroke-width:3;fill:none}.placement-svg .query-label{fill:#006a62;font-weight:bold}.placement-svg .support{font-size:9px;fill:#826327}.note{font-size:12px;color:#536e80}.chip{border-radius:12px;padding:3px 9px;font-size:12px;background:#edf3f6}.amr{color:#a3492b}.alternative{color:#4857a6}.context{color:#566a76}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:9px 12px;border-bottom:1px solid #e0e9ee;text-align:left}th{background:#eef4f7}tr.active{background:#e4f5f1}td.num{font-variant-numeric:tabular-nums}details>summary{cursor:pointer;font-weight:650}a{color:#076a70}[hidden]{display:none!important}@media print{main{margin:0;padding:0}.toolbar{display:none}.scroll{overflow:visible}.placement-svg{min-width:0}header{-webkit-print-color-adjust:exact}}
"""
_PAGE_JS = """
const chooser=document.getElementById('placement-choice');
function showPlacement(value){document.querySelectorAll('[data-placement-view]').forEach(x=>{x.hidden=x.dataset.placementView!==value});document.querySelectorAll('[data-placement-row]').forEach(x=>x.classList.toggle('active',x.dataset.placementRow===value));chooser.value=value;}
chooser.addEventListener('change',()=>showPlacement(chooser.value));
document.querySelectorAll('[data-show-placement]').forEach(x=>x.addEventListener('click',()=>{showPlacement(x.dataset.showPlacement);document.getElementById('tree-card').scrollIntoView({behavior:'smooth'});}));
"""
_PAGE_CSS += DIAGNOSTIC_CSS


def _digests(layer, record):
    result = {}
    for source in [record.get('artifact_checksums'),
                   (record.get('placement_review') or {}).get('artifact_checksums'),
                   (record.get('deep_cache') or {}).get('checksums'),
                   layer.get('artifact_checksums')]:
        for path, digest in (source or {}).items():
            result.setdefault(str(path), []).append(digest)
    cache = layer.get('reference_cache') or {}
    if cache.get('directory'):
        for relative, digest in (cache.get('verified_artifact_hashes') or {}).items():
            part = Path(relative)
            if part.is_absolute() or '..' in part.parts:
                raise ValueError('unsafe reference cache artifact path')
            result.setdefault(str(Path(cache['directory']) / part), []).append(digest)
    return result


def _splits(root):
    """Unrooted branch-length signature; degree-two roots are combined."""
    tips = set()
    def collect(node):
        if not node.children:
            tips.add(node.name)
        for child in node.children:
            collect(child)
    collect(root)
    result = {}
    def visit(node):
        descendants = {node.name} if not node.children else set()
        for child in node.children:
            side = visit(child)
            if child.length is None:
                raise ValueError('reference tree branch length missing')
            other = tips - side
            key = min(tuple(sorted(side)), tuple(sorted(other)))
            result[key] = result.get(key, 0.0) + child.length
            descendants.update(side)
        return descendants
    visit(root)
    return result


def _validated(candidate, record):
    layer = (candidate.get('analyses') or {}).get('family_placement') or {}
    if layer.get('status') != 'completed':
        raise ValueError('no completed family placement recorded')
    if record.get('execution_status') != 'completed' or candidate.get('execution') != 'completed':
        raise ValueError('candidate or DeepSafe execution is not completed')
    query = (candidate.get('query') or {}).get('id')
    artifacts = layer.get('artifacts') or []
    digests = _digests(layer, record)
    contents = {}
    for filename in ['reference.treefile', 'labels.json', 'reference_alignment.faa',
                     'query_alignment.faa', 'query.faa', 'epa_result.jplace']:
        paths = list(dict.fromkeys(p for p in artifacts if isinstance(p, str)
                                   and Path(p).name == filename))
        if len(paths) != 1:
            raise ValueError(filename + ': expected exactly one recorded artifact')
        content, error = _verified(paths[0], digests.get(paths[0], []))
        if error:
            raise ValueError(filename + ': ' + error)
        contents[filename] = content
    labels = json.loads(contents['labels.json'])
    jplace = json.loads(contents['epa_result.jplace'])
    reference = _parse_alignment(contents['reference_alignment.faa'])
    aligned_query = _parse_alignment(contents['query_alignment.faa'])
    original_query = _parse_alignment(contents['query.faa'])
    if set(aligned_query) != {query} or set(original_query) != {query}:
        raise ValueError('query identity differs from the recorded placement')
    if set(labels) != set(reference) or len(reference) > MAX_TIPS:
        raise ValueError('reference labels differ from the alignment')
    if len(aligned_query[query]) != len(next(iter(reference.values()))):
        raise ValueError('query and reference alignment column counts differ')
    normalization = layer.get('normalization') or {}
    qnorm = normalization.get('query') or {}
    query_hash = (candidate.get('query') or {}).get('sequence_sha256')
    if (qnorm.get('source_sha256') != query_hash or
            qnorm.get('alignment_sequence_sha256') != _sha(original_query[query].encode())):
        raise ValueError('query sequence differs from its recorded checksum')
    dropped = (layer.get('query_alignment') or {}).get('dropped_insertion_positions_1based', [])
    if (not isinstance(dropped, list) or any(type(p) is not int or p < 1 or p > len(original_query[query]) for p in dropped)
            or len(set(dropped)) != len(dropped)):
        raise ValueError('invalid dropped-query insertion positions')
    retained = ''.join(r for p, r in enumerate(original_query[query], 1) if p not in set(dropped))
    if aligned_query[query].replace('-', '').replace('.', '') != retained:
        raise ValueError('aligned query residues differ from recorded query and insertions')
    for tip, sequence in reference.items():
        norm = (normalization.get('references') or {}).get(tip) or {}
        expected = labels[tip].get('sequence_sha256')
        if norm.get('source_sha256') == expected:
            expected = norm.get('alignment_sequence_sha256')
        if not expected or _sha(sequence.replace('-', '').replace('.', '').encode()) != expected:
            raise ValueError('reference sequence differs from its label checksum: ' + tip)
    scores = analyze_jplace(jplace, labels, query)
    stored = layer.get('scores') or {}
    if stored != scores:
        raise ValueError('saved placement scores differ from the verified native output')
    root = _Newick(jplace['tree']).parse()
    native = _parse_tree(contents['reference.treefile'], reference)
    left, right = _splits(root), _splits(native)
    if set(left) != set(right) or any(not math.isclose(left[k], right[k], rel_tol=1e-6, abs_tol=1e-7) for k in left):
        raise ValueError('jplace tree differs from the recorded reference tree')
    try:
        diagnostics = diagnose_jplace(jplace, labels, query, expected_scores=stored)
    except Exception as exc:
        diagnostics = {'schema': 'basis-placement-diagnostics-v1', 'status': 'failed',
                       'error': str(exc), 'classification_policy_changed': False,
                       'function_probability': None}
    return {'layer': layer, 'query': query, 'protein': str(candidate.get('protein_id') or query),
            'root': root, 'scores': scores, 'labels': labels, 'contents': contents,
            'diagnostics': diagnostics}


def verified_placement_diagnostics(candidate, deep_record):
    """Derive report-only diagnostics from verified artifacts without mutations."""
    try:
        return _validated(candidate, deep_record)['diagnostics']
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        return {'schema': 'basis-placement-diagnostics-v1', 'status': 'not_assessed',
                'reason': 'verified_native_artifacts_unavailable', 'error': str(exc),
                'classification_policy_changed': False, 'function_probability': None}


def _grafted(root, placement, query):
    """Split from the child/distal end, retaining the measured pendant length."""
    root = copy.deepcopy(root)
    target = placement['edge_num']
    found = False
    def graft(parent):
        nonlocal found
        for index, child in enumerate(parent.children):
            if child.edge == target:
                distal = min(child.length, placement['distal_length'])
                proximal = child.length - distal
                child.length = distal
                inserted = type(child)(children=[child, type(child)(name=query,
                    length=placement['pendant_length'])], length=proximal)
                inserted.placement_point = True
                parent.children[index] = inserted
                found = True
                return
            graft(child)
    graft(root)
    if not found:
        # An artificial serialization-root edge has no biological attachment.
        return None
    return root


def _svg(root, placement, labels, query, protein):
    grafted = _grafted(root, placement, query)
    if grafted is None:
        return '<p class="note">This placement uses the artificial serialization-root edge; no biological attachment is drawn.</p>'
    nodes, leaves = [], []
    def visit(node, distance=0.0):
        node.x = distance
        nodes.append(node)
        if node.children:
            for child in node.children:
                visit(child, distance + child.length)
            node.y = (node.children[0].y + node.children[-1].y) / 2
        else:
            node.y = 30 + len(leaves) * 38
            leaves.append(node)
    visit(grafted)
    maximum = max(n.x for n in nodes)
    if not math.isfinite(maximum):
        raise ValueError('nonfinite cumulative tree distance')
    scale = 680 / maximum if maximum else 1
    height = len(leaves) * 38 + 95
    svg = [f'<svg class="placement-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1260 {height}" role="img" aria-label="Reference tree with query placed on edge {placement["edge_num"]}">',
           '<style>.branch{stroke:#7e97a5;stroke-width:1.6;fill:none}.query-branch{stroke:#047a74;stroke-width:3;fill:none}text{font:12px system-ui;fill:#355065}.query-label{fill:#006a62;font-weight:bold}.support{font-size:9px;fill:#826327}</style>']
    for node in nodes:
        x = 20 + node.x * scale
        if node.children:
            svg.append(f'<path class="branch" d="M{x:.5f},{node.children[0].y:.3f} V{node.children[-1].y:.3f}"/>')
            if node.name:
                svg.append(f'<text class="support" x="{x+4:.3f}" y="{node.y-6:.3f}">{_esc(node.name)}</text>')
            for child in node.children:
                isquery = child.name == query
                svg.append(f'<path class="{"query-branch" if isquery else "branch"}" data-branch-length="{child.length:.12g}" d="M{x:.5f},{child.y:.3f} H{20+child.x*scale:.5f}"/>')
            if getattr(node, 'placement_point', False):
                svg.append(f'<circle cx="{x:.5f}" cy="{node.y:.3f}" r="4" fill="#047a74"><title>Query insertion point on edge {placement["edge_num"]}</title></circle>')
        else:
            if node.name == query:
                label, group, color = protein + ' · QUERY', 'placed query, not a reference', '#006a62'
            else:
                info = labels[node.name]
                label = ', '.join(info.get('accessions') or [node.name])
                group = info.get('group') or 'Context reference'
                color = {'amr':'#a3492b', 'alternative':'#4857a6', 'context':'#566a76'}[info['role']]
            group_label = _esc(group.replace('_', ' '))
            svg.append(f'<path d="M{x+3:.3f},{node.y} H720" stroke="#e2eaee" stroke-dasharray="2 3"/><text x="730" y="{node.y+4}" class="{"query-label" if node.name==query else "reference-label"}" style="fill:{color}">{_esc(label)}<title>{_esc(node.name + " | " + label + " | " + group)}</title></text><text x="990" y="{node.y+4}" style="font-size:11px;fill:{color}">{group_label}</text>')
    bar = maximum / 5 if maximum else 0
    y = height - 28
    svg.append(f'<path d="M20,{y-7} V{y} H{20+bar*scale:.3f} V{y-7}" stroke="#40566c" fill="none"/><text x="20" y="{y+17}">{bar:.3g} substitutions / site</text></svg>')
    return ''.join(svg)


def _relative(data):
    key = _sha(json.dumps({'viewer': 2, 'protein': data['protein'],
        'query': data['query'], 'sources': {name: _sha(content)
            for name, content in sorted(data['contents'].items())},
        'diagnostics': data['diagnostics']},
        sort_keys=True).encode())[:24]
    return 'report-data/placement-view-' + key + '.html'


def _page(data, exports):
    rows = sorted(data['scores']['placements'], key=lambda r: (-r['weight'], r['edge_num']))
    diagnostics = data.get('diagnostics') or {}
    diagnostic_edges = {edge['edge_num']:edge for edge in diagnostics.get('edges', [])} if diagnostics.get('status') == 'completed' else {}
    title = data['protein'] + ' · family placement tree'
    body = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + _esc(title) + '</title><style>' + _PAGE_CSS + '</style></head><body><main><header><div class="eyebrow">BaSIS · DeepSafe · reference placement</div><h1>' + _esc(title) + '</h1><p>A reference-only tree, with the query attached at one recorded placement at a time.</p></header><section class="card" id="tree-card"><div class="toolbar"><label for="placement-choice"><strong>Query placement</strong></label><select id="placement-choice">'
    body += ''.join(f'<option value="{row["edge_num"]}">Edge {row["edge_num"]} · LWR {row["weight"]:.6g}' + (' · highest weight' if index == 0 else '') + '</option>' for index, row in enumerate(rows))
    body += '</select></div><p class="note">LWR is the placement likelihood weight ratio, conditional on this reference panel, alignment and model. It is not a probability of resistance or function. The displayed root is arbitrary. The query was placed onto the reference tree; it was not included when that tree was inferred.</p><div class="legend"><span class="chip" style="color:#006a62">● Query placement</span><span class="chip amr">AMR reference group</span><span class="chip alternative">Alternative function group</span><span class="chip context">Context reference</span></div>'
    for index, row in enumerate(rows):
        edge = diagnostic_edges.get(row['edge_num']) or {}
        edge_caption = _edge_caption(edge, row)
        body += f'<div class="scroll" tabindex="0" aria-label="Scrollable placement tree for edge {row["edge_num"]}" data-placement-view="{row["edge_num"]}"' + (' hidden' if index else '') + '><p class="note">'+edge_caption+'</p>' + _svg(data['root'], row, data['labels'], data['query'], data['protein']) + '</div>'
    body += '<p class="note">Horizontal branch lengths are substitutions per site. Query insertion uses the recorded distal distance from the child end of the selected edge and its recorded pendant length. Vertical spacing is for readability. Native internal labels are reference-tree branch support, not support for query function. Changing the placement rescales the view. On narrow screens, scroll the tree and table horizontally; keyboard users can focus each scroll area.</p></section>'
    body += render_placement_diagnostics(diagnostics, data['scores'])
    body += '<section class="card"><h2>All reported placements</h2><p class="note">Weights are shown without renormalization. Unresolved group weight: ' + f'{data["scores"]["unresolved_mass"]:.6g}; unreported weight: {data["scores"]["missing_mass"]:.6g}.' + '</p><div class="scroll" tabindex="0" aria-label="Scrollable placement weights table"><table><thead><tr><th>Edge</th><th>LWR</th><th>Assigned group</th><th>Reason and cut sides</th><th>Distal length</th><th>Pendant length</th><th>View</th></tr></thead><tbody>'
    for index, row in enumerate(rows):
        body += f'<tr data-placement-row="{row["edge_num"]}"' + (' class="active"' if not index else '') + f'><td>{row["edge_num"]}</td><td class="num">{row["weight"]:.12g}</td><td>{_esc(row["group"] or "Unresolved")}</td><td>'+_edge_caption(diagnostic_edges.get(row['edge_num']) or {},row)+f'</td><td class="num">{row["distal_length"]:.8g}</td><td class="num">{row["pendant_length"]:.8g}</td><td><button type="button" data-show-placement="{row["edge_num"]}">Show edge {row["edge_num"]}</button></td></tr>'
    body += '</tbody></table></div></section><section class="card"><h2>Verified downloads</h2><p class="note">The Newick contains references only; the jplace file records query placements. The SVG is a display of the highest-weight attachment, not a newly inferred tree. Original source bytes are preserved in the native exports.</p><div class="links">'
    body += ''.join('<a class="button" download href="' + _esc(Path(path).name) + '">' + _esc(label) + '</a>' for label, path in exports)
    return body + '</div></section></main><script>' + _PAGE_JS + '</script></body></html>'


def _edge_caption(edge, row):
    if not edge:
        return '<strong>Original reason:</strong> '+_esc(row.get('reason', 'not recorded').replace('_',' '))
    result = '<strong>'+_esc(edge.get('category','not recorded').replace('_',' '))+'</strong>'
    result += '<br>Original reason: '+_esc(edge.get('legacy_reason','not recorded').replace('_',' '))
    groups = {group for side in edge.get('sides') or [] for group in side.get('labelled_group_counts') or {}}
    for index, side in enumerate(edge.get('sides') or []):
        result += '<br>Side '+('A' if index == 0 else 'B')+': '+_esc(diagnostic_side_text(side,groups))
    return result


def render_placement_tree(candidate, deep_record, outdir):
    """Return a launch panel; export an offline page and sealed native sources."""
    layer = (candidate.get('analyses') or {}).get('family_placement') or {}
    if layer.get('status') != 'completed':
        return ''
    try:
        data = _validated(candidate, deep_record)
        rows = sorted(data['scores']['placements'], key=lambda r: (-r['weight'], r['edge_num']))
        best_svg = _svg(data['root'], rows[0], data['labels'], data['query'], data['protein'])
        exports = []
        for label, filename, prefix, suffix in [
            ('Reference tree · Newick', 'reference.treefile', 'placement-reference-tree', '.nwk'),
            ('Query placements · jplace', 'epa_result.jplace', 'placement-native', '.jplace'),
            ('Reference alignment · FASTA', 'reference_alignment.faa', 'placement-reference-alignment', '.faa'),
            ('Query alignment · FASTA', 'query_alignment.faa', 'placement-query-alignment', '.faa'),
            ('Reference labels · JSON', 'labels.json', 'placement-labels', '.json')]:
            exports.append((label, _export(Path(outdir), prefix, suffix, data['contents'][filename])))
        if best_svg.startswith('<svg'):
            exports.append(('Highest-weight view · SVG', _export(Path(outdir), 'placement-tree-view', '.svg', best_svg.encode())))
        exports.append(('Localization diagnostics · JSON', _export(Path(outdir), 'placement-diagnostics', '.json', json.dumps(data['diagnostics'],indent=2,sort_keys=True,allow_nan=False).encode())))
        page = _page(data, exports)
        relative = _relative(data)
        target = Path(outdir) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page, encoding='utf-8')
        target.with_suffix('.sha256').write_text(_sha(page.encode('utf-8')) + '\n', encoding='ascii')
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        return '<p class="tree-note">Placement tree visualization unavailable: ' + _esc(exc) + '. Saved analyses are unchanged; no native tools were rerun.</p>'
    historical_diagnostics = render_placement_diagnostics(data['diagnostics'], data['scores']) if not layer.get('diagnostics') else ''
    return '<section class="placement-tree-launch"><h4>Explore the family placement tree</h4><p>Open the reference tree with ' + _esc(data['protein']) + ' highlighted at its highest-weight placement. Switch between recorded placements and inspect readable reference labels.</p><div class="placement-tree-links"><a class="button placement-tree-primary" href="' + _esc(relative) + '" target="_blank" rel="noopener">Open placement tree</a>' + ''.join('<a class="button" download href="' + _esc(path) + '">' + _esc(label) + '</a>' for label, path in exports[:2]) + '</div><p>Placement weights describe phylogenetic association; they are not probabilities of resistance.</p>'+historical_diagnostics+'</section>'


def exported_tree_href(candidate, deep_record, outdir):
    """Link only an existing view whose native sources still pass verification."""
    try:
        data = _validated(candidate, deep_record)
        relative = _relative(data)
        path = Path(outdir) / relative
        seal = path.with_suffix('.sha256')
        if not path.is_file() or not seal.is_file() or seal.stat().st_size > 65:
            return None
        if path.stat().st_size > 40_000_000:
            return None
        return relative if _sha(path.read_bytes()) == seal.read_text(encoding='ascii').strip() else None
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return None
