"""Plain-language summaries of saved tool results, with no new interpretation."""
from __future__ import annotations

from collections import Counter
from html import escape

from .report_sample_scope import module_scope_rows, record_requested
from .report_screening import PURPOSES, describe_finding


def tool_results(run, samples, records, findings):
    """Keep execution, assessment limits and retained finding counts distinct."""
    from .report import _execution, _interpretation, _module, _sample_id

    excluded = {"deepsafe", "deep", "pathogenfinder2", "pf2"}
    source = {}
    for record in records:
        if _module(record) not in excluded:
            source.setdefault((_sample_id(record), _module(record)), []).append(record)
    scopes = {(row["sample_id"], row["module"]): row for row in module_scope_rows(run, samples, records)}
    keys = set(source) | {key for key, row in scopes.items() if row["requested"] is True and key[1] not in excluded}
    rows = []
    for sid, module in sorted(keys):
        matching = source.get((sid, module), [])
        record = matching[0] if matching else {}
        scope = scopes.get((sid, module), {})
        if matching and not any(record_requested(rec, run) for rec in matching):
            continue
        own = [f for f in findings if str(f.get("sample_id")) == sid and (f.get("module") or f.get("tool")) == module]
        groups = Counter(describe_finding(f)["group"] for f in own)
        execution = _execution(record) if matching else "not_recorded"
        interpretation = _interpretation(record) if matching else "not_recorded"
        limited = scope.get("scope_incomplete") is True or any(r.get("scope_incomplete") is True or r.get("complete") is False for r in matching)
        reason = str(record.get("reason") or scope.get("reason") or "")
        if len(matching) > 1:
            outcome, tone = "Multiple saved tool records", "warn"
            explanation = "Review the individual source records; one completion status cannot summarize all of them."
        elif not matching:
            outcome, tone = "Requested result missing", "warn"
            explanation = "This tool was requested, but no saved result record was supplied."
        elif execution != "completed":
            outcome = {"failed": "Tool failed", "blocked": "Tool blocked", "not_started": "Tool not started",
                       "not_assessed": "Not assessed", "not_requested": "Not requested"}.get(execution, "Completion not recorded")
            tone = "bad" if execution == "failed" else "warn"
            explanation = "This is not a negative result. Any retained finding rows remain provisional."
        elif limited:
            outcome, tone = "Assessment incomplete", "warn"
            explanation = "The tool finished, but part of its requested assessment remains incomplete."
        elif interpretation == "inconclusive":
            outcome, tone = "Inconclusive result", "warn"
            explanation = "The tool finished and recorded an inconclusive result. Zero saved rows do not mean a negative result."
        elif interpretation == "not_detected" and not own:
            outcome, tone = "No findings reported", "neutral"
            explanation = "The tool reported no findings within its recorded scope and thresholds."
        elif interpretation == "not_detected" and own:
            outcome, tone = "Review the saved result", "warn"
            explanation = "The source records a not-detected interpretation alongside retained finding rows. Inspect their qualifications."
        elif own:
            outcome, tone = "Findings reported", "info"
            explanation = "These are tool annotations; the detailed findings retain their source descriptions and qualifications."
        else:
            outcome, tone = "No finding rows saved", "neutral"
            explanation = "An empty finding table alone does not establish a negative result. Check the recorded interpretation."
        rows.append({"sample_id": sid, "module": module, "outcome": outcome, "tone": tone,
                     "explanation": explanation, "execution": execution, "interpretation": interpretation,
                     "limited": limited, "reason": reason, "total": len(own), "groups": dict(groups)})
    return rows


def render_tool_results(rows, names):
    esc = lambda value: escape(str(value), quote=True)
    parts = ['<article class="panel tool-results" id="tool-results"><div class="panel-head"><div>'
             '<h3>Results by tool</h3><p class="muted">Start with the recorded result, then open its findings.</p></div>'
             '<a class="button" href="#screening-findings">Browse all findings</a></div>'
             '<p class="small">Finished tools, conclusive assessments and finding counts describe different things. '
             'Counts below are saved source rows; the same gene can appear in more than one tool.</p>'
             '<div class="table-wrap"><table><thead><tr><th scope="col">Tool / sample</th>'
             '<th scope="col">Recorded result</th><th scope="col">Saved findings</th><th scope="col">Read results</th>'
             '</tr></thead><tbody>']
    for row in rows:
        module, sid = row["module"], row["sample_id"]
        counts = " · ".join(str(row["groups"].get(group, 0)) + " " + label for group, label in
                            (("review", "main review"), ("exploratory", "exploratory"), ("provisional", "provisional"))
                            if row["groups"].get(group))
        detail = '<details class="tool-result-detail"><summary>Explanation and recorded status</summary><p>' + esc(row["explanation"]) + '</p>'
        detail += '<p><strong>What this tool reports:</strong> ' + esc(PURPOSES.get(module, "Recorded tool results.")) + '</p>'
        detail += '<p>Execution: ' + esc(row["execution"].replace("_", " ")) + '. Recorded interpretation: ' + esc(row["interpretation"].replace("_", " ")) + '.</p></details>'
        reason = '<p class="small tool-reason">Recorded reason: ' + esc(row["reason"].replace("_", " ")) + '</p>' if row["reason"] and row["tone"] in {"warn", "bad"} else ""
        action = ('<a class="button" href="#screening-findings" data-findings-tool="' + esc(module) + '" data-findings-sample="' + esc(sid)
                  + '">View ' + str(row["total"]) + ' finding' + ('' if row["total"] == 1 else 's') + '</a>') if row["total"] else '<span class="muted">No finding rows</span>'
        parts.append('<tr><td><strong>' + esc(names.get(module, module)) + '</strong><p class="small muted">' + esc(sid) + '</p></td>'
                     '<td><span class="badge ' + row["tone"] + '">' + esc(row["outcome"]) + '</span>' + reason + detail + '</td>'
                     '<td><strong>' + str(row["total"]) + '</strong>' + ('<p class="small">' + esc(counts) + '</p>' if counts else '') + '</td>'
                     '<td>' + action + '</td></tr>')
    if not rows:
        parts.append('<tr><td colspan="4">No requested screening tool records were supplied.</td></tr>')
    parts.append('</tbody></table></div></article>')
    return ''.join(parts)


CSS = r"""
.report-screening .hero{padding:24px 30px}.report-screening .hero-art{display:none}
.report-screening .hero h1{font-size:34px}.report-screening .hero-meta{margin-top:12px;padding-top:12px}
.report-screening .stats{grid-template-columns:repeat(2,minmax(0,1fr));margin:14px 0}
.report-screening .stat{padding:15px 20px}.report-screening .stat-value{font-size:28px}
.sample-context>summary{cursor:pointer;font-weight:600}.sample-context .grid-2{margin-top:16px}
.report-screening .sample-context .grid-2{grid-template-columns:1fr}
.tool-results table{min-width:660px}.tool-results th:nth-child(1){width:19%}.tool-results th:nth-child(2){width:44%}
.tool-results td{vertical-align:top}.tool-result-detail{margin-top:8px}.tool-result-detail summary{cursor:pointer;font-size:12px;color:#087c78}
.tool-result-detail p{font-size:12px;margin:8px 0}.tool-reason{max-width:650px;overflow-wrap:anywhere}
@media(max-width:620px){.report-screening .hero{padding:20px}.report-screening .hero h1{font-size:28px}}
"""
