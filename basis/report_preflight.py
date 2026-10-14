"""Offline presentation of saved preflight checks; never runs or infers analyses."""
from __future__ import annotations

from collections import Counter
import csv
from html import escape
import io
import json
import os
from pathlib import Path
import shutil
import tempfile

from .report_sample_scope import scope_label
from .util import sha256_file


SAMPLE_COLUMNS = (
    "sample_id", "source_row", "status", "input_status", "input_mode", "taxon", "taxon_status",
    "taxon_rank", "taxon_source", "hybas_metadata_status", "contigs", "assembly_bp",
    "protein_count", "errors", "warnings", "input_warnings", "input_hashes",
)
MODULE_COLUMNS = (
    "sample_id", "module", "check_status", "scope_label", "planned_action", "requested",
    "scope_incomplete", "reason", "declared_reason", "declared_exclusion", "errors", "warnings",
)


def _text(value, default="Not recorded"):
    return default if value is None or value == "" else str(value)


def _esc(value, default="Not recorded"):
    return escape(_text(value, default), quote=True)


def _items(value):
    return value if isinstance(value, list) else [] if value is None else [value]


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _human(value):
    return _text(value).replace("_", " ").capitalize()


def _tone(status):
    return {"failed": "bad", "passed": "ok", "passed_with_warnings": "warn",
            "withheld": "warn", "held": "warn"}.get(status, "neutral")


def _badge(value, tone="neutral"):
    return '<span class="badge ' + tone + '">' + _esc(value) + '</span>'


def _messages(values, title, *, errors=False):
    values = _items(values)
    if not values:
        return ""
    return ('<details class="messages ' + ('errors' if errors else 'warnings') + '"' +
            (' open' if errors else '') + '><summary>' + _esc(title) + ' · ' + str(len(values)) +
            '</summary><ul>' + ''.join('<li>' + _esc(v) + '</li>' for v in values) + '</ul></details>')


def _tsv_value(value):
    if value is None:
        return ""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
    if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")):
        text = "'" + text
    return text


def _tsv(columns, rows):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, delimiter="\t", extrasaction="ignore")
    writer.writeheader()
    writer.writerows({key: _tsv_value(row.get(key)) for key in columns} for row in rows)
    return buffer.getvalue()


def _sample_errors(sample):
    return bool(_items(sample.get("errors"))) or any(
        _items(_mapping(item).get("errors")) or _mapping(item).get("status") == "failed"
        for item in _mapping(sample.get("module_checks")).values())


def _sample_status(sample):
    if _sample_errors(sample) or sample.get("input_status") == "failed":
        return "failed"
    return sample.get("status") or "not_recorded"


def _sample_has_warnings(sample):
    return bool(_items(sample.get("warnings")) or _items(sample.get("input_warnings")) or any(
        _items(_mapping(item).get("warnings")) for item in _mapping(sample.get("module_checks")).values()))


def _scope(decision):
    return scope_label(decision) if decision else ("Decision not recorded", "neutral")


def _rows(samples):
    sample_rows, module_rows = [], []
    for sample in samples:
        context, metrics = _mapping(sample.get("input_context")), _mapping(sample.get("metrics"))
        row = {key: sample.get(key) for key in SAMPLE_COLUMNS}
        row.update({key: context.get(key) for key in (
            "input_mode", "taxon", "taxon_status", "taxon_rank", "taxon_source", "hybas_metadata_status")})
        row.update({key: metrics.get(key) for key in ("contigs", "assembly_bp", "protein_count")})
        sample_rows.append(row)
        for module, item in _mapping(sample.get("module_checks")).items():
            item = _mapping(item)
            decision = _mapping(item.get("decision"))
            module_rows.append({
                "sample_id": sample.get("sample_id"), "module": module,
                "check_status": item.get("status"), "scope_label": _scope(decision)[0],
                "planned_action": decision.get("action"), "requested": decision.get("requested"),
                "scope_incomplete": decision.get("scope_incomplete"), "reason": decision.get("reason"),
                "declared_reason": decision.get("declared_reason"),
                "declared_exclusion": item.get("declared_exclusion"),
                "errors": item.get("errors", []), "warnings": item.get("warnings", []),
            })
    return sample_rows, module_rows


CSS = """
:root{color-scheme:light;--ink:#193444;--muted:#526b79;--line:#d9e4e9;--accent:#006d77;--paper:#f3f7f8}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:#005e71;text-underline-offset:3px}a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #e5a83c;outline-offset:4px}header{background:#153b49;color:#fff;padding:44px max(5vw,24px) 32px;border-bottom:5px solid #17a3a2}header p{color:#d7e8ee;max-width:78ch}h1{font-size:clamp(28px,4vw,40px);line-height:1.15;margin:8px 0 14px;letter-spacing:-.8px}h2{font-size:22px;margin:0 0 12px}h3{font-size:18px;margin:0}p{margin:8px 0 16px}.eyebrow{font-size:11px;letter-spacing:2px;font-weight:750;text-transform:uppercase}main{max-width:1500px;margin:auto;padding:28px max(3vw,20px) 60px}.panel{background:#fff;border:1px solid var(--line);border-radius:14px;padding:24px;margin-bottom:22px;box-shadow:0 5px 18px #173b4910}.panel-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:22px 0}.stat{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px}.stat strong{font-size:29px;display:block;line-height:1.3}.stat span{color:var(--muted);font-size:13px}.badge{display:inline-block;white-space:normal;line-height:1.35;font-size:12px;font-weight:750;padding:5px 9px;border-radius:7px;background:#edf1f3;color:#455b66}.badge.ok{background:#e3f4eb;color:#195e3e}.badge.bad{background:#fce9e9;color:#962c35}.badge.warn{background:#fff2d9;color:#7c5208}.badge.info{background:#e4f1f5;color:#16556b}.callout{border-left:4px solid var(--accent);background:#e8f4f5;padding:15px 18px;border-radius:0 8px 8px 0}.muted,.small{color:var(--muted)}.small{font-size:12px}code{font-family:ui-monospace,monospace;font-size:12px;overflow-wrap:anywhere}.downloads{display:flex;flex-wrap:wrap;gap:10px;margin:20px 0}.button{display:inline-block;border:1px solid #b9ced7;background:#fff;border-radius:8px;padding:8px 13px;text-decoration:none;font-size:13px;font-weight:650}.filters{display:flex;gap:16px;align-items:end;flex-wrap:wrap;margin:18px 0}.filters label{font-size:13px;font-weight:700}.filters input,.filters select{display:block;margin-top:5px;background:#fff;color:var(--ink);border:1px solid #9fb6c1;border-radius:7px;padding:10px;font:inherit}.filters input{width:min(400px,78vw)}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}caption{text-align:left;color:var(--muted);font-size:12px;padding:6px 0 14px}th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--line);padding:12px 10px}thead th{background:#edf4f6;font-size:11px;text-transform:uppercase;letter-spacing:.6px}tbody th{font-weight:700;min-width:140px}td p{margin:5px 0}td .small{display:block;margin-top:5px}.matrix td{min-width:165px}.kv{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;margin:20px 0}.kv dt{font-size:11px;font-weight:750;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}.kv dd{margin:3px 0 0;overflow-wrap:anywhere}.messages{margin:12px 0;padding:10px 14px;border:1px solid var(--line);border-radius:8px}.messages.errors{background:#fff5f5;border-color:#ecc5c5}.messages.warnings{background:#fffcf4;border-color:#eaddb9}summary{cursor:pointer;font-weight:700}li{margin:5px 0;overflow-wrap:anywhere}.module-details{margin-top:18px}.module-details>summary{padding:12px 0}.empty{padding:20px;border:1px dashed #a6bcc7;border-radius:9px;color:var(--muted)}[hidden]{display:none!important}.footer{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.skip{position:absolute;left:10px;top:-60px;background:#fff;padding:10px}.skip:focus{top:8px}.nowrap{white-space:nowrap}@media(max-width:760px){.stats,.kv{grid-template-columns:repeat(2,minmax(0,1fr))}.panel{padding:18px}main{padding:20px 14px 40px}}@media print{header{background:#fff;color:#193444;padding:15px}header p{color:#526b79}.filters,.downloads{display:none}.panel,.stat{box-shadow:none;break-inside:avoid}.table-wrap{overflow:visible}main{padding:10px}.sample-card[hidden],tr[hidden]{display:revert!important}}
"""


SCRIPT = """
(() => {
 const search = document.getElementById('sample-search');
 const status = document.getElementById('sample-status');
 const cards = Array.from(document.querySelectorAll('.sample-card'));
 const rows = Array.from(document.querySelectorAll('.matrix-sample'));
 const filter = () => {
   const query = search.value.trim().toLocaleLowerCase();
   const visible = new Set();
   cards.forEach(card => {
     const show = (!query || card.dataset.search.toLocaleLowerCase().includes(query)) &&
       (!status.value || card.dataset.status === status.value);
     card.hidden = !show;
     if (show) visible.add(card.id);
   });
   rows.forEach(row => { row.hidden = !visible.has(row.dataset.sample); });
   document.getElementById('filter-count').textContent = `${visible.size} of ${cards.length} samples shown`;
   document.getElementById('no-matches').hidden = visible.size > 0 || cards.length === 0;
 };
 search.addEventListener('input', filter); status.addEventListener('change', filter);
 filter();
})();
"""


def _module_details(sample):
    checks = _mapping(sample.get("module_checks"))
    if not checks:
        return '<p class="empty">Module scope not evaluated for this row. No disabled or excluded modules have been inferred.</p>'
    html = '<details class="module-details" open><summary>Module checks and planned scope · ' + str(len(checks)) + '</summary><div class="table-wrap"><table><caption>Check status describes preflight only. Planned actions describe a future run.</caption><thead><tr><th scope="col">Module</th><th scope="col">Planned scope</th><th scope="col">Preflight check</th><th scope="col">Details</th></tr></thead><tbody>'
    for module, check in checks.items():
        check = _mapping(check)
        decision = _mapping(check.get("decision"))
        label, tone = _scope(decision)
        details = '<p>' + _esc(decision.get("reason")) + '</p>'
        if decision.get("declared_reason"):
            details += '<p>Declared reason: ' + _esc(decision["declared_reason"]) + '</p>'
        declaration = check.get("declared_exclusion")
        if declaration is not None:
            active = label == "Excluded"
            details += '<p><strong>Exclusion declaration: ' + ('active' if active else 'not applied by this decision') + '</strong></p>'
            details += '<pre><code>' + _esc(json.dumps(declaration, ensure_ascii=False, sort_keys=True, indent=2)) + '</code></pre>'
        details += _messages(check.get("errors"), "Module errors", errors=True)
        details += _messages(check.get("warnings"), "Module warnings")
        requested = decision.get("requested")
        details += '<p class="small">Requested: ' + ("Yes" if requested is True else "No" if requested is False else "Not recorded") + ' · Scope incomplete: ' + _esc(decision.get("scope_incomplete")) + '</p>'
        status = check.get("status", "not_recorded")
        html += '<tr><th scope="row">' + _esc(module) + '</th><td>' + _badge(label, tone) + '</td><td>' + _badge(_human(status), _tone(status)) + '</td><td>' + details + '</td></tr>'
    return html + '</tbody></table></div></details>'


def _sample_card(sample, number):
    context, metrics = _mapping(sample.get("input_context")), _mapping(sample.get("metrics"))
    status = _sample_status(sample)
    search = ' '.join(_text(value, "") for value in (sample.get("sample_id"), context.get("taxon"), context.get("input_mode")))
    html = '<article class="panel sample-card" id="sample-' + str(number) + '" data-status="' + _esc(status) + '" data-search="' + _esc(search) + '"><div class="panel-head"><div><p class="eyebrow">Sample preflight</p><h3>' + _esc(sample.get("sample_id")) + '</h3></div>' + _badge(_human(status), _tone(status)) + '</div>'
    fields = {
        "Source sheet row": sample.get("source_row"),
        "Input checks": _human(sample.get("input_status")), "Input mode": context.get("input_mode"),
        "Declared taxon": context.get("taxon") or "Unresolved / not supplied", "Taxonomy status": context.get("taxon_status"),
        "Taxonomy source": context.get("taxon_source"), "HybAs metadata": context.get("hybas_metadata_status"),
        "Contigs": metrics.get("contigs"), "Assembly bases": metrics.get("assembly_bp"),
        "Proteins": metrics.get("protein_count"),
    }
    html += '<dl class="kv">' + ''.join('<div><dt>' + _esc(key) + '</dt><dd>' + _esc(value) + '</dd></div>' for key, value in fields.items()) + '</dl>'
    html += _messages(sample.get("errors"), "Sample errors", errors=True)
    html += _messages(sample.get("warnings"), "Sample warnings")
    html += _messages(sample.get("input_warnings"), "Input consistency warnings")
    if context.get("hybas_metadata_policy"):
        html += '<p class="small">Metadata policy: ' + _esc(context["hybas_metadata_policy"]) + '</p>'
    if context.get("taxonomic_classification_performed") is False:
        html += '<p class="small">No taxonomic classification was performed by these input checks.</p>'
    if sample.get("input_hashes"):
        html += '<details><summary>Recorded input hashes</summary><pre><code>' + _esc(json.dumps(sample["input_hashes"], indent=2, ensure_ascii=False, sort_keys=True)) + '</code></pre></details>'
    return html + _module_details(sample) + '</article>'


def _render(result, samples, module_rows, provenance):
    global_checks = _mapping(result.get("global_checks"))
    global_errors = _items(global_checks.get("errors", result.get("errors", [])))
    global_warnings = _items(global_checks.get("warnings", result.get("warnings", [])))
    statuses = Counter(_sample_status(sample) for sample in samples)
    overall = result.get("status") or "not_recorded"
    if global_errors or statuses.get("failed"):
        overall = "failed"
    if not samples and overall in {"passed", "passed_with_warnings"}:
        overall = "no_sample_checks_recorded"
    resource_checked = result.get("resource_checks_performed") is True
    options = _mapping(result.get("check_options"))
    scope_counts = Counter(row["scope_label"] for row in module_rows)
    html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BaSIS · Batch preflight</title><style>' + CSS + '</style></head><body><a class="skip" href="#main">Skip to preflight results</a><header><div class="eyebrow">BACTROMICS / BaSIS</div><h1>Batch preflight</h1><p>Review input consistency, recorded module scope and resource checks before starting a run.</p>' + _badge(_human(overall), _tone(overall)) + '</header><main id="main">'
    html += '<div class="callout"><strong>Checks and execution are separate.</strong><p>This report does not execute native analyses or establish biological function or organism safety. A passed preflight is not native execution acceptance. An available command prefix alone does not verify the tools inside its environment.</p><p><strong>' + ('Resource checks recorded' if resource_checked else 'Resources not checked') + '</strong> · ' + ('Resource checks may cover shared configuration only. Inspect individual module statuses and prefix advisories below.' if resource_checked else 'A not_checked resource status makes no claim of native readiness.') + '</p>'
    if options:
        html += '<p class="small">Requested check options: ' + ' · '.join(_esc(key) + ': ' + _esc(options.get(key)) for key in ("deep", "extend", "executables")) + '</p>'
    html += '</div>'
    stats = [(len(samples), "Sample check records"), (statuses.get("failed", 0), "Samples with failed checks"),
             (sum(_sample_has_warnings(s) for s in samples), "Samples with warnings"), (len(global_errors), "Global errors")]
    html += '<div class="stats">' + ''.join('<div class="stat"><strong>' + str(value) + '</strong><span>' + label + '</span></div>' for value, label in stats) + '</div>'
    html += '<nav class="downloads" aria-label="Preflight downloads">' + ''.join('<a class="button" download href="' + filename + '">' + label + '</a>' for filename, label in (("evidence.json", "Full check record · JSON"), ("samples.tsv", "Sample checks · TSV"), ("module-scope.tsv", "Module scope · TSV"))) + '</nav>'
    html += '<section class="panel" id="global"><h2>Shared checks</h2><p>Global errors affect the entire preflight even when individual sample checks pass.</p>'
    html += _messages(global_errors, "Global errors", errors=True) + _messages(global_warnings, "Global warnings")
    if not global_errors and not global_warnings:
        html += '<p class="muted">No shared errors or warnings recorded.</p>'
    html += '</section><section class="panel"><h2>Sample and module overview</h2><p class="small">Recorded scope: ' + (' · '.join(_esc(label) + ': ' + str(count) for label, count in sorted(scope_counts.items())) or 'No module decisions recorded') + '</p>'
    html += '<div class="filters"><label for="sample-search">Search sample, taxon or input mode<input type="search" id="sample-search" placeholder="Find a sample…"></label><label for="sample-status">Sample check status<select id="sample-status"><option value="">All statuses</option>' + ''.join('<option value="' + _esc(status) + '">' + _esc(_human(status)) + '</option>' for status in sorted(statuses)) + '</select></label><span id="filter-count" role="status" aria-live="polite"></span></div><noscript><p>All samples are shown. Search and filtering require JavaScript; evidence downloads work without it.</p></noscript>'
    modules = sorted({row["module"] for row in module_rows})
    if samples:
        html += '<div class="table-wrap"><table class="matrix"><caption>Planned scope and preflight checks. Empty module records mean not evaluated, not disabled.</caption><thead><tr><th scope="col">Sample</th><th scope="col">Input checks</th>' + ''.join('<th scope="col">' + _esc(module) + '</th>' for module in modules) + '</tr></thead><tbody>'
        for index, sample in enumerate(samples):
            html += '<tr class="matrix-sample" data-sample="sample-' + str(index) + '"><th scope="row"><a href="#sample-' + str(index) + '">' + _esc(sample.get("sample_id")) + '</a></th><td>' + _badge(_human(sample.get("input_status")), _tone(sample.get("input_status"))) + '</td>'
            checks = _mapping(sample.get("module_checks"))
            for module in modules:
                if module not in checks:
                    html += '<td><span class="muted">Not evaluated</span></td>'
                    continue
                item = _mapping(checks[module])
                label, tone = _scope(_mapping(item.get("decision")))
                html += '<td>' + _badge(label, tone) + '<span class="small">Check: ' + _esc(_human(item.get("status"))) + '</span></td>'
            html += '</tr>'
        html += '</tbody></table></div>'
    else:
        html += '<p class="empty">No per-sample check records were supplied. Review the shared errors and source evidence.</p>'
    html += '</section><p class="empty" id="no-matches" hidden>No samples match the current filters.</p><section aria-label="Per-sample details">' + ''.join(_sample_card(sample, index) for index, sample in enumerate(samples)) + '</section>'
    html += '<footer class="footer"><p>Evidence is preserved as supplied in evidence.json. TSV exports prefix formula-like values with an apostrophe for spreadsheet use. Search filters do not change the exported records.</p><p>Recorded schema: ' + _esc(result.get("schema")) + ' · Recorded overall status: ' + _esc(result.get("status")) + '</p>'
    if provenance:
        html += '<p>Configuration snapshot at report creation (not a claim that these bytes were validated): <code>' + _esc(provenance["path"]) + '</code><br>SHA-256: <code>' + _esc(provenance["sha256"]) + '</code></p>'
    return html + '</footer></main><script>' + SCRIPT + '</script></body></html>'


def write_preflight_report(result, outdir, *, config_path=None):
    """Write a fresh report from supplied checks, preserving the original result.

    All content is staged before an exclusive destination claim. The HTML entry
    point is committed last; caught failures remove only this call's new output.
    No sample paths, databases or environments are read by this writer.
    """
    outdir = Path(outdir).expanduser().absolute()
    if os.path.lexists(outdir):
        raise FileExistsError(f"Preflight report destination already exists: {outdir}")
    if not isinstance(result, dict):
        raise ValueError("Preflight result must be a mapping")
    samples = result.get("sample_checks", [])
    if not isinstance(samples, list) or any(not isinstance(sample, dict) for sample in samples):
        raise ValueError("Preflight sample_checks must be a list of mappings")
    evidence = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    provenance = None
    if config_path is not None:
        path = Path(config_path).expanduser().absolute()
        provenance = {"path": str(path), "sha256": sha256_file(path)}
    sample_rows, module_rows = _rows(samples)
    content = {
        "evidence.json": evidence,
        "samples.tsv": _tsv(SAMPLE_COLUMNS, sample_rows),
        "module-scope.tsv": _tsv(MODULE_COLUMNS, module_rows),
        "index.html": _render(result, samples, module_rows, provenance),
    }
    # Creating a missing parent is allowed; existing destinations are never reused.
    outdir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".basis-preflight-", dir=outdir.parent))
    claimed = False
    try:
        for filename, text in content.items():
            (stage / filename).write_text(text, encoding="utf-8", newline="")
        outdir.mkdir(exist_ok=False)
        claimed = True
        for filename in content:  # index.html is last, after every linked export.
            os.replace(stage / filename, outdir / filename)
    except BaseException:
        if claimed:
            shutil.rmtree(outdir)
        raise
    finally:
        shutil.rmtree(stage)
    return {"html": str(outdir / "index.html"), "evidence_json": str(outdir / "evidence.json"),
            "samples_tsv": str(outdir / "samples.tsv"), "module_scope_tsv": str(outdir / "module-scope.tsv")}
