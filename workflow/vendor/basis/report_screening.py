"""Readable views of saved screening annotations; no analysis or reclassification.

Native descriptions and classifications are copied from each finding's own
source row. Missing fields remain missing: a gene or drug class is never used
to invent an antibiotic, mechanism, phenotype, or description.
"""
from __future__ import annotations

import csv
import html
import json
import math
import re

from .report_context import finding_context


PURPOSES = {
    "amrfinder": "Gene and variant annotations, including resistance, stress and virulence categories.",
    "rgi": "Matches to CARD resistance models and their recorded classifications.",
    "vfdb": "Sequence matches to reference proteins in the virulence-factor database.",
    "bacmet": "Sequence matches to reference proteins associated with biocides and metals.",
    "antismash": "Predicted biosynthetic regions and product classes.",
    "genomad": "Predicted viral and plasmid sequences.",
    "mobsuite": "Plasmid reconstruction, typing and recorded mobility predictions.",
    "integronfinder": "Predicted integrons and associated components.",
    "isescan": "Predicted insertion sequences, with complete or partial labels when recorded.",
    "icescreen": "Predicted integrative and mobilizable elements within its recorded taxonomic scope.",
    "minced": "Predicted CRISPR repeat-spacer arrays.",
    "crisprcasfinder": "Predicted CRISPR arrays and Cas systems.",
    "mlst": "Sequence-type assignments and allele-scheme results.",
}
CATEGORIES = {
    "amr": "Antimicrobial resistance annotation", "virulence": "Virulence-associated annotation",
    "stress": "Stress-response annotation", "biosynthetic": "Biosynthetic region",
    "bgc": "Biosynthetic region", "plasmid": "Plasmid prediction", "prophage": "Viral prediction",
    "integron": "Integron prediction", "is": "Insertion-sequence prediction",
    "ice": "Integrative-element prediction", "crispr": "CRISPR / Cas prediction", "typing": "Sequence typing",
}
EVIDENCE = {
    "curated_model": "Curated reference / model match", "native": "Tool-reported prediction",
    "native_prediction": "Tool-reported prediction", "homology": "Sequence similarity match",
    "exploratory": "Exploratory match", "prediction": "Tool-reported prediction", "typing": "Sequence typing",
}
EXPORT_COLUMNS = (
    "sample_id", "module", "finding_id", "review_group", "execution_status", "module_interpretation",
    "gene_or_feature", "source_description", "source_classifications", "category", "evidence",
    "match_details", "protein_id", "contig", "start", "end", "strand", "reference", "notes",
)


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def _present(value):
    return _text(value).strip().lower() not in {"", "na", "n/a", "none", "null", "-", "."}


def _esc(value):
    return html.escape(_text(value), quote=True)


def _key(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _get(row, *names):
    # Prefer exact native headers, retaining compatible historical spelling.
    for name in names:
        if name in row and _present(row[name]):
            return _text(row[name])
        matches = [v for k, v in row.items() if _key(k) == _key(name) and _present(v)]
        if len(matches) == 1:
            return _text(matches[0])
    return ""


def _percent(value):
    if isinstance(value, bool):
        return "Not recorded"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "Not recorded"
    return f"{number:g}%" if math.isfinite(number) and 0 <= number <= 100 else "Not recorded"


def describe_finding(finding):
    """Build display text from one saved finding without changing it."""
    from .report import _exploratory_finding

    module = finding.get("module", finding.get("tool", "unspecified"))
    attrs = finding.get("attributes") or {}
    attrs = attrs if isinstance(attrs, dict) else {}
    native = attrs.get("raw") if isinstance(attrs.get("raw"), dict) else attrs.get("native")
    native = native if isinstance(native, dict) else {}
    context = finding_context(finding)
    title = finding.get("gene") or finding.get("family") or finding.get("reference") or "Unnamed feature"
    description = _get(native, "Element name", "Sequence name", "Protein name", "Description", "Product")
    fields = []

    def add(label, value):
        if _present(value):
            fields.append((label, _text(value)))

    if module == "amrfinder":
        add("Class", _get(native, "Class"))
        add("Subclass", _get(native, "Subclass"))
        add("Element type", _get(native, "Type", "Element type"))
        add("Element subtype", _get(native, "Subtype", "Element subtype") or attrs.get("subtype"))
    elif module == "rgi":
        description = _get(native, "Best_Hit_ARO") or _text(finding.get("family"))
        for label in ("Drug Class", "Antibiotic", "Resistance Mechanism", "AMR Gene Family"):
            add(label, _get(native, label))
        add("Model type", attrs.get("model_type") or _get(native, "Model_type"))
    elif module in {"vfdb", "bacmet"}:
        description = _text(attrs.get("subject_title")) or description
        add("Reference", finding.get("reference"))
    elif module == "isescan":
        title = context["source_call"]
    elif module == "antismash":
        add("Product classes", native.get("products"))
    elif module == "mobsuite":
        add("Predicted mobility", attrs.get("predicted_mobility"))
    elif module == "mlst":
        add("Allele scheme", attrs.get("scheme"))
        add("Sequence type", attrs.get("sequence_type"))
    elif module in {"minced", "crisprcasfinder"}:
        for key, label in (("feature_type", "Feature type"), ("repeat_count", "Repeat count"),
                           ("spacer_count", "Spacer count"), ("evidence_level", "Tool evidence level")):
            add(label, attrs.get(key))
    if not description:
        description = _text(finding.get("description"))
    if not description and finding.get("family") != title:
        description = _text(finding.get("family"))

    execution = _text(finding.get("execution_status")) or "unknown"
    group = "provisional" if execution != "completed" else "exploratory" if _exploratory_finding(finding) else "review"
    evidence = EVIDENCE.get(finding.get("evidence_level"), _text(finding.get("evidence_level")).replace("_", " ")) or "Not recorded"
    method = attrs.get("native_method") or _get(native, "Method")
    cutoff = attrs.get("cutoff") or _get(native, "Cut_Off")
    if _present(method):
        evidence += " · method " + _text(method)
    if _present(cutoff):
        evidence += " · " + _text(cutoff)
    notes = list(context["caveats"])
    for flag in attrs.get("flags") or []:
        notes.append("Source flag: " + _text(flag).replace("_", " "))
    if attrs.get("nudged") is True:
        notes.append("RGI reports this hit as nudged from its original cutoff category.")
    if group == "provisional":
        notes.insert(0, "Retained record; the source module did not complete. Treat this finding as provisional.")
    elif group == "exploratory":
        notes.insert(0, "Exploratory or below-threshold record; kept separate from the main review findings.")
    matches = []
    if module in {"amrfinder", "rgi", "vfdb", "bacmet"} or finding.get("identity") is not None:
        matches.append(("Identity to reference", _percent(finding.get("identity"))))
        matches.append(("Reference coverage", _percent(finding.get("coverage"))))
    if "query_coverage" in attrs:
        matches.append(("Query coverage", _percent(attrs["query_coverage"])))
    if attrs.get("reference_length_ratio_percent") is not None:
        matches.append(("Length ratio (not alignment coverage)", _text(attrs["reference_length_ratio_percent"]) + "%"))
    return {
        "title": _text(title), "description": description, "classifications": fields,
        "category": CATEGORIES.get(finding.get("category"), _text(finding.get("category")).replace("_", " ")),
        "evidence": evidence, "matches": matches, "notes": notes, "native": native, "group": group,
    }


def _pairs(items):
    return '<dl class="finding-pairs">' + ''.join(
        '<div><dt>' + _esc(k) + '</dt><dd>' + _esc(v) + '</dd></div>' for k, v in items) + '</dl>'


def write_readable_findings(path, findings):
    """Export all screening rows, including explicit exploratory/provisional rows."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS, delimiter="\t")
        writer.writeheader()
        for finding in findings:
            if finding.get("module") in {"pathogenfinder2", "pf2", "deepsafe"}:
                continue
            data = describe_finding(finding)
            row = {key: finding.get(key) for key in EXPORT_COLUMNS}
            row.update(gene_or_feature=data["title"], source_description=data["description"],
                       source_classifications=json.dumps(dict(data["classifications"]), ensure_ascii=False),
                       category=data["category"], evidence=data["evidence"], review_group=data["group"],
                       match_details="; ".join(k + ": " + v for k, v in data["matches"]),
                       notes="; ".join(data["notes"]))
            # Original text remains in evidence.json and findings.tsv. Protect
            # this new convenience export when opened in spreadsheet software.
            writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                             else v for k, v in row.items()})


def render_screening_findings(findings, names, source_paths):
    rows = [f for f in findings if f.get("module") not in {"pathogenfinder2", "pf2", "deepsafe"}]
    parts = ['<article class="panel screening-reader" id="screening-findings"><div class="panel-head"><div><h3>Screening findings</h3>'
             '<p class="muted">Read the gene or feature, its original description, and the tool’s classifications together.</p></div>'
             '<div class="downloads"><a class="button" href="screening-findings.tsv">Readable findings · TSV</a>'
             '<a class="button" href="findings.tsv">Original normalized findings · TSV</a></div></div>',
             '<p>Descriptions and classifications below are copied from saved source annotations. A drug name or class '
             'describes the source annotation; it is not a measured susceptibility result for this sample.</p>',
             '<details class="reading-guide"><summary>How to read these results</summary><dl class="finding-pairs">'
             '<div><dt>Gene / feature</dt><dd>The name reported by the tool. Protein IDs and coordinates locate it in your input.</dd></div>'
             '<div><dt>Classifications</dt><dd>Original drug, class, subclass, mechanism or feature labels. A class is not expanded into a list of drugs.</dd></div>'
             '<div><dt>Identity</dt><dd>Similarity within the reported sequence alignment; it is not a probability of resistance.</dd></div>'
             '<div><dt>Coverage</dt><dd>How much of the reference or query the reported match covers. The denominator is labelled explicitly.</dd></div>'
             '<div><dt>Review groups</dt><dd>Main review findings follow the saved evidence rules. Exploratory records retain their weaker or below-threshold status. Provisional records come from incomplete modules.</dd></div>'
             '</dl><p>Counts are source records. The same locus can appear in more than one tool; these rows are not independent confirmations.</p></details>',
             '<div class="filters"><label>Search all findings<input id="finding-search" type="search" '
             'placeholder="Gene, drug, description, protein or location…"></label>'
             '<label>Tool<select id="finding-module"><option value="">All tools</option>']
    modules = sorted({f.get("module", "unspecified") for f in rows})
    parts.extend('<option value="' + _esc(m) + '">' + _esc(names.get(m, m)) + '</option>' for m in modules)
    parts.append('</select></label><label>Evidence group<select id="finding-group">'
                 '<option value="review">Main review findings</option><option value="">All saved findings</option>'
                 '<option value="exploratory">Exploratory / below threshold</option>'
                 '<option value="provisional">Provisional · module incomplete</option></select></label></div>'
                 '<p id="finding-count" class="small muted" role="status" aria-live="polite">'
                 + str(len(rows)) + ' saved findings. Filters and pages require JavaScript; all rows are available below without it.</p>'
                 '<div class="table-wrap"><table id="finding-table"><thead><tr>'
                 '<th scope="col">Gene / feature and description</th><th scope="col">Reported classifications</th>'
                 '<th scope="col">Tool and evidence</th><th scope="col">Match details</th>'
                 '<th scope="col">Sample and location</th></tr></thead><tbody>')
    for index, finding in enumerate(rows):
        data = describe_finding(finding)
        module = finding.get("module", "unspecified")
        search = ' '.join(_text(v) for v in (finding.get("sample_id"), finding.get("protein_id"),
                         finding.get("contig"), finding.get("reference"), data["title"], data["description"],
                         data["classifications"], data["category"], data["evidence"]))
        group_label = {"review": "Main review", "exploratory": "Exploratory", "provisional": "Provisional"}[data["group"]]
        detail = '<details class="finding-details"><summary>Details and original source fields</summary>'
        detail += _pairs([(k, finding.get(key) if finding.get(key) is not None else "Not recorded") for k, key in
                          (("Finding ID", "finding_id"), ("Gene", "gene"), ("Family", "family"),
                           ("Reference", "reference"), ("Recorded interpretation", "interpretation"),
                           ("Module interpretation", "module_interpretation"), ("Execution", "execution_status"))])
        if data["native"]:
            detail += '<h4>Original source fields</h4>' + _pairs(data["native"].items())
        else:
            detail += '<p>No original source row was retained in this saved finding.</p>'
        for source in source_paths.get((finding.get("sample_id"), module), []):
            detail += '<a class="text-link" href="' + _esc(source) + '">Full saved module record · JSON</a> '
        detail += '</details>'
        location = [("Sample", finding.get("sample_id")), ("Protein / locus", finding.get("protein_id") or finding.get("locus_id"))]
        if finding.get("contig"):
            location.append(("Contig", finding["contig"]))
        if finding.get("start") is not None and finding.get("end") is not None:
            location.append(("Coordinates", f'{finding["start"]}–{finding["end"]} ({finding.get("strand") or "strand not recorded"})'))
        parts.append('<tr id="screening-finding-' + str(index + 1) + '" data-search="' + _esc(search.lower())
                     + '" data-module="' + _esc(module) + '" data-group="' + data["group"] + '"><td>'
                     '<strong class="finding-name">' + _esc(data["title"]) + '</strong><p class="finding-description">'
                     + _esc(data["description"] or "No descriptive annotation recorded.") + '</p>' + detail + '</td><td>'
                     + (_pairs(data["classifications"]) if data["classifications"] else '<span class="muted">Not recorded</span>')
                     + '</td><td><strong>' + _esc(names.get(module, module)) + '</strong><p>' + _esc(data["category"])
                     + '</p><span class="badge ' + ('warn' if data["group"] != "review" else 'info') + '">' + group_label
                     + '</span><p class="small">' + _esc(data["evidence"]) + '</p>'
                     + ''.join('<p class="small finding-note">' + _esc(note) + '</p>' for note in data["notes"])
                     + '</td><td>' + (_pairs(data["matches"]) if data["matches"] else '<span class="muted">Not applicable / not recorded</span>')
                     + '</td><td>' + _pairs([(k, v if _present(v) else "Not recorded") for k, v in location]) + '</td></tr>')
    parts.append('</tbody></table></div><p id="finding-empty" class="empty" hidden>No findings match these filters. '
                 'Try All saved findings or clear your search.</p><div id="finding-pages" class="finding-pages" hidden>'
                 '<button class="button" id="finding-prev" type="button">Previous page</button>'
                 '<span id="finding-page" class="small"></span>'
                 '<button class="button" id="finding-next" type="button">Next page</button></div>'
                 '<p class="small muted">Search covers every saved screening row, including rows on other pages. '
                 'The readable TSV and printed table contain all evidence groups. Missing source fields are shown as not recorded.</p></article>')
    return ''.join(parts)


CSS = r"""
.screening-reader #finding-table{min-width:1100px;table-layout:fixed}
.screening-reader #finding-table th:nth-child(1){width:29%}
.screening-reader #finding-table th:nth-child(2){width:20%}
.screening-reader #finding-table th:nth-child(3){width:21%}
.screening-reader #finding-table th:nth-child(4){width:14%}
.screening-reader #finding-table th:nth-child(5){width:16%}
.screening-reader td{vertical-align:top;overflow-wrap:anywhere}
.finding-name{font-size:15px;color:#163f4d}.finding-description{margin:7px 0 12px;line-height:1.55}
.finding-pairs{margin:0}.finding-pairs>div{margin:0 0 10px}
.finding-pairs dt{font-size:11px;color:#546d7a;font-weight:600;margin-bottom:2px}
.finding-pairs dd{margin:0;font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}
.finding-details{margin-top:12px}.finding-details>summary{color:#087c78;font-weight:600;cursor:pointer;font-size:12px}
.finding-details[open]{padding:10px;background:#f2f7f8;border-radius:8px;max-height:600px;overflow:auto}
.finding-details h4{margin:16px 0 10px}.finding-note{border-left:3px solid #c39038;padding-left:9px}
.reading-guide{padding:12px 16px;background:#f2f7f8;border-radius:10px;margin:16px 0}
.reading-guide>summary{cursor:pointer;font-weight:600}.reading-guide[open]>.finding-pairs{margin-top:14px}
.finding-pages{display:flex;align-items:center;justify-content:center;gap:18px;margin-top:16px}
.finding-pages[hidden],#finding-table tr[hidden],#finding-empty[hidden]{display:none}
.finding-pages button:disabled{opacity:.45;cursor:default}
@media print{#finding-table tr[hidden]{display:table-row!important}.finding-pages{display:none!important}
 .screening-reader .table-wrap{overflow:visible}.screening-reader #finding-table{min-width:0;font-size:10px}}
"""
