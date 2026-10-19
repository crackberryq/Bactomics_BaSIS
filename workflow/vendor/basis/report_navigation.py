"""Navigation over saved evidence; no analyses, selection or interpretation changes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote, urlsplit

from .report_links import candidate_anchor, candidate_section_anchor

SECTIONS = {'overview': 'Overview', 'domains': 'Domains', 'motifs': 'Motifs & patterns',
            'trees': 'Trees & alignments', 'placement': 'Placement',
            'references': 'Comparators', 'files': 'Files'}


def candidate_fingerprint(candidate):
    from .report import _jsonable
    return hashlib.sha256(json.dumps(_jsonable(candidate), sort_keys=True,
                          ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def candidate_entry(sid, c, parent, outdir):
    from .report import _execution, _exploratory_finding
    from .report_dashboard import export_json
    from .report_motif_views import motif_search_terms
    from .report_placement import placement_search_terms
    ident = c.get('protein_id') or c.get('candidate_id') or 'Unidentified candidate'
    a = c.get('assessment') or {}
    sources = [s for s in c.get('source_evidence', []) or [] if isinstance(s, dict)]
    labels = sorted({str(s.get('gene') or s.get('family')) for s in sources
                     if not _exploratory_finding(s) and (s.get('gene') or s.get('family'))})
    label = ('Attention rank ' + str(c.get('attention_rank') or 'not recorded') +
             ' · exploratory prioritization') if c.get('selection_queue') == 'ml_attention' else (
             ' / '.join(labels[:3]) or str(c.get('family') or c.get('gene') or 'Family review'))
    interpretation = a.get('interpretation', c.get('interpretation', 'unresolved'))
    reason = a.get('reason') or c.get('reason') or 'not_recorded'
    gaps = a.get('missing_comparison_groups') or []
    layers = {k: v.get('status', 'not_assessed') for k, v in (c.get('analyses') or {}).items()
              if isinstance(v, dict)}
    search = ' '.join(str(x) for x in [sid, ident, label, c.get('gene', ''),
             c.get('family', ''), interpretation, reason, a.get('followup', ''),
             ' '.join(map(str, gaps)), ' '.join(str(s.get('gene') or s.get('family') or '') for s in sources),
             ' '.join(map(str, c.get('notes') or [])), motif_search_terms(c), placement_search_terms(c)]).lower()
    return {'sample_id': sid, 'protein_id': ident, 'candidate_id': c.get('candidate_id'),
            'candidate_sha256': candidate_fingerprint(c), 'anchor': candidate_anchor(sid, ident),
            'sections': {k: candidate_section_anchor(sid, ident, k) for k in SECTIONS},
            'evidence_href': export_json(outdir, 'candidate', [sid, c.get('candidate_id'), ident], c),
            'label': label, 'search_text': search, 'execution': c.get('execution', 'unknown'),
            'parent_execution': _execution(parent), 'interpretation': interpretation,
            'followup': a.get('followup', 'review_existing_evidence'), 'reason': reason,
            'missing_comparison_groups': gaps, 'layer_statuses': layers}


def write_navigation_index(run, deep_records, outdir):
    from .report import _sample_id
    outdir = Path(outdir)
    entries = [candidate_entry(_sample_id(rec), c, rec, outdir)
               for rec in deep_records for c in rec.get('candidates', []) or []
               if isinstance(c, dict) and c.get('sample_id') in (None, '', _sample_id(rec))
               and c.get('selected', 'selected' not in c)]
    path = outdir / 'report-navigation.json'
    data = {'schema': 'basis-report-navigation-v1', 'run_id': run.get('run_id'),
            'report_sha256': hashlib.sha256((outdir / 'report.html').read_bytes()).hexdigest(),
            'evidence_sha256': hashlib.sha256((outdir / 'evidence.json').read_bytes()).hexdigest(),
            'candidates': entries}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def render_navigator(entries):
    from .report_dashboard import esc, human, badge, link
    rows = []
    for e in entries:
        reason = human(e['reason'])
        if e['missing_comparison_groups']:
            reason += ' · Missing comparators: ' + ', '.join(map(str, e['missing_comparison_groups']))
        links = ' '.join(link('#' + e['sections'][k], SECTIONS[k], 'text-link')
                         for k in ('motifs', 'trees', 'placement', 'references', 'files'))
        cells = [esc(e['sample_id']), link('#' + e['anchor'], e['protein_id'], 'text-link') +
                 '<p class="small muted">' + esc(e['label']) + '</p>', badge(e['execution']) +
                 '<p class="small">' + esc(human(e['interpretation'])) + '</p>',
                 esc(reason) + '<p class="small muted">' + esc(human(e['followup'])) + '</p>',
                 '<div class="evidence-shortcuts">' + links + '</div>']
        rows.append('<tr data-navigation-candidate="' + esc(e['anchor']) + '">' +
                    ''.join('<td>' + cell + '</td>' for cell in cells) + '</tr>')
    return ('<div class="panel evidence-navigator" id="deep-navigator"><h3>Evidence navigator</h3>'
            '<p class="muted">Use the candidate search and filters below to find annotations, motifs, '
            'review reasons or missing comparators. Links open the recorded evidence, including explicit '
            'holds and unavailable files.</p><div class="table-wrap"><table><thead><tr>' +
            ''.join('<th scope="col">' + h + '</th>' for h in
                    ('Sample', 'Candidate', 'Execution / interpretation', 'Recorded review reason', 'Open evidence')) +
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>' +
            ('' if rows else '<p class="empty">No selected candidates recorded.</p>') + '</div>')


def render_comparators(c, outdir, key):
    from .report_dashboard import esc, table, link, export_json
    refs = c.get('references') or []
    gaps = (c.get('assessment') or {}).get('missing_comparison_groups') or []
    body = '<p class="muted">Recorded comparator selection and source review status. Inclusion does not establish function.</p>'
    if gaps:
        body += '<p class="callout"><strong>Missing comparison groups</strong><br>' + esc(', '.join(map(str, gaps))) + '</p>'
    if refs:
        body += table(['Reference', 'Source review status', 'Selection reason', 'Comparison group'], [
            (esc(v.get('accession') or v.get('reference_id') or v.get('id') or 'not recorded'),
             esc(v.get('review_status', 'not recorded')), esc(v.get('selection_reason', 'not recorded')),
             esc(v.get('comparison_group', 'not recorded'))) for v in refs if isinstance(v, dict)])
    else:
        body += '<p class="empty">No selected comparator references recorded.</p>'
    return body + link(export_json(outdir, 'comparators', key, refs), 'Comparator records · JSON')


def render_candidate_files(c, outdir, key, raw):
    from .report_dashboard import esc, table, link, export_json
    body = '<div class="downloads">' + link(raw, 'Complete candidate evidence · JSON')
    for name, data in [('analyses', c.get('analyses') or {}), ('comparators', c.get('references') or []),
                       ('consolidated', c.get('consolidated_evidence') or {})]:
        body += link(export_json(outdir, name, key, data), name.capitalize() + ' · JSON')
    body += '</div><p class="small muted">Downloads above contain saved records. Verified tree and alignment exports appear under Trees & alignments; placement exports appear under Placement.</p>'
    paths = []
    def add(value):
        if isinstance(value, (str, Path)) and str(value) not in paths:
            paths.append(str(value))
        elif isinstance(value, list):
            for item in value:
                add(item)
    add((c.get('query') or {}).get('path'))
    add(c.get('artifacts')); add(c.get('evidence_paths'))
    analyses = c.get('analyses') or {}
    for layer, names in {'context': ('sequence_path',), 'domains': ('domain_table', 'native_output'),
                         'phylogeny': ('input', 'labels', 'alignment', 'tree', 'artifacts')}.items():
        for name in names:
            add((analyses.get(layer) or {}).get(name))
    rows = []
    root = Path(outdir).resolve()
    for value in paths:
        href = None
        state = 'Unavailable in this report bundle; saved location only'
        try:
            if not urlsplit(value).scheme and '\\' not in value:
                p = Path(value)
                p = (p if p.is_absolute() else root / p).resolve()
                if p.is_relative_to(root) and p.is_file():
                    href = quote(p.relative_to(root).as_posix(), safe='/')
                    state = 'Local file; contents not revalidated by reporting'
        except (OSError, ValueError):
            pass
        rows.append((link(href, value, 'text-link') if href else esc(value), esc(state)))
    if rows:
        body += '<details class="evidence-list"><summary>Recorded file locations (' + str(len(rows)) + ')</summary>' + table(['Recorded path', 'Availability'], rows) + '</details>'
    else:
        body += '<p class="empty">No additional candidate file locations recorded.</p>'
    return body
