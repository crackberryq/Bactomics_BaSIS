"""Present recorded DeepSafe routing and stage coverage without running analyses."""
from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from .report_links import candidate_anchor, candidate_section_anchor

SCHEMA = "basis-deep-workflow-v1"


def recorded_workflow(candidate):
    """Return only compatible, candidate-matched saved workflow metadata.

    Legacy records remain unrecorded: source annotations are never used to
    reconstruct family routing or to assert that a stage was attempted.
    """
    value = candidate.get("workflow_coverage")
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        return None
    for key in ("candidate_id", "protein_id"):
        if value.get(key) != candidate.get(key):
            return None
    if candidate.get("sample_id") is not None and value.get("sample_id") is not None and value["sample_id"] != candidate["sample_id"]:
        return None
    if value.get("selected") != candidate.get("selected"):
        return None
    return value


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _values(value):
    return value if isinstance(value, list) else []


def workflow_search_terms(candidate):
    value = recorded_workflow(candidate)
    if value is None:
        return "workflow routing not recorded"
    profile = _mapping(value.get("profile"))
    placement = _mapping(value.get("placement"))
    terms = ["workflow", value.get("routing_status"), value.get("routing_reason"),
             profile.get("id"), profile.get("status"), profile.get("reason"),
             placement.get("panel_id"), placement.get("status"), placement.get("reason"),
             placement.get("route_status"), placement.get("route_reason"),
             placement.get("registry_id"), value.get("followup")]
    terms.extend(_values(profile.get("matched_profiles")))
    terms.extend(_values(placement.get("matched_panels")))
    terms.extend(_values(placement.get("adequacy_reasons")))
    for stage in _values(value.get("stages")):
        if isinstance(stage, dict):
            terms.extend(stage.get(key) for key in ("name", "status", "reason"))
    return " ".join(str(term) for term in terms if term is not None).lower()


def candidate_workflow_details(candidate, parent_status="not_assessed"):
    from .report_dashboard import badge, esc, execution_badge, human, kv, table

    value = recorded_workflow(candidate)
    body = '<div class="workflow-coverage"><h4>Automated workflow coverage</h4>'
    if value is None:
        return body + ('<p class="empty">Routing metadata: not recorded. This saved candidate '
                       'does not contain a compatible workflow coverage record; its existing '
                       'analysis evidence remains available below.</p></div>')
    profile = _mapping(value.get("profile"))
    placement = _mapping(value.get("placement"))
    body += '<p>Recorded routing: ' + badge(value.get("routing_status", "not_recorded"))
    body += ' · ' + esc(human(value.get("routing_reason"))) + '</p>'
    if parent_status != "completed" or candidate.get("execution") != "completed":
        body += ('<p class="callout">Candidate or parent execution is not completed. '
                 'Routing records describe saved workflow state, not successful completion.</p>')
    body += table(['Route', 'Recorded identifier', 'Status', 'Reason'], [
        ('Family profile', esc(profile.get("id") or "not recorded"),
         badge(profile.get("status", "not_recorded")), esc(human(profile.get("reason")))),
        ('Placement panel', esc(placement.get("panel_id") or "not recorded"),
         badge(placement.get("status", "not_recorded")), esc(human(placement.get("reason"))))])
    if placement.get("route_status"):
        body += '<p class="small">Placement routing: ' + badge(placement["route_status"])
        if placement.get("route_reason"):
            body += ' · ' + esc(human(placement["route_reason"]))
        body += '</p>'
    for title, values in (("Matched profiles", profile.get("matched_profiles")),
                          ("Matched panels", placement.get("matched_panels")),
                          ("Recorded panel holds", placement.get("adequacy_reasons"))):
        if _values(values):
            body += '<p class="small"><strong>' + esc(title) + ':</strong> ' + esc(', '.join(map(str, values))) + '</p>'
    stages = [stage for stage in _values(value.get("stages")) if isinstance(stage, dict)]
    if stages:
        body += table(['Workflow stage', 'Effective status', 'Recorded reason'], [
            (esc(human(stage.get("name"))), execution_badge(stage.get("status", "not_recorded"), parent_status, candidate.get("execution")),
             esc(human(stage.get("reason")))) for stage in stages])
    else:
        body += '<p class="empty">Workflow stage coverage: not recorded.</p>'
    if placement.get("registry_id") or placement.get("registry_sha256"):
        body += '<details><summary>Routing registry provenance</summary>' + kv({
            'Registry': placement.get("registry_id") or "not recorded",
            'SHA-256': placement.get("registry_sha256") or "not recorded"}) + '</details>'
    body += ('<p class="small muted">Configured routing coverage is separate from family validation. '
             'Stage completion does not establish protein function.</p></div>')
    return body


def _relative_href(value):
    if not isinstance(value, str) or not value:
        return None
    decoded = unquote(value)
    parsed = urlsplit(decoded)
    if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or
            '\\' in decoded or ':' in decoded or decoded.startswith('/') or
            any(part in {'.', '..'} for part in decoded.split('/')) or
            any(ord(char) < 32 for char in decoded)):
        return None
    return value if PurePosixPath(decoded).parts else None


def render_workflow_overview(selected_pairs, deep_records, exported_files=None):
    """Render all selected routing records and link existing evidence sections."""
    from .report import _execution, _sample_id
    from .report_dashboard import badge, esc, execution_badge, human, link, stat

    selected_pairs = list(selected_pairs)
    parents = {id(candidate): record for record in deep_records
               for candidate in record.get("candidates", []) or []
               if isinstance(candidate, dict) and
               candidate.get("sample_id") in (None, '', _sample_id(record))}
    deferred = sum(isinstance(candidate, dict) and candidate.get("execution") == "deferred" and
                   candidate.get("sample_id") in (None, '', _sample_id(record))
                   for record in deep_records for candidate in record.get("candidates", []) or [])
    recorded = [recorded_workflow(candidate) for _, candidate in selected_pairs]
    states = Counter(value.get("routing_status", "not_recorded") if value else "not_recorded"
                     for value in recorded)
    profiles = {str(_mapping(value.get("profile")).get("id")) for value in recorded
                if value and _mapping(value.get("profile")).get("status") == "matched"
                and _mapping(value.get("profile")).get("id")}
    panels = {str(panel) for value in recorded if value
              and _mapping(value.get("placement")).get("route_status") == "matched"
              for panel in _values(_mapping(value.get("placement")).get("matched_panels"))}
    body = ('<div class="panel" id="deep-workflow"><div class="panel-head"><div>'
            '<h3>Automated family workflow coverage</h3><p class="muted">Recorded routing, '
            'review holds and stage coverage for every selected candidate.</p></div>')
    downloads = []
    for key, label in (("json", "Workflow coverage · JSON"), ("tsv", "Workflow coverage · TSV")):
        href = _relative_href(_mapping(exported_files).get(key))
        if href:
            downloads.append(link(href, label, 'button'))
    body += '<div class="downloads">' + ''.join(downloads) + '</div></div>'
    body += '<div class="mini-stats">' + stat('Routing records', str(sum(value is not None for value in recorded)) + ' / ' + str(len(selected_pairs)), 'selected candidates with saved workflow metadata')
    body += stat('Matched profiles', str(len(profiles)), 'distinct configured profiles matched')
    body += stat('Matched panels', str(len(panels)), 'distinct configured panels matched; analysis may be held') + '</div>'
    inventory_rows = []
    for record in deep_records:
        inventory = _mapping(_mapping(record.get("workflow_coverage")).get("family_inventory"))
        if not inventory:
            continue
        registry = _mapping(inventory.get("placement_registry"))
        inventory_rows.append('<li>' + esc(_sample_id(record)) + ': ' +
                              str(len(_values(inventory.get("profiles")))) + ' configured profile definitions; ' +
                              (str(len(_values(registry.get("panels")))) + ' configured placement panels'
                               if registry else 'placement registry not recorded') + '.</li>')
    if inventory_rows:
        body += '<details><summary>Configured family inventory</summary><ul>' + ''.join(inventory_rows)
        body += '</ul><p class="small muted">Available definitions are separate from matched candidates and independent validation.</p></details>'
    body += '<p class="small">' + ' · '.join(esc(human(key)) + ': ' + str(count) for key, count in sorted(states.items())) + '</p>'
    body += '<p class="small muted">' + str(deferred) + (' deferred candidates remain outside this selected-candidate table. '
             'A configured family or completed stage is not a validated function assignment. '
             'Use the candidate filters below to filter these rows.</p>')
    rows = []
    for sid, candidate in selected_pairs:
        ident = candidate.get("protein_id") or candidate.get("candidate_id") or "Unidentified candidate"
        value = recorded_workflow(candidate)
        profile = _mapping(value.get("profile")) if value else {}
        placement = _mapping(value.get("placement")) if value else {}
        routing = badge(value.get("routing_status", "not_recorded") if value else "not_recorded")
        routing += '<p class="small">' + esc(human(value.get("routing_reason")) if value else 'routing metadata not recorded') + '</p>'
        profile_cell = esc(profile.get("id") or "not recorded") + '<p class="small">' + esc(human(profile.get("status"))) + '</p>'
        panel_cell = esc(placement.get("panel_id") or "not recorded") + '<p class="small">' + esc(human(placement.get("status"))) + '</p>'
        panel_cell += '<p class="small muted">' + esc(human(placement.get("reason"))) + '</p>'
        if placement.get("route_status"):
            panel_cell += '<p class="small">Routing: ' + esc(human(placement["route_status"])) + '</p>'
        if not placement.get("panel_id") and _values(placement.get("matched_panels")):
            panel_cell += '<p class="small muted">Matched: ' + esc(', '.join(map(str, placement["matched_panels"]))) + '</p>'
        parent = parents.get(id(candidate))
        execution = execution_badge(candidate.get("execution", "not_recorded"), _execution(parent or {}))
        if parent is None or _execution(parent) != "completed":
            execution += '<p class="small">Parent execution: ' + esc(human(_execution(parent or {}))) + '</p>'
        if value and value.get("followup"):
            execution += '<p class="small muted">' + esc(human(value["followup"])) + '</p>'
        shortcuts = ' '.join(link('#' + candidate_section_anchor(sid, ident, section), label, 'text-link')
                             for section, label in (("overview", "Coverage"), ("trees", "Tree evidence"),
                                                    ("placement", "Placement evidence")))
        cells = [esc(sid) + '<br>' + link('#' + candidate_anchor(sid, ident), ident, 'text-link'),
                 routing, profile_cell, panel_cell, execution, shortcuts]
        rows.append('<tr data-navigation-candidate="' + candidate_anchor(sid, ident) + '">' +
                    ''.join('<td>' + cell + '</td>' for cell in cells) + '</tr>')
    if rows:
        body += '<div class="table-wrap"><table><thead><tr>' + ''.join('<th scope="col">' + heading + '</th>' for heading in
                 ('Sample / candidate', 'Routing', 'Family profile', 'Placement panel', 'Execution / follow-up', 'Open evidence'))
        body += '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
    else:
        body += '<p class="empty">No selected candidate routing records to display.</p>'
    return body + '</div>'
