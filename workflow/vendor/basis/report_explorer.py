"""Offline, evidence-backed genome explorer presentation.

This module draws recorded intervals only. It never infers islands, physical
topology, gene functions, or the absence of a feature from an empty track.
"""
from __future__ import annotations

import html
import json

from .report_explorer_assets import CSS, JS

DISPLAY_LABELS = {
    'amr': 'AMR candidates', 'vfdb': 'VFDB matches',
    'provirus': 'Viruses / proviruses', 'bgc': 'Biosynthetic regions',
    'integron': 'Integron predictions', 'plasmid': 'Plasmid predictions',
    'ml_attention': 'ML attention priorities', 'selected': 'DeepSafe candidates',
    'genomic_islands': 'Genomic islands',
}


def _esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def _extent(contig):
    length = contig.get("length")
    if isinstance(length, int) and not isinstance(length, bool) and length > 0:
        return length
    return max([1] + [int(x.get("end") or 1) for key in ("genes", "regions")
                      for x in (contig.get(key) or [])])


def _static_overview(sample):
    contigs = sample.get("contigs") or []
    if not contigs:
        return '<p class="empty">No coordinate-resolved contigs are available for this sample.</p>'
    contig = contigs[0]
    extent = _extent(contig)
    tracks = [t for t in sample.get("tracks", []) if t.get("count")]
    height = 65 + 29 * len(tracks)
    svg = [f'<svg class="gx-svg" viewBox="0 0 1060 {height}" role="img" aria-label="Recorded genomic features on contig {_esc(contig["id"])}">',
           '<rect width="1060" height="100%" fill="#fff"/>',
           '<text x="190" y="18" fill="#617385" font-size="11">1 bp</text>',
           f'<text x="1028" y="18" text-anchor="end" fill="#617385" font-size="11">{extent:,} bp</text>']
    colors = {'amr': '#b95947', 'vfdb': '#aa761a', 'provirus': '#8060ae', 'bgc': '#21846c',
              'integron': '#ab588f', 'plasmid': '#3c92a4', 'ml_attention': '#4779ba', 'selected': '#203f59'}
    for i, track in enumerate(tracks):
        y = 40 + 29 * i
        short_label = DISPLAY_LABELS.get(track['id'], str(track['label'])[:25])
        svg.append(f'<text x="10" y="{y+4}" font-size="12" fill="#385167">{_esc(short_label)}'
                   f'<title>{_esc(track["label"])}</title></text>')
        svg.append(f'<line x1="190" x2="1030" y1="{y}" y2="{y}" stroke="#e4ebef" stroke-width="6"/>')
        for r in contig.get("regions", []):
            if r.get("track") != track.get("id"):
                continue
            x = 190 + (r["start"] - 1) / extent * 840
            width = max(2, (r["end"] - r["start"] + 1) / extent * 840)
            svg.append(f'<rect x="{x:.2f}" y="{y-7}" width="{width:.2f}" height="14" rx="2" '
                       f'fill="{colors.get(track["id"], "#718292")}"><title>{_esc(r.get("label"))} '
                       f'· {r["start"]:,}–{r["end"]:,}</title></rect>')
    svg.append('</svg>')
    return '<div class="gx-scroll">' + ''.join(svg) + '</div>'


def render_genome_explorer(payload):
    """Return compact HTML and safely embedded data; CSS/JS are exported separately."""
    samples = payload.get("samples") or []
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    raw = raw.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    raw = raw.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    summary_rows = []
    for sample in samples:
        contigs = sample.get("contigs") or []
        for track in sample.get("tracks") or []:
            summary_rows.append('<tr><td>' + _esc(sample.get("id")) + '</td><td>' + _esc(track.get("label"))
                                + '</td><td>' + _esc(str(track.get("status", "not_assessed")).replace('_', ' '))
                                + '</td><td>' + _esc(track.get("count", 0)) + '</td></tr>')
    body = '''<div id="genome-explorer" class="gx">
      <div class="panel gx-intro"><div class="panel-head"><div><p class="eyebrow">GENOMIC CONTEXT</p>
      <h3>Explore where the evidence occurs</h3></div><span class="badge info">Linear coordinate view</span></div>
      <p class="small muted">Recorded screening candidates, predicted regions and analysis priorities share one coordinate axis.
      Spatial overlap does not establish transfer, pathogenicity or function. Coordinates are 1-based and inclusive.</p>
      <div class="gx-controls" id="gx-controls" hidden>
        <label>Sample<select id="gx-sample" aria-label="Genome sample"></select></label>
        <label>Contig<select id="gx-contig" aria-label="Genome contig"></select></label>
        <label class="gx-search-label">Find a protein or annotation<input id="gx-search" type="search" autocomplete="off"
          placeholder="Protein ID, product or source annotation…" aria-label="Search genome proteins and annotations" aria-controls="gx-search-results"/></label>
      </div>
      <div id="gx-search-results" class="gx-search-results" hidden></div>
      <div id="gx-track-list" class="gx-track-list"></div>
      <p class="gx-note" id="gx-metadata"></p>
      <div class="gx-legend"><span><i style="background:#087c78"></i>Selected core candidate</span>
      <span><i style="background:#4779ba"></i>ML attention priority</span><span><i style="background:#9baeba"></i>Other annotated protein</span>
      <span>Arrowheads show recorded strand</span></div>
      </div><div class="panel"><div class="panel-head"><div><h3>Genome overview</h3>
      <p class="small muted">Click a feature to inspect it, or the coordinate axis to move the window.</p></div>
      <div class="gx-export" id="gx-export" hidden><button class="button" id="gx-export-svg" type="button">Export SVG</button>
      <button class="button" id="gx-export-png" type="button">Export PNG</button></div></div>
      <p class="gx-scroll-hint">Swipe or scroll horizontally to explore the full diagrams on a narrow screen.</p>
      <div id="gx-overview">'''
    body += _static_overview(samples[0]) if samples else '<p class="empty">No genome coordinate data recorded.</p>'
    body += '''</div><div id="gx-interactive" hidden>
      <div class="gx-window-controls"><strong>Neighborhood window</strong>
        <label>Start (bp)<input id="gx-start" type="number" min="1" step="1" inputmode="numeric" aria-label="Neighborhood start coordinate"/></label>
        <label>End (bp)<input id="gx-end" type="number" min="1" step="1" inputmode="numeric" aria-label="Neighborhood end coordinate"/></label>
        <button class="button" id="gx-apply" type="button">Apply</button>
        <div class="gx-navigation" role="group" aria-label="Navigate genomic window">
        <button class="button" id="gx-pan-left" type="button" aria-label="Pan left">←</button>
        <button class="button" id="gx-zoom-in" type="button" aria-label="Zoom in">+</button>
        <button class="button" id="gx-zoom-out" type="button" aria-label="Zoom out">-</button>
        <button class="button" id="gx-pan-right" type="button" aria-label="Pan right">→</button>
        <button class="button" id="gx-full" type="button">Whole contig</button></div>
      </div><p id="gx-window-status" class="small muted" role="status" aria-live="polite"></p>
      <div id="gx-neighborhood"></div>
      <div id="gx-gene-detail" class="gx-gene-detail" aria-live="polite"></div>
      </div></div>
      <details class="panel gx-provenance"><summary>Track coverage and coordinate provenance</summary>
      <p class="small muted">An empty track is not an absence claim. Genomic-island predictions require a dedicated recorded analysis;
      prophages and biosynthetic regions remain separate feature types. Counts are sample-wide mapped features,
      with repeated source calls merged within each track; they are not counts of independent hazards.</p>
      <div class="table-wrap"><table><thead><tr><th scope="col">Sample</th><th scope="col">Track</th>
      <th scope="col">Recorded status</th><th scope="col">Mapped features</th></tr></thead><tbody>'''
    body += ''.join(summary_rows) or '<tr><td colspan="4">No track metadata recorded.</td></tr>'
    body += '</tbody></table></div>'
    for sample in samples:
        body += '<h4>' + _esc(sample.get('id')) + '</h4><ul class="gx-source-notes">'
        for contig in sample.get('contigs') or []:
            length = contig.get('length')
            body += '<li>Contig ' + _esc(contig.get('id')) + ': ' + (f'{length:,} bp' if isinstance(length, int) else 'length not recorded')
            body += '; length source: ' + _esc(contig.get('length_source', 'unavailable')).replace('_', ' ') + '.</li>'
        for note in sample.get('notes') or []:
            body += '<li>' + _esc(note) + '</li>'
        body += '</ul>'
    body += '''</details><noscript><p class="callout">JavaScript is disabled. The static overview and track coverage remain available;
    enable JavaScript to search, zoom, inspect gene neighborhoods and export images. No network connection is needed.</p></noscript>
    </div>'''
    body += '<script type="application/json" id="basis-genome-data">' + raw + '</script>'
    return body
