# BaSIS AWARE 0.4.0a3

**Alpha prerelease.** Tag: `v0.4.0a3`.

AMRFinder-reported review cues now have an explicit AWARE scope,
`amrfinder-review`. New standard configurations use this scope; older saved
screening bundles retain their policy until an explicit follow-up overlay selects
it. DeepSafe remains disabled by default. The default five-candidate ceiling
limits selection per isolate; deferred findings remain in the review report.

The report now keeps AMR review obligations separate from job completion and
exports every review row to `tables/amr-review.tsv`. Terminal unassessed candidate
results require a reason. Missing review exports regenerate automatically from
saved evidence. Linked exact findings cannot bypass a selected investigation.

Saved-screening follow-up records its executing version separately from the
original screening version. Updated Bactologix adapters can select this release
through a reviewed compatibility contract while preserving the screening run.
Use the separate `Followup.smk` entry point and review the native dry-run before
execution; follow-up does not run screening or prepare missing resources.

Publication requires Python 3.11/3.12, offline browser and real Conda recovery
checks. Synthetic acceptance covers a nonempty selection, budget deferral,
interrupted/resumed stages, reports and an unchanged no-op rerun. No real isolate
analysis or native biological tool was run for this software acceptance.

This remains alpha pending the native AFB01/isolate1 acceptance and scientific
review. It does not recover unreported AMRFinder alignments, establish function,
or automatically close expert review. See [the full flow](AWARE_TO_REPORT.md)
and [acceptance gates](ACCEPTANCE.md).
