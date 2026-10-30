"""Account for AMR review independently of scheduling and execution completion.

These are derived, versioned review cues, never edits to saved native evidence.
No automatic analysis result constitutes recorded expert review closure.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from html import escape
import json

from .amr_review import VERSION, REASONS, review_finding
from .deep_reporting import parent_execution

COLUMNS = ("sample_id", "finding_id", "protein_id", "gene", "native_method",
           "source_execution", "review_status", "investigation_required", "review_reasons",
           "followup_status", "candidate_ids", "recorded_interpretations", "policy")


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
    return {"candidate_id": candidate.get("candidate_id"), "status": status,
            "parent_execution": parent_execution(parent), "recorded_execution": state,
            "recorded_interpretation": assessment.get("interpretation"),
            "function_established": assessment.get("function_established"),
            "reasons": sorted(set(reasons))}


def build_review_summary(records, deep_records=()):
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


def render_review_summary(summary):
    if not summary["rows"]:
        return ""
    esc = lambda value: escape(str(value if value is not None else "Not recorded"), quote=True)
    counts = summary["counts"]
    number = counts["investigation_findings"]
    investigation = str(number) + (" finding requires investigation" if number == 1 else " findings require investigation")
    body = ('<article class="panel" id="amr-review"><h3>AMR evidence review outstanding</h3>'
            '<p><strong>' + investigation + '</strong>; '
            + str(counts["native_call_findings"]) + ' other native AMR calls remain for context review.</p>'
            '<p>Follow-up selection and completed jobs do not close these review questions. '
            'Deferred, failed and inconclusive follow-up remains visible.</p>'
            '<a class="button" href="amr-review.tsv" download>All AMR review findings · TSV</a>'
            '<details><summary>Why these findings need review · ' + str(counts["review_findings"]) + ' source rows</summary>'
            '<p class="small">Showing up to 10 rows; the download contains every row and linked candidate ID.</p>'
            '<div class="table-wrap"><table><thead><tr><th>Sample / finding</th><th>Review reason</th>'
            '<th>Follow-up</th></tr></thead><tbody>')
    for row in summary["rows"][:10]:
        why = " ".join(REASONS[reason] for reason in row["review_reasons"])
        if row["source_execution"] != "completed":
            why += " Source completion is not established; this finding remains provisional."
        status = "; ".join(value.replace("_", " ") for value in row["followup_status"])
        reasons = sorted({reason for followup in row["followups"] for reason in followup["reasons"]})
        if reasons:
            status += ": " + "; ".join(reason.replace("_", " ") for reason in reasons)
        body += ('<tr><td>' + esc(row["sample_id"]) + ' · <strong>' + esc(row["gene"]) + '</strong><p class="small">'
                 + esc(row["protein_id"]) + ' · ' + esc(row["native_method"]) + '</p></td><td>' + esc(why)
                 + '</td><td>' + esc(status) + '</td></tr>')
    return body + '</tbody></table></div><p class="small">' + esc(summary["scope"]) + '</p><p class="small">' + esc(summary["limitation"]) + ' Policy: ' + VERSION + '.</p></details></article>'
