"""Render saved AWARE admission counts; never rerun selection while reporting."""
import html
import json


def render_aware(records):
    esc = lambda value: html.escape(str(value), quote=True)
    blocks = []
    for record in records:
        audit = record.get("aware_selection")
        if not isinstance(audit, dict) or audit.get("schema") != "basis-aware-v1":
            continue
        counts, policy = audit["counts"], audit["effective_policy"]
        rows = "".join("<tr>" + "".join("<td>" + esc(value) + "</td>" for value in
                       (tool, data["received"], data["evaluated"], data["qualifying"], data["selected_loci"])) + "</tr>"
                       for tool, data in sorted(audit["by_tool"].items()))
        deferred = "".join("<li>" + esc(reason) + ": " + esc(number) + "</li>"
                           for reason, number in sorted(audit["deferral_reasons"].items()))
        blocks.append('<div class="panel"><h3>BaSIS AWARE · ' + esc(record.get("sample_id", "")) + '</h3>'
            '<p>Preset: <strong>' + esc(policy["preset"]) + '</strong> · ' + esc(policy["version"]) + '</p>'
            '<p>' + esc(counts["findings_received"]) + ' findings received → ' + esc(counts["findings_evaluated"]) +
            ' evaluated → ' + esc(counts["findings_qualifying"]) + ' qualifying findings. ' +
            esc(counts["unique_candidates"]) + ' unique candidates → ' + esc(counts["eligible_candidates"]) +
            ' eligible → <strong>' + esc(counts["selected_candidates"]) + ' selected</strong>.</p>'
            '<p>' + esc(counts["admission_deferred"]) + ' deferred by admission criteria; ' +
            esc(counts["budget_deferred"]) + ' deferred by resource budgets. ' +
            esc(counts["unique_selected_sequences"]) + ' unique extracted sequences.</p>'
            '<p class="muted">Admission controls computational follow-up. Deferred findings remain in the evidence; '
            'these presets are not validated safety or functional classifications. Sequence counts do not establish computation reuse.</p>'
            '<div class="table-wrap"><table><thead><tr><th>Tool</th><th>Received</th><th>Evaluated</th><th>Qualifying</th>'
            '<th>Selected loci supported</th></tr></thead><tbody>' + rows + '</tbody></table></div>'
            '<p class="small muted">One locus can have multiple tools. Tool counts are not additive across loci.</p>'
            '<details><summary>Deferral reasons</summary><ul>' + deferred + '</ul></details>'
            '<details><summary>Effective settings and manual overrides</summary><pre>' +
            esc(json.dumps(policy, indent=2)) + '</pre></details></div>')
    return "".join(blocks)
