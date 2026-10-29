"""Compact, self-contained presentation of recorded BaSIS evidence.

Raw records are exported alongside the dashboard. Rendering never runs an
analysis or upgrades the interpretation of a source finding.
"""
from __future__ import annotations
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .report_assets import CSS, JS
from .deep_reporting import effective_execution
from .report_links import candidate_anchor, candidate_section_anchor
from .report_navigation import (SECTIONS, candidate_entry, render_navigator,
                                render_comparators, render_candidate_files)
from .report_citation import AUTHOR, CITATION_NOTE, citation_text
from .report_motif_views import candidate_motif_details, motif_search_terms, render_motif_overview, CSS as MOTIF_CSS
from .report_placement import candidate_placement_details, placement_search_terms, render_placement_overview, CSS as PLACEMENT_CSS
from .report_context import finding_context
from .report_screening import render_screening_findings, PURPOSES, CSS as SCREENING_CSS
from .report_overview import tool_results, render_tool_results, CSS as OVERVIEW_CSS
from .report_sample_scope import render_sample_scope, record_requested, missing_requested_count
from .report_deep_workflow import (candidate_workflow_details, render_workflow_overview,
                                   workflow_search_terms)

NAMES = {'amrfinder': 'AMRFinderPlus', 'rgi': 'CARD / RGI', 'vfdb': 'VFDB',
         'antismash': 'antiSMASH', 'genomad': 'geNomad', 'mobsuite': 'MOB-suite',
         'integronfinder': 'IntegronFinder', 'pathogenfinder2': 'PathogenFinder2',
         'deepsafe': 'DeepSafe', 'bacmet': 'BacMet', 'isescan': 'ISEScan',
         'icescreen': 'ICEscreen', 'minced': 'MinCED',
         'crisprcasfinder': 'CRISPRCasFinder', 'mlst': 'MLST'}
OPTIONAL_SCOPE = {
    'bacmet': ('BacMet', 'Biocide and metal resistance homologs',
               'Native adapter available; a prepared BacMet database and validated configuration are required.'),
    'isescan': ('ISEScan', 'Insertion sequences and their transposases',
                'Native adapter available; installation and a native validation run are required.'),
    'icescreen': ('ICEscreen', 'Integrative conjugative and mobilizable elements in Bacillota',
                  'Native adapter available; requires annotated GenBank, explicit Bacillota scope and native validation.'),
    'crisprcasfinder': ('CRISPRCasFinder', 'CRISPR arrays and associated Cas proteins',
                        'Native adapter available; uses CRISPRCasFinder and its pinned CASFinder models.'),
    'minced': ('MinCED', 'CRISPR arrays',
               'Native adapter available; detects repeat-spacer arrays, without Cas-protein typing. CRISPRCasFinder is an alternative workflow.'),
    'mlst': ('MLST', 'Sequence typing using applicable allele schemes',
             'Native adapter available; no applicable scheme is reported separately from a completed type assignment.')}
QUEUES = {'native_screen': 'Core screening', 'ml_attention': 'ML attention', 'exploratory': 'Exploratory'}
LAYERS = {'integrity': 'Sequence integrity', 'context': 'Genomic context', 'domains': 'Pfam domains',
          'motifs': 'Custom motifs', 'prosite_patterns': 'PROSITE patterns',
          'prosite_profiles': 'PROSITE profiles', 'phylogeny': 'Phylogenetic review',
          'family_placement': 'Reference-panel placement'}


def _helpers():
    from . import report
    return report


def esc(x):
    return _helpers()._esc(x)


def human(x):
    return str(x or 'not recorded').replace('_', ' ')


def num(x):
    return f'{x:,}' if isinstance(x, (int, float)) else '—'


def badge(value, tone=None):
    tone = tone or {'completed': 'ok', 'failed': 'bad', 'unknown': 'warn', 'blocked': 'warn', 'not_started': 'warn',
                    'provisional': 'warn', 'not_assessed': 'neutral', 'not_detected': 'neutral'}.get(str(value), 'info')
    return '<span class="badge ' + tone + '">' + esc(human(value)) + '</span>'


def execution_badge(recorded, *parents):
    """Show completion only when its enclosing executions completed."""
    effective = effective_execution(recorded, *parents)
    if effective == 'provisional':
        detail = 'Recorded execution: ' + human(recorded) + '; enclosing execution: ' + ', '.join(human(parent) for parent in parents)
        return '<span title="' + esc(detail) + '">' + badge(effective) + '</span>'
    return badge(effective)


def table(heads, rows, ident=''):
    return '<div class="table-wrap"><table' + (' id="' + ident + '"' if ident else '') + '><thead><tr>' + ''.join('<th scope="col">' + esc(x) + '</th>' for x in heads) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + str(v) + '</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def kv(items):
    return '<dl class="kv">' + ''.join('<div><dt>' + esc(k) + '</dt><dd>' + esc(v) + '</dd></div>' for k, v in items.items()) + '</dl>'


def section(ident, number, title, subtitle, content):
    return f'<section class="section" id="{ident}"><div class="section-head"><span class="section-number">{number}</span><div><h2>{esc(title)}</h2><p class="muted">{esc(subtitle)}</p></div></div>{content}</section>'


def stat(label, value, note):
    return '<div class="stat"><span class="stat-label">' + esc(label) + '</span><strong class="stat-value">' + esc(value) + '</strong><span class="stat-note">' + esc(note) + '</span></div>'


def link(href, title, cls='button'):
    return '<a class="' + cls + '" href="' + esc(href) + '">' + esc(title) + '</a>'


def export_json(outdir, kind, key, data):
    relative = 'report-data/' + kind + '-' + hashlib.sha256(str(key).encode()).hexdigest()[:16] + '.json'
    dest = outdir / relative
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(_helpers()._jsonable(data), ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    return relative


def _omitted_row(record):
    r = _helpers()
    module = r._module(record)
    title, purpose, support = OPTIONAL_SCOPE.get(module, (module, 'See module configuration', 'See module documentation.'))
    config = record.get('effective_config') or {}
    reason = record.get('reason')
    if reason in (None, '', 'module_disabled') and config.get('disabled_reason'):
        reason = config['disabled_reason']
    disabled = reason == 'module_disabled' or config.get('enabled') is False
    explanations = {
        'alternative_not_selected_minced': 'Alternative workflow not selected; MinCED was selected for CRISPR-array screening.',
        'no_applicable_scheme_identified': 'No applicable MLST scheme identified for this study; sequence typing was not assessed.'}
    why = explanations.get(reason, 'Disabled in this run’s configuration' if disabled else human(reason or 'reason_not_recorded'))
    return (esc(r._sample_id(record)), '<strong>' + esc(title) + '</strong>',
            esc(purpose), badge(r._execution(record)), esc(why), esc(support))


def _date(value):
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc).strftime('%d %b %Y · %H:%M UTC')
    except (ValueError, TypeError):
        return str(value or 'Time not recorded')


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _features(c):
    a = c.get('analyses') or {}
    rows = []
    domains = a.get('domains') or {}
    if domains.get('status') == 'completed':
        for h in domains.get('hits', []):
            if h.get('passes_ga') is True:
                rows.append(('Pfam', h.get('domain_name'), h.get('query_start'), h.get('query_end')))
    for layer, label in [('prosite_patterns', 'Pattern'), ('prosite_profiles', 'Profile')]:
        result = a.get(layer) or {}
        if result.get('status') != 'completed':
            continue
        for h in result.get('hits', []):
            rows.append((label, str(h.get('accession', '')) + ' ' + str(h.get('name', h.get('profile_name', ''))), h.get('start'), h.get('end')))
    motifs = a.get('motifs') or {}
    if motifs.get('status') == 'completed':
        for rule in motifs.get('rules', []):
            for h in rule.get('matches', []):
                rows.append(('Motif', rule.get('rule_id'), h.get('start'), h.get('end')))
    return rows


def feature_map(c, rows):
    length = (c.get('query') or {}).get('length')
    if not isinstance(length, int) or length < 1 or not rows:
        return '<p class="empty">No accepted feature intervals recorded for this candidate.</p>'
    usable = [r for r in rows if isinstance(r[2], int) and isinstance(r[3], int) and 1 <= r[2] <= r[3] <= length]
    colors = {'Pfam': '#147d7b', 'Pattern': '#b17618', 'Profile': '#426bc1', 'Motif': '#8960ad'}
    shown = usable[:18]
    height = 34 + len(shown) * 27
    s = f'<div class="feature-scroll"><svg class="feature-map" viewBox="0 0 850 {height}" role="img" aria-label="Accepted feature intervals on a {length} amino acid protein">'
    s += '<text x="224" y="16">1 aa</text><text x="812" y="16" text-anchor="end">' + str(length) + ' aa</text>'
    for i, (source, label, start, end) in enumerate(shown):
        y = 34 + i * 27
        x = 225 + (start - 1) / length * 580
        width = max(2, (end - start + 1) / length * 580)
        full = f'{source}: {label} · {start}–{end}'
        short = (str(label)[:23] + '…') if len(str(label)) > 24 else str(label)
        s += f'<text x="0" y="{y + 3}">{esc(source + " · " + short)}</text><line x1="225" x2="805" y1="{y}" y2="{y}" stroke="#e1e8ee" stroke-width="6"/>'
        s += f'<rect x="{x:.2f}" y="{y - 7}" width="{width:.2f}" height="14" rx="3" fill="{colors[source]}" tabindex="0" role="button" aria-label="{esc(full)}" data-feature-detail="{esc(full)}"><title>{esc(full)}</title></rect>'
    s += '</svg></div><div class="feature-actions"><button class="button" data-export-feature="svg">Export protein map · SVG</button><p class="feature-inspector small" role="status">Select a colored interval to inspect its annotation and coordinates.</p></div><p class="small muted">1-based inclusive query coordinates. Overlapping annotations are related observations, not independent evidence votes.</p>'
    if len(usable) > 18:
        s += '<p class="small muted">First 18 intervals illustrated; all accepted intervals appear below.</p>'
    return s


def _candidate(c, sid, outdir, deep_record=None, placement_view=''):
    r = _helpers()
    ident = c.get('protein_id') or c.get('candidate_id') or 'Unidentified candidate'
    queue = c.get('selection_queue', 'exploratory')
    assessment = c.get('assessment') or {}
    interpretation = assessment.get('interpretation', c.get('interpretation', 'unresolved'))
    analyses = c.get('analyses') or {}
    summary = c.get('consolidated_evidence') or {}
    raw = export_json(outdir, 'candidate', [sid, c.get('candidate_id'), ident], c)
    sources = c.get('source_evidence') or []
    native_labels = sorted({str(f.get('gene') or f.get('family')) for f in sources if isinstance(f, dict) and not r._exploratory_finding(f) and (f.get('gene') or f.get('family'))})
    exploratory_labels = sorted({str(f.get('gene') or f.get('family')) for f in sources if isinstance(f, dict) and r._exploratory_finding(f) and f.get('category') != 'ml_attention' and (f.get('gene') or f.get('family'))})
    parent_execution = r._execution(deep_record) if deep_record else 'not_assessed'
    completed_evidence = parent_execution == 'completed' and c.get('execution') == 'completed'
    features = _features(c) if completed_evidence else []
    entry = candidate_entry(sid, c, deep_record or {}, outdir)
    search, title_note = entry['search_text'] + ' ' + workflow_search_terms(c), entry['label']
    def section_open(name):
        return '<div class="candidate-section" id="' + candidate_section_anchor(sid, ident, name) + '" tabindex="-1"><h4>' + esc(SECTIONS[name]) + '</h4>'
    result = '<details class="candidate" id="' + candidate_anchor(sid, ident) + '" data-protein="' + esc(ident) + '" data-search="' + esc(search) + '" data-sample="' + esc(sid) + '" data-queue="' + esc(queue) + '" data-interpretation="' + esc(interpretation) + '"><summary><div class="candidate-title"><strong>' + esc(ident) + '</strong><span>' + esc(title_note) + '</span></div><div class="candidate-meta">' + badge(QUEUES.get(queue, human(queue))) + execution_badge(c.get('execution', 'unknown'), parent_execution) + '</div></summary><div class="candidate-body">'
    result += '<nav class="candidate-section-nav" aria-label="Candidate evidence sections">' + ''.join(link('#' + entry['sections'][k], title, 'button') for k, title in SECTIONS.items()) + '</nav>' + section_open('overview')
    if c.get('protein_id') and c.get('contig') and c.get('start'):
        result += '<button class="button" data-locus-sample="' + esc(sid) + '" data-locus-protein="' + esc(c['protein_id']) + '">Show genomic neighborhood ↗</button>'
    result += '<div class="callout"><strong>' + esc(human(interpretation).capitalize()) + '</strong><p>' + esc(human(assessment.get('followup', 'review existing evidence'))) + ' · ' + esc(human(assessment.get('reason', c.get('reason', 'not recorded')))) + '</p></div>'
    refs = c.get('references') or []
    unique = {v['sequence_sha256'] for v in refs if isinstance(v, dict) and v.get('sequence_sha256')}
    result += '<div class="mini-stats">' + stat('Protein length', num((c.get('query') or {}).get('length')), 'amino acids') + stat('Comparator records', str(len(refs)), str(len(unique)) + ' unique sequences with hashes') + stat('Recorded features', str(len(features)), 'accepted domain / motif intervals') + '</div>'
    result += '<h4>Analysis coverage</h4>' + '<div class="layer-grid">' + ''.join('<div><span>' + esc(title) + '</span>' + execution_badge((analyses.get(k) or {}).get('status', 'not_assessed'), parent_execution, c.get('execution')) + '</div>' for k, title in LAYERS.items()) + '</div>'
    result += candidate_workflow_details(c, parent_execution)
    if not completed_evidence:
        result += '<p class="callout">Candidate or parent execution is not completed. Retained evidence is provisional; accepted feature maps and tree views are withheld.</p>'
    result += '</div>' + section_open('domains')
    result += '<h4>Protein feature map</h4>' + feature_map(c, features)
    if features:
        result += '<details class="evidence-list"><summary>Feature coordinates and source labels (' + str(len(features)) + ')</summary>' + table(['Source', 'Recorded annotation', 'Start', 'End'], [(esc(a), esc(b), esc(s), esc(e)) for a, b, s, e in features]) + '</details>'
    result += '</div>' + section_open('motifs')
    result += candidate_motif_details(c, parent_execution)
    sites = (summary.get('residue_observations') or []) if completed_evidence else []
    if sites:
        result += '<h4>Mapped residue conditions</h4>' + table(['Position', 'Residue', 'Recorded condition'], [(esc(v.get('query_position')), esc(v.get('observed_residue')), '<br>'.join(esc(str(t.get('rule')) + ': ' + human(t.get('status')) + ' · expected ' + str(t.get('expected_residues'))) for t in v.get('conditions', []))) for v in sites])
    result += '<p class="small muted">Full ProRule logic is not evaluated. Unsupported conditions: ' + str(len(summary.get('unsupported_conditions') or [])) + '; unmapped conditions: ' + str(len(summary.get('unmapped_residue_conditions') or [])) + '.</p>'
    missing = assessment.get('missing_comparison_groups') or []
    if missing:
        result += '<p class="callout"><strong>Comparator gaps</strong><br>' + esc(', '.join(missing)) + '</p>'
    tree = analyses.get('phylogeny') or {}
    tree_summary = summary.get('tree_summary') or {}
    runs = tree_summary.get('runs') or tree.get('runs') or []
    result += '</div>' + section_open('trees')
    result += '<h4>Phylogenetic follow-up</h4><p>' + execution_badge(tree.get('status', 'not_assessed'), parent_execution, c.get('execution')) + ' ' + esc(human(tree.get('reason', 'not recorded'))) + '</p>'
    if runs and completed_evidence:
        result += table(['Run', 'Model', 'SH-aLRT', 'UFBoot', 'Reference annotations on query side'], [(esc(v.get('name')), esc(v.get('selected_model', v.get('model'))), esc(v.get('sh_alrt')), esc(v.get('ufboot')), '<br>'.join(esc(str(x.get('accession')) + ' · ' + str(x.get('review_status'))) for x in v.get('query_side_references', [])) or 'See candidate evidence') for v in runs])
        result += '<p class="small muted">Support describes a tree split, not a functional classification. Reference annotations retain their recorded review status.</p>'
    if tree.get('status') == 'completed' and completed_evidence:
        from .report_tree_views import render_tree_alignment
        result += render_tree_alignment(c, deep_record or {}, outdir)
    elif not completed_evidence:
        result += '<p class="empty">Tree display withheld because candidate or parent execution is not completed. Saved evidence remains available under Files.</p>'
    else:
        result += '<p class="empty">No completed tree view recorded. Review the recorded status and reason above.</p>'
    result += '</div>' + section_open('placement')
    result += candidate_placement_details(c, parent_execution)
    result += placement_view
    result += '</div>' + section_open('references')
    result += render_comparators(c, outdir, [sid, c.get('candidate_id'), ident])
    if exploratory_labels:
        result += '<details class="evidence-list"><summary>Exploratory source labels · not established functions</summary><p>' + esc('; '.join(exploratory_labels)) + '</p></details>'
    source_caveats = sorted({note for finding in sources if isinstance(finding, dict)
                             and not r._exploratory_finding(finding)
                             for note in finding_context(finding)['caveats']})
    if source_caveats:
        result += '<div class="callout"><strong>Source annotation limitations</strong><ul>' + ''.join('<li>' + esc(note) + '</li>' for note in source_caveats) + '</ul></div>'
    warnings = assessment.get('warnings') or c.get('notes') or []
    if warnings:
        result += '<details class="evidence-list"><summary>Review notes (' + str(len(warnings)) + ')</summary><ul>' + ''.join('<li>' + esc(x) + '</li>' for x in warnings) + '</ul></details>'
    result += '<p class="small muted">' + esc(sid) + ' · ' + esc(c.get('contig')) + ':' + esc(c.get('start')) + '–' + esc(c.get('end')) + ' · ' + esc(human(queue)) + '</p>'
    result += '</div>' + section_open('files')
    result += render_candidate_files(c, outdir, [sid, c.get('candidate_id'), ident], raw)
    result += '</div></div></details>'
    return result


def _extend(record, outdir, raw, *, deep_enabled=True):
    r = _helpers()
    metrics = record.get('metrics') or {}
    status = r._execution(record)
    prediction = metrics.get('prediction_mean')
    sd = metrics.get('prediction_std')
    valid = status == 'completed'
    title = '<div class="panel-head"><div><p class="eyebrow">' + esc(r._sample_id(record)) + ' / EXTEND</p><h3>PathogenFinder2</h3></div>' + badge(status) + '</div>'
    body = '<article class="panel model-panel">' + title
    if valid and _number(prediction):
        body += '<div class="model-result"><div><span class="stat-label">Native model prediction</span><h4 class="model-label">' + esc(metrics.get('native_phenotype', 'Label not recorded')) + '</h4><p class="muted">Model output; no organism-level safety verdict.</p></div><div class="model-score"><strong>' + f'{prediction:.5f}' + '</strong><span>ensemble mean · SD ' + (f'{sd:.5f}' if _number(sd) else 'not recorded') + '</span></div></div>'
    else:
        body += '<p class="callout">No completed model prediction is available. Any retained scores remain provisional.</p>'
    nominations = stat('DeepSafe nominations', num(metrics.get('attention_selected_count')) if valid else '—', 'attention-ranked candidates') if deep_enabled else ''
    body += '<div class="mini-stats">' + stat('Proteins assessed', num(metrics.get('input_protein_count')) if valid else '—', 'full supplied proteome') + stat('Attention coverage', num(metrics.get('attention_protein_count')) if valid else '—', 'proteins with recorded scores') + nominations + '</div>'
    scores = metrics.get('ensemble_member_scores') or []
    if valid and scores:
        body += '<h4>Individual model scores</h4><div class="model-bars">'
        for i, value in enumerate(scores):
            if not _number(value):
                continue
            width = max(0, min(100, value * 100))
            body += '<div class="model-bar"><span>Model ' + str(i + 1) + '</span><div class="bar-track"><span class="bar-segment" style="width:' + f'{width:.3f}' + '%"></span></div><b>' + f'{value:.5f}' + '</b></div>'
        body += '</div><p class="small muted">Bars use the native 0–1 model scale. SD reflects variation between ensemble members, not a confidence interval or exposure-risk estimate.</p>'
    native_commands = [c for c in (record.get('provenance') or {}).get('commands', []) if isinstance(c, dict) and 'predict' in (c.get('argv') or [])]
    last = native_commands[-1] if native_commands else {}
    body += '<details class="evidence-list"><summary>Execution details</summary>' + kv({'Execution': status, 'Mode': (record.get('effective_config') or {}).get('mode'), 'Cache reused': record.get('cache_hit', 'not recorded'), 'Embeddings reused': metrics.get('embeddings_reused', 'not recorded'), 'Started (UTC)': record.get('started_at'), 'Finished (UTC)': record.get('finished_at'), 'Prediction command exit code': last.get('returncode', 'not recorded'), 'Prediction command seconds': last.get('elapsed_seconds', 'not recorded'), 'Recorded BaSIS interpretation': r._interpretation(record)}) + '</details>'
    body += '<p class="small muted">Attention prioritizes protein review. It does not establish virulence, resistance, function or causality.</p>' + link(raw, 'Model evidence · JSON') + '</article>'
    return body


def render_dashboard(run, samples, records, deep_records, findings, recommendations, outdir):
    from .report_scope import deep_requested
    deep_enabled = deep_requested(run, deep_records)
    view = run.get('report_view', 'combined')
    if view not in {'screening', 'deep', 'combined'}:
        raise ValueError('Unknown report_view: ' + str(view))
    show_screening = view != 'deep'
    from .report_genome import build_genome_data
    from .report_explorer import render_genome_explorer
    from .report_explorer_assets import CSS as GENOME_CSS, JS as GENOME_JS
    from .report_evidence_views import render_evidence_matrix, render_attention_explorer, CSS as EVIDENCE_CSS, JS as EVIDENCE_JS
    from .report_tree_views import CSS as TREE_CSS, JS as TREE_JS
    from .report_placement_tree import render_placement_tree, exported_tree_href, CSS as PLACEMENT_TREE_CSS, JS as PLACEMENT_TREE_JS
    r = _helpers()
    outdir = Path(outdir)
    combined = list(records) + list(deep_records)
    raw_paths = {}
    for i, record in enumerate(combined):
        raw_paths[id(record)] = export_json(outdir, 'module', [r._sample_id(record), r._module(record), i], record)
    run_path = export_json(outdir, 'run', run.get('run_id'), {'run': run, 'samples': samples})
    raw_recs = export_json(outdir, 'recommendations', run.get('run_id'), recommendations)
    candidates = [(r._sample_id(rec), c) for rec in deep_records for c in rec.get('candidates', []) or []
                  if isinstance(c, dict) and c.get('sample_id') in (None, '', r._sample_id(rec))]
    candidate_records = {id(c): rec for rec in deep_records for c in rec.get('candidates', []) or []
                         if isinstance(c, dict) and c.get('sample_id') in (None, '', r._sample_id(rec))}
    cross_sample_candidates = sum(isinstance(c, dict) and c.get('sample_id') not in (None, '', r._sample_id(rec))
                                 for rec in deep_records for c in rec.get('candidates', []) or [])
    genome_data = build_genome_data(run, samples, records, deep_records)
    export_json(outdir, 'genome', run.get('run_id'), genome_data)
    # Missing selection metadata stays visible for legacy inputs; explicitly
    # deferred candidates are exported, not repeated thousands of times in HTML.
    selected = [(sid, c) for sid, c in candidates if c.get('selected', 'selected' not in c)]
    deferred = [(sid, c) for sid, c in candidates if c.get('selected') is False]
    deferred_rows = [dict(sample_id=sid, candidate_id=c.get('candidate_id'), protein_id=c.get('protein_id'),
                          selection_queue=c.get('selection_queue'), execution=c.get('execution'), reason=c.get('reason'),
                          selected=False, review_status='Requires attention',
                          followup_status=('Follow-up not performed' if c.get('execution') in {'deferred', 'not_started'}
                                           else 'Recorded follow-up status: ' + human(c.get('execution'))))
                     for sid, c in deferred]
    if deep_enabled:
        r._write_tsv(outdir / 'deferred-candidates.tsv', ['sample_id','candidate_id','protein_id','selection_queue','execution','reason',
                                                       'selected','review_status','followup_status'], deferred_rows)
    requested = [v for v in combined if record_requested(v, run)]
    requested_count = len(requested) + missing_requested_count(run, combined)
    omitted = [v for v in combined if v not in requested]
    completed = sum(r._execution(v) == 'completed' for v in requested)
    failed = sum(r._execution(v) in {'failed','unknown'} for v in requested)
    done = sum(c.get('execution') == 'completed'
               and r._execution(candidate_records.get(id(c), {})) == 'completed'
               for _, c in selected)
    selection_unavailable = not selected and any(r._execution(rec) in {'failed', 'blocked', 'not_started'} for rec in deep_records)
    sids = sorted({r._sample_id(v) for v in combined} | {str(s.get('sample_id','unknown')) for s in samples})
    q = Counter(c.get('selection_queue','exploratory') for _, c in selected)
    tool_rows = tool_results(run, samples, records, findings) if show_screening else []
    models = [v for v in records if r._module(v) in {'pathogenfinder2','pf2'}
              and (record_requested(v, run) or v.get('findings') or v.get('metrics'))]
    nav_sections = [('overview','01','Overview')]
    if show_screening:
        nav_sections += [('screens','02','Screening results')]
        if models:
            nav_sections += [('extend','03','Model predictions')]
    nav_sections += [('inputs','04' if show_screening else '02','Inputs & scope'),
                     ('genome','05' if show_screening else '03','Genome explorer')]
    if deep_enabled:
        nav_sections += [('evidence-matrix','06' if show_screening else '04','Evidence matrix'),
                         ('deep','07','DeepSafe')]
    nav_sections += [('recommendations','08','Review priorities'),('provenance','09','Provenance')]
    branding = '<svg width="34" height="34" viewBox="0 0 36 36" aria-hidden="true"><rect width="36" height="36" rx="10" fill="#1ba9a0"/><path d="M10 10h9a5 5 0 0 1 0 10h-9m0-5h11a5 5 0 0 1 0 10H10V10" fill="none" stroke="white" stroke-width="2.4"/></svg>'
    nav = ''.join('<a class="nav-link" href="#' + ident + '"><span>' + number + '</span>' + title + '</a>' for ident, number, title in nav_sections)
    navigator = link('#deep-navigator', 'Evidence navigator') if deep_enabled else link('#screening-findings', 'Read screening findings')
    report_title = {'screening': 'Screening report', 'deep': 'DeepSafe follow-up report'}.get(view, 'Genomic evidence report')
    hero_eyebrow = {'screening': 'CORE SCREENING · MODEL INSIGHT', 'deep': 'DEEPSAFE · FOCUSED FOLLOW-UP'}.get(view, 'CORE SCREENING · DEEP EVIDENCE · MODEL INSIGHT')
    if view == 'screening' and not models:
        hero_eyebrow = 'SCREENING RESULTS'
    hero_title = esc(report_title) if view != 'combined' else 'Genomic evidence,<br>ready for review.'
    hero_intro = {'screening': 'Read what each tool reported, find the gene or feature,<br class="desktop-only"> and see where the assessment remains incomplete.', 'deep': 'Inspect saved follow-up analyses and source context,<br class="desktop-only"> and review what remains unresolved.'}.get(view, 'Trace the screening calls, inspect focused analyses,<br class="desktop-only"> and see exactly what remains unresolved.')
    start = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>BaSIS · ' + esc(report_title) + '</title><style>' + CSS + '</style></head><body><a class="skip-link" href="#overview">Skip to report</a><div class="app"><aside class="sidebar"><a class="brand" href="#overview">' + branding + '<span>BaSIS<small>BACTOMICS ENVIRONMENT</small></span></a><div class="sidebar-caption">' + esc(report_title.upper()) + '</div><nav aria-label="Report sections">' + nav + '</nav><div class="sidebar-bottom"><span class="status-dot"></span> Offline report<small>Version ' + esc(run.get('version')) + '<br>Research screening evidence</small></div></aside><main class="main"><div class="topbar"><span>Bactomics <span class="muted">/</span> BaSIS <span class="muted">/ Genomic assessment</span></span><div class="topbar-actions">' + navigator + '<a class="button" href="#citation">Cite BaSIS</a><button class="button" data-action="print">Print / Save PDF</button></div></div>'
    all_done = requested_count > 0 and completed == requested_count
    headline = ('Requested modules completed' if not selected else 'Selected analyses completed') if all_done and done == len(selected) and not run.get('engine_error') else 'Execution review required'
    attention = any(row['tone'] in {'warn', 'bad'} for row in tool_rows)
    scope_limited = run.get('completeness') in {'incomplete', 'failed'}
    if show_screening and all_done and done == len(selected) and not run.get('engine_error'):
        headline = 'Tools finished · review assessment limits' if attention or scope_limited else 'All requested tools finished'
    overview_tone = 'ok' if all_done and done == len(selected) and not run.get('engine_error') and not (show_screening and (attention or scope_limited)) else 'warn'
    hero = '<header class="hero"><div><p class="eyebrow">' + esc(hero_eyebrow) + '</p><h1>' + hero_title + '</h1><p>' + hero_intro + '</p><div class="hero-meta">' + badge(headline, overview_tone) + '<span>' + esc(_date(run.get('finished_at'))) + '</span></div></div><div class="hero-art" aria-hidden="true"><svg viewBox="0 0 250 210"><g fill="none" stroke="#60d4c4" stroke-width="1.5" opacity=".65"><circle cx="125" cy="105" r="82"/><circle cx="125" cy="105" r="65" stroke-dasharray="5 9"/><ellipse cx="125" cy="105" rx="37" ry="82" transform="rotate(35 125 105)"/><path d="M49 75L172 40 192 130 91 174 49 75 192 130M91 174L172 40"/></g><g fill="#abf0df"><circle cx="49" cy="75" r="6"/><circle cx="172" cy="40" r="8"/><circle cx="192" cy="130" r="5"/><circle cx="91" cy="174" r="7"/></g><text x="125" y="112" text-anchor="middle" fill="white" font-size="29" font-family="sans-serif">BaSIS</text></svg></div></header>'
    candidate_stats = (stat('Selected candidates', f'{done} / {len(selected)}' if selected else 'Unavailable' if selection_unavailable else 'None selected', 'completed DeepSafe analyses' if selected else 'candidate selection unavailable — inspect execution issues' if selection_unavailable else 'no selected-candidate analyses requested') + stat('Requires attention', num(len(deferred)), 'not selected for this follow-up run · reasons retained')) if deep_enabled else ''
    overview = hero + '<div class="stats">' + stat('Tools finished', f'{completed} / {requested_count}', 'completed execution; recorded result and assessment scope are shown separately') + candidate_stats + stat('Execution failures', num(failed), 'failed or unknown requested module records') + '</div>'
    deferred_note = num(len(deferred)) + ' candidates remain deferred. ' if deep_enabled else ''
    overview += '<div class="callout"><strong>What does the completion count mean?</strong><p>The tools-finished count describes execution. An inconclusive or incomplete assessment can remain after a tool finishes. Recorded overall scope: ' + esc(human(run.get('completeness'))) + '. ' + deferred_note + 'Completed analyses do not establish protein function or organism safety.</p></div>'
    failed_resources = [item for item in run.get('resource_preparation', [])
                        if item.get('status') in {'failed', 'blocked', 'not_started'}]
    failed_records = [rec for rec in requested if r._execution(rec) in {'failed', 'blocked', 'not_started', 'unknown'}]
    if failed_resources or failed_records or run.get('engine_error'):
        issues = '<article class="panel" id="execution-issues"><h3>Execution issues</h3><p>Failed workers, blocked dependencies and jobs not started are listed separately below. Independent completed results retain their recorded status.</p>'
        if run.get('engine_error'):
            issues += '<p class="small">' + esc(run['engine_error']) + '</p>'
        if run.get('scheduler_log'):
            issues += r._artifact_list([{'path': run['scheduler_log'], 'label': 'Snakemake scheduler log'}], outdir)
        if failed_resources:
            issues += '<h4>Resource preparation</h4>' + table(
                ['Resource', 'Status', 'Recorded cause', 'Blocked by', 'Diagnostics'],
                [(esc(item.get('resource_id')), badge(item.get('status')),
                  esc(item.get('error') or 'Resource preparation did not complete; inspect the scheduler log.'),
                  esc(', '.join(item.get('blocked_by') or []) or '—'),
                  r._artifact_list([{'path': item[key], 'label': label} for key, label in
                                    [('command_log', 'Command log'), ('receipt', 'Preparation receipt')]
                                    if item.get(key)], outdir)) for item in failed_resources])
        if failed_records:
            issues += '<details class="evidence-list"><summary>Affected module records (' + str(len(failed_records)) + ')</summary>' + table(
                ['Sample', 'Module', 'Execution status', 'Recorded cause', 'Evidence'],
                [(esc(r._sample_id(rec)), esc(NAMES.get(r._module(rec), r._module(rec))),
                  badge(r._execution(rec)),
                  esc(rec.get('error') or rec.get('reason') or 'No completed result recorded.'),
                  link(raw_paths[id(rec)], 'Evidence', 'text-link')) for rec in failed_records]) + '</details>'
        overview += issues + '</article>'
    replays = run.get('report_replays') or []
    if replays:
        replay = replays[-1]
        overview += '<div class="callout"><strong>Saved-evidence reporting refresh</strong><p>This report was rendered from saved evidence. Native records, candidate selection, analyses, interpretations and original execution status are unchanged. No native analysis or GPU inference ran during this refresh.</p><p>Source-file integrity is recorded; native files, database contents and scientific conclusions were not independently revalidated.</p>' + kv({'Refreshed (UTC)': replay.get('report_generated_at_utc'), 'Renderer version': replay.get('renderer_version'), 'Source run': replay.get('source_run_id')}) + link('replay-audit.json', 'Reporting refresh audit · JSON') + '</div>'
    placement_review = run.get('placement_review') or {}
    if placement_review:
        overview += '<div class="callout" id="placement-review-notice"><strong>New placement-only review of saved evidence</strong><p>The original assessment dates and prior module results remain historical. This review adds family-placement evidence to the verified saved DeepSafe candidates.</p>'
        if placement_review.get('core_and_extend_rerun') is False:
            overview += '<p>Core screening and Extend / GPU inference were not rerun.</p>'
        if placement_review.get('other_deep_analyses_rerun') is False:
            overview += '<p>Other DeepSafe analyses were not rerun; their recorded interpretations are preserved.</p>'
        overview += kv({'Original assessment finished (UTC)': run.get('finished_at'),
                        'Placement review started (UTC)': placement_review.get('started_at'),
                        'Placement review finished (UTC)': placement_review.get('finished_at'),
                        'Review scope': placement_review.get('scope'),
                        'Source evidence': placement_review.get('source_evidence'),
                        'Source evidence SHA256': placement_review.get('source_sha256')}) + link(run_path, 'Placement review audit · JSON') + '</div>'
    placement_refresh = run.get('placement_report_refresh') or {}
    if placement_refresh:
        overview += '<div class="callout" id="placement-report-refresh"><strong>Placement tree reporting refresh</strong><p>Available verified tree artifacts are linked in DeepSafe; unavailable artifacts are reported explicitly. This reporting refresh preserves the recorded analyses and interpretations.</p><p>No native analyses or GPU inference ran during this refresh.</p>' + kv({'Report refreshed (UTC)': placement_refresh.get('generated_at_utc'), 'Source evidence': placement_refresh.get('source_evidence'), 'Source evidence SHA256': placement_refresh.get('source_sha256')}) + link(run_path, 'Report refresh audit · JSON') + '</div>'
        if placement_refresh.get('placement_diagnostics') and (outdir / 'placement-diagnostics.json').is_file():
            overview += '<div class="callout"><strong>Placement boundary diagnostics</strong><p>Tree-location concentration and functional-group evidence are reported separately. Shared boundaries remain unresolved; original scores and decisions are preserved.</p>' + link('placement-diagnostics.json', 'Verified placement diagnostics · JSON') + '</div>'
    reconciliation = run.get('report_reconciliation') or {}
    if reconciliation:
        overview += '<div class="callout"><strong>Reconciled saved-evidence report</strong><p>The failed ICEscreen record was replaced by a verified successful retry: ' + esc(reconciliation.get('predicted_element_count')) + ' predicted elements; ' + esc(reconciliation.get('unassigned_integrases')) + ' unassigned integrase signatures. Integrase signatures are not predicted ICE/IME elements.</p><p>No native analyses or GPU inference ran during reconciliation. DeepSafe analyses, interpretations and deferred scope were preserved.</p>' + kv({'Original assessment finished (UTC)': reconciliation.get('original_run', {}).get('finished_at'), 'ICEscreen retry finished (UTC)': reconciliation.get('retry_finished_at'), 'Reconciliation generated (UTC)': reconciliation.get('report_generated_at_utc')}) + link(run_path, 'Reconciliation provenance · JSON') + '</div>'
    refresh = run.get('report_refresh') or {}
    if refresh:
        overview += '<div class="callout"><strong>Reporting update</strong><p>BacMet evidence, partial insertion-sequence predictions and reference limitations are shown explicitly. ' + esc(refresh.get('canonical_label_corrections', 0)) + ' candidate gene/family label(s) corrected from qualifying source evidence. These remain database annotations; no function was assigned.</p><p>Native records, candidate analyses, interpretation, selection and deferred scope are unchanged. The original reconciliation hashes describe the source snapshot; this update records separate before/after metadata hashes.</p>' + kv({'Report refreshed (UTC)': refresh.get('report_generated_at_utc'), 'Source run': refresh.get('source_run_id')}) + link(run_path, 'Reporting update audit · JSON') + '</div>'
    if cross_sample_candidates:
        overview += '<div class="callout warn"><strong>Candidate sample mismatch</strong><p>' + str(cross_sample_candidates) + ' candidate records disagree with their parent sample. They are excluded from linked views and retained in the complete evidence export.</p></div>'
    if run.get('validation_mode') == 'synthetic_fixture':
        overview += '<div class="callout"><strong>SYNTHETIC SOFTWARE DEMONSTRATION</strong><p>Artificial inputs and imported calls exercise the software; no real isolate was assessed.</p></div>'
    if show_screening:
        overview += '<details class="panel sample-context"><summary>Sample and input context · ' + esc(', '.join(sids)) + '</summary>'
    overview += '<div class="grid-2"><article class="panel"><p class="eyebrow">SAMPLE CONTEXT</p><h3>' + esc(', '.join(sids) or 'No samples supplied') + '</h3>'
    for sample in samples:
        overview += '<p><strong>' + esc(sample.get('sample_id')) + '</strong> · ' + r._sample_taxon_label(sample) + '</p>'
    overview += '<p class="small muted">Declared taxonomy and HybAs read-based targets are recorded separately.</p><a href="#provenance">Inspect sample provenance →</a></article>'
    if deep_enabled:
        overview += '<article class="panel"><p class="eyebrow">FOCUSED FOLLOW-UP</p><h3>How candidates reached DeepSafe</h3><div class="queue-summary">'
        for name in ['native_screen','ml_attention','exploratory']:
            overview += '<div><span>' + esc(QUEUES[name]) + '</span><strong>' + str(q[name]) + '</strong></div>'
        overview += '</div><p class="small muted">Queues are deduplicated candidate selections, not counts of hazards.</p></article>'
    overview += '</div>'
    if show_screening:
        overview += '</details>'
    content = section('overview', '01', 'Assessment overview', 'A concise account of this run and its evidence coverage.', overview)
    background = section('inputs', '04' if show_screening else '02', 'Inputs and module scope', 'Input provenance, recorded exclusions and holds for each sample.', render_sample_scope(run, samples, combined, NAMES))
    background += section('genome', '05' if show_screening else '03', 'Genome explorer', 'Locate findings, inspect neighboring genes and follow each candidate into its evidence.', render_genome_explorer(genome_data))
    if deep_enabled:
        background += section('evidence-matrix', '06' if show_screening else '04', 'Candidate evidence matrix', 'Compare analysis coverage and review holds across selected proteins.', render_evidence_matrix(selected, records, candidate_records))
    core = [v for v in records if r._module(v) not in {'pathogenfinder2','pf2'} and v in requested]
    screenrows = []
    for rec in core:
        own = [f for f in findings if f['sample_id'] == r._sample_id(rec) and f['module'] == r._module(rec)]
        follow = [f for f in own if f['execution_status'] == 'completed' and not r._exploratory_finding(f)]
        if r._execution(rec) == 'completed' and not own and r._interpretation(rec) == 'not_detected':
            desc = 'No findings were detected within this screen’s recorded scope and thresholds.'
        elif r._execution(rec) != 'completed':
            desc = 'No completed-screen conclusion; retained findings are provisional.'
        else:
            desc = str(len(follow)) + ' findings in the main review group'
        if r._module(rec) == 'isescan' and r._execution(rec) == 'completed' and own:
            types = Counter(finding_context(f)['is_prediction_type'] for f in own)
            desc = f"{types['partial']} partial; {types['complete']} complete; {sum(v for k, v in types.items() if k not in {'partial', 'complete'})} completeness not recorded. Predictions do not establish activity."
        screenrows.append((esc(r._sample_id(rec)), '<strong>' + esc(NAMES.get(r._module(rec), r._module(rec))) + '</strong><p class="small muted">' + esc(PURPOSES.get(r._module(rec), 'Recorded screening results.')) + '</p>', badge(r._execution(rec)), esc(human(r._interpretation(rec))), num(len(own)), esc(desc), link(raw_paths[id(rec)], 'Evidence', 'text-link')))
    screens = render_tool_results(tool_rows, NAMES) + '<details class="panel"><summary>Technical tool records and scope · ' + str(len(core)) + ' records</summary>' + table(['Sample','Screen','Execution','Interpretation','All records','Scope / review','Source'], screenrows) + '</details>'
    if omitted:
        screens += '<details class="panel" id="optional-modules"><summary>' + str(len(omitted)) + ' optional module records not assessed</summary><p>Not assessed means that no result was produced for this module in the recorded run. It does not mean a negative result. Core + DeepSafe + Extend executes the configured scope; optional modules require explicit enablement.</p>' + table(['Sample','Module','What it assesses','Recorded status','Why not run','Integration in this release'], [_omitted_row(v) for v in omitted]) + '</details>'
    source_paths = {}
    for rec in core:
        key = (r._sample_id(rec), r._module(rec))
        source_paths.setdefault(key, []).append(raw_paths[id(rec)])
    if show_screening:
        screens += render_screening_findings(findings, NAMES, source_paths)
    if show_screening:
        content += section('screens','02','Core screening evidence','Complementary screens, with source calls kept distinct from validated function.',screens)
        if models:
            extend = ''.join(_extend(v,outdir,raw_paths[id(v)], deep_enabled=deep_enabled) for v in models)
            extend += render_attention_explorer(records, selected, show_deep=deep_enabled)
            content += section('extend','03','Model predictions · Extend','Recorded PathogenFinder2 outputs, model variation and attention coverage.',extend)
    content += background
    selected_execution = str(done) + ' / ' + str(len(selected)) + ' completed' if selected else 'no candidates selected'
    if not selected and any(r._execution(rec) in {'failed', 'blocked', 'not_started'} for rec in deep_records):
        states = ', '.join(sorted({human(r._execution(rec)) for rec in deep_records
                                  if r._execution(rec) in {'failed', 'blocked', 'not_started'}}))
        selected_execution = 'unavailable — DeepSafe ' + states
    elif not selected and not (run.get('options') or {}).get('deep'):
        selected_execution = 'not requested — no candidates selected'
    deep = '<div class="panel"><div class="panel-head"><div><h3>Deep family assessment</h3><p class="muted">' + str(len(selected)) + ' selected candidates · ' + str(q['native_screen']) + ' from core screens · ' + str(q['ml_attention']) + ' from ML attention</p></div>' + link('deferred-candidates.tsv','Requires attention · TSV') + '</div><p>Selected execution: <strong>' + selected_execution + '</strong>. Biological interpretations and review holds remain attached to each candidate.</p>'
    if deferred:
        reasons = Counter(str(c.get('reason') or 'reason_not_recorded') for _, c in deferred)
        deep += '<div class="callout"><strong>Requires attention: ' + num(len(deferred)) + ' records</strong><p>These records were not selected for this follow-up run. The export marks unstarted or deferred work as “Follow-up not performed” and retains any other recorded execution status. This is a workflow review flag, not a biological conclusion.</p><details><summary>Recorded reasons</summary><ul>'
        deep += ''.join('<li>' + esc(human(reason)) + ': ' + num(count) + '</li>' for reason, count in sorted(reasons.items()))
        deep += '</ul></details></div>'
    deep += '<div class="filters"><label>Search candidates<input id="candidate-search" type="search" placeholder="Protein, annotation, domain…"></label><label>Origin<select id="queue-filter"><option value="">All queues</option>' + ''.join('<option value="' + esc(k) + '">' + esc(QUEUES.get(k,human(k))) + '</option>' for k in sorted(q)) + '</select></label><label>Sample<select id="sample-filter"><option value="">All samples</option>' + ''.join('<option value="' + esc(k) + '">' + esc(k) + '</option>' for k in sids) + '</select></label><label>Interpretation<select id="interpretation-filter"><option value="">All interpretations</option>' + ''.join('<option value="' + esc(k) + '">' + esc(human(k)) + '</option>' for k in sorted({str((c.get('assessment') or {}).get('interpretation', c.get('interpretation','unresolved'))) for _,c in selected})) + '</select></label></div><div class="panel-head"><p id="candidate-count" class="small muted" aria-live="polite">' + str(len(selected)) + ' candidates</p><div><button class="button" data-action="expand-candidates">Expand visible</button> <button class="button" data-action="collapse-candidates">Collapse all</button></div></div></div><p id="candidate-empty" class="empty" hidden>No candidates match these filters.</p>'
    placement_views, placement_links = {}, {}
    for sid, c in selected:
        parent = candidate_records.get(id(c)) or {}
        placement_views[id(c)] = render_placement_tree(c, parent, outdir)
        href = exported_tree_href(c, parent, outdir)
        if href:
            placement_links[(sid, str(c.get('candidate_id') or ''), str(c.get('protein_id') or ''))] = href
    entries = [candidate_entry(sid, c, candidate_records.get(id(c)) or {}, outdir) for sid, c in selected]
    for entry, (_, candidate) in zip(entries, selected):
        entry['search_text'] += ' ' + workflow_search_terms(candidate)
    deep = render_navigator(entries) + deep
    deep += ''.join(_candidate(c,sid,outdir,candidate_records.get(id(c)),placement_views[id(c)]) for sid,c in selected)
    from .report_aware import render_aware
    deep = render_aware(deep_records) + render_workflow_overview(selected, deep_records, run.get('deep_workflow_artifacts')) + render_placement_overview(selected, deep_records, tree_links=placement_links) + render_motif_overview(selected, deep_records) + deep
    if not selected:
        deep += '<p class="panel empty">No selected candidate evidence supplied.</p>'
    if deep_enabled:
        content += section('deep','07','DeepSafe · focused review','Inspect domains, motifs, comparator gaps, bounded trees and reference-panel placement.',deep)
    recs = ''
    for i, rec in enumerate(recommendations):
        ids = rec.get('evidence_ids') or []
        recraw = export_json(outdir,'priority',rec.get('recommendation_id',i),rec)
        title = {'amr': 'AMR', 'bgc': 'Biosynthetic clusters', 'ml_attention': 'ML attention',
                 'stress': 'Biocide / metal-associated homology'}.get(rec.get('category'), human(rec.get('category')).capitalize())
        recs += '<article class="rec-card"><div class="panel-head"><div><p class="eyebrow">' + esc(rec.get('sample_id')) + ' · ' + esc(human(rec.get('assessment_type'))) + '</p><h3>' + esc(title) + '</h3></div>' + badge(rec.get('priority','review'),'warn') + '</div><p>' + esc(rec.get('recommendation')) + '</p><p class="small muted">' + esc(rec.get('trigger')) + '</p>'
        if rec.get('missing_comparison_groups'):
            recs += '<p class="small">Comparator gaps: ' + esc(', '.join(rec['missing_comparison_groups'])) + '</p>'
        recs += '<div class="panel-head"><span class="small muted">' + num(len(ids)) + ' linked source IDs · ' + esc(human(rec.get('provided_phenotype_status'))) + '</span>' + link(recraw,'Trace evidence','text-link') + '</div></article>'
    priority_scope = 'source findings and recorded DeepSafe holds' if deep_enabled else 'source findings and recorded screening limitations'
    recs = '<div class="callout"><strong>Review questions, not completed experiments.</strong><p>Recommendations reflect ' + priority_scope + '. Laboratory assessments require an applicable question and organism-specific interpretation.</p></div><div class="grid-2">' + recs + '</div>'
    if not recommendations:
        recs += '<p class="panel empty">No finding-specific follow-up was generated. This does not establish that no further assessment is needed.</p>'
    content += section('recommendations','08','Priorities for assessment','Resolve model applicability and comparator gaps before interpreting affected findings.',recs)
    provenance = '<article class="panel"><h3>Run record</h3>' + kv({'Run ID':run.get('run_id'),'Software':run.get('version'),'Started (UTC)':run.get('started_at'),'Finished (UTC)':run.get('finished_at'),'Engine':run.get('engine'),'Validation mode':run.get('validation_mode'),'Recorded completeness':run.get('completeness'),'Configuration SHA256':run.get('config_hash'),'Source SHA256':run.get('source_hash')}) + link(run_path,'Run and input evidence · JSON') + '</article>'
    provenance += '<article class="panel" id="citation"><p class="eyebrow">SOFTWARE CITATION</p><h3>Cite BaSIS</h3><p><strong>Author: ' + esc(AUTHOR) + '</strong></p><p class="citation-text">' + esc(citation_text(run.get('version') or None)) + '</p><div class="downloads">' + ''.join('<a class="button" href="' + path + '" download>' + title + '</a>' for path,title in [('BaSIS.ris','RIS · EndNote / Zotero'),('BaSIS.bib','BibTeX'),('CITATION.cff','CITATION.cff'),('CITATION.txt','Plain text')]) + '</div><p class="small muted">' + esc(CITATION_NOTE) + ' A publication DOI and release date have not been added to this citation.</p></article>'
    for sample in samples:
        provenance += '<details class="panel"><summary><strong>' + esc(sample.get('sample_id')) + '</strong> · ' + r._sample_taxon_label(sample) + '</summary>' + r._render_sample_taxonomy(sample) + '<h4>User-provided phenotype evidence</h4>'
        ph = r._phenotypes(sample)
        provenance += kv({str(p.get('assay',p.get('name','Phenotype'))): p for p in ph}) if ph else '<p>No phenotype evidence supplied.</p>'
        provenance += '<p class="small muted">Supplied phenotype records are unreviewed; no wet-lab experiment was performed by BaSIS.</p></details>'
    provenance += '<article class="panel"><h3>Evidence downloads</h3><p class="muted">The dashboard is compact; complete machine-readable records preserve the full audit trail.</p><div class="downloads">' + ''.join(link(path,title) for path,title in [('report-navigation.json','Report navigation index · JSON'),('evidence.json','Complete evidence · JSON'),('findings.tsv','All findings · TSV'),('recommendations.tsv','Review priorities · TSV'),('module-scope.tsv','Inputs and module decisions · TSV'),('methods.txt','Methods and execution'),(raw_recs,'Full priority linkage · JSON')]) + '</div><details class="evidence-list"><summary>Recorded native artifact locations</summary>' + ''.join(r._artifact_list(v.get('artifacts',[]),outdir) for v in combined) + '</details></article>'
    # A bounded sample of rejected records keeps their meaning visible without
    # serializing every source payload into the page or search attributes.
    weak = [f for f in findings if r._exploratory_finding(f) and f.get('category') != 'ml_attention']
    if weak:
        provenance += '<details class="panel"><summary>Exploratory evidence retained · ' + num(len(weak)) + ' records</summary><p>These calls are not promoted to established functions. The full set is available in findings.tsv and evidence.json.</p>' + table(['Sample','Protein','Source annotation','Interpretation'],[(esc(v.get('sample_id')),esc(v.get('protein_id')),esc(v.get('gene')),esc(v.get('interpretation'))) for v in weak[:5]]) + '</details>'
    if (Path(outdir) / 'source-artifacts.html').is_file():
        provenance += '<article class="panel"><h3>Files from the saved run</h3><p class="muted">Browse the original report and exactly recorded artifacts. File availability is separate from the saved analysis result.</p><a class="button" href="source-artifacts.html">Open source files</a></article>'
    content += section('provenance','09','Provenance & downloads','Recorded inputs, taxonomy, sources and exported evidence.',provenance)
    start = start.replace('</style>', GENOME_CSS + EVIDENCE_CSS + TREE_CSS + MOTIF_CSS + PLACEMENT_CSS + PLACEMENT_TREE_CSS + SCREENING_CSS + OVERVIEW_CSS + '</style>', 1)
    if view == 'screening':
        start = start.replace('<body>', '<body class="report-screening">', 1)
    return start + content + '<footer>BaSIS · Bactomics environment · Author: ' + esc(AUTHOR) + ' · <a href="#citation">Cite this software</a><br>Research screening evidence. No global safety score or organism-level safety verdict is computed.<br><span class="small">All primary content is available offline. Interactive views link recorded evidence and do not change its interpretation.</span></footer></main></div><script>' + JS + GENOME_JS + EVIDENCE_JS + TREE_JS + PLACEMENT_TREE_JS + '</script></body></html>'
