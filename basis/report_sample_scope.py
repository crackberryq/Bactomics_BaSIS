"""Present recorded input provenance and per-sample scheduling decisions.

This layer reports saved decisions. It neither infers taxonomy nor changes a
module's applicability, execution state or scientific interpretation.
"""
from __future__ import annotations

from collections import defaultdict
from html import escape


SCOPE_COLUMNS = (
    "sample_id", "input_mode", "taxon", "taxon_rank", "taxon_source",
    "taxon_phylum", "module", "planned_action", "requested",
    "scope_incomplete", "reason", "declared_reason", "warnings",
    "execution_status", "decision_source",
)


def _text(value, default="Not recorded"):
    return str(value) if value not in (None, "") else default


def _esc(value, default="Not recorded"):
    return escape(_text(value, default), quote=True)


def _identity(record):
    return (str(record.get("sample_id", record.get("sample", "unknown"))),
            str(record.get("module", record.get("tool", "unspecified"))))


def _execution(record):
    value = record.get("execution_status", record.get("execution", record.get("status", "unknown")))
    if isinstance(value, dict):
        value = value.get("status", "unknown")
    return str(value)


def _decision(record, run):
    sid, module = _identity(record)
    decisions = run.get("module_decisions") or {}
    sample_decisions = decisions.get(sid) if isinstance(decisions, dict) else None
    decision = sample_decisions.get(module) if isinstance(sample_decisions, dict) else None
    if isinstance(decision, dict):
        return decision, "run.module_decisions"
    decision = record.get("module_decision")
    if isinstance(decision, dict):
        return decision, "record.module_decision"
    return {}, "not_recorded"


def record_requested(record, run):
    """Respect explicit per-sample decisions, retaining the legacy fallback."""
    decision, _ = _decision(record, run)
    if isinstance(decision.get("requested"), bool):
        return decision["requested"]
    enabled = set(run.get("enabled_modules") or [])
    module = _identity(record)[1]
    return ((record.get("effective_config") or {}).get("enabled") is True
            or module in enabled
            or (module in {"deep", "deepsafe"} and bool((run.get("options") or {}).get("deep")))
            or _execution(record) != "not_assessed")


def module_scope_rows(run, samples, records):
    """Create export rows without synthesizing missing planning metadata."""
    sample_map = {str(sample.get("sample_id", "unknown")): sample for sample in samples}
    by_key = defaultdict(list)
    for record in records:
        by_key[_identity(record)].append(record)
    decisions = run.get("module_decisions") or {}
    if isinstance(decisions, dict):
        for sid, modules in decisions.items():
            if isinstance(modules, dict):
                for module in modules:
                    by_key.setdefault((str(sid), str(module)), [])
    rows = []
    for (sid, module), matching in sorted(by_key.items()):
        record = matching[0] if matching else {"sample_id": sid, "module": module}
        decision, source = _decision(record, run)
        warnings = decision.get("warnings") or []
        if not isinstance(warnings, list):
            warnings = [warnings]
        warnings = list(warnings)
        if len(matching) > 1:
            warnings.append("Multiple execution records are present; inspect source evidence for their individual provenance.")
        sample = sample_map.get(sid, {})
        rows.append({
            "sample_id": sid,
            "input_mode": sample.get("input_mode") or "not_recorded",
            "taxon": sample.get("taxon") or "",
            "taxon_rank": sample.get("taxon_rank") or "",
            "taxon_source": sample.get("taxon_source") or "",
            "taxon_phylum": sample.get("taxon_phylum") or "",
            "module": module,
            "planned_action": decision.get("action") or "not_recorded",
            "requested": decision.get("requested") if isinstance(decision.get("requested"), bool) else None,
            "scope_incomplete": decision.get("scope_incomplete") if isinstance(decision.get("scope_incomplete"), bool) else None,
            "reason": decision.get("reason") or "",
            "declared_reason": decision.get("declared_reason") or "",
            "warnings": warnings,
            "execution_status": "; ".join(sorted({_execution(item) for item in matching})) if matching else "not_recorded",
            "decision_source": source,
        })
    return rows


def missing_requested_count(run, records):
    """Keep requested-but-missing records in the completion denominator."""
    return sum(row["requested"] is True and row["execution_status"] == "not_recorded"
               for row in module_scope_rows(run, [], records))


def _badge(label, tone="neutral"):
    return '<span class="badge ' + tone + '">' + _esc(label) + '</span>'


def _human(value):
    return _text(value).replace("_", " ")


def _yes_no(value):
    return "Yes" if value is True else "No" if value is False else "Not recorded"


def scope_label(decision):
    """Label recorded scope without treating every unrequested item as excluded."""
    action = decision.get("planned_action", decision.get("action"))
    reason = decision.get("reason")
    requested = decision.get("requested")
    if requested is False:
        explicit = ((reason == "sample_scope_exclusion" and action == "withheld")
                    or (reason == "sample_exclusion"
                        and bool(str(decision.get("declared_reason") or "").strip())))
        if explicit:
            return "Excluded", "neutral"
        if action == "disabled":
            return "Disabled", "neutral"
        return "Not requested", "neutral"
    if action == "withheld":
        return ("Held — requested" if requested is True else "Held — request not recorded"), "warn"
    return ({"run": "Planned run", "disabled": "Disabled"}.get(action, _human(action)),
            "info" if action == "run" else "neutral")


def _mode_note(sample):
    mode = sample.get("input_mode")
    labels = {"external": "External annotated inputs", "hybas": "HybAs inputs", "auto": "Automatic input-source detection"}
    label = labels.get(mode, _human(mode) if mode else "Input mode not recorded")
    if mode == "external":
        note = "HybAs metadata and Kraken results are not required for external annotated inputs. Missing taxonomy remains unresolved."
    elif mode == "hybas":
        note = "HybAs source metadata is recorded separately from the declared sample taxonomy."
    elif mode == "auto":
        note = "Automatic mode describes input-source discovery; it does not establish the sample taxonomy."
    else:
        note = "Historical evidence does not record an explicit input mode. No input source has been inferred during reporting."
    return label, note


def render_sample_scope(run, samples, records, module_names=None):
    """Return an offline, keyboard-accessible HTML fragment of recorded scope."""
    module_names = module_names or {}
    rows = module_scope_rows(run, samples, records)
    sample_map = {str(sample.get("sample_id", "unknown")): sample for sample in samples}
    for row in rows:
        sample_map.setdefault(row["sample_id"], {"sample_id": row["sample_id"]})
    html = '<div class="callout"><strong>Planned scope and execution are separate.</strong><p>Explicit exclusions are outside the requested scope. A requested module held for missing information remains incomplete. A planned run is not a completed result.</p></div>'
    html += '<p><a class="button" href="module-scope.tsv" download>Inputs and module decisions · TSV</a></p>'
    if not sample_map:
        return html + '<p class="panel empty">No sample context or module decisions were supplied.</p>'
    for sid, sample in sample_map.items():
        own = [row for row in rows if row["sample_id"] == sid]
        mode, note = _mode_note(sample)
        held = sum(row["planned_action"] == "withheld" and row["requested"] is True for row in own)
        labels = [scope_label(row)[0] for row in own]
        excluded = labels.count("Excluded")
        disabled = labels.count("Disabled")
        not_requested = labels.count("Not requested")
        html += '<article class="panel"><div class="panel-head"><div><p class="eyebrow">INPUT PROVENANCE</p><h3>' + _esc(sid) + '</h3></div>' + _badge(mode, "info") + '</div>'
        fields = {
            "Taxon": sample.get("taxon") or "Unresolved / not supplied",
            "Taxon rank": sample.get("taxon_rank"),
            "Taxonomy source": _human(sample.get("taxon_source")),
            "Declared phylum": sample.get("taxon_phylum"),
        }
        html += '<dl class="kv">' + ''.join('<div><dt>' + _esc(key) + '</dt><dd>' + _esc(value) + '</dd></div>' for key, value in fields.items()) + '</dl><p class="small muted">' + _esc(note) + '</p>'
        context = sample.get("hybas_context")
        if isinstance(context, dict) and context and sample.get("input_mode") != "external":
            html += '<p class="small">Recorded HybAs metadata status: <strong>' + _esc(_human(context.get("status"))) + '</strong>. <a href="#provenance">Open source provenance</a></p>'
        if not own:
            html += '<p class="empty">Module decisions and execution records are not recorded for this sample.</p></article>'
            continue
        html += '<details class="evidence-list"' + (' open' if held else '') + '><summary>Module decisions · ' + str(len(own)) + ' records · ' + str(held) + ' held · ' + str(excluded) + ' excluded · ' + str(disabled) + ' disabled · ' + str(not_requested) + ' not requested</summary>'
        html += '<div class="table-wrap"><table><caption>' + _esc(sid) + ' — recorded scope and execution</caption><thead><tr>' + ''.join('<th scope="col">' + heading + '</th>' for heading in ("Module", "Planned action", "Actual execution", "Reason and review notes")) + '</tr></thead><tbody>'
        for row in own:
            label, tone = scope_label(row)
            execution = row["execution_status"]
            execution_tone = "ok" if execution == "completed" else "bad" if execution == "failed" else "warn" if execution in {"blocked", "not_started"} else "neutral"
            if row["declared_reason"]:
                notes = '<p>Declared reason: ' + _esc(row["declared_reason"]) + '</p><p class="small muted">Recorded decision: ' + _esc(row["reason"]) + '</p>'
            else:
                notes = _esc(row["reason"])
            if row["warnings"]:
                notes += '<ul>' + ''.join('<li>' + _esc(warning) + '</li>' for warning in row["warnings"]) + '</ul>'
            notes += '<p class="small muted">Requested: ' + _yes_no(row["requested"]) + ' · Scope incomplete: ' + _yes_no(row["scope_incomplete"]) + '</p>'
            html += '<tr><th scope="row">' + _esc(module_names.get(row["module"], row["module"])) + '</th><td>' + _badge(label, tone) + '</td><td>' + _badge(_human(execution), execution_tone) + '</td><td>' + notes + '</td></tr>'
        html += '</tbody></table></div></details></article>'
    return html
