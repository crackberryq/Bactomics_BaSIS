# Refresh saved reports and connect native artifacts

BaSIS rc10 provides source-artifact navigation through the normal installed
reporting commands. No isolate-specific refresh script is required. Rendering
uses saved evidence; it does not execute native analyses, GPU inference or
biological reassessment.

## One report

```bash
basis report --evidence /study/results/evidence.json \
  --expected-sha256 RECORDED_SHA256 \
  --artifact-root /study/original-native-results \
  --outdir /study/reports/review-1
```

The source checksum is optional; when supplied, it must match before any output
is created. The output must be new and outside the source report directory.
The command retains original samples, module records, candidates, selections,
interpretations, historical run identifiers and overall scope. It appends one
report replay audit to the saved reporting history.

The full report links to **Source artifacts**. This page records exact paths
declared in module `artifacts` lists and the original `report.html` entrypoint.
Relative declared paths are resolved against the original evidence directory.
It never recursively discovers the contents of a declared directory. A native
HTML report may itself depend on adjacent assets, so keep its original folder
in place.

## One control panel

```bash
basis control-panel --results /study/reports/review-1 \
  --artifact-root /study/results \
  --artifact-root /study/original-native-results \
  --outdir /study/panels/review-1
```

Repeat `--results` for multiple reports. The panel shows the original run ID,
source version, sample names and recorded execution scope alongside refreshed
navigation. Original reports and exact declared native files appear in the
inventory when they are within an explicitly allowed root. No arbitrary
external directory is scanned.

Artifact roots are permissions for the current command. Saved metadata never
authorizes additional paths. A report refresh permits its selected source
directory by default; a control panel permits each selected report directory.
External paths require `--artifact-root` on each command. The batch manifest
workflow explicitly selects all original evidence files and passes their parent
directories to the panel as source roots.

## Availability labels

| Label | Meaning |
| --- | --- |
| `file` | Exact declared file exists inside an allowed root; link is available. |
| `directory` | Exact declared folder exists; no file link and no missing-file warning. |
| `missing` | Path is inside an allowed root but absent. |
| `outside_allowed_roots` | Current command did not authorize that location. |
| `symlink` | A declared path traverses a symbolic link; it is recorded without a link. |
| `error` | Path cannot be inspected, including an invalid path value or access error. |
| `unsupported_file_type` | Existing path is neither a regular file nor a directory. |

Symbolic links in any component of a declared artifact path are excluded,
including an alias under an explicitly selected root. Root arguments are
resolved to canonical paths for authorization; saved artifact declarations are
not silently rewritten. Permission errors and missing files do not become
biological findings.

## Integrity and provenance

`source-artifacts.json` identifies the original source evidence checksum and run
metadata, exact declarations, current availability and selected roots. It is
bound to the refreshed `evidence.json` checksum and sealed in
`replay-audit.json`. The panel verifies those bindings and declaration identity
before adding links, then rechecks paths using its own explicit roots. A stale
or inconsistent index produces a navigation warning and no source-index links.

These are consistency checks, not digital signatures or native-artifact content
validation. Original native files are linked rather than copied. Checksums
cannot independently establish biological correctness or validate a scientific
claim. Execution status, unresolved results and deferred candidates remain as
recorded.

Original evidence is checked again after rendering. A changed source or
renderer mutation leaves `replay-status.json` unfinalized and no successful
replay audit. Existing output directories are never overwritten.

## Batch source manifest

`basis report --sources sources.json --artifact-root /study --outdir /new/review -v` refreshes the explicit snapshots and writes one `control-panel/index.html`. The JSON is a nonempty list with an `evidence` path per entry; relative paths are relative to the manifest. Optional `evidence_sha256` pins bytes before any reports are created. Optional `sample_id` requires that exact single sample throughout the source records. Duplicate evidence paths are rejected. Multi-sample sources omit `sample_id`. No latest-run discovery or source substitution occurs.

The original source manifest is copied verbatim and `refresh-audit.json` records source hashes, original run identity, report paths and source-preservation checks. Existing outputs are never overwritten. If rendering fails, the audit remains failed; use a new directory after correcting the cause.
