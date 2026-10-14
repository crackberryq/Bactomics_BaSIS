"""Offline, evidence-preserving reports for BaSIS.

This module renders evidence and assessment priorities. It never converts a
screening result into an organism-level safety classification.
"""
from __future__ import annotations

from .progress import tracked

import csv
import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import quote


MOBILE_CATEGORIES = {"plasmid", "prophage", "integron", "is", "ice"}
FINDING_COLUMNS = (
    "sample_id", "module", "execution_status", "module_interpretation",
    "finding_id", "locus_id", "category", "gene", "family", "protein_id",
    "contig", "start", "end", "strand", "reference", "identity", "coverage",
    "evidence_level", "interpretation", "attributes",
)
RECOMMENDATION_COLUMNS = (
    "recommendation_id", "sample_id", "category", "priority", "assessment_type",
    "trigger", "evidence_ids", "recommendation", "provided_phenotype_status",
    "finding_record_count", "distinct_target_count", "candidate_ids",
    "excluded_taxonomic_hold_evidence_ids", "missing_comparison_groups",
)


def _plain(value):
    if value is None:
        return "—"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def _esc(value):
    return html.escape(_plain(value), quote=True)


def _jsonable(value):
    """Normalize serializable containers without mutating their originals."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _as_list(value):
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _sample_id(record):
    return str(record.get("sample_id", record.get("sample", "unknown")))


def _module(record):
    return str(record.get("module", record.get("tool", "unspecified")))


def _execution(record):
    value = record.get("execution_status", record.get("execution", record.get("status", "unknown")))
    if isinstance(value, dict):
        value = value.get("status", "unknown")
    return str(value)


def _interpretation(record):
    # Failed/not-assessed evidence must never acquire a negative interpretation
    # merely because an upstream caller provided an empty findings list.
    if _execution(record) != "completed":
        return "unavailable"
    return str(record.get("interpretation", "inconclusive"))


def _finding_id(finding, sample_id, module, index):
    existing = finding.get("finding_id") or finding.get("id")
    if existing:
        return str(existing)
    payload = json.dumps([sample_id, module, index, _jsonable(finding)], sort_keys=True)
    return "report-" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def _all_findings(records):
    rows = []
    for record in records:
        for index, finding in enumerate(record.get("findings", []) or []):
            if not isinstance(finding, dict):
                continue
            row = dict(finding)
            row.update(sample_id=_sample_id(record), module=_module(record),
                       execution_status=_execution(record),
                       module_interpretation=_interpretation(record))
            row["finding_id"] = _finding_id(finding, row["sample_id"], row["module"], index)
            rows.append(row)
    return rows


def _phenotypes(sample):
    """User phenotype descriptions stay separate from verified pipeline evidence."""
    source = sample.get("phenotypes", [])
    if isinstance(source, str):
        source = [{"assay": "user description", "result": source}]
    elif isinstance(source, dict):
        if any(k in source for k in ("assay", "name", "result")):
            source = [source]
        else:
            source = [dict(v, assay=k) if isinstance(v, dict) else {"assay": k, "result": v}
                      for k, v in source.items()]
    result = []
    for item in _as_list(source):
        item = dict(item) if isinstance(item, dict) else {"assay": "user description", "result": item}
        supplied = bool(item.get("result") is not None and str(item.get("result")).strip()) or bool(item.get("source"))
        pending = str(item.get("status", "")).lower() in {"planned", "pending", "not_assessed", "not_performed", "missing"}
        item["evidence_status"] = "available_unreviewed" if supplied and not pending else "not_assessed"
        result.append(item)
    return result


def _phenotype_kinds(sample):
    kinds = set()
    for p in _phenotypes(sample):
        if p["evidence_status"] != "available_unreviewed":
            continue
        words = " ".join(str(p.get(k, "")) for k in ("assay", "name", "category", "result")).lower()
        if re.search(r"antimicrobial|antibiotic|susceptib|\bast\b|\bmic\b|\bamr\b", words):
            kinds.add("amr")
        if "hemol" in words or "haemol" in words:
            kinds.add("hemolysis")
        if "cytotoxic" in words or "toxicolog" in words:
            kinds.add("toxicity")
    return kinds


def _interval_overlap(a, b):
    if not a.get("contig") or a.get("contig") != b.get("contig"):
        return False
    try:
        a_start, a_end, b_start, b_end = (int(v) for v in (a.get("start"), a.get("end"), b.get("start"), b.get("end")))
    except (ValueError, TypeError):
        return False
    if min(a_start, a_end, b_start, b_end) < 1 or a_start > a_end or b_start > b_end:
        return False
    return max(a_start, b_start) <= min(a_end, b_end)


def _exploratory_finding(finding):
    """Keep weak evidence inspectable without treating it as a phenotype trigger."""
    attributes = finding.get("attributes") or {}
    return (finding.get("evidence_level") == "exploratory"
            or finding.get("interpretation") in {"below_reporting_threshold", "exploratory_homologue"}
            or (isinstance(attributes, dict) and attributes.get("passes_threshold") is False))


def _recommendation_trigger(findings):
    trigger = str(len(findings)) + " finding record(s) selected for review"
    for module, label in (("vfdb", "VFDB"), ("bacmet", "BacMet")):
        matches = [f for f in findings if f.get("module", "").lower() == module]
        if not matches:
            continue
        targets = {(str(f.get("sample_id")), "protein", str(f["protein_id"]))
                   if f.get("protein_id") else (str(f.get("sample_id")), "locus", str(f["locus_id"]))
                   for f in matches if f.get("protein_id") or f.get("locus_id")}
        passing = all(isinstance(f.get("attributes"), dict)
                      and f["attributes"].get("passes_threshold") is True for f in matches)
        trigger += ("; " + label + ": " + str(len(matches)) + (" threshold-passing" if passing else " non-exploratory")
                    + " alignment record(s), " + str(len(targets)) + " distinct identified protein/locus target(s)")
    return trigger


def build_recommendations(samples, records, deep_records):
    """Produce controlled, evidence-linked assessment suggestions, not protocols."""
    combined = list(records) + list(deep_records)
    findings = _all_findings(combined)
    sample_map = {str(s.get("sample_id", "unknown")): s for s in samples}
    recommendations = []

    def add(sample_id, category, priority, assessment_type, trigger, ids, wording, provided=False):
        ids = sorted(set(str(i) for i in ids if i is not None and str(i)))
        signature = json.dumps([sample_id, category, assessment_type, trigger, ids], sort_keys=True)
        recommendations.append({
            "recommendation_id": "rec-" + hashlib.sha256(signature.encode()).hexdigest()[:12],
            "sample_id": sample_id, "category": category, "priority": priority,
            "assessment_type": assessment_type, "trigger": trigger,
            "evidence_ids": ids, "recommendation": wording,
            "provided_phenotype_status": "available_unreviewed" if provided else "not_provided_for_this_assessment",
        })

    for record in combined:
        if _execution(record) in {"failed", "unknown"}:
            add(_sample_id(record), "execution", "resolve_first", "computational_review",
                _module(record) + ": " + _execution(record), [],
                "Review the recorded error and native outputs, resolve the execution or parsing problem, "
                "and repeat the affected screen before interpreting absence of findings.")
        for candidate in record.get("candidates", []) or []:
            if not isinstance(candidate, dict) or candidate.get("execution", candidate.get("execution_status")) != "failed":
                continue
            add(_sample_id(record), "deep_execution", "resolve_first", "computational_review",
                "Deep candidate " + str(candidate.get("candidate_id", "unknown")) + ": failed",
                candidate.get("finding_ids", [candidate.get("finding_id")]) or [],
                "Review the failed candidate analysis and recorded reason before using its family assessment. "
                "Retain the original screening call and unresolved interpretation until the affected analysis is resolved.")

    sample_ids = sorted(set(sample_map) | {_sample_id(r) for r in combined})
    for sid in sample_ids:
        sample = sample_map.get(sid, {})
        kinds = _phenotype_kinds(sample)
        usable = [f for f in findings if f["sample_id"] == sid and f["execution_status"] == "completed"]
        substantive = [f for f in usable if not _exploratory_finding(f)]
        exploratory = [f for f in usable if _exploratory_finding(f)
                       and f.get("category") in {"amr", "virulence", "bgc", "stress"}]
        if exploratory:
            add(sid, "exploratory_evidence", "review", "computational_review",
                str(len(exploratory)) + " exploratory or below-threshold finding record(s)",
                [f["finding_id"] for f in exploratory],
                "Exploratory and below-threshold evidence is retained for computational review of annotation "
                "specificity, model applicability and genomic context. These records do not by themselves "
                "trigger a phenotype assessment or an AMR/virulence mobile-context concern. Their presence "
                "does not establish a hazard, and their exclusion from those triggers does not establish absence.")
        for category in ("amr", "virulence", "bgc", "ml_attention"):
            # Attention evidence is exploratory by design; retain its separate
            # model-applicability review without promoting it to a phenotype call.
            selected = [f for f in (usable if category == "ml_attention" else substantive)
                        if f.get("category") == category]
            if not selected:
                continue
            ids = [f["finding_id"] for f in selected]
            provided = False
            if category == "amr":
                provided = "amr" in kinds
                if provided:
                    wording = ("Review the supplied antimicrobial-susceptibility evidence and confirm isolate identity, "
                               "assay scope and applicable interpretation criteria against these genomic findings. "
                               "A repeat assessment is warranted only if that review identifies an unresolved gap.")
                else:
                    wording = ("Consider an antimicrobial-susceptibility assessment appropriate to the organism and "
                               "detected resistance mechanism, with qualified interpretation. Genomic detection alone "
                               "does not establish expressed resistance.")
            elif category == "virulence":
                provided = bool(kinds & {"hemolysis", "toxicity"})
                wording = ("Review the implicated gene or system, annotation specificity and completeness to identify "
                           "a plausible phenotype. Consider a targeted phenotypic assessment only if it would resolve "
                           "a relevant concern for the intended use.")
                if provided:
                    wording += (" Review the supplied hemolysis or toxicity evidence first; it may address part of "
                                "this question, but does not test every virulence-associated function.")
            elif category == "bgc":
                provided = "toxicity" in kinds
                wording = ("Review predicted product identity, cluster completeness and evidence of a relevant "
                           "hazard before selecting any product or toxicity assessment. A biosynthetic cluster "
                           "prediction alone does not establish toxin production.")
                if provided:
                    wording += " Review the supplied toxicity evidence for coverage before requesting additional assessment."
            else:
                wording = ("Review model applicability, taxonomic coverage and uncertainty alongside the independent "
                           "genomic and phenotype evidence. Seek a qualified assessment if unresolved uncertainty "
                           "affects the intended use; a model output is not an exposure-risk probability.")
            add(sid, category, "review", "phenotype_evidence_review" if provided else "targeted_assessment_review",
                _recommendation_trigger(selected), ids, wording, provided)

        stress = [f for f in substantive if f.get("category") == "stress"]
        if stress:
            add(sid, "stress", "review", "computational_review",
                _recommendation_trigger(stress), [f["finding_id"] for f in stress],
                "Review biocide/metal-associated homology, reference annotation and completeness, "
                "alignment coverage, and genomic context alongside any available DeepSafe evidence. "
                "BacMet reference annotations describe the source proteins; sequence matches do not "
                "establish expressed tolerance, antibiotic resistance, co-selection or a hazard in this isolate. "
                "This priority requests computational annotation and context review, not an automatic phenotype assessment.")

        mobile = [f for f in substantive if f.get("category") in MOBILE_CATEGORIES]
        concerns = [f for f in substantive if f.get("category") in {"amr", "virulence"}]
        linked = [(m, f) for m in mobile for f in concerns if _interval_overlap(m, f)]
        if linked:
            add(sid, "mobile_context", "review", "genomic_context_review",
                "Reported genomic intervals overlap; this is a context flag, not proof of transferability.",
                [f["finding_id"] for pair in linked for f in pair],
                "Review assembly support and the candidate locus's linkage to the reported mobile element. "
                "Determine whether a targeted confirmatory assessment is justified; interval overlap alone "
                "does not establish mobility or transferability.")

        for record in combined:
            if _sample_id(record) != sid or _execution(record) != "completed":
                continue
            if _module(record).lower() in {"pathogenfinder2", "pf2"} and _interpretation(record) in {"inconclusive", "detected"}:
                if not any(r["sample_id"] == sid and r["category"] == "ml_attention" for r in recommendations):
                    add(sid, "ml_attention", "review", "model_evidence_review",
                        _module(record) + ": " + _interpretation(record), [],
                        "Review the model result, applicability and uncertainty alongside independent genomic and "
                        "phenotype evidence. The prediction does not establish organism safety or quantify exposure risk.")
    from .report_recommendations import refine_recommendations
    return refine_recommendations(samples, records, deep_records, recommendations)


def _artifact_link(value, outdir):
    """Link only to a file beneath the report root; never expose file:// or JS URLs."""
    text = str(value)
    if not text or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text) or "\\" in text:
        return None
    try:
        path = Path(text)
        lexical = path if path.is_absolute() else Path(outdir) / path
        if any(p.is_symlink() for p in (lexical, *lexical.parents)):
            return None
        candidate = lexical.resolve(strict=True)
        relative = candidate.relative_to(outdir.resolve())
        if not candidate.is_file():
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    if relative == Path("."):
        return None
    return quote(relative.as_posix(), safe="/._-")


def _artifact_list(values, outdir):
    pieces = []
    for value in _as_list(values):
        if isinstance(value, dict):
            path = value.get("path", value.get("artifact", ""))
            label = value.get("label", value.get("name", path))
        else:
            path, label = value, value
        href = _artifact_link(path, outdir)
        if href:
            pieces.append('<li><a href="' + _esc(href) + '">' + _esc(label) + '</a></li>')
        else:
            pieces.append('<li><span class="muted">' + _esc(label) + ' (not linked: unavailable file, directory, symlink or outside report bundle)</span></li>')
    return '<ul class="artifacts">' + "".join(pieces) + '</ul>' if pieces else '<p class="muted">No artifact links recorded.</p>'


def _pill(value):
    color = {"completed": "ok", "failed": "bad", "not_assessed": "neutral", "unknown": "warn",
             "detected": "warn", "inconclusive": "warn", "not_detected": "neutral", "unavailable": "neutral"}.get(str(value), "neutral")
    return '<span class="pill ' + color + '">' + _esc(value).replace("_", " ") + '</span>'


def _kv(values):
    return '<dl class="kv">' + ''.join('<div><dt>' + _esc(k) + '</dt><dd>' + _esc(v) + '</dd></div>' for k, v in values.items()) + '</dl>'


def _render_finding(finding, outdir):
    title = finding.get("gene") or finding.get("family") or finding.get("protein_id") or finding.get("category") or "Finding"
    coords = "unresolved"
    if finding.get("contig") is not None and finding.get("start") is not None and finding.get("end") is not None:
        coords = f'{finding["contig"]}:{finding["start"]}–{finding["end"]}'
    attributes = finding.get("attributes", {})
    artifact_values = finding.get("artifacts", [])
    return ('<details class="gene"><summary><strong>' + _esc(title) + '</strong><span>' +
            _esc(finding.get("category", "unspecified")) + ' · ' + _esc(coords) + '</span></summary>' +
            _kv({"Finding ID": finding.get("finding_id"), "Locus ID": finding.get("locus_id"),
                 "Protein ID": finding.get("protein_id"), "Family": finding.get("family"),
                 "Coordinates (1-based inclusive)": coords, "Strand": finding.get("strand"),
                 "Reference": finding.get("reference"), "Identity (as reported)": finding.get("identity"),
                 "Coverage (as reported)": finding.get("coverage"), "Evidence level": finding.get("evidence_level"),
                 "Finding interpretation": finding.get("interpretation"), "Source execution": finding.get("execution_status")}) +
            ('<details><summary>Source attributes</summary><pre>' + _esc(json.dumps(_jsonable(attributes), indent=2, ensure_ascii=False)) + '</pre></details>' if attributes else '') +
            (_artifact_list(artifact_values, outdir) if artifact_values else '') + '</details>')


def _render_candidate(candidate, outdir):
    values = {k: v for k, v in candidate.items() if k not in {"artifacts", "analysis", "analyses", "evidence_paths", "references", "source_evidence", "consolidated_evidence"}}
    paths = list(_as_list(candidate.get("artifacts"))) + list(_as_list(candidate.get("evidence_paths")))
    query = candidate.get("query", {})
    if isinstance(query, dict) and query.get("path"):
        paths.append(query["path"])
    analyses = candidate.get("analyses", {})
    if isinstance(analyses, dict):
        for analysis in analyses.values():
            if isinstance(analysis, dict):
                paths.extend(analysis[key] for key in ("sequence_path", "input", "labels", "alignment", "tree", "domain_table", "native_output") if analysis.get(key))
    paths = list(dict.fromkeys(str(p) for p in paths))
    assessment = candidate.get("assessment", {})
    assessment_html = ('<h4>Interpretation and follow-up</h4>' + _kv(assessment)) if assessment else ''
    from .deep_consolidation import consolidate, render_summary
    assessment_html += render_summary(consolidate(candidate))
    from .deep_reporting import tree_reference_summary
    summary = tree_reference_summary(candidate)
    motifs = analyses.get('motifs')
    if motifs:
        assessment_html += '<h4>Motif assessment</h4>' + _kv(motifs)
    profile_scan = analyses.get('prosite_profiles')
    if profile_scan:
        assessment_html += '<h4>PROSITE profiles and residue observations</h4>' + _kv(profile_scan)
    prosite = analyses.get('prosite_patterns')
    if prosite:
        assessment_html += '<h4>PROSITE pattern assessment</h4>' + _kv(prosite)
    tree_runs = summary['runs']
    if tree_runs:
        assessment_html += '<h4>Tree references and grouping</h4>' + _kv({k:v for k,v in summary.items() if k != 'runs'})
        assessment_html += '<table><tr><th>Tree run</th><th>SH-aLRT</th><th>UFBoot</th><th>Query-side reference annotations</th></tr>'
        for row in tree_runs:
            refs = row['query_side_references']
            text = '; '.join(str(r['tip']) + ': ' + str(r['accession']) + ' | ' + str(r['database_label']) + ' | review: ' + str(r['review_status']) + ' | group: ' + str(r['comparison_group']) for r in refs)
            assessment_html += '<tr>' + ''.join('<td>' + _esc(v) + '</td>' for v in (row.get('name'), row.get('sh_alrt'), row.get('ufboot'), text or 'Reference mapping unavailable')) + '</tr>'
        assessment_html += '</table><p>Support describes an unrooted split, not a functional classification.</p>'
    return ('<details class="gene"><summary><strong>Deep assessment: ' +
            _esc(candidate.get("gene") or candidate.get("family") or candidate.get("candidate_id") or candidate.get("finding_id") or "candidate") +
            '</strong> ' + _pill(candidate.get("execution", candidate.get("execution_status", "unknown"))) + '</summary>' + assessment_html + _kv(values) +
            '<details><summary>Selected references</summary><pre>' + _esc(json.dumps(_jsonable(candidate.get("references", [])), indent=2, ensure_ascii=False)) +
            '</pre></details><details><summary>Analysis and source evidence</summary><pre>' +
            _esc(json.dumps(_jsonable({"analyses": analyses, "source_evidence": candidate.get("source_evidence", [])}), indent=2, ensure_ascii=False)) +
            '</pre></details>' + _artifact_list(paths, outdir) + '</details>')


def _render_record(record, rows, outdir, layer):
    sid, module = _sample_id(record), _module(record)
    own = [f for f in rows if f["sample_id"] == sid and f["module"] == module]
    categories = sorted({str(f.get("category", "unspecified")) for f in own})
    searchable = " ".join([sid, module, layer, json.dumps(_jsonable(record), ensure_ascii=False)]).lower()
    notes = _as_list(record.get("notes"))
    errors = _as_list(record.get("errors", record.get("error")))
    state = _execution(record)
    message = ""
    if state != "completed":
        message = '<p class="callout">This module provides no completed-screen conclusion. Any retained findings are provisional artifacts, not a validated negative result.</p>'
    elif not own:
        if _interpretation(record) == "not_detected":
            message = '<p class="muted">No findings were detected within this screen’s recorded scope and thresholds.</p>'
        else:
            message = '<p class="muted">No discrete findings are recorded. Read the module interpretation and metrics; an empty list does not itself establish absence.</p>'
    if module == 'deepsafe':
        from .deep_reporting import completion_summary
        message += '<h4>Selected execution and deferred scope</h4>' + _kv(completion_summary(record))
    if record.get("complete") is False:
        message += '<p class="callout">Overall candidate coverage is incomplete. Deferred work, selected-candidate execution and biological interpretation are reported separately.</p>'
    return ('<article class="module filterable" data-sample="' + _esc(sid) + '" data-state="' + _esc(state) +
            '" data-categories="' + _esc(" ".join(categories)) + '" data-search="' + _esc(searchable) + '">' +
            '<div class="module-head"><div><p class="eyebrow">' + _esc(layer) + ' · ' + _esc(sid) + '</p><h3>' + _esc(module) +
            '</h3></div><div>' + _pill(state) + ' ' + _pill(_interpretation(record)) + '</div></div>' + message +
            ('<ul class="notes">' + ''.join('<li>' + _esc(n) + '</li>' for n in notes) + '</ul>' if notes else '') +
            ('<div class="error"><strong>Recorded error</strong><pre>' + _esc('\n'.join(_plain(e) for e in errors)) + '</pre></div>' if errors else '') +
            ''.join(_render_finding(f, outdir) for f in own) +
            ''.join(_render_candidate(c, outdir) for c in record.get("candidates", []) or [] if isinstance(c, dict)) +
            '<details class="evidence"><summary>Metrics, provenance and native evidence</summary>' +
            _kv({k: record[k] for k in ("metrics", "tool_version", "database_version", "provenance", "input_hash", "input_hashes", "config_hash") if k in record}) +
            _artifact_list(record.get("artifacts", []), outdir) + '</details></article>')


def _tsv_value(value):
    if value is None:
        return ""
    text = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list, tuple)) else str(value)
    # Preserve machine records in JSON. Prevent spreadsheet formula execution in
    # the human-facing TSV exports when arbitrary tool labels begin with =,+,-,@.
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        text = "'" + text
    return text


def _write_tsv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _tsv_value(row.get(k)) for k in columns})


CSS = """
:root{--ink:#182c3c;--muted:#536674;--line:#d6e0e7;--teal:#14666b;--bg:#f3f6f8;--paper:#fff;--warn:#86540c;--bad:#a33238}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}a{color:#09636c;text-underline-offset:3px;overflow-wrap:anywhere}button,input,select{font:inherit}a:focus-visible,summary:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #d9a93b;outline-offset:3px}
header{background:#173a4a;color:white;padding:42px max(24px,calc((100vw - 1160px)/2)) 32px}header h1{font-size:36px;letter-spacing:-1px;margin:0 0 9px}header p{margin:0 0 12px;max-width:950px;color:#d9e7ec}header .eyebrow{color:#a9d7d7}.eyebrow{text-transform:uppercase;letter-spacing:1.4px;font-size:11px;font-weight:750;margin:0 0 4px;color:var(--teal)}main{max-width:1208px;margin:auto;padding:24px}h2{font-size:23px;margin:30px 0 12px}h3{font-size:19px;margin:0}p{margin:8px 0 12px}.notice,.callout{padding:14px 18px;border-left:4px solid #bc8b33;background:#fff8e9;border-radius:3px}.notice strong{display:block}.muted{color:var(--muted)}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:20px 0}.stat{background:white;border:1px solid var(--line);border-radius:9px;padding:15px 19px}.stat b{display:block;font-size:30px;line-height:1.2}.stat span{color:var(--muted);font-size:13px}.panel,.module{background:white;border:1px solid var(--line);border-radius:10px;padding:22px;margin:14px 0}.module-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:14px}.pill{display:inline-block;border-radius:20px;padding:3px 9px;font-size:12px;margin:2px 0;white-space:nowrap}.ok{background:#def0eb;color:#25604e}.warn{background:#fff0cf;color:var(--warn)}.bad{background:#fce5e5;color:var(--bad)}.neutral{background:#eaf0f4;color:#425b6a}.table-scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th{text-align:left;background:#eef3f6;color:#395364;font-weight:650}th,td{padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:top}.filters{display:flex;gap:12px;flex-wrap:wrap;padding:16px;background:#e8eff3;border-radius:8px}.filters label{display:flex;flex-direction:column;font-size:12px;font-weight:600;flex:1;min-width:130px}.filters input,.filters select{padding:8px 10px;border:1px solid #afc1cc;border-radius:5px;background:white;width:100%;margin-top:4px;color:var(--ink)}.filters label:first-child{flex:2}summary{cursor:pointer;overflow-wrap:anywhere}details.gene{border:1px solid var(--line);border-radius:6px;margin:9px 0}details.gene>summary{padding:13px 15px;background:#f7f9fa}details.gene>summary span{display:inline-block;margin-left:13px;font-size:12px;color:var(--muted)}details.gene>details,details.gene>.artifacts{margin:12px 16px}details.evidence{margin-top:15px;border-top:1px solid var(--line);padding-top:14px}details.evidence>summary{font-size:13px;font-weight:600;color:var(--teal)}.kv{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 20px;margin:10px 16px}.kv div{padding:9px 0;border-bottom:1px solid #edf1f4;min-width:0}.kv dt{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}.kv dd{margin:3px 0 0;overflow-wrap:anywhere;font-size:13px;white-space:pre-wrap}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f7;padding:13px;border-radius:5px;font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}.error{color:var(--bad)}.error pre{background:#fff0f0}.artifacts{font-size:13px;padding-left:20px}.recommendation{border-top:1px solid var(--line);padding:17px 0}.recommendation:first-child{border-top:0}.recommendation h3{font-size:15px}.recommendation p{font-size:14px;margin:7px 0}.evidence-id{font:11px ui-monospace,monospace;color:var(--muted);overflow-wrap:anywhere}.downloads{display:flex;gap:18px;flex-wrap:wrap;font-size:13px}.small{font-size:12px}.hidden,[hidden]{display:none!important}footer{max-width:1208px;margin:0 auto;padding:10px 24px 35px;color:var(--muted);font-size:12px}@media(max-width:700px){header{padding:26px 20px}header h1{font-size:28px}main{padding:15px}.stats{grid-template-columns:repeat(2,1fr)}.module-head{display:block}.kv{grid-template-columns:1fr}.panel,.module{padding:16px}}@media print{body{background:white}header{background:white;color:var(--ink);padding:18px 0}header p{color:var(--muted)}main{padding:0}.filters{display:none}.module,.panel{break-inside:avoid}.hidden{display:block!important}details{display:block}a{color:var(--ink)}}
"""


SCRIPT = """
(() => {
  const q = document.getElementById('q'), sample = document.getElementById('sample-filter');
  const state = document.getElementById('state-filter'), category = document.getElementById('category-filter');
  const panels = Array.from(document.querySelectorAll('.filterable'));
  function update() {
    const query = q.value.trim().toLowerCase(); let shown = 0;
    panels.forEach(p => {
      const yes = (!sample.value || p.dataset.sample === sample.value) &&
        (!state.value || p.dataset.state === state.value) &&
        (!category.value || p.dataset.categories.split(' ').includes(category.value)) &&
        (!query || p.dataset.search.includes(query));
      p.hidden = !yes; if (yes) shown++;
    });
    document.getElementById('filter-count').textContent = shown + ' of ' + panels.length + ' module records shown';
    document.getElementById('empty-filter').hidden = shown !== 0;
  }
  [q,sample,state,category].forEach(el => el.addEventListener('input', update)); update();
})();
"""


def _methods(run, records, deep_records):
    lines = ["BaSIS methods and execution record", "", "Software version: " + _plain(run.get("version")),
             "Run ID: " + _plain(run.get("run_id")), "Started: " + _plain(run.get("started_at")),
             "Finished: " + _plain(run.get("finished_at")), "Configuration hash: " + _plain(run.get("config_hash")),
             "Options: " + _plain(run.get("options")), "Validation mode: " + _plain(run.get("validation_mode")), "",
             "This file records the available execution evidence. It is not a claim that all configured analyses completed.",
             "Coordinates in normalized findings are 1-based inclusive. Unknown coordinates remain unset.",
             "Not detected applies only to a completed screen and its documented scope, database and thresholds.",
             "Homology, curated-model calls, predictions and exploratory assessments remain distinct evidence types.",
             "Model outputs are not interpreted as exposure-risk probabilities. No organism-level safety verdict is computed.",
             "User-provided phenotypes are available_unreviewed; this workflow does not verify or perform those experiments.",
             "HybAs Kraken targets describe read-based operational decisions; they are not independent final-assembly species identifications.",
             "Declared taxonomy is retained separately from imported HybAs targets; sample source evidence is recorded in evidence.json.",
             "TSV exports prefix spreadsheet formula-like text with an apostrophe; evidence.json preserves original values.", ""]
    if run.get("report_replays"):
        replay = run["report_replays"][-1]
        lines += ["Saved-evidence report replay; no native analyses or GPU inference performed.",
                  "Reporting renderer version: " + _plain(replay.get("renderer_version")),
                  "Report generated at (UTC): " + _plain(replay.get("report_generated_at_utc")),
                  "Source evidence: " + _plain(replay.get("source_evidence")),
                  "Original records, candidate selection, analyses and interpretations are unchanged.",
                  "Source-file integrity is recorded; native artifacts and scientific conclusions are not independently revalidated.", ""]
    if run.get("report_reconciliation"):
        review = run["report_reconciliation"]
        lines += ["Report-only reconciliation; no native analyses or GPU inference performed.",
                  "Started/Finished above describe report generation, not a new biological analysis.",
                  "Original assessment finished: " + _plain(review.get("original_run", {}).get("finished_at")),
                  "ICEscreen retry finished: " + _plain(review.get("retry_finished_at")),
                  "Source evidence: " + _plain(review.get("source_evidence")),
                  "Source SHA256: " + _plain(review.get("source_sha256")),
                  "Retry evidence: " + _plain(review.get("retry_evidence")),
                  "Retry SHA256: " + _plain(review.get("retry_sha256")),
                  "The reconciliation preserved original DeepSafe candidate evidence and deferred scope.", ""]
    if isinstance(run.get("report_refresh"), dict):
        refresh = run["report_refresh"]
        lines += ["Report-only metadata refresh; no native analyses or GPU inference performed.",
                  "Report generated at (UTC): " + _plain(refresh.get("report_generated_at_utc")),
                  "Canonical candidate label corrections: " + _plain(refresh.get("canonical_label_corrections")),
                  "Refresh source evidence: " + _plain(refresh.get("source_evidence")),
                  "The original source snapshot is preserved. Recorded analyses, interpretations and deferred scope remain unchanged.",
                  "Historical reconciliation hashes describe the source snapshot; the new refresh audit records before/after hashes for the reporting metadata changes.", ""]
    for record in list(records) + list(deep_records):
        lines.append(f'{_sample_id(record)} / {_module(record)}: execution={_execution(record)}; interpretation={_interpretation(record)}')
        for key in ("tool_version", "database_version", "database_manifest", "input_hash", "input_hashes", "config_hash", "provenance", "parameters", "commands", "metrics", "notes", "error", "errors"):
            if key in record:
                lines.append("  " + key + ": " + _plain(record[key]))
        lines.append("")
    return "\n".join(lines) + "\n"


def _sample_taxon_label(sample):
    label = _esc(sample.get("taxon") or "Taxon not supplied")
    if sample.get("taxon_source") == "hybas_kraken_operational_target":
        label += " · HybAs read-based operational target"
    elif sample.get("taxon_source") == "user_confirmed":
        label += " · user-confirmed"
    return label


def _render_sample_taxonomy(sample):
    content = '<h3>Taxonomy and source evidence</h3>'
    content += _kv({label: sample[key] for key, label in (
        ("taxon", "Taxon"), ("taxon_rank", "Rank"), ("taxon_source", "Source"),
        ("taxon_status", "Status")) if sample.get(key)})
    context = sample.get("hybas_context") or {}
    if not context:
        return content
    content += '<p class="small muted">HybAs targets guide read processing. Their taxonomic rank is preserved; they do not establish a final-assembly species identification.</p>'
    target = context.get("selected_target") or {}
    content += _kv({"HybAs metadata status": context.get("status"),
                    "HybAs taxonomy status": context.get("taxonomy_status", "not_recorded"),
                    "HybAs operational target": target.get("name") or "Not available",
                    "Target rank": target.get("rank") or "Not available",
                    "Target taxid": target.get("taxid") or "Not available",
                    "Target source": target.get("source") or "Not available",
                    "Assembly binding": (context.get("binding") or {}).get("status")})
    reads = context.get("read_taxonomy") or {}
    if reads:
        content += '<h4>Kraken read classifications</h4><p class="small muted">Kraken-reported percentages for leading species, shown separately for each platform.</p>'
        for platform, evidence in reads.items():
            content += '<p><strong>' + _esc(platform) + '</strong> · ' + _esc(evidence.get("status")) + '</p>'
            species = evidence.get("top_species") or []
            if species:
                content += '<ul>' + ''.join(
                    '<li>' + _esc(row.get("name")) + ' · ' + _esc(row.get("percentage")) +
                    '% (Kraken report) · taxid ' + _esc(row.get("taxid")) + '</li>' for row in species) + '</ul>'
    if context.get("warnings"):
        content += '<ul>' + ''.join('<li>' + _esc(message) + '</li>' for message in context["warnings"]) + '</ul>'
    content += '<details><summary>HybAs source evidence and checksums</summary><pre>'
    content += _esc(json.dumps(_jsonable(context), indent=2, ensure_ascii=False)) + '</pre></details>'
    return content


@tracked("Writing consolidated report and evidence artifacts")
def write_report(run: dict, samples: list[dict], records: list[dict], deep_records: list[dict], outdir: Path) -> dict:
    """Write the complete offline report bundle; return absolute artifact paths."""
    outdir = Path(outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    records, deep_records = list(records), list(deep_records)
    combined = records + deep_records
    findings = _all_findings(combined)
    recommendations = build_recommendations(samples, records, deep_records)
    artifacts = {"html": outdir / "report.html", "findings_tsv": outdir / "findings.tsv",
                 "recommendations_tsv": outdir / "recommendations.tsv", "evidence_json": outdir / "evidence.json",
                 "methods_txt": outdir / "methods.txt", "module_scope_tsv": outdir / "module-scope.tsv"}
    from .report_citation import write_citations
    artifacts.update({key: Path(path) for key, path in
                      write_citations(outdir, run.get("version") or None).items()})
    _write_tsv(artifacts["findings_tsv"], FINDING_COLUMNS, findings)
    _write_tsv(artifacts["recommendations_tsv"], RECOMMENDATION_COLUMNS, recommendations)
    from .report_sample_scope import SCOPE_COLUMNS, module_scope_rows
    _write_tsv(artifacts["module_scope_tsv"], SCOPE_COLUMNS, module_scope_rows(run, samples, combined))
    evidence = {"schema_version": "basis-report-1", "run": run, "samples": samples,
                "records": records, "deep_records": deep_records, "normalized_report_findings": findings,
                "recommendations": recommendations,
                "provided_phenotypes": {str(s.get("sample_id", "unknown")): _phenotypes(s) for s in samples},
                "interpretation_limit": "Research screening evidence; no organism-level safety verdict or global safety score."}
    artifacts["evidence_json"].write_text(json.dumps(_jsonable(evidence), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifacts["methods_txt"].write_text(_methods(run, records, deep_records), encoding="utf-8")

    from .report_dashboard import render_dashboard
    content = render_dashboard(run, samples, records, deep_records, findings, recommendations, outdir)
    artifacts["html"].write_text(content, encoding="utf-8")
    from .report_navigation import write_navigation_index
    artifacts["report_navigation_json"] = write_navigation_index(run, deep_records, outdir)
    return {key: str(path) for key, path in artifacts.items()}
