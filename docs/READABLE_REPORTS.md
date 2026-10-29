# Reading the screening report

The screening report shows the source annotation alongside the gene or feature
name. For example, an antibiotic name that AMRFinderPlus recorded in an element
description or subclass is visible and searchable without opening a native TSV.
BaSIS does not expand a drug class into an inferred list of antibiotics.

## Finding the result you need

The report presents screening results before input details and the genome
explorer. **Results by tool** explains the saved result for every requested
screening tool, including requested results with no supplied record. It keeps
**No findings reported**, **Inconclusive result**, **Assessment incomplete**,
and **Not assessed** distinct. An empty table alone is not labelled negative.

**View findings** selects that exact tool and sample and includes all its saved
evidence groups, with their original qualifications still visible. The sample
selector appears when multiple samples have findings. The source rows are not
deduplicated across tools or promoted into new conclusions.

**Tools finished**, such as 14 / 14, counts completed execution. A tool can
finish while leaving its assessment incomplete or inconclusive. Such results
retain a visible review notice and the recorded reason. The expandable tool
explanation shows what the tool assesses and its saved execution/interpretation.

Sample and input context is expandable near the top. Empty optional model
panels are omitted when no requested or retained model output exists; disabled
module records remain in scope information and exports. Model results that do
exist remain separate from the screening findings.

Use **Read screening findings** at the top of the report. The finding table shows:

- The gene or feature and its full recorded description.
- Source classifications: AMRFinderPlus class/subclass and element type; CARD
  drug class, antibiotic, mechanism and family when those fields were recorded;
  other tools' recorded feature or product information.
- The tool, evidence category and any existing qualification.
- Separately labelled identity, reference coverage and available query coverage.
  RGI's reference length ratio is labelled as a length ratio, never as alignment
  coverage or a confidence score.
- The sample, input protein/locus and available coordinates.

Search by gene, drug, description, classification, reference or location. Search
and tool filters cover every saved row, including rows beyond the former
250-record display limit. Interactive pages display 50 matching rows at a time.
Without JavaScript the complete table remains visible. Printing includes all
evidence groups, including rows outside the current interactive filter.

**Main review findings** uses the existing evidence classification. Use the
evidence-group selector to inspect **Exploratory / below threshold** records,
**Provisional · module incomplete** records, or **All saved findings**. Displaying
these records does not promote them, change thresholds or change follow-up
selection. Counts refer to source records, not unique genes or independent
confirmations across tools.

Expand **Details and original source fields** to read the saved native row in a
labelled layout. Unrecorded descriptions and classifications remain explicit.
The report also retains **Technical tool records and scope**. Failed and incomplete module records remain visible there and in the
existing execution-issues section.

**Readable findings · TSV** downloads `tables/screening-findings.tsv` with the
descriptions, classifications, evidence groups and match details in separate
columns. It contains every screening row. Potential spreadsheet formula cells
are quoted in this convenience export; the original normalized TSV and JSON
records retain their original values.

## Existing results

This is a rendering update. Native parsers, analysis commands, thresholds,
evidence records and candidate selection are unchanged. It reads descriptions
already retained in `attributes.raw`, `attributes.native`, and source-title
metadata. It requires no new database search.

The standalone workflow tracks report code separately from native workers. A
report-only refresh can use the existing `safety.reporting` entry point with the
saved sample, configuration and evidence files; see its `--help` for the explicit
paths and report view. Keep the workflow stopped while refreshing its output
bundle. If updating from a version with other analysis changes, inspect the
native workflow's dry-run before resuming the full workflow. This rendering
change does not override normal source/input invalidation.

The description is a tool's annotation. Drug labels are not measured
susceptibility results for the sample. Raw tool artifacts and the full saved
module records remain linked for reproducibility and detailed review.

## Source schemas and checks

- [AMRFinderPlus output documentation](https://github.com/ncbi/amr/wiki/Interpreting-results)
  and the parser's supported current/historical header aliases.
- [CARD / RGI output definitions](https://github.com/arpcard/rgi/blob/master/docs/rgi_main.rst).
- The other native adapters' saved source rows and reference titles; reporting
  performs no new annotation lookup or association across findings.

`test_report_screening.py` exercises native AMRFinder parsing into readable
output, historical headers, missing values, source escaping, all-row exports,
coverage labels, qualifications, output-path aliases and preservation of saved
evidence. It uses synthetic annotations only.

The dedicated `report-browser` CI job checks actual offline Chromium behavior:
search beyond row 250, pagination, evidence filters, source-field expansion,
phone-width containment, no-JavaScript access and complete printed tables.
It preserves screenshots of the synthetic fixture. Local browser tests use
`BASIS_TEST_REPORT_BROWSER=1`; an explicitly chosen compatible Chromium can be
supplied with `BASIS_CHROMIUM_EXECUTABLE`.
