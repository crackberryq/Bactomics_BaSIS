"""Offline linked views of recorded candidate evidence and ML nominations.

Colors describe execution/evidence state. They are not a risk score, a vote
count, or a functional classifier. No scientific analysis is performed here.
"""
from __future__ import annotations

from collections import defaultdict
from html import escape
import math


def _esc(value):
    return escape(str(value if value is not None else ""), quote=True)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _items(value):
    return value if isinstance(value, list) else []


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def _fmt(value):
    value = _number(value)
    return "Not recorded" if value is None else f"{value:.5g}"


def _chip(label, state="unknown", detail=""):
    return (f'<span class="eve-chip eve-{_esc(state)}" title="{_esc(detail or label)}">'
            f'{_esc(label)}</span>')


def _link(sid, protein, selected=True):
    if not selected:
        return f'<strong>{_esc(protein or "Unmapped protein")}</strong>'
    from .report_links import candidate_anchor
    anchor = candidate_anchor(sid, protein)
    return (f'<a href="#{_esc(anchor)}" data-eve-candidate="{_esc(anchor)}">'
            f'{_esc(protein or "Unmapped protein")}</a>')


def _locus(sid, protein):
    if not protein:
        return ""
    return (f'<button class="eve-locus" type="button" data-eve-sample="{_esc(sid)}" '
            f'data-eve-protein="{_esc(protein)}" title="Open genomic neighborhood">'
            'Neighborhood <span aria-hidden="true">↗</span></button>')


def _module_states(records):
    states = defaultdict(set)
    for r in records:
        if isinstance(r, dict):
            states[(str(r.get("sample_id", "")), r.get("module") or r.get("tool"))].add(
                r.get("execution_status", "not_assessed"))
    return states


def _weak(f):
    a = _dict(f.get("attributes"))
    return (str(f.get("evidence_level", "")).lower() == "exploratory"
            or str(a.get("cutoff", "")).lower() == "loose"
            or a.get("passes_threshold") is False)


def _held_findings(candidate):
    tax = _dict(candidate.get("taxonomy_review"))
    return {str(row["finding_id"]) for row in _items(tax.get("checks"))
            if isinstance(row, dict) and row.get("finding_id")
            and "review_required" in str(row.get("status", ""))}


def _source_cell(sid, candidate, module, states):
    statuses = states.get((str(sid), module), set())
    # A failed or missing module must never promote retained provisional calls.
    if "completed" not in statuses:
        return _chip("Failed" if "failed" in statuses else "Not assessed",
                     "failed" if "failed" in statuses else "unknown",
                     "No completed source module in this evidence bundle")
    sources = [f for f in _items(candidate.get("source_evidence"))
               if isinstance(f, dict) and (f.get("tool") or f.get("module")) == module]
    passing = [f for f in sources if not _weak(f)]
    if not passing:
        if sources:
            return _chip("Exploratory only", "weak", "Only Loose or below-threshold source records; labels are not promoted")
        return _chip("No linked hit", "none", "Source module completed; no source finding linked to this candidate")
    held = _held_findings(candidate)
    labels = []
    for f in passing:
        a = _dict(f.get("attributes"))
        method = a.get("native_method") if module == "amrfinder" else a.get("cutoff")
        if module in {"vfdb", "bacmet"} and a.get("passes_threshold") is True:
            method = "Threshold pass"
        method = str(method or "Recorded call")
        if module == "rgi":
            method = method.capitalize()
        name = str(f.get("gene") or f.get("reference") or "Unnamed match")
        full_label = f'{method}: {name}'
        # Long catalogue descriptions belong in the tooltip and candidate card;
        # retaining them in a narrow matrix turns one row into a paragraph.
        label = f'{method}: {name}' if len(name) <= 24 else method
        hold = str(f.get("finding_id") or f.get("id")) in held
        if hold:
            label += " · taxon hold"
            full_label += " · taxon hold"
        labels.append(_chip(label, "hold" if hold else "hit", full_label))
    return '<div class="eve-source-stack">' + "".join(dict.fromkeys(labels)) + '</div>'


def _layer_cell(candidate, name):
    execution = candidate.get("execution")
    if execution != "completed":
        return _chip("Failed" if execution == "failed" else "Not assessed",
                     "failed" if execution == "failed" else "unknown",
                     "Candidate execution did not complete; retained analysis details are not promoted")
    layer = _dict(_dict(candidate.get("analyses")).get(name))
    status = layer.get("status")
    reason = layer.get("reason") or status
    if status == "no_applicable_rule":
        return _chip("No applicable rule", "na", reason)
    if status != "completed":
        return _chip("Failed" if status == "failed" else "Not assessed",
                     "failed" if status == "failed" else "unknown", reason)
    if name == "motifs":
        rules = _items(layer.get("rules"))
        if not rules:
            return _chip("Completed", "unknown", "No rule-result count recorded")
        # Preserve the explicit rule-result status and older interpretation form.
        present = sum((r.get("status") == "present" or r.get("interpretation") == "present")
                      for r in rules if isinstance(r, dict))
        if present:
            return _chip(f"{present} present", "hit", f"{len(rules)} applicable rule results recorded")
        absent = sum((r.get("status") == "absent" or r.get("interpretation") == "absent")
                     for r in rules if isinstance(r, dict))
        if absent == len(rules):
            return _chip("No motif match", "none", "Applicable rules evaluated; no motif match recorded")
        return _chip("Review rule results", "hold", "Applicable rule results include an unresolved state")
    hits = layer.get("hits")
    count = len(hits) if isinstance(hits, list) else _number(layer.get("hit_count"))
    if count is None:
        return _chip("Completed", "unknown", "Match count not recorded")
    if count == 0:
        return _chip("No match", "none", reason)
    ga = (layer.get("postfilter") == "model_GA_sequence_and_domain"
          or bool(hits) and isinstance(hits, list) and all(
              isinstance(h, dict) and h.get("passes_ga") is True for h in hits))
    noun = ("GA hits" if ga else "hits") if name == "domains" else "matches"
    return _chip(f"{int(count)} {noun}", "hit", "Recorded annotations; overlapping matches are not independent evidence")


def _reference_cell(candidate):
    if candidate.get("execution") != "completed":
        return _chip("Failed" if candidate.get("execution") == "failed" else "Not assessed",
                     "failed" if candidate.get("execution") == "failed" else "unknown")
    analyses = _dict(candidate.get("analyses"))
    layer = _dict(analyses.get("references"))
    if layer.get("status") != "completed":
        return _chip("Failed" if layer.get("status") == "failed" else "Not assessed",
                     "failed" if layer.get("status") == "failed" else "unknown")
    count = _number(layer.get("selected_count"))
    if count is None and isinstance(candidate.get("references"), list):
        count = len(candidate["references"])
    missing = _items(_dict(candidate.get("assessment")).get("missing_comparison_groups"))
    if missing:
        return _chip(f"Hold · {len(missing)} gaps", "hold", "Missing reviewed comparator groups: " + ", ".join(map(str, missing)))
    if count == 0:
        partial = _items(_dict(analyses.get("partial_references")).get("hits"))
        return _chip("Partial only" if partial else "No eligible refs", "hold" if partial else "none",
                     "No eligible whole-protein comparator; partial evidence does not fill whole-protein coverage")
    if count is None:
        return _chip("Completed", "unknown", "Comparator count not recorded")
    return _chip(f"{int(count)} refs", "hit", "Selected comparator records, not necessarily unique sequences or validated family anchors")


def _taxon_cell(candidate):
    if candidate.get("execution") != "completed":
        return _chip("Not evaluated", "unknown", "Candidate execution did not complete")
    tax = _dict(candidate.get("taxonomy_review"))
    status = tax.get("status")
    if status == "review_required" or _held_findings(candidate):
        return _chip("Review hold", "hold", "Taxonomic applicability requires review; no phenotype is inferred")
    if status in (None, "not_evaluated", "not_assessed"):
        return _chip("Not evaluated", "unknown", tax.get("note") or "Unknown scope is not established applicability")
    if status == "failed":
        return _chip("Failed", "failed")
    return _chip(str(status).replace("_", " "), "info", tax.get("note") or "Recorded scope review only")


def _tree_cell(candidate):
    if candidate.get("execution") != "completed":
        return _chip("Failed" if candidate.get("execution") == "failed" else "Not assessed",
                     "failed" if candidate.get("execution") == "failed" else "unknown")
    tree = _dict(_dict(candidate.get("analyses")).get("phylogeny"))
    assessment = _dict(candidate.get("assessment"))
    status, reason = tree.get("status"), str(tree.get("reason") or "")
    if status == "failed":
        return _chip("Failed", "failed", reason)
    if status != "completed":
        is_hold = str(assessment.get("followup") or "").startswith("hold_") or any(
            word in reason for word in ("review", "comparator", "profile"))
        return _chip("Review hold" if is_hold else "Not assessed", "hold" if is_hold else "unknown", reason)
    unresolved = assessment.get("interpretation") in {"subgroup_unresolved", "unresolved"}
    return _chip("Done · unresolved" if unresolved else "Done · review", "hold" if unresolved else "info",
                 "Recorded tree execution complete; " + str(assessment.get("interpretation", "interpretation requires review")))


def _parent_evidence_state(candidate, sample_id, candidate_records):
    """Keep saved follow-up provisional until its own parent completed."""
    from .report import _execution, _sample_id

    parent = (candidate_records or {}).get(id(candidate))
    if not isinstance(parent, dict) or not parent:
        reason = "Parent execution not recorded"
    elif _sample_id(parent) != sample_id:
        reason = "Parent sample does not match this candidate"
    else:
        status = _execution(parent)
        if status == "completed":
            return None, ""
        reason = "Parent execution: " + status.replace("_", " ")
    detail = (reason + "; candidate execution: " + str(candidate.get("execution") or "not recorded")
              + ". Retained follow-up evidence is provisional, not successful completion.")
    return _chip("Provisional", "hold", detail), reason


def render_evidence_matrix(selected_pairs, records, candidate_records=None):
    """Render saved evidence with the same parent-completion gate as details.

    ``candidate_records`` maps object identity to the originating parent record;
    repeated candidate identifiers in different samples must not share status.
    Missing parents leave follow-up provisional. Independent source calls retain
    the execution status of their own native records.
    """
    pairs = [(str(sid), c) for sid, c in selected_pairs
             if isinstance(c, dict) and c.get("selected") is not False]
    states = _module_states(records)
    queues = sorted({str(c.get("selection_queue") or "unrecorded") for _, c in pairs})
    rows = []
    for sid, c in pairs:
        protein = str(c.get("protein_id") or c.get("candidate_id") or "Unmapped protein")
        queue = str(c.get("selection_queue") or "unrecorded")
        cells = [_source_cell(sid, c, module, states) for module in ("amrfinder", "rgi", "vfdb", "bacmet")]
        parent_cell, parent_note = _parent_evidence_state(c, sid, candidate_records)
        if parent_cell is not None:
            cells += [parent_cell] * 7
        else:
            cells += [_layer_cell(c, name) for name in ("domains", "motifs", "prosite_patterns", "prosite_profiles")]
            cells += [_reference_cell(c), _taxon_cell(c), _tree_cell(c)]
        # Weak labels are deliberately absent from search/headlines and source cells.
        source_labels = [str(f.get("gene", "")) for f in _items(c.get("source_evidence"))
                         if isinstance(f, dict) and not _weak(f) and "completed" in
                         states.get((sid, f.get("tool") or f.get("module")), set())]
        search = " ".join([sid, protein, queue] + source_labels).lower()
        rows.append(f'<tr data-eve-row data-eve-queue="{_esc(queue)}" data-eve-search="{_esc(search)}">'
                    f'<th scope="row">{_link(sid, protein)}<small>{_esc(sid)} · {_esc(queue.replace("_", " "))}</small>'
                    + (f'<small class="eve-parent-status">{_esc(parent_note)}</small>' if parent_note else '') +
                    f'{_locus(sid, protein)}</th>' + "".join(f"<td>{cell}</td>" for cell in cells) + '</tr>')
    headers = ("Protein / queue", "AMRFinder", "RGI", "VFDB", "BacMet · stress", "Pfam domains", "Custom motifs",
               "PROSITE patterns", "PROSITE profiles", "Comparators", "Taxon scope", "Phylogeny")
    options = ''.join(f'<option value="{_esc(q)}">{_esc(q.replace("_", " "))}</option>' for q in queues)
    legend = ''.join(_chip(label, state) for label, state in [
        ("Recorded match", "hit"), ("No match", "none"), ("No applicable rule", "na"),
        ("Not assessed", "unknown"), ("Provisional", "hold"), ("Review hold", "hold"), ("Failed", "failed")])
    return ('<div class="eve-view eve-matrix-view">'
            '<p class="section-intro">Compare the evidence behind each selected protein. Cells describe recorded results '
            'and analysis coverage, not hazard, confidence scores, or independent votes.</p>'
            f'<div class="eve-legend" aria-label="Matrix legend">{legend}</div>'
            '<div class="eve-filters"><label>Find a protein or source call'
            '<input type="search" data-eve-search-input placeholder="Protein, sample, or screened gene…"></label>'
            f'<label>Selection queue<select data-eve-queue-input><option value="">All queues</option>{options}</select></label>'
            f'<span class="eve-count" data-eve-count aria-live="polite">{len(rows)} proteins</span></div>'
            '<div class="eve-table-scroll" tabindex="0" role="region" aria-label="Candidate evidence matrix; scroll horizontally for all layers">'
            '<table class="eve-matrix"><colgroup><col class="eve-protein-column">'
            + '<col class="eve-layer-column">' * (len(headers) - 1)
            + '</colgroup><thead><tr>' + ''.join(f'<th scope="col">{h}</th>' for h in headers) +
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
            '<p class="eve-empty" data-eve-empty hidden>No proteins match these filters.</p>'
            '<p class="eve-footnote">Hover a cell for its recorded status details. “No match” applies only to the completed '
            'analysis and its configured thresholds. Missing taxonomy review does not establish model applicability. '
            'Follow-up evidence remains provisional when its parent execution did not complete or is not recorded. '
            'BacMet cells describe biocide/metal-associated homology; they do not establish expressed tolerance or antibiotic resistance. '
            'Open a protein to inspect the underlying evidence.</p></div>')


def _attention_plot(scores, scale):
    parts = ['<svg class="eve-attention-dots" viewBox="0 0 200 38" role="img" '
             'aria-label="Ensemble member attention scores on this sample’s displayed nomination scale">',
             '<line x1="8" y1="17" x2="192" y2="17" stroke="#cbd8e2" stroke-width="2"/>']
    colors = ("#087c78", "#2858a2", "#98531b", "#7861a8")
    for index, value in enumerate(scores):
        score = _number(value)
        if score is None or score < 0:
            continue
        x = 8 + 184 * score / scale if scale else 8
        y = 12 + (index % 2) * 10
        parts.append(f'<circle cx="{x:.3f}" cy="{y}" r="4" fill="{colors[index % len(colors)]}">'
                     f'<title>Member {index + 1}: {_fmt(score)}</title></circle>')
    parts.append('<text x="8" y="36">0</text>'
                 f'<text x="192" y="36" text-anchor="end">{_fmt(scale)}</text></svg>')
    return ''.join(parts)


def render_attention_explorer(records, selected_pairs, *, show_deep=True):
    """Return recorded PF2 nominations, including those not selected by DeepSafe."""
    selected = {(str(sid), str(c.get("protein_id"))): c for sid, c in selected_pairs
                if show_deep and isinstance(c, dict) and c.get("selected") is not False}
    groups = []
    for record in records:
        if (record.get("module") or record.get("tool")) != "pathogenfinder2":
            continue
        sid = str(record.get("sample_id") or "")
        if record.get("execution_status") != "completed":
            groups.append(f'<p class="eve-attention-unavailable">{_esc(sid)}: attention nominations unavailable '
                          f'({_esc(record.get("execution_status") or "not assessed")}). Retained provisional findings are not displayed.</p>')
            continue
        findings = [f for f in _items(record.get("findings"))
                    if isinstance(f, dict) and f.get("category") == "ml_attention"]
        findings.sort(key=lambda f: (_number(_dict(f.get("attributes")).get("rank"))
                                    if _number(_dict(f.get("attributes")).get("rank")) is not None else float("inf"),
                                    str(f.get("protein_id") or "")))
        values = [value for f in findings for value in [_number(v) for v in
                  [_dict(f.get("attributes")).get("attention_max")] +
                  _items(_dict(f.get("attributes")).get("model_attention_sums"))]
                  if value is not None and value >= 0]
        scale = max(values, default=0)
        rows = []
        for f in findings:
            a = _dict(f.get("attributes"))
            protein = str(f.get("protein_id") or a.get("protein_id") or "Unmapped protein")
            candidate = selected.get((sid, protein))
            rank = _fmt(a.get("rank"))
            mean, maximum = _fmt(a.get("attention_mean")), _fmt(a.get("attention_max"))
            scores = _items(a.get("model_attention_sums"))
            details = ', '.join(f'M{i+1}: {_fmt(value)}' for i, value in enumerate(scores)) or "Member scores not recorded"
            plot = _attention_plot(scores, scale) if scores else '<span class="eve-muted">Not recorded</span>'
            state = ''
            if show_deep:
                state = 'Not selected for DeepSafe'
                if candidate is not None:
                    state = 'DeepSafe: ' + str(candidate.get("execution") or "not assessed")
            state_html = f'<small>{_esc(state)}</small>' if state else ''
            evidence_id = str(f.get("finding_id") or f.get("id") or "ID not recorded")
            rows.append(f'<tr data-eve-attention-row data-eve-attention-search="{_esc((sid + " " + protein).lower())}">'
                        f'<td>{rank}</td><th scope="row">{_link(sid, protein, candidate is not None)}'
                        f'{state_html}{_locus(sid, protein)}</th>'
                        f'<td class="eve-numeric">{maximum}</td><td class="eve-numeric">{mean}</td>'
                        f'<td>{plot}<details class="eve-score-details"><summary>Member values</summary>'
                        f'<p>{_esc(details)}</p><p class="eve-evidence-id">Evidence: {_esc(evidence_id)}</p></details></td></tr>')
        metrics = _dict(record.get("metrics"))
        coverage = _fmt(metrics.get("attention_protein_count"))
        input_count = _fmt(metrics.get("input_protein_count"))
        groups.append(f'<article class="eve-attention-group"><h4>{_esc(sid)} · {len(findings)} recorded nominations</h4>'
                      f'<p class="eve-footnote">Native attention coverage: {coverage} / {input_count} input proteins. '
                      'The table contains saved nominations only; it is not the full proteome score distribution. '
                      f'Dot scale: 0–{_fmt(scale)} within this sample’s displayed nominations.</p>'
                      '<div class="eve-table-scroll" tabindex="0" role="region" aria-label="Recorded ML attention nominations">'
                      '<table class="eve-attention-table"><thead><tr><th scope="col">Rank</th><th scope="col">Protein</th>'
                      '<th scope="col">Maximum member attention</th><th scope="col">Mean member attention</th>'
                      '<th scope="col">Ensemble member attention</th></tr></thead><tbody>' + ''.join(rows) +
                      '</tbody></table></div></article>')
    return ('<div class="eve-view eve-attention-view"><h3>ML attention explorer</h3>'
            '<p>Attention nominates proteins for inspection. These values are model attention weights, not pathogenicity '
            'probabilities, functional annotations, or evidence of causality. Native ranking uses the maximum model sum '
            'after summing token weights within each protein and model.</p>'
            '<label class="eve-attention-filter">Find a nominated protein'
            '<input type="search" data-eve-attention-input placeholder="Protein or sample…"></label>'
            + (''.join(groups) or '<p>No PathogenFinder2 record is available.</p>') +
            '<p class="eve-empty" data-eve-attention-empty hidden>No nominations match this search.</p></div>')


CSS = r"""
.eve-view { min-width:0; }
.eve-legend { display:flex; flex-wrap:wrap; gap:7px; margin:12px 0 18px; }
.eve-chip { display:inline-block; padding:4px 7px; border-radius:5px; font-size:10px; line-height:1.35; font-weight:650; border:1px solid transparent; max-width:190px; overflow-wrap:anywhere; }
.eve-hit { color:#08615f; background:#e7f3f0; border-color:#c5e2db; }
.eve-none { color:#4c5d6d; background:#f0f3f6; border-color:#dbe3e9; }
.eve-na { color:#696276; background:#f2eef7; border-color:#e2dceb; }
.eve-unknown { color:#627281; background:#fff; border:1px dashed #aebbc6; }
.eve-weak { color:#536b8b; background:#f1f5fb; border-color:#dce5f0; }
.eve-info { color:#2858a2; background:#eaf1fc; border-color:#cdddf7; }
.eve-hold { color:#89500b; background:#fff3d8; border-color:#eddaad; }
.eve-failed { color:#a8303d; background:#fcecf0; border-color:#f1c5cc; }
.eve-filters { display:flex; gap:14px; flex-wrap:wrap; align-items:end; margin:14px 0; }
.eve-filters label,.eve-attention-filter { font-size:11px; font-weight:650; color:var(--muted,#5e6c79); display:block; }
.eve-filters label:first-child { flex:1 1 210px; }
.eve-filters input,.eve-filters select,.eve-attention-filter input { display:block; width:100%; padding:9px 11px; margin-top:5px; border:1px solid #ccd8e0; background:#fff; color:#1b2d40; border-radius:7px; font-size:12px; }
.eve-attention-filter { max-width:390px; margin:18px 0; }
.eve-count { font-size:11px; padding:8px 0; color:#5e6c79; white-space:nowrap; }
.eve-table-scroll { overflow:auto; border:1px solid #dce3e8; border-radius:9px; max-width:100%; }
.eve-table-scroll table { width:100%; border-collapse:separate; border-spacing:0; font-size:11px; margin:0; }
.eve-table-scroll table.eve-matrix { table-layout:fixed; width:1510px; min-width:1510px; }
.eve-matrix .eve-protein-column { width:180px; }
.eve-matrix .eve-layer-column { width:133px; }
.eve-matrix .eve-chip { max-width:117px; overflow-wrap:normal; word-break:normal; }
.eve-matrix th,.eve-matrix td { padding:10px 8px; border-bottom:1px solid #e8edf1; text-align:left; vertical-align:top; }
.eve-table-scroll thead th { background:#eef3f6; font-size:10px; color:#43566b; font-weight:750; }
.eve-matrix tbody th { position:sticky; left:0; background:#fff; z-index:1; width:180px; box-shadow:1px 0 0 #dce3e8; }
.eve-matrix tbody tr:nth-child(even) th,.eve-matrix tbody tr:nth-child(even) td { background:#fafcfd; }
.eve-matrix tbody tr:hover th,.eve-matrix tbody tr:hover td { background:#f0f7f8; }
.eve-source-stack { display:flex; gap:4px; flex-direction:column; }
.eve-table-scroll tbody th small { display:block; margin-top:4px; font-size:9px; color:#667887; font-weight:450; }
.eve-locus { display:block; margin:5px 0 0; border:0; background:transparent; color:#087c78; padding:0; font-size:10px; text-align:left; }
.eve-locus:hover { text-decoration:underline; }
.eve-footnote,.eve-muted { color:#657684; font-size:11px; }
.eve-footnote { margin:12px 0; line-height:1.6; }
.eve-empty { background:#f5f7fa; border:1px dashed #bbc9d3; border-radius:6px; padding:14px; color:#5e6c79; }
.eve-attention-view { border-top:1px solid #dce3e8; padding-top:20px; margin-top:20px; }
.eve-attention-view h3 { margin:0 0 8px; }
.eve-attention-group h4 { margin:18px 0 7px; font-size:14px; }
.eve-attention-table { min-width:670px; }
.eve-attention-table th,.eve-attention-table td { padding:9px 12px; text-align:left; border-bottom:1px solid #e8edf1; vertical-align:middle; }
.eve-attention-table tbody tr:hover { background:#f7fafb; }
.eve-attention-table .eve-numeric { font-variant-numeric:tabular-nums; font-family:ui-monospace,SFMono-Regular,Consolas,monospace; }
.eve-attention-dots { width:200px; height:38px; max-width:100%; display:block; }
.eve-attention-dots text { font-family:ui-monospace,monospace; font-size:8px; fill:#657684; }
.eve-score-details { font-size:10px; margin:2px 0 0; }
.eve-score-details summary { color:#526779; cursor:pointer; }
.eve-score-details p { margin:5px 0; overflow-wrap:anywhere; }
.eve-evidence-id { color:#657684; }
@media(max-width:600px) { .eve-filters { gap:8px; } }
@media print { .eve-filters,.eve-attention-filter,.eve-locus { display:none!important; } .eve-table-scroll { overflow:visible; border:0; } .eve-table-scroll table.eve-matrix { min-width:0; width:100%; table-layout:fixed; } .eve-matrix .eve-protein-column { width:15%; } .eve-matrix .eve-layer-column { width:8.5%; } .eve-matrix th,.eve-matrix td { padding:4px 2px; } .eve-chip { font-size:6.5px; padding:2px; } .eve-matrix tbody th { position:static; min-width:0; font-size:7px; overflow-wrap:anywhere; } .eve-matrix thead th { font-size:7px; } .eve-attention-table { min-width:0; } }
"""

JS = r"""
(() => {
  document.querySelectorAll('.eve-matrix-view').forEach(view => {
    const input=view.querySelector('[data-eve-search-input]'), select=view.querySelector('[data-eve-queue-input]');
    const rows=Array.from(view.querySelectorAll('[data-eve-row]'));
    function filter() {
      const q=(input.value||'').toLowerCase().trim(), queue=select.value;
      let visible=0;
      rows.forEach(row => { row.hidden=!(row.dataset.eveSearch.includes(q) && (!queue || row.dataset.eveQueue===queue)); if(!row.hidden) visible++; });
      view.querySelector('[data-eve-count]').textContent=`${visible} / ${rows.length} proteins`;
      view.querySelector('[data-eve-empty]').hidden=visible!==0;
    }
    input.addEventListener('input',filter); select.addEventListener('change',filter);
  });
  document.querySelectorAll('.eve-attention-view').forEach(view => {
    const input=view.querySelector('[data-eve-attention-input]');
    const rows=Array.from(view.querySelectorAll('[data-eve-attention-row]'));
    input.addEventListener('input',()=>{
      const q=input.value.toLowerCase().trim(); let visible=0;
      rows.forEach(row=>{row.hidden=!row.dataset.eveAttentionSearch.includes(q);if(!row.hidden)visible++;});
      view.querySelector('[data-eve-attention-empty]').hidden=visible!==0;
    });
  });
  document.addEventListener('click',event=>{
    const candidate=event.target.closest('[data-eve-candidate]');
    if(candidate){
      event.preventDefault();
      document.dispatchEvent(new CustomEvent('basis:open-candidate',{detail:{anchor:candidate.dataset.eveCandidate}}));
    }
    const locus=event.target.closest('[data-eve-protein]');
    if(locus) document.dispatchEvent(new CustomEvent('basis:focus-locus',{detail:{sample_id:locus.dataset.eveSample,protein_id:locus.dataset.eveProtein}}));
  });
})();
"""

EVIDENCE_VIEWS_CSS = CSS
EVIDENCE_VIEWS_JS = JS
