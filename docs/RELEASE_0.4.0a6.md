# BaSIS AWARE 0.4.0a6

Release date: 2026-11-09.

This alpha prerelease carries the pre-beta annotation and evidence-integrity
repairs into the `basis-aware` installation channel. The same fixes are available
in the separate `development` branch for parallel testing.

- Reject incomplete protein FASTA exports when an ordinary or partial GFF CDS
  has no supplied protein. Explicit pseudogene exclusions remain visible warnings.
- Check GenBank CDS coverage, intervals, strands and supplied translations against
  the matching GFF and protein FASTA. Inconsistent, missing or duplicated eligible
  CDSs reject instead of allowing different modules to see different coverage.
- Show saved input/translation warnings in the report overview, retain all details
  and JSON evidence, and distinguish completed tool execution from input limits.
- Reject contradictory native coordinates before mapping a hit to an annotated
  protein. Declared aliases and contained partial alignments remain supported.
- Track coverage-validator code in the workflow so changes trigger revalidation.

See [annotation integrity and supported conventions](ANNOTATION_INTEGRITY.md).
Joined/remote CDS representations remain unsupported; they are rejected rather
than silently flattened. Existing analysis thresholds, reference collections,
biological classifications and placement behavior are unchanged.

The release adds 27 synthetic regressions covering annotation exports, coordinate
consistency and report presentation. Software tests do not establish biological
sensitivity, specificity or organism safety. Native and independent scientific
acceptance gates remain outstanding; this release does not promote BaSIS to beta.

Use an independent Bactologix Dev workspace/environment to compare development
with the `basis-aware` release, and retain the exact source commits for each run.
