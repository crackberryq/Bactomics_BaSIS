# Evidence reporting

`basis.report.write_report(run, samples, records, deep_records, outdir)` produces:

| Return key | File | Purpose |
| --- | --- | --- |
| `html` | `report.html` | Standalone offline report with execution table, sample phenotypes, assessment priorities, module filters and expandable locus evidence. |
| `findings_tsv` | `findings.tsv` | Normalized findings with source execution state and traceable finding IDs. |
| `recommendations_tsv` | `recommendations.tsv` | Controlled, evidence-linked assessment suggestions. |
| `evidence_json` | `evidence.json` | Original records, samples and run metadata, plus derived report findings/recommendations. |
| `methods_txt` | `methods.txt` | Available execution/provenance information and interpretation limits. |

Paths returned are absolute. Keep `report.html` with its results/logs tree when sharing the bundle. The report has no external styles, scripts, fonts or network requests. It escapes tool and user text. Native evidence links resolve only beneath the report directory; outside paths remain unlinked text. Spreadsheet formula-like TSV text is prefixed with an apostrophe, while JSON preserves original values.

## State and evidence

Module records use `sample_id`, `module`, `execution_status`, `interpretation`, `findings`, `metrics`, `artifacts`, `notes` and optional error/provenance fields. The report never infers completion from a file's existence or an empty findings list. Failed and not-assessed modules display an unavailable interpretation; retained intermediate findings do not generate biological assessment recommendations. Completed `not_detected` records apply only to the recorded scope and thresholds.

The core and optional PF2 records belong in `records`; optional deep module records belong in `deep_records`. Disabled optional modules should still be supplied by the orchestrator as `not_assessed` records so omissions remain explicit. Deep candidates retain source finding IDs, selection reasons, reference selections, uncertainty and evidence links. The report does not independently assign a biological family or transform a model output into exposure risk.

Root-assigned `finding_id` and `locus_id` values are retained. A report-local stable ID is generated only if a source finding has no ID. Unknown coordinates are not replaced by zero. The report shows identity/coverage values as reported, without inventing units or threshold interpretations.

## Supplied phenotype evidence

`sample.phenotypes` accepts a list of records (for example `assay`, `result`, `source`), an assay-to-result mapping, or a description string. Supplied results or sources are marked `available_unreviewed`: recording a user's result does not validate it or imply that BaSIS performed the assay. An assay name alone, or a record explicitly marked planned/pending/not performed, remains `not_assessed` and does not suppress an otherwise relevant recommendation.

Existing susceptibility evidence causes AMR follow-up to request review of that evidence and its coverage before suggesting repetition. Hemolysis evidence is relevant to part of a virulence assessment; it is not treated as testing all virulence-associated functions. Existing toxicity evidence similarly prompts coverage review before additional assessment.

## Assessment suggestions

Controlled templates address AMR phenotype evidence, virulence-associated functional uncertainty, uncertain BGC products, model applicability and mobile context. They recommend qualified assessment, not laboratory procedures or an automatic safe/unsafe verdict. General mobile-element predictions alone do not trigger a transferability claim or experimental recommendation. A mobile-context recommendation requires overlapping reported intervals with an AMR or virulence finding on the same contig; the report explicitly labels overlap as a context flag rather than proof of mobility.

Execution failures generate computational repair/review suggestions. No-hit records alone do not generate unnecessary wet-lab recommendations. The absence of automatically generated suggestions does not establish that an organism or proposed use needs no further assessment.

Tests cover failed-versus-negative state, supplied susceptibility evidence, optional/deep visibility, context overlap, escaping, restricted artifact links and inert TSV text. These are software behavior tests, not biological validation.
