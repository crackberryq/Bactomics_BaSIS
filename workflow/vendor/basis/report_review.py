"""Account for AMR review independently of scheduling and execution completion.

These are derived, versioned review cues, never edits to saved native evidence.
No automatic analysis result constitutes recorded expert review closure.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from html import escape
import json
import re

from .amr_review import VERSION, REASONS, review_finding
from .deep_reporting import parent_execution

COLUMNS = ("sample_id", "finding_id", "protein_id", "gene", "native_method",
           "source_execution", "review_status", "investigation_required", "review_reasons",
           "followup_status", "candidate_ids", "recorded_interpretations", "policy", "placements")


def _snapshot(run):
    """Compare saved source bytes, excluding the extra follow-up record."""
    inputs = run["report_inputs"]
    values = [(key, inputs[key]["sha256"]) for key in ("sample", "config")]
    sources = run["evidence_sources"]
    modules = [item["module"] for item in sources]
    if len(modules) != len(set(modules)):
        raise ValueError("Duplicate report source")
    if ("deepsafe" in modules) != (run["report_view"] == "deep"):
        raise ValueError("Unexpected DeepSafe source for report view")
    values.extend(("evidence:" + item["module"], item["sha256"]) for item in sources)
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
           for _, value in values):
        raise ValueError("Invalid report snapshot hash")
    return sorted(pair for pair in values if pair[0] != "evidence:deepsafe")


def matching_screening_snapshot(screening_run, deep_run):
    """Whether both report headers record the same screening input bytes."""
    try:
        return (screening_run.get("report_view") == "screening"
                and deep_run.get("report_view") == "deep"
                and _snapshot(screening_run) == _snapshot(deep_run))
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def _key(sid, finding):
    # Link only identical saved source metadata within the same sample. Neither
    # a matching gene label nor a reused finding ID alone establishes identity.
    fields = ("protein_id", "contig", "start", "end", "strand", "gene", "family",
              "reference", "identity", "coverage", "attributes")
    return (str(sid), str(finding.get("finding_id") or finding.get("id") or ""),
            json.dumps({key: finding.get(key) for key in fields}, sort_keys=True, default=str))


def _followup(candidate, parent):
    state = candidate.get("execution", "not_recorded")
    assessment = candidate.get("assessment") or {}
    if candidate.get("selected") is False:
        status = "deferred"
    elif parent_execution(parent) != "completed":
        status = "provisional" if state == "completed" else parent_execution(parent)
    elif state != "completed":
        status = state
    elif assessment.get("interpretation") in {"inconclusive", "unresolved", "subgroup_unresolved"}:
        status = "completed_unresolved"
    else:
        status = "completed_requires_review"
    reasons = [str(candidate["reason"])] if candidate.get("reason") else []
    for decision in (candidate.get("aware") or {}).get("evidence", []):
        if not decision.get("eligible"):
            reasons.extend(decision.get("reasons") or [])
    placement = (candidate.get("analyses") or {}).get("family_placement") or {}
    adequacy = ((placement.get("panel") or {}).get("adequacy") or {}).get("reasons") or []
    return {"candidate_id": candidate.get("candidate_id"), "status": status,
            "placement_status": placement.get("status"), "placement_reason": placement.get("reason"),
            "placement_adequacy": adequacy,
            "parent_execution": parent_execution(parent), "recorded_execution": state,
            "recorded_interpretation": assessment.get("interpretation"),
            "function_established": assessment.get("function_established"),
            "reasons": sorted(set(reasons))}


def build_review_summary(records, deep_records=(), *, include_orphaned_sources=True):
    from .report import _all_findings

    findings = _all_findings(records)
    keys = {_key(f["sample_id"], f) for f in findings if review_finding(f)}
    linked = defaultdict(list)
    for parent in deep_records:
        sid = parent.get("sample_id", "unknown")
        for candidate in parent.get("candidates") or []:
            if candidate.get("sample_id") not in (None, "", sid):
                continue
            followup = _followup(candidate, parent)
            for source in candidate.get("source_evidence") or []:
                if source.get("sample_id") not in (None, "", sid) or review_finding(source) is None:
                    continue
                key = _key(sid, source)
                if key not in keys and not include_orphaned_sources:
                    continue
                if followup not in linked[key]:
                    linked[key].append(followup)
                if key not in keys:
                    # A historical deep-only bundle can retain the source finding
                    # without its original parent record. Do not assume success.
                    findings.append({**source, "sample_id": sid,
                                     "execution_status": "not_recorded"})
                    keys.add(key)
    rows = []
    for finding in findings:
        review = review_finding(finding)
        if review is None:
            continue
        followups = linked.get(_key(finding["sample_id"], finding), [])
        rows.append({"sample_id": finding["sample_id"],
                     "finding_id": finding.get("finding_id") or finding.get("id"),
                     "protein_id": finding.get("protein_id"), "gene": finding.get("gene"),
                     "native_method": review["native_method"],
                     "source_execution": finding.get("execution_status", "not_recorded"),
                     "review_status": "requires_review", "policy": VERSION,
                     "investigation_required": review["investigation_required"],
                     "review_reasons": review["reasons"], "followups": followups,
                     "followup_status": sorted({f["status"] for f in followups}) or ["no_linked_followup"],
                     "placements": [{"candidate_id": f["candidate_id"], "status": f["placement_status"],
                                     "reason": f["placement_reason"], "adequacy": f["placement_adequacy"]}
                                    for f in followups if f["placement_status"]],
                     "candidate_ids": sorted({str(f["candidate_id"]) for f in followups if f["candidate_id"]}),
                     "recorded_interpretations": sorted({str(f["recorded_interpretation"]) for f in followups
                                                         if f["recorded_interpretation"]})})
    rows.sort(key=lambda row: (not row["investigation_required"], str(row["sample_id"]), str(row["finding_id"])))
    counts = {"review_findings": len(rows),
              "investigation_findings": sum(row["investigation_required"] for row in rows),
              "native_call_findings": sum(not row["investigation_required"] for row in rows),
              "provisional_source_findings": sum(row["source_execution"] != "completed" for row in rows)}
    source_states = [parent_execution(record) for record in records
                     if record.get("module", record.get("tool")) == "amrfinder"]
    empty_status = ("no_reported_amr_findings" if source_states and all(s == "completed" for s in source_states)
                    else "not_assessed")
    return {"schema": VERSION, "status": "requires_review" if rows else empty_status,
            "source_execution_states": source_states,
            "counts": counts, "rows": rows,
            "reason_counts": dict(Counter(reason for row in rows for reason in row["review_reasons"])),
            "scope": "Derived AMRFinder AMR review cues from saved metadata. Counts are source findings, not unique proteins. "
                     "No rejected or unreported alignments are recovered. Other tools remain in their own review sections.",
            "limitation": "Operational review rules, not a validated biological classifier. Computational completion, "
                          "deferral and an empty queue never constitute expert review closure or deployment authorization."}


def render_review_summary(summary, *, followup_href=None, screening_only=False):
    if not summary["rows"]:
        return ""
    esc = lambda value: escape(str(value if value is not None else "Not recorded"), quote=True)
    counts = summary["counts"]
    number = counts["investigation_findings"]
    investigation = str(number) + (" finding requires investigation" if number == 1 else " findings require investigation")
    linked = {(row["sample_id"], f["candidate_id"]): f
              for row in summary["rows"] for f in row["followups"] if f["candidate_id"]}
    overview = ""
    if linked:
        completed = [f for f in linked.values() if f["status"].startswith("completed_")]
        placed = sum(f["placement_status"] == "completed" for f in completed)
        no_panel = sum("no_supported_family_panel" in f["placement_adequacy"] for f in completed)
        deferred = sum(f["status"] == "deferred" for f in linked.values())
        overview = (f'<p><strong>Linked candidates {len(linked)}</strong> · Completed follow-up {len(completed)}'
                    f' · Completed placement {placed} · No supported panel {no_panel} · Deferred {deferred}</p>')
    if followup_href:
        overview += '<p><a href="' + esc(followup_href) + '">Open matched DeepSafe follow-up report</a></p>'
    body = ('<article class="panel" id="amr-review"><h3>AMR evidence review outstanding</h3>'
            '<p><strong>' + investigation + '</strong>; '
            + str(counts["native_call_findings"]) + ' other native AMR calls remain for context review.</p>'
            '<p>Follow-up selection and completed jobs do not close these review questions. '
            'Deferred, failed and inconclusive follow-up remains visible.</p>'
            + overview + '<a class="button" href="amr-review.tsv" download>All AMR review findings · TSV</a>'
            '<details><summary>Why these findings need review · ' + str(counts["review_findings"]) + ' source rows</summary>'
            '<p class="small">Showing up to 10 rows; the download contains every row and linked candidate ID.</p>'
            '<div class="table-wrap"><table><thead><tr><th>Sample / finding</th><th>Review reason</th>'
            '<th>Follow-up</th></tr></thead><tbody>')
    for row in summary["rows"][:10]:
        why = " ".join(REASONS[reason] for reason in row["review_reasons"])
        if row["source_execution"] != "completed":
            why += " Source completion is not established; this finding remains provisional."
        status = "; ".join(value.replace("_", " ") for value in row["followup_status"])
        if screening_only and row["followup_status"] == ["no_linked_followup"]:
            status = "No matching follow-up in this report snapshot"
        for placement in row["placements"]:
            if "no_supported_family_panel" in placement["adequacy"]:
                status += " · No supported reference panel"
            elif placement["status"] == "completed":
                status += " · Reference placement completed"
            else:
                status += " · Placement " + str(placement["status"]).replace("_", " ")
        reasons = sorted({reason for followup in row["followups"] for reason in followup["reasons"]})
        if reasons:
            status += ": " + "; ".join(reason.replace("_", " ") for reason in reasons)
        body += ('<tr><td>' + esc(row["sample_id"]) + ' · <strong>' + esc(row["gene"]) + '</strong><p class="small">'
                 + esc(row["protein_id"]) + ' · ' + esc(row["native_method"]) + '</p></td><td>' + esc(why)
                 + '</td><td>' + esc(status) + '</td></tr>')
    return body + '</tbody></table></div><p class="small">' + esc(summary["scope"]) + '</p><p class="small">' + esc(summary["limitation"]) + ' Policy: ' + VERSION + '.</p></details></article>'
