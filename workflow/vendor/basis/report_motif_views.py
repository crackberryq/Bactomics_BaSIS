"""Readable, source-backed motif results for the offline DeepSafe report.

This module renders recorded observations. It does not scan sequences, infer
function, or treat correlated domain/profile/motif calls as independent votes.
"""
from collections import Counter
from html import escape

from .report_links import candidate_anchor


LAYERS = (("motifs", "Custom motif rules"), ("prosite_patterns", "PROSITE patterns"),
          ("prosite_profiles", "PROSITE profiles"))


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _esc(value):
    return escape(str(value if value is not None else ""), quote=True)


def _human(value):
    return str(value or "not recorded").replace("_", " ")


def _rule_status(rule):
    return str(rule.get("status") or rule.get("interpretation") or "not_recorded")


def _observation(candidate, key, parent_status):
    if parent_status != "completed" or candidate.get("execution") != "completed":
        return "failed" if "failed" in (parent_status, candidate.get("execution")) else "not_assessed"
    layer = _dict(_dict(candidate.get("analyses")).get(key))
    status = layer.get("status")
    if status != "completed":
        return status if status in {"no_applicable_rule", "failed"} else "not_assessed"
    if key == "motifs":
        rules = [r for r in _list(layer.get("rules")) if isinstance(r, dict)]
        if any(_rule_status(r) == "present" for r in rules):
            return "matched"
        if rules and all(_rule_status(r) in {"absent", "not_detected"} for r in rules):
            return "no_match"
        return "completed_unknown"
    if isinstance(layer.get("hits"), list):
        return "matched" if layer["hits"] else "no_match"
    count = layer.get("hit_count")
    if isinstance(count, (int, float)) and not isinstance(count, bool) and count >= 0:
        return "matched" if count > 0 else "no_match"
    return "completed_unknown"


def _table(headers, rows):
    return ('<div class="mv-table-wrap"><table class="mv-table"><thead><tr>'
            + ''.join('<th scope="col">' + _esc(h) + '</th>' for h in headers)
            + '</tr></thead><tbody>'
            + ''.join('<tr>' + ''.join('<td>' + cell + '</td>' for cell in row) + '</tr>' for row in rows)
            + '</tbody></table></div>')


def _span(match):
    start, end = match.get("start"), match.get("end")
    if (isinstance(start, int) and not isinstance(start, bool)
            and isinstance(end, int) and not isinstance(end, bool) and 1 <= start <= end):
        return f"{start}–{end}"
    return "Not recorded"


def candidate_motif_details(candidate, parent_status="completed"):
    """Show custom-rule observations and compact PROSITE results for a protein."""
    head = '<section class="mv-candidate"><h4>Motif and pattern results</h4>'
    if parent_status != "completed" or candidate.get("execution") != "completed":
        return (head + '<p class="mv-note">This candidate or its parent DeepSafe record did not complete. '
                'Any retained motif observations are provisional; completed-result claims are withheld here. '
                'Inspect the complete candidate evidence for the recorded execution details.</p></section>')
    analyses = _dict(candidate.get("analyses"))
    custom = _dict(analyses.get("motifs"))
    status = custom.get("status")
    body = '<h5>Custom motif rules</h5>'
    if status == "no_applicable_rule":
        body += ('<p>No applicable custom rule in this run’s registry. This does not mean that the protein lacks motifs. '
                 'The separate PROSITE pattern and profile results are shown below.</p>')
    elif status != "completed":
        body += ('<p>Custom motif evaluation: <strong>' + _esc(_human(status or "not_assessed"))
                 + '</strong>. ' + _esc(_human(custom.get("reason"))) + '.</p>')
    else:
        rules = [r for r in _list(custom.get("rules")) if isinstance(r, dict)]
        rows = []
        for rule in rules:
            name = rule.get("name") or _human(rule.get("rule_id"))
            label = '<strong>' + _esc(name) + '</strong>'
            if rule.get("rule_id"):
                label += '<small>' + _esc(rule["rule_id"]) + '</small>'
            pattern = '<code>' + _esc(rule["pattern"]) + '</code>' if rule.get("pattern") else 'Not recorded'
            rule_status = _rule_status(rule)
            matches = [m for m in _list(rule.get("matches")) if isinstance(m, dict)]
            if matches:
                for match in matches:
                    sequence = '<code>' + _esc(match["sequence"]) + '</code>' if match.get("sequence") else 'Not recorded'
                    rows.append((label, pattern, sequence, _esc(_span(match)), _esc(_human(rule_status))))
            else:
                no_match = 'No match recorded' if rule_status in {"absent", "not_detected"} else 'Not recorded'
                rows.append((label, pattern, no_match, '—', _esc(_human(rule_status))))
        if rows:
            body += _table(["Recorded rule", "Pattern", "Matched sequence", "Positions (aa)", "Observation"], rows)
            body += '<p class="mv-small">Positions are 1-based inclusive query coordinates. A rule reported as present is a sequence observation.</p>'
        else:
            body += '<p>Custom motif evaluation completed, but no per-rule observations are recorded in this evidence bundle.</p>'
        for rule in rules:
            limits = [str(v) for v in _list(rule.get("limitations")) if v]
            basis = rule.get("evidence_basis")
            if basis or limits:
                body += '<div class="mv-rule-context"><strong>' + _esc(rule.get("name") or _human(rule.get("rule_id"))) + ' · interpretation context</strong>'
                if basis:
                    body += '<p>' + _esc(basis) + '</p>'
                if limits:
                    body += '<ul>' + ''.join('<li>' + _esc(v) + '</li>' for v in limits) + '</ul>'
                body += '</div>'
    body += '<h5>Broad PROSITE scans</h5>'
    rows = []
    for key, label in LAYERS[1:]:
        layer = _dict(analyses.get(key))
        observation = _observation(candidate, key, parent_status)
        if observation in {"matched", "no_match", "completed_unknown"}:
            hits = layer.get("hits")
            count = len(hits) if isinstance(hits, list) else layer.get("hit_count")
            if observation == "no_match":
                description = 'No match detected in the recorded scan'
            elif observation == "matched":
                description = str(count) + ' recorded match' + ('' if count == 1 else 'es')
            else:
                description = 'Match count not recorded'
            matches = []
            for hit in _list(hits):
                if not isinstance(hit, dict):
                    continue
                name = ' '.join(str(v) for v in [hit.get("accession"), hit.get("name") or hit.get("profile_name")] if v)
                match_text = _esc(name or 'Unnamed match') + ' · ' + _esc(_span(hit)) + ' aa'
                missing = sorted({str(v) for v in _list(hit.get('unavailable_rule_ids')) if v}
                    | {str(rule.get('rule_id')) for rule in _list(hit.get('residue_rules'))
                       if isinstance(rule, dict) and rule.get('status') == 'rule_unavailable' and rule.get('rule_id')})
                if missing:
                    match_text += ('<br><span class="mv-note">Residue annotations not assessed for unavailable '
                                   'release rules: ' + _esc(', '.join(missing)) + '.</span>')
                matches.append(match_text)
            rows.append((_esc(label), 'Completed', _esc(description), '<br>'.join(matches) or '—'))
        else:
            rows.append((_esc(label), _esc(_human(layer.get("status") or "not_assessed")),
                         _esc(_human(layer.get("reason"))), '—'))
    body += _table(["Scan", "Execution", "Result", "Recorded matches / positions"], rows)
    body += ('<p class="mv-note">A zero-match PROSITE pattern scan does not negate a custom motif or profile match. '
             'These analyses use different representations and thresholds. Domain, motif and profile observations can '
             'overlap and are not independent confirmations of function. Motif presence alone does not establish '
             'resistance, virulence or organism safety.</p>')
    return head + body + '</section>'


def motif_search_terms(candidate):
    """Bounded semantic search terms, with no complete protein sequences or raw payloads."""
    terms = ['motif', 'motifs', 'pattern', 'patterns', 'profile', 'profiles', 'PROSITE', 'sequence']
    # Source aliases are searchable annotations; this does not promote them to function.
    for key in ('gene', 'family'):
        if candidate.get(key):
            terms.append(str(candidate[key]))
    terms.extend(str(v) for v in _list(candidate.get("family_labels")) if v)
    analyses = _dict(candidate.get("analyses"))
    for rule in _list(_dict(analyses.get("motifs")).get("rules")):
        if not isinstance(rule, dict):
            continue
        terms.extend(str(rule[key]) for key in ('rule_id', 'name', 'pattern', 'status') if rule.get(key))
        for match in _list(rule.get("matches")):
            if isinstance(match, dict) and match.get("sequence"):
                terms.append(str(match["sequence"]))
    for key in ('prosite_patterns', 'prosite_profiles'):
        for hit in _list(_dict(analyses.get(key)).get("hits")):
            if isinstance(hit, dict):
                terms.extend(str(hit[k]) for k in ('accession', 'name', 'profile_name') if hit.get(k))
    return ' '.join(dict.fromkeys(terms)).lower()


def render_motif_overview(selected_pairs, deep_records):
    """Summarize selected-protein scan coverage with explicit custom-match links."""
    parents = {}
    parent_keys = {}
    for record in deep_records:
        if not isinstance(record, dict):
            continue
        sid = str(record.get('sample_id') or '')
        for candidate in _list(record.get('candidates')):
            if not isinstance(candidate, dict) or candidate.get('sample_id') not in (None, '', sid):
                continue
            parents[id(candidate)] = record.get('execution_status')
            key = (sid, str(candidate.get('candidate_id') or ''), str(candidate.get('protein_id') or ''))
            parent_keys.setdefault(key, set()).add(record.get('execution_status'))
    pairs = [(str(sid), c) for sid, c in selected_pairs if isinstance(c, dict) and c.get('selected') is not False]
    counts = {key: Counter() for key, _ in LAYERS}
    matches = {}
    for sid, candidate in pairs:
        status = parents.get(id(candidate))
        if status is None:
            key = (sid, str(candidate.get('candidate_id') or ''), str(candidate.get('protein_id') or ''))
            options = parent_keys.get(key, set())
            status = next(iter(options)) if len(options) == 1 else 'not_assessed'
        for key, _ in LAYERS:
            counts[key][_observation(candidate, key, status)] += 1
        if _observation(candidate, 'motifs', status) == 'matched':
            pid = str(candidate.get('protein_id') or candidate.get('candidate_id') or 'Unmapped protein')
            names = [str(r.get('name') or _human(r.get('rule_id'))) for r in
                     _list(_dict(_dict(candidate.get('analyses')).get('motifs')).get('rules'))
                     if isinstance(r, dict) and _rule_status(r) == 'present']
            matches[(sid, pid)] = names
    rows = []
    unknown = 0
    for key, label in LAYERS:
        count = counts[key]
        complete = sum(count[state] for state in ('matched', 'no_match', 'completed_unknown'))
        unknown += count['completed_unknown']
        rows.append((_esc(label), str(complete), str(count['matched']), str(count['no_match']),
                     str(count['no_applicable_rule']), str(count['not_assessed']), str(count['failed'])))
    body = ('<article class="mv-overview"><h3>Motif and profile coverage</h3>'
            '<p>These checks are part of DeepSafe. Counts below describe selected proteins, not numbers of motifs or hazards.</p>'
            + _table(['Analysis', 'Completed', 'With matches', 'No match', 'No applicable rule', 'Not assessed', 'Failed'], rows))
    if unknown:
        body += '<p class="mv-small">' + str(unknown) + ' completed analysis records have no conclusive match count recorded.</p>'
    body += ('<p class="mv-small">Custom rules are evaluated only when applicable; broad PROSITE pattern and profile scans '
             'are separate layers. “No applicable rule” does not mean that no motif exists.</p>')
    if matches:
        body += '<h4>Proteins with recorded custom motif matches</h4><ul class="mv-match-links">'
        for (sid, protein), names in list(matches.items())[:20]:
            body += ('<li><a href="#' + _esc(candidate_anchor(sid, protein)) + '">' + _esc(protein) + '</a> · '
                     + _esc(sid) + ' · ' + _esc('; '.join(dict.fromkeys(names))) + '</li>')
        body += '</ul>'
        if len(matches) > 20:
            body += '<p class="mv-small">First 20 matched proteins linked here; search “motif” in the candidate list to inspect all selected results.</p>'
    else:
        body += '<p>No completed custom-motif matches are recorded for the selected scope.</p>'
    return body + '</article>'


CSS = r"""
.mv-overview { border:1px solid #c8dedb; border-radius:12px; background:#f5fbf9; padding:22px; margin:0 0 22px; }
.mv-overview h3 { margin:0 0 8px; }
.mv-candidate { border-top:1px solid #dce3e8; margin-top:20px; padding-top:5px; }
.mv-candidate h5 { font-size:12px; margin:16px 0 9px; color:#23465d; }
.mv-table-wrap { overflow:auto; max-width:100%; border:1px solid #dce3e8; border-radius:7px; }
.mv-table { border-collapse:collapse; width:100%; min-width:580px; font-size:12px; }
.mv-table th,.mv-table td { padding:10px 12px; text-align:left; vertical-align:top; border-bottom:1px solid #e3eaf0; }
.mv-table th { color:#43566b; background:#edf3f5; font-size:10px; }
.mv-table td small { display:block; color:#657684; font-size:10px; overflow-wrap:anywhere; margin-top:5px; }
.mv-table code { font-size:11px; color:#24476a; white-space:normal; overflow-wrap:anywhere; }
.mv-rule-context { background:#f5f7fa; padding:13px 15px; margin:12px 0; border-left:3px solid #8497ad; border-radius:4px; font-size:11px; }
.mv-rule-context p { margin:5px 0; }
.mv-rule-context ul { margin:7px 0 0; padding-left:18px; }
.mv-small,.mv-note { color:#5e6c79; font-size:11px; line-height:1.65; }
.mv-note { padding:12px 0 0; }
.mv-match-links { margin:8px 0; padding-left:20px; font-size:12px; }
.mv-match-links li { margin:6px 0; }
@media print { .mv-table-wrap { overflow:visible; } .mv-table { min-width:0; font-size:9px; } .mv-table th,.mv-table td { padding:5px; } }
"""
