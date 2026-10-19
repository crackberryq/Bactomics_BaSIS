"""Offline presentation of recorded reference-panel placement evidence.

This is a renderer, not a classifier. It never turns likelihood weights into
functional probabilities, rescales missing mass, or infers a result from a hold.
"""
from __future__ import annotations

from collections import Counter
from html import escape
import json
import math

from .report_links import candidate_anchor


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _esc(value):
    return escape(str(value if value is not None else ""), quote=True)


def _human(value):
    return str(value or "not_recorded").replace("_", " ")


def _text(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return "Not recorded"
    return str(value)


def _mass(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 <= value <= 1 + 1e-6)


def _valid_scores(scores):
    """Reject malformed presentation data without silently repairing it."""
    groups = scores.get("group_support")
    if not isinstance(groups, dict) or not all(_mass(v) for v in groups.values()):
        return False
    keys = ("assigned_mass", "unresolved_mass", "missing_mass", "reported_mass")
    if not all(_mass(scores.get(k)) for k in keys):
        return False
    assigned, unresolved, missing, reported = (scores[k] for k in keys)
    return (abs(sum(groups.values()) - assigned) <= 1e-6
            and abs(assigned + unresolved - reported) <= 1e-6
            and abs(reported + missing - 1) <= 1e-6)


def _state(candidate, parent_status):
    if parent_status != "completed" or candidate.get("execution") != "completed":
        return "failed" if "failed" in (parent_status, candidate.get("execution")) else "not_assessed"
    layer = _dict(_dict(candidate.get("analyses")).get("family_placement"))
    if not layer:
        return "not_recorded"
    status = layer.get("status")
    return status if status in {"completed", "insufficient_evidence", "failed"} else "not_assessed"


def _table(headers, rows):
    return ('<div class="fp-table-wrap"><table class="fp-table"><thead><tr>'
            + ''.join('<th scope="col">' + _esc(h) + '</th>' for h in headers)
            + '</tr></thead><tbody>'
            + ''.join('<tr>' + ''.join('<td>' + v + '</td>' for v in row) + '</tr>' for row in rows)
            + '</tbody></table></div>')


def _status(state):
    tone = {"completed": "recorded", "failed": "failed", "insufficient_evidence": "hold"}.get(state, "unknown")
    label = {"completed": "Placement completed", "insufficient_evidence": "Evidence hold",
             "not_recorded": "Not recorded", "not_assessed": "Not assessed", "failed": "Execution failed"}.get(state, _human(state))
    return '<span class="fp-status fp-' + tone + '">' + _esc(label) + '</span>'


def _role(role):
    if not isinstance(role, str):
        return "Role not recorded"
    return {"amr": "AMR comparator", "alternative": "Competing function", "context": "Context only"}.get(role, "Role not recorded")


def _distribution(scores):
    groups = scores["group_support"]
    roles = _dict(scores.get("group_roles"))
    rows = []
    for group, value in sorted(groups.items(), key=lambda item: (-item[1], str(item[0]))):
        rows.append((str(group), value, "group", _role(roles.get(group))))
    rows.extend([
        ("Unresolved placement", scores["unresolved_mass"], "unresolved", "Reported mass outside an eligible group interior"),
        ("Unreported mass", scores["missing_mass"], "missing", "Mass omitted from the recorded placements; not redistributed")])
    html = '<div class="fp-distribution">'
    for name, value, tone, note in rows:
        # Clamping only protects the display against accepted floating-point
        # tolerance. The numerical value and underlying evidence are unchanged.
        width = min(1, value) * 100
        html += ('<div class="fp-mass-row"><div><strong>' + _esc(name) + '</strong><small>' + _esc(note)
                 + '</small></div><div class="fp-mass-track" aria-hidden="true"><span class="fp-mass-' + tone
                 + '" style="width:' + f'{width:.6f}' + '%"></span></div><output aria-label="'
                 + _esc(name + ' likelihood-weight mass') + '">' + f'{value:.4f}' + '</output></div>')
    return html + '</div><p class="fp-note">Uncalibrated placement likelihood-weight mass on a 0–1 scale. Missing mass is retained; displayed values are not renormalized.</p>'


DIAGNOSTIC_CATEGORIES = {
    "assigned_group_interior": "Inside a qualified group",
    "competing_group_boundary": "Competing-group boundary",
    "context_branch": "Context branch",
    "artificial_root_edge": "Artificial serialization-root edge",
    "other_unassigned": "Other unassigned location",
}


def diagnostic_summary(diagnostics, scores=None):
    """Validate display fields independently of unchanged functional scores."""
    diagnostics = _dict(diagnostics)
    if not diagnostics:
        return {"status": "not_recorded"}
    if diagnostics.get("status") != "completed":
        return {"status": "failed", "error": diagnostics.get("error") or diagnostics.get("reason") or "Diagnostic calculation did not complete"}
    categories = _dict(diagnostics.get("exclusive_category_mass"))
    original = _dict(diagnostics.get("original_group_support"))
    split_set = _dict(diagnostics.get("split_set_95"))
    valid = (diagnostics.get("schema") == "basis-placement-diagnostics-v1"
             and set(categories) == set(DIAGNOSTIC_CATEGORIES)
             and all(_mass(v) for v in categories.values())
             and all(_mass(v) for v in original.values())
             and abs(sum(original.values())-categories['assigned_group_interior']) <= 1e-6
             and _mass(diagnostics.get("reported_mass")) and _mass(diagnostics.get("unreported_mass"))
             and abs(sum(categories.values()) - diagnostics["reported_mass"]) <= 1e-6
             and abs(diagnostics["reported_mass"] + diagnostics["unreported_mass"] - 1) <= 1e-6
             and (diagnostics.get("max_biological_split_lwr") is None or _mass(diagnostics.get("max_biological_split_lwr")))
             and _mass(diagnostics.get('biological_split_mass'))
             and _mass(diagnostics.get('excluded_artificial_root_mass'))
             and abs(diagnostics['biological_split_mass']+diagnostics['excluded_artificial_root_mass']-diagnostics['reported_mass']) <= 1e-6
             and abs(diagnostics['excluded_artificial_root_mass']-categories['artificial_root_edge']) <= 1e-6
             and type(split_set.get("selected_split_count")) is int and split_set["selected_split_count"] >= 0
             and isinstance(split_set.get('selected_split_ids'),list)
             and all(isinstance(x,str) for x in split_set['selected_split_ids'])
             and len(set(split_set['selected_split_ids'])) == split_set['selected_split_count'] == len(split_set['selected_split_ids'])
             and _mass(split_set.get("cumulative_original_mass"))
             and split_set['cumulative_original_mass'] <= diagnostics['biological_split_mass']+1e-6
             and split_set.get('target_original_mass') == 0.95
             and split_set.get('ties_at_cutoff_included') is True
             and split_set.get("status") in {"reached", "insufficient_retained_mass"})
    if scores is not None:
        scores = _dict(scores)
        valid = (valid and set(original) == set(_dict(scores.get("group_support")))
                 and all(_mass(scores["group_support"].get(k)) and abs(v-scores["group_support"][k]) <= 1e-6 for k,v in original.items())
                 and _mass(scores.get("missing_mass")) and abs(diagnostics["unreported_mass"]-scores["missing_mass"]) <= 1e-6)
    if not valid:
        return {"status": "failed", "error": "Diagnostic fields are missing, inconsistent, or disagree with the original group scores"}
    return {"status": "completed", "reason": diagnostics.get("reason"), "categories": categories,
            "max_split_lwr": diagnostics.get("max_biological_split_lwr"), "split_set": split_set,
            "unreported_mass": diagnostics["unreported_mass"], "original_group_support": original}


def diagnostic_side_text(side, all_groups=None):
    """Readable counts on one unrooted cut side; no ancestry is implied."""
    side = _dict(side)
    counts = _dict(side.get("labelled_group_counts"))
    group_names = sorted(all_groups if all_groups is not None else counts)
    text = "; ".join(str(group).replace('_', ' ') + ": " + str(counts.get(group,0)) for group in group_names)
    context = side.get("context_tip_count")
    return (text + "; " if text else "") + "context: " + str(context if context is not None else "not recorded")


def render_placement_diagnostics(diagnostics, scores=None):
    """Explain location concentration separately from functional assignment."""
    summary = diagnostic_summary(diagnostics, scores)
    if summary["status"] == "not_recorded":
        return '<p class="fp-note">Localization diagnostics were not recorded in this historical evidence. Inspect the verified tree diagnostics below when available; the original placement and group scores remain unchanged.</p>'
    if summary["status"] != "completed":
        return '<p class="fp-note fp-diagnostic-unavailable"><strong>Localization diagnostics unavailable.</strong> ' + _esc(summary.get("error")) + '. Original placement results and decisions are unchanged.</p>'
    categories, split_set = summary["categories"], summary["split_set"]
    max_mass = 'Not available' if summary["max_split_lwr"] is None else f'{summary["max_split_lwr"]:.6g}'
    body = '<section class="fp-diagnostics"><h4>Localization and functional support are different</h4>'
    largest = max(categories.values())
    leading = [DIAGNOSTIC_CATEGORIES[key] for key,value in categories.items() if value == largest]
    body += '<p class="fp-localization-explanation"><strong>'
    if largest > 0:
        body += ('Largest placement-weight categor'+('ies' if len(leading)>1 else 'y')+': '
                 +_esc('; '.join(leading))+f' ({largest:.12g}'+(' each' if len(leading)>1 else '')+').')
    else:
        body += 'No positive reported placement weight.'
    body += '</strong> The overall assignment or abstention is reported separately; this localization summary does not change it.'
    unassigned = {key:value for key,value in categories.items() if key != 'assigned_group_interior'}
    residual = max(unassigned.values())
    if residual > 0:
        remaining = [DIAGNOSTIC_CATEGORIES[key] for key,value in unassigned.items() if value == residual]
        body += '<br>Largest unassigned location categor'+('ies' if len(remaining)>1 else 'y')+': '+_esc('; '.join(remaining))+f' ({residual:.12g}'+(' each' if len(remaining)>1 else '')+').'
    else:
        body += '<br>No reported placement weight lies outside qualified group interiors.'
    body += '</p>'
    if categories["competing_group_boundary"] > 0:
        body += ('<p class="fp-boundary-explanation"><strong>Boundary weight remains unassigned: '
                 +f'{categories["competing_group_boundary"]:.12g}'+'.</strong> '
                 'Only this portion lies on boundaries between comparator groups. Its presence, however small, does not establish that the overall assignment is unresolved. Boundary weight is not reassigned.</p>')
    body += '<div class="fp-diagnostic-cards">'
    for label,value in [('Strongest biological split · LWR',max_mass),
                        ('Inside qualified groups · raw mass',f'{categories["assigned_group_interior"]:.6g}'),
                        ('Competing boundaries · raw mass',f'{categories["competing_group_boundary"]:.6g}')]:
        body += '<div><span>'+_esc(label)+'</span><strong>'+_esc(value)+'</strong></div>'
    body += '</div><p class="fp-note">A canonical split describes an unrooted tree cut. Equivalent serialized edges are combined for localization only; artificial root edges are excluded. The strongest split weight is not functional confidence or branch-support evidence.</p>'
    body += '<h5>Where the original placement weight lies</h5>'+_table(['Exclusive location category','Original weight'],
        [(_esc(label),f'{categories[key]:.6g}') for key,label in DIAGNOSTIC_CATEGORIES.items()]
        + [('Unreported weight · not redistributed',f'{summary["unreported_mass"]:.6g}')])
    count = split_set['selected_split_count']
    body += ('<p class="fp-split-set"><strong>95% placement-weight split set:</strong> '+str(count)+' biological split'+('' if count==1 else 's')
             +' · cumulative original weight '+f'{split_set["cumulative_original_mass"]:.6g}'+' · '
             +('95% reached' if split_set['status']=='reached' else 'insufficient retained mass to reach 95%')+'. '
             'This is a weight summary, not a confidence interval or calibrated probability. Cutoff ties are retained.</p>')
    boundaries = [edge for edge in _list(_dict(diagnostics).get('edges')) if isinstance(edge,dict)
                  and edge.get('category') == 'competing_group_boundary' and _mass(edge.get('likelihood_weight')) and edge['likelihood_weight']>0]
    if boundaries:
        body += '<details class="fp-boundary-details"><summary>Weighted competing boundaries · cut-side reference counts</summary>'+_table(
            ['Edge','Original weight','Side A','Side B'], [(_esc(edge.get('edge_num')),f'{edge["likelihood_weight"]:.6g}',
              _esc(diagnostic_side_text((_list(edge.get('sides'))+[{},{}])[0],summary['original_group_support'])),_esc(diagnostic_side_text((_list(edge.get('sides'))+[{},{}])[1],summary['original_group_support'])))
             for edge in sorted(boundaries,key=lambda e:(-e['likelihood_weight'],str(e.get('edge_num'))))])
        body += '<p class="fp-note">Sides A and B are the two sides of an unrooted cut. These labels do not establish an ancestor, root or direction of evolution. Context counts are unlabelled references.</p></details>'
    body += '<p class="fp-note">Recorded diagnostic label (weight-category description, not an overall decision): '+_esc(_human(summary.get('reason')))+'. Original group support and the recorded assignment or abstention remain unchanged. Functional probability is not estimated.</p>'
    return body+'</section>'


DIAGNOSTIC_CSS = """
.fp-diagnostics{margin:18px 0;padding:18px;border:1px solid #ccdcdf;border-radius:10px;background:#f8fbfc}.fp-diagnostics h4{font-size:17px;margin:0 0 10px}.fp-diagnostics h5{font-size:13px;margin:18px 0 8px}.fp-diagnostics p{font-size:12px;line-height:1.6}.fp-boundary-explanation{padding:12px 14px;background:#fff6e6;border-left:3px solid #bd9341;border-radius:4px;color:#6e582f}.fp-diagnostic-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:14px 0}.fp-diagnostic-cards>div{background:white;border:1px solid #d7e4e8;border-radius:8px;padding:13px}.fp-diagnostic-cards span{display:block;font-size:11px;color:#526c7b}.fp-diagnostic-cards strong{display:block;font-size:22px;color:#20596a;margin-top:4px}.fp-diagnostics .fp-table-wrap{overflow:auto;max-width:100%}.fp-diagnostics .fp-table{width:100%;border-collapse:collapse;font-size:12px}.fp-diagnostics .fp-table th,.fp-diagnostics .fp-table td{padding:9px 11px;border-bottom:1px solid #dbe6ec;vertical-align:top;text-align:left}.fp-diagnostics .fp-table th{background:#edf4f7}.fp-diagnostics summary{font-size:13px;cursor:pointer;font-weight:650}.fp-diagnostics .fp-note,.fp-diagnostic-unavailable{color:#526c7b;font-size:12px}.fp-boundary-details{margin:14px 0}.fp-split-set{border-top:1px solid #dbe6ec;padding-top:12px}.fp-localization-summary{font-size:11px;color:#536d7c;line-height:1.55;margin-top:8px}.fp-localization-summary strong{font-weight:650}@media(max-width:620px){.fp-diagnostic-cards{grid-template-columns:1fr}.fp-diagnostics{padding:13px}}
"""
DIAGNOSTIC_CSS += '.fp-localization-explanation{padding:12px 14px;background:#edf5f8;border-left:3px solid #608f9d;border-radius:4px;color:#355866}.fp-boundary-explanation{background:#f4f7f9;border-left-color:#91a8b3;color:#46606f}'


def _recorded_panel(layer):
    panel = _dict(layer.get("panel"))
    if panel:
        return panel
    # The native adapter exposes flat audit fields. The normal pipeline wraps
    # these in panel, but old/direct native imports may retain the flat form.
    return {"panel_id": layer.get("panel_id"), "panel_sha256": layer.get("panel_sha256"),
            "adequacy": _dict(layer.get("panel_adequacy")),
            "provenance": _dict(layer.get("panel_provenance"))}


def _panel_context(layer):
    panel = _recorded_panel(layer)
    definition = _dict(_dict(panel.get("provenance")).get("panel_definition"))
    scope = definition.get("interpretation_scope")
    notes = _list(definition.get("interpretation_notes")) + _list(definition.get("limitations"))
    body = '<div class="fp-scope"><strong>Reference interpretation</strong>'
    body += ('<p>Competing-function references are not universal negative controls for resistance. '
             'Context-only references are not labelled functional anchors.</p>')
    if definition.get("display_name"):
        body += '<p><strong>' + _esc(definition["display_name"]) + '</strong></p>'
    if scope:
        body += '<p>' + _esc(_text(scope)) + '</p>'
    if notes:
        body += '<ul>' + ''.join('<li>' + _esc(_text(note)) + '</li>' for note in notes) + '</ul>'
    return body + '</div>'


def _member_details(panel):
    members = [m for m in _list(panel.get("members")) if isinstance(m, dict)]
    if not members:
        return ''
    rows = []
    for member in members:
        accessions = _list(member.get("accessions"))
        identifiers = '<br>'.join(_esc(a) for a in accessions) or _esc(member.get("tip_id") or "Not recorded")
        if len(accessions) > 1:
            identifiers += '<small>' + str(len(accessions)) + ' accession records · one unique sequence</small>'
        group = _esc(member.get("group")) if member.get("group") is not None else 'No assigned functional group'
        role = _role(member.get("role"))
        if member.get("label_conflict") is True:
            role = 'Label conflict · excluded from labelled groups'
        evidence_rows = []
        for evidence in _list(member.get("evidence")):
            if not isinstance(evidence, dict):
                continue
            qualified = evidence.get("qualifies_as_functional_anchor")
            anchor = 'Yes' if qualified is True else ('No' if qualified is False else 'Not recorded')
            item = ('<strong>' + _esc(evidence.get("accession") or evidence.get("reference_id") or 'Recorded source')
                    + '</strong><br>' + _esc(_human(evidence.get("evidence_basis")))
                    + ' · source-qualified anchor: ' + anchor)
            for key in ("evidence_notes", "identity_link"):
                if evidence.get(key):
                    item += '<p>' + _esc(_text(evidence[key])) + '</p>'
            pubmed = _list(evidence.get("pubmed_ids"))
            if pubmed:
                item += '<small>PubMed: ' + _esc(', '.join(map(str, pubmed))) + '</small>'
            evidence_rows.append(item)
        rows.append((identifiers, group, _esc(role), '<hr>'.join(evidence_rows) or 'No per-reference evidence recorded'))
    return ('<details class="fp-panel-audit"><summary>Reference evidence and member-specific caveats · '
            + str(len(members)) + ' unique sequences</summary>'
            + '<p class="fp-note">Source-qualified anchors retain the scope of their published experiments. '
              'A result in one tested host does not establish activity or phenotype in another host.</p>'
            + _table(["Accessions", "Recorded group", "Panel role", "Evidence and caveats"], rows)
            + '</details>')


def _panel_details(layer, scores):
    panel = _recorded_panel(layer)
    adequacy = _dict(panel.get("adequacy")) or _dict(layer.get("adequacy"))
    provenance = _dict(panel.get("provenance"))
    rows = []
    for label, value in [("Panel ID", panel.get("panel_id") or layer.get("panel_id")),
                         ("Panel SHA256", panel.get("panel_sha256") or layer.get("panel_sha256")),
                         ("Recorded scope", layer.get("scope"))]:
        if value is not None:
            rows.append((_esc(label), '<span class="fp-wrap">' + _esc(_text(value)) + '</span>'))
    for key, value in adequacy.items():
        rows.append((_esc("Adequacy · " + _human(key)), _esc(_text(value))))
    for key, value in provenance.items():
        if key == "panel_definition":
            definition = _dict(value)
            for name in ("validation_status", "calibration_status", "min_anchors_per_group"):
                if name in definition:
                    rows.append((_esc("Panel definition · " + _human(name)), _esc(_text(definition[name]))))
            continue
        rows.append((_esc("Provenance · " + _human(key)), '<span class="fp-wrap">' + _esc(_text(value)) + '</span>'))
    groups = _dict(scores.get("reference_group_counts"))
    if groups:
        for group, count in groups.items():
            rows.append((_esc("Reference group · " + str(group)), _esc(_text(count))))
    if not rows:
        return '<p class="fp-note">Reference-panel adequacy and provenance were not recorded in this candidate summary. Inspect the complete evidence export.</p>'
    return ('<details class="fp-panel-audit"><summary>Reference-panel adequacy and provenance</summary>'
            + _table(["Recorded field", "Evidence"], rows)
            + '<p class="fp-note">A source-reviewed panel is not a validated functional classifier. Identical reference sequences are not independent examples.</p></details>')


def candidate_placement_details(candidate, parent_status="completed"):
    """Render explicit holds or recorded placement mass for one candidate."""
    head = '<section class="fp-candidate"><h4>Reference-panel phylogenetic placement</h4>'
    layer = _dict(_dict(candidate.get("analyses")).get("family_placement"))
    state = _state(candidate, parent_status)
    body = '<p>' + _status(state) + '</p>'
    if parent_status != "completed" or candidate.get("execution") != "completed":
        return (head + body + '<p class="fp-note">This candidate or its parent DeepSafe record did not complete. '
                'Retained placement results are provisional; score display is withheld.</p></section>')
    if not layer:
        return (head + body + '<p class="fp-note">No family-placement analysis is recorded in this evidence bundle. '
                'An earlier exploratory tree does not imply that this separate placement analysis ran.</p></section>')
    body += '<p class="fp-reason">' + _esc(_human(layer.get("reason"))) + '</p>'
    scores = _dict(layer.get("scores"))
    if _recorded_panel(layer).get("panel_id"):
        body += _panel_context(layer)
    if state == "completed":
        if _valid_scores(scores):
            body += ('<p class="fp-scope"><strong>Relationship to the supplied comparator panel.</strong> '
                     'These weights describe possible locations on the reference tree. They are not the probability '
                     'that this protein confers resistance, or that the isolate is resistant.</p>')
            body += _distribution(scores)
            body += render_placement_diagnostics(layer.get('diagnostics'), scores)
            assessment = scores.get("confidence_assessment")
            if assessment is not None:
                body += '<p><strong>Recorded placement assessment:</strong> ' + _esc(_human(_text(assessment))) + '</p>'
        else:
            body += ('<p class="fp-note">Placement execution is recorded as completed, but the score fields are missing or inconsistent. '
                     'The distribution is withheld; inspect the complete candidate evidence.</p>')
    elif state == "insufficient_evidence":
        body += ('<p class="fp-note">Assessment was withheld because the available panel or query did not satisfy the recorded evidence requirements. '
                 'This is neither a resistance call nor evidence of absence.</p>')
    elif state == "failed":
        body += '<p class="fp-note">The requested placement analysis failed. Any retained scores are not presented as completed results.</p>'
    else:
        body += '<p class="fp-note">This analysis was not assessed in the recorded run. No placement result is inferred.</p>'
    body += '<p class="fp-probability"><strong>Functional probability:</strong> Not estimated · no calibrated functional model applied.</p>'
    if scores.get("function_probability") is not None or layer.get("function_probability") is not None:
        body += '<p class="fp-note">A numerical functional-probability field is present in the source, but this uncalibrated placement view does not interpret or display it.</p>'
    body += _panel_details(layer, scores)
    body += _member_details(_recorded_panel(layer))
    warnings = _list(scores.get("warnings")) + _list(layer.get("warnings"))
    if warnings:
        body += '<details class="fp-panel-audit"><summary>Recorded placement limitations</summary><ul>'
        body += ''.join('<li>' + _esc(_text(w)) + '</li>' for w in warnings)
        body += '</ul></details>'
    return head + body + '</section>'


def placement_search_terms(candidate):
    layer = _dict(_dict(candidate.get("analyses")).get("family_placement"))
    if not layer:
        return ""
    panel = _dict(layer.get("panel"))
    terms = ["placement", "reference panel", str(layer.get("status") or ""), str(layer.get("reason") or ""),
             str(panel.get("panel_id") or layer.get("panel_id") or "")]
    terms.extend(map(str, _dict(_dict(layer.get("scores")).get("group_support"))))
    return ' '.join(terms).lower()


def render_placement_overview(selected_pairs, deep_records, tree_links=None):
    """Summarize execution and holds without promoting stale retained scores."""
    parents = {}
    parent_keys = {}
    for record in deep_records:
        if not isinstance(record, dict):
            continue
        sid = str(record.get("sample_id") or "")
        for candidate in _list(record.get("candidates")):
            if not isinstance(candidate, dict) or candidate.get("sample_id") not in (None, "", sid):
                continue
            parents[id(candidate)] = record.get("execution_status")
            key = (sid, str(candidate.get("candidate_id") or ""), str(candidate.get("protein_id") or ""))
            parent_keys.setdefault(key, set()).add(record.get("execution_status"))
    pairs = [(str(sid), c) for sid, c in selected_pairs if isinstance(c, dict) and c.get("selected") is not False
             and c.get("sample_id") in (None, "", str(sid))]
    counts = Counter()
    rows = []
    for sid, candidate in pairs:
        parent = parents.get(id(candidate))
        if parent is None:
            key = (sid, str(candidate.get("candidate_id") or ""), str(candidate.get("protein_id") or ""))
            options = parent_keys.get(key, set())
            parent = next(iter(options)) if len(options) == 1 else "not_assessed"
        state = _state(candidate, parent)
        counts[state] += 1
        layer = _dict(_dict(candidate.get("analyses")).get("family_placement"))
        if not layer:
            continue
        protein = str(candidate.get("protein_id") or candidate.get("candidate_id") or "Unmapped protein")
        scores = _dict(layer.get("scores"))
        score = '—'
        if state == "completed" and _valid_scores(scores):
            best = scores.get("best_group")
            if isinstance(best, str) and best in scores["group_support"]:
                score = _esc(best) + ' · ' + f'{scores["group_support"][best]:.4f}'
            elif scores["assigned_mass"] == 0:
                score = 'No mass assigned to a group'
        reason = _esc(_human(layer.get("reason")))
        href = (tree_links or {}).get((sid, str(candidate.get('candidate_id') or ''), str(candidate.get('protein_id') or '')))
        if state == 'completed' and href and isinstance(href, str) and not any(v in href for v in (':', '..', '\\')) and not href.startswith('/'):
            reason += '<br><a class="button fp-tree-shortcut" href="' + _esc(href) + '" target="_blank" rel="noopener">Open placement tree ↗</a>'
        rows.append((_esc(sid), '<a href="#' + _esc(candidate_anchor(sid, protein)) + '">' + _esc(protein) + '</a>',
                     _status(state), score, reason))
    body = ('<article class="fp-overview"><p class="eyebrow">REFERENCE-PANEL PLACEMENT</p><h3>Family relationships and evidence holds</h3>'
            '<p>Placement compares a query with characterized AMR and competing-function references. '
            'Scores describe tree placement within the supplied panel; they are not resistance probabilities.</p><div class="fp-stats">')
    for label, key in [("Completed", "completed"), ("Evidence holds", "insufficient_evidence"), ("Failed", "failed"),
                       ("Not assessed", "not_assessed"), ("Not recorded", "not_recorded")]:
        body += '<div><strong>' + str(counts[key]) + '</strong><span>' + label + '</span></div>'
    body += '</div>'
    if rows:
        body += _table(["Sample", "Protein", "Placement state", "Leading group · raw mass", "Recorded reason"], rows[:40])
        if len(rows) > 40:
            body += '<p class="fp-note">First 40 candidate records shown. Search “placement” below to inspect all recorded candidate analyses.</p>'
    else:
        body += '<p class="fp-note">No family-placement records are available for this selected scope. Existing trees and motif scans remain separate evidence layers.</p>'
    body += '<p class="fp-note">Open a protein to inspect unresolved and unreported mass, reference-panel adequacy and provenance. An evidence hold is an explicit abstention, not a negative result.</p>'
    return body + '</article>'


CSS = r"""
.fp-overview { border:1px solid #cfd8ed; border-radius:12px; background:#f7f9fe; padding:22px; margin:0 0 22px; }
.fp-overview h3 { margin:0 0 8px; }
.fp-tree-shortcut { margin-top:10px; white-space:nowrap; }
.fp-stats { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:8px; margin:18px 0; }
.fp-stats>div { padding:12px; background:#fff; border:1px solid #dce3ee; border-radius:8px; }
.fp-stats strong { display:block; font-size:23px; color:#294e78; }
.fp-stats span { display:block; margin-top:4px; font-size:11px; color:#53657b; }
.fp-candidate { border-top:1px solid #dce3e8; margin-top:22px; padding-top:5px; }
.fp-status { display:inline-block; padding:4px 8px; border-radius:5px; font-size:11px; font-weight:650; background:#e8edf3; color:#43566b; }
.fp-recorded { background:#e5ecfb; color:#264879; } .fp-hold { background:#f6ecd8; color:#795716; }
.fp-failed { background:#fbe8e6; color:#903731; }
.fp-scope,.fp-probability { background:#f1f5fb; border-left:3px solid #6485b3; padding:12px 14px; border-radius:4px; }
.fp-distribution { margin:18px 0 8px; padding:6px 0; }
.fp-mass-row { display:grid; grid-template-columns:minmax(140px,1fr) minmax(90px,1.2fr) 64px; align-items:center; gap:16px; margin:13px 0; font-size:12px; }
.fp-mass-row small { display:block; color:#68778a; font-size:10px; margin-top:4px; line-height:1.45; }
.fp-mass-row output { font-variant-numeric:tabular-nums; color:#24476a; text-align:right; }
.fp-mass-track { height:12px; border:1px solid #dce4ef; background:#edf1f7; border-radius:5px; overflow:hidden; }
.fp-mass-track span { display:block; height:100%; min-width:0; background:#6384ba; }
.fp-mass-track .fp-mass-unresolved { background:#96a1af; }
.fp-mass-track .fp-mass-missing { background:repeating-linear-gradient(45deg,#bbc4cf 0px,#bbc4cf 3px,#e7ecf2 3px,#e7ecf2 6px); }
.fp-note { color:#5e6c79; font-size:11px; line-height:1.65; }
.fp-reason,.fp-wrap { overflow-wrap:anywhere; }
.fp-panel-audit { border:1px solid #dce3ed; border-radius:7px; margin:12px 0; padding:12px; font-size:12px; }
.fp-panel-audit summary { cursor:pointer; color:#294e78; font-weight:600; }
.fp-panel-audit .fp-table-wrap { margin-top:12px; }
.fp-panel-audit td small { display:block; margin-top:7px; color:#68778a; font-size:10px; }
.fp-panel-audit td p { margin:7px 0; } .fp-panel-audit td hr { border:0; border-top:1px solid #dce3ed; margin:12px 0; }
.fp-scope p { margin:8px 0; } .fp-scope ul { padding-left:20px; font-size:11px; line-height:1.6; }
.fp-table-wrap { overflow:auto; max-width:100%; border:1px solid #dce3e8; border-radius:7px; }
.fp-table { border-collapse:collapse; width:100%; min-width:580px; font-size:12px; }
.fp-table th,.fp-table td { padding:10px 12px; text-align:left; vertical-align:top; border-bottom:1px solid #e3eaf0; overflow-wrap:anywhere; }
.fp-table th { color:#43566b; background:#edf2f8; font-size:10px; }
@media(max-width:650px) { .fp-stats { grid-template-columns:repeat(3,minmax(0,1fr)); } .fp-mass-row { gap:8px; grid-template-columns:minmax(110px,1fr) minmax(60px,1fr) 52px; } }
@media print { .fp-table-wrap { overflow:visible; } .fp-table { min-width:0; font-size:9px; } .fp-table th,.fp-table td { padding:5px; } }
"""
CSS += DIAGNOSTIC_CSS
