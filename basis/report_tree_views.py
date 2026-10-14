"""Offline, report-only views of explicitly recorded phylogenetic artifacts.

No directory discovery, native execution, interpretation changes, or unverified
sequence files. Each displayed/exported source must match a recorded SHA-256.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import html
import json
import math
from pathlib import Path
import re

CSS = """
.tree-run{margin:12px 0;border:1px solid #dce5eb;border-radius:10px;padding:12px}
.tree-run>summary{cursor:pointer;font-weight:650}.tree-scroll{overflow:auto;margin:12px 0}
.tree-svg{display:block;min-width:850px;width:100%;background:#fff}
.tree-svg text{font:12px system-ui,sans-serif;fill:#40566c}.tree-svg .tree-query{fill:#006e69;font-weight:750}
.tree-svg .tree-support{font-size:9px;fill:#775318}.tree-svg .tree-branch{stroke:#76909d;stroke-width:1.3;fill:none}
.alignment-frame{width:100%;height:390px;border:1px solid #dce5eb;border-radius:8px;background:#fff}
.tree-links{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.tree-note{font-size:12px;color:#536879}
@media print{.alignment-frame{display:none}.tree-scroll{overflow:visible}.tree-svg{min-width:0}}
"""
JS = ""

MAX_SOURCE_BYTES = 2_000_000
MAX_TIPS = 100
MAX_COLUMNS = 5_000
MAX_PREVIEW_ROWS = 60
MAX_PREVIEW_COLUMNS = 1_500


def _esc(value):
    return html.escape(str(value), quote=True)


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _verified(path, digests):
    """Read only a supplied path; inconsistent/missing digests fail closed."""
    if not path:
        return None, "not recorded"
    known = {str(v).lower() for v in digests if v is not None}
    if not known or any(not re.fullmatch(r"[0-9a-f]{64}", v) for v in known):
        return None, "recorded checksum unavailable"
    if len(known) != 1:
        return None, "conflicting recorded checksums"
    try:
        p = Path(path)
        if not p.is_file():
            return None, "native file unavailable on this computer"
        if p.stat().st_size > MAX_SOURCE_BYTES:
            return None, "native file exceeds the report preview size limit"
        with p.open("rb") as handle:
            content = handle.read(MAX_SOURCE_BYTES + 1)
        if len(content) > MAX_SOURCE_BYTES:
            return None, "native file exceeds the report preview size limit"
        if _sha(content) != next(iter(known)):
            return None, "checksum mismatch; preview withheld"
        return content, None
    except (OSError, TypeError, ValueError):
        return None, "native file could not be read"


def _parse_alignment(content):
    sequences = {}
    key = None
    for line in content.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            key = line[1:].split()[0] if line[1:].split() else ""
            if not key or key in sequences:
                raise ValueError("empty or duplicate alignment ID")
            sequences[key] = ""
        elif key is None or re.search(r"[^A-Za-z*?.-]", line):
            raise ValueError("unsupported alignment sequence")
        else:
            sequences[key] += line.upper()
    lengths = {len(v) for v in sequences.values()}
    if not sequences or len(sequences) > MAX_TIPS or len(lengths) != 1 or not next(iter(lengths)):
        raise ValueError("alignment must contain equal nonempty rows within preview limits")
    if next(iter(lengths)) > MAX_COLUMNS:
        raise ValueError("alignment exceeds the report column limit")
    return sequences


@dataclass
class _Node:
    name: str = ""
    length: float | None = None
    children: list = field(default_factory=list)
    x: float = 0
    y: float = 0


def _parse_tree(content, expected):
    """Parse bounded Newick, including quoted labels and ordinary comments."""
    raw = content.decode("utf-8").strip()
    tokens = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char.isspace():
            index += 1
        elif char == "[":
            end = raw.find("]", index + 1)
            if end < 0:
                raise ValueError("unterminated Newick comment")
            index = end + 1
        elif char in "(),:;":
            tokens.append(char); index += 1
        elif char == "'":
            index += 1; label = ""
            while index < len(raw):
                if raw[index] == "'":
                    if index + 1 < len(raw) and raw[index + 1] == "'":
                        label += "'"; index += 2; continue
                    index += 1; break
                label += raw[index]; index += 1
            else:
                raise ValueError("unterminated Newick label")
            tokens.append(label)
        else:
            end = index
            while end < len(raw) and not raw[end].isspace() and raw[end] not in "(),:;[":
                end += 1
            tokens.append(raw[index:end]); index = end
    pos = 0
    leaves = []
    def node(depth=0):
        nonlocal pos
        if depth > MAX_TIPS or pos >= len(tokens):
            raise ValueError("invalid or excessive Newick nesting")
        result = _Node()
        if tokens[pos] == "(":
            pos += 1; result.children.append(node(depth + 1))
            while pos < len(tokens) and tokens[pos] == ",":
                pos += 1; result.children.append(node(depth + 1))
            if pos >= len(tokens) or tokens[pos] != ")" or len(result.children) < 2:
                raise ValueError("malformed Newick branching")
            pos += 1
            if pos < len(tokens) and tokens[pos] not in ",):;":
                result.name = tokens[pos]; pos += 1
        else:
            if tokens[pos] in "(),:;":
                raise ValueError("missing Newick tip")
            result.name = tokens[pos]; pos += 1; leaves.append(result.name)
        if pos < len(tokens) and tokens[pos] == ":":
            pos += 1
            try:
                result.length = float(tokens[pos]); pos += 1
            except (ValueError, IndexError) as exc:
                raise ValueError("invalid Newick length") from exc
            if not math.isfinite(result.length) or result.length < 0:
                raise ValueError("invalid Newick length")
        return result
    root = node()
    if tokens[pos:] != [";"] or len(leaves) != len(set(leaves)) or set(leaves) != set(expected):
        raise ValueError("tree tip set differs from verified alignment")
    return root


def _refs(value):
    if not isinstance(value, dict):
        return []
    return [r for r in (value.get("references") or value.get("reference_aliases") or []) if isinstance(r, dict)]


def _label(tip, labels, query_id, protein_id, compact=False):
    if tip == query_id:
        return str(protein_id or tip) + " · QUERY"
    refs = _refs(labels.get(tip))
    if not refs:
        return tip
    accessions = list(dict.fromkeys(str(r.get("accession") or r.get("reference_id") or "") for r in refs))
    accessions = [a for a in accessions if a]
    if compact:
        # Native AMRFinder accessions may carry pipe-delimited metadata. This
        # shortens display text only; full identities stay in the SVG title and
        # checked source exports.
        accessions = list(dict.fromkeys(a.split('|', 1)[0] for a in accessions))
    statuses = sorted({str(r.get("review_status") or "unreviewed") for r in refs})
    text = tip + " · " + ", ".join(accessions[:3])
    if len(accessions) > 3:
        text += " +" + str(len(accessions) - 3)
    return text + " [" + "/".join(statuses) + "]"


def _embedded_labels(tree, sequences):
    """Link embedded labels only when the aligned reference sequence matches."""
    verified = {}
    for tip, info in (tree.get("reference_labels") or {}).items():
        if tip not in sequences:
            continue
        aligned_hash = _sha(sequences[tip].replace("-", "").replace(".", "").encode())
        refs = _refs(info)
        matching = []
        for ref in refs:
            normalization = (tree.get("normalization") or {}).get(ref.get("reference_id"), {})
            expected = ref.get("sequence_sha256")
            if (normalization.get("source_sha256") == expected
                    and normalization.get("alignment_sequence_sha256")):
                expected = normalization["alignment_sequence_sha256"]
            if aligned_hash == expected:
                matching.append(ref)
        if matching:
            verified[tip] = {"references": matching}
    return verified


def _tree_svg(root, labels, query_id, protein_id):
    nodes = []
    leaves = []
    def visit(n, distance=0):
        if not math.isfinite(distance):
            raise ValueError("cumulative tree branch length is not finite")
        nodes.append(n); n.x = distance
        if n.children:
            for child in n.children:
                visit(child, distance + (child.length or 0))
            n.y = (n.children[0].y + n.children[-1].y) / 2
        else:
            n.y = 30 + 24 * len(leaves); leaves.append(n)
    visit(root)
    if any(n.length is None for n in nodes[1:]):
        raise ValueError("tree branch lengths not fully recorded")
    maximum = max(n.x for n in nodes)
    scale = 580 / maximum if maximum > 0 else 1
    height = 90 + len(leaves) * 24
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" class="tree-svg" viewBox="0 0 1120 {height}" role="img" aria-label="Recorded phylogenetic tree, query highlighted">',
           '<style>.tree-branch{stroke:#76909d;stroke-width:1.3;fill:none}text{font:12px system-ui,sans-serif;fill:#40566c}.tree-query{fill:#006e69;font-weight:bold}.tree-support{font-size:9px;fill:#775318}</style>']
    for n in nodes:
        x = 18 + n.x * scale
        if n.children:
            svg.append(f'<path class="tree-branch" d="M{x:.3f} {n.children[0].y:.3f} V{n.children[-1].y:.3f}"/>')
            for child in n.children:
                svg.append(f'<path class="tree-branch" d="M{x:.3f} {child.y:.3f} H{18 + child.x * scale:.3f}"/>')
            if n.name:
                support_width = max(13, len(n.name) * 5.5 + 5)
                svg.append(f'<rect x="{x + 1:.3f}" y="{n.y - 15:.3f}" width="{support_width:.3f}" height="12" rx="2" fill="white" fill-opacity=".92"/>')
                svg.append(f'<text class="tree-support" x="{x + 3:.3f}" y="{n.y - 5:.3f}">{_esc(n.name)}</text>')
        else:
            label = _label(n.name, labels, query_id, protein_id)
            display_label = _label(n.name, labels, query_id, protein_id, compact=True)
            short = display_label[:66] + "…" if len(display_label) > 67 else display_label
            cls = ' class="tree-query"' if n.name == query_id else ""
            svg.append(f'<path d="M{x:.3f} {n.y:.3f} H640" stroke="#cbd7df" stroke-width=".8" stroke-dasharray="3 4" fill="none"/>')
            if n.name == query_id:
                svg.append(f'<circle cx="{x:.3f}" cy="{n.y:.3f}" r="3.5" fill="#008b81"/>')
            svg.append(f'<text{cls} x="650" y="{n.y + 4:.3f}"><title>{_esc(label)}</title>{_esc(short)}</text>')
    if maximum > 0:
        bar = maximum / 5
        y = height - 26
        svg.append(f'<path class="tree-branch" d="M18 {y - 3} V{y + 3} M18 {y} H134 M134 {y - 3} V{y + 3}"/><text x="18" y="{y + 19}">{bar:.3g} substitutions / site</text>')
    svg.append('</svg>')
    return ''.join(svg)


def _span_sequence(sequence, query):
    groups = []
    previous = None
    chars = ""
    for residue, q in zip(sequence, query):
        cls = "gap" if residue in "-." else "ambiguous" if residue not in "ACDEFGHIKLMNPQRSTVWY" else "same" if residue == q and q not in "-." else "different"
        if cls != previous and chars:
            groups.append('<span class="' + previous + '">' + _esc(chars) + '</span>'); chars = ""
        previous = cls; chars += residue
    if chars:
        groups.append('<span class="' + previous + '">' + _esc(chars) + '</span>')
    return ''.join(groups)


def _alignment_html(sequences, labels, query_id, protein_id, title):
    order = [query_id] + [key for key in sequences if key != query_id]
    order = order[:MAX_PREVIEW_ROWS]
    columns = min(len(sequences[query_id]), MAX_PREVIEW_COLUMNS)
    content = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + _esc(title) + '</title><style>body{margin:14px;font:13px system-ui;color:#244255;background:#fff}h1{font-size:17px}.scroll{overflow:auto}table{border-collapse:collapse;margin:14px 0}th{text-align:left;font-size:11px;position:sticky;left:0;background:#fff;max-width:290px;min-width:210px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-right:14px}td{padding:1px 0}.seq{font:13px/1.5 monospace;letter-spacing:.06em;white-space:pre}.query th{color:#006e69;font-weight:800}.same{background:#dff2ee}.different{background:#fff1d5}.gap{color:#9aa9b0;background:#f3f5f7}.ambiguous{background:#e7e5f6}p{color:#536879}.legend span{padding:3px 5px;margin-right:8px}caption{text-align:left;font-weight:bold;margin:8px 0}</style><h1>' + _esc(title) + '</h1><p>Alignment columns, not protein residue coordinates. Colors compare each row to the query; agreement does not establish function.</p><p class="legend"><span class="same">Same residue</span><span class="different">Different residue</span><span class="gap">Gap</span><span class="ambiguous">Ambiguous / noncanonical</span></p>'
    content += f'<p>Showing {len(order)} of {len(sequences)} sequences and {columns} of {len(sequences[query_id])} columns. The checked FASTA export preserves the entire alignment.</p><div class="scroll">'
    for start in range(0, columns, 80):
        end = min(start + 80, columns)
        content += f'<table><caption>Columns {start + 1}–{end}</caption><tbody>'
        for tip in order:
            label = _label(tip, labels, query_id, protein_id)
            content += '<tr' + (' class="query"' if tip == query_id else '') + '><th title="' + _esc(label) + '">' + _esc(label) + '</th><td class="seq">' + _span_sequence(sequences[tip][start:end], sequences[query_id][start:end]) + '</td></tr>'
        content += '</tbody></table>'
    return content + '</div></html>'


def _export(outdir, prefix, suffix, content):
    path = Path(outdir) / "report-data" / (prefix + '-' + _sha(content)[:20] + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path.relative_to(outdir).as_posix()


def _artifact_digests(tree, record):
    hashes = {}
    for owner in [record, record.get('provenance') or {}, tree]:
        for key, value in (owner.get('artifact_checksums') or {}).items():
            hashes.setdefault(str(key), []).append(value)
    followup = tree.get('followup_evidence') or {}
    directory = followup.get('source_directory')
    if directory:
        for relative, value in (followup.get('artifact_hashes') or {}).items():
            part = Path(relative)
            if part.is_absolute() or '..' in part.parts:
                continue
            hashes.setdefault(str(Path(directory) / part), []).append(value)
    return hashes


def _run_paths(tree, run, artifacts):
    treepath = run.get('tree')
    alignment = run.get('alignment')
    name = str(run.get('name', 'baseline'))
    if not treepath:
        # Historical Cfr evidence records its explicit artifact paths and three
        # display names. Match named run directories, never enumerate disk.
        aliases = {'alternative_model': {'alternative_model', 'full_alternative_model'},
                   'occupancy80': {'occupancy80', 'occupancy80_model_selection'}}.get(name, {name})
        matches = [p for p in artifacts if Path(p).parent.name in aliases and Path(p).suffix == '.treefile']
        if len(matches) == 1:
            treepath = matches[0]
    if not alignment and treepath:
        matches = [p for p in artifacts if Path(p).parent == Path(treepath).parent and Path(p).name == 'alignment.faa']
        if len(matches) == 1:
            alignment = matches[0]
    return treepath, alignment


def render_tree_alignment(candidate, deep_record, outdir):
    """Render at most three checksum-verified, recorded tree/alignment runs.

    Returns HTML; CSS and JS constants are intended for the report's head/footer.
    Missing/mismatched native outputs produce short notes and never rerun tools.
    """
    tree = (candidate.get('analyses') or {}).get('phylogeny') or {}
    if tree.get('status') != 'completed':
        return ''
    query_id = (candidate.get('query') or {}).get('id')
    if not query_id:
        return '<p class="tree-note">Tree visualization unavailable: query identity was not recorded.</p>'
    followup = tree.get('followup_evidence') or {}
    runs = tree.get('runs') or followup.get('runs') or []
    runs = [r for r in runs if isinstance(r, dict) and r.get('execution') == 'completed'][:3]
    if not runs:
        return '<p class="tree-note">No completed native tree files were recorded for visualization.</p>'
    artifacts = [p for p in tree.get('artifacts', []) if isinstance(p, str)]
    hashes = _artifact_digests(tree, deep_record)
    labels = {}
    labels_note = 'Reference labels are opaque IDs where a verified mapping is unavailable.'
    labelpath = tree.get('labels')
    if not labelpath:
        matches = [p for p in artifacts if Path(p).name == 'labels.json']
        labelpath = matches[0] if len(matches) == 1 else None
    label_content, label_error = _verified(labelpath, hashes.get(str(labelpath), []))
    if label_content:
        try:
            parsed = json.loads(label_content)
            if isinstance(parsed, dict):
                labels = parsed
                labels_note = 'Reference identities use the checksum-verified label map; review statuses remain source annotations.'
                _export(Path(outdir), 'tree-labels', '.json', label_content)
        except (ValueError, UnicodeError):
            labels_note = 'Recorded label map could not be parsed; opaque reference IDs retained.'
    body = []
    for run in runs:
        name = str(run.get('name') or 'Recorded run')
        treepath, alignmentpath = _run_paths(tree, run, artifacts)
        tree_bytes, tree_error = _verified(treepath, hashes.get(str(treepath), []) + [run.get('tree_sha256')])
        aligned_bytes, alignment_error = _verified(alignmentpath, hashes.get(str(alignmentpath), []) + [run.get('alignment_sha256')])
        if tree_error or alignment_error:
            reasons = '; '.join(dict.fromkeys(v for v in [tree_error, alignment_error] if v))
            body.append('<p class="tree-note">' + _esc(name.replace('_', ' ')) + ': visualization unavailable — ' + _esc(reasons) + '.</p>')
            continue
        try:
            sequences = _parse_alignment(aligned_bytes)
            if query_id not in sequences:
                raise ValueError('recorded query ID absent from alignment')
            expected_query_hash = (candidate.get('query') or {}).get('sequence_sha256')
            if name == 'baseline' and expected_query_hash:
                query_sequence = sequences[query_id].replace('-', '').replace('.', '')
                # The analysis removes one terminal stop marker before MAFFT.
                # The report may verify either source representation, but never
                # rewrites the recorded FASTA or changes its source checksum.
                if expected_query_hash not in {_sha(query_sequence.encode()), _sha((query_sequence + '*').encode())}:
                    raise ValueError('baseline query sequence differs from recorded query checksum')
            root = _parse_tree(tree_bytes, sequences)
            # Embedded mappings are checked against unmasked sequence content.
            verified_labels = _embedded_labels(tree, sequences)
            current_labels = {**verified_labels, **labels}
            svg = _tree_svg(root, current_labels, query_id, candidate.get('protein_id'))
            title = str(candidate.get('protein_id') or query_id) + ' · ' + name.replace('_', ' ')
            alignment_page = _alignment_html(sequences, current_labels, query_id, candidate.get('protein_id'), title)
        except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
            body.append('<p class="tree-note">' + _esc(name.replace('_', ' ')) + ': visualization withheld — ' + _esc(exc) + '.</p>')
            continue
        tree_link = _export(Path(outdir), 'tree', '.nwk', tree_bytes)
        fasta_link = _export(Path(outdir), 'alignment', '.faa', aligned_bytes)
        svg_link = _export(Path(outdir), 'tree-view', '.svg', svg.encode())
        page_link = _export(Path(outdir), 'alignment-view', '.html', alignment_page.encode())
        suffix = ' · ' + str(run.get('selected_model')) if run.get('selected_model') else ''
        body.append('<details class="tree-run"><summary>' + _esc(name.replace('_', ' ').capitalize() + suffix) + ' · tree and alignment</summary><div class="tree-scroll">' + svg + '</div><p class="tree-note">Branch lengths: substitutions per site. Internal labels preserve native branch support. Display orientation does not establish a root; tree placement and support do not establish function.</p><div class="tree-links">' + ''.join('<a class="button" href="' + _esc(path) + '" download>' + _esc(label) + '</a>' for path, label in [(svg_link,'Export tree SVG'), (tree_link,'Checked Newick'), (fasta_link,'Checked alignment FASTA')]) + '</div><details><summary>Explore the reference alignment</summary><p><a href="' + _esc(page_link) + '" target="_blank" rel="noopener">Open alignment viewer</a></p><iframe class="alignment-frame" sandbox="" loading="lazy" title="' + _esc(title + ' alignment') + '" src="' + _esc(page_link) + '"></iframe></details></details>')
    return '<div class="tree-views"><h4>Recorded trees and alignments</h4><p class="tree-note">' + _esc(labels_note) + '</p>' + ''.join(body) + '</div>'
