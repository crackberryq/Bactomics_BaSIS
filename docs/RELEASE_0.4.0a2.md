# BaSIS AWARE 0.4.0a2

**Alpha prerelease — 2026-10-29.** Release branch: `basis-aware`. Tag: `v0.4.0a2`.

This release makes the readable screening report available in the normal
Snakemake run and in Bactologix installations that support this exact release
commit. The report improvements already merged into `basis-aware` are now
included in a named, discoverable release.

## Report changes

- **Results by tool** leads with each tool's recorded result and opens that
  exact tool/sample's findings. Technical input details follow the results.
- Full saved gene/feature descriptions and recorded drug/class labels are
  visible and searchable. Original source fields remain expandable.
- **Tools finished**, such as 14 / 14, describes execution. No findings,
  inconclusive results, incomplete assessments and unassessed tools retain
  separate wording and recorded reasons.
- Every saved finding is available through search, pagination and the readable
  `tables/screening-findings.tsv` export. Existing evidence groups and
  qualifications remain visible. Empty optional model sections are omitted.
- Normal `BaSIS.smk` execution writes this format automatically to
  `<isolate>/safety/report.html`. No manual refresh script or extra setting is
  required for new runs. Separate follow-up reports retain their own scope.

The release also includes the ISEScan zero-result parser/recovery fix from
[PR #9](https://github.com/crackberryq/Bactomics_BaSIS/pull/9). A completed native
zero-result output can be retained without converting an incomplete assessment
into a negative result. See [ISEScan recovery](ISESCAN.md).

## Update and test

Bactologix discovers published releases, but its adapter must support the
release's exact commit before it can run that installation. Refresh the BaSIS
catalogue, select `v0.4.0a2`, complete the existing setup/check flow, then create
a test draft bound to that installation. Existing drafts retain their source
binding. A Bactologix version that supports only `1d2d96b` needs a compatibility
update; selecting a newly discovered release alone does not bypass that check.

Keep running report exports on their current checkout until they finish.
Existing saved reports require regeneration to show the new format. A refresh
uses saved sample/configuration/evidence snapshots; it does not relabel the
original analysis as having run with the newer release. Moving a result folder
does not automatically migrate historical absolute paths or restart receipts.

For a fixed source checkout:

```bash
git clone --branch v0.4.0a2 --single-branch \
  https://github.com/crackberryq/Bactomics_BaSIS.git BaSIS-aware-0.4.0a2
```

Follow the README for the standalone controller. Inspect the existing dry-run
before resuming an analysis: the ISEScan parser source change can invalidate
affected native stages under normal source tracking. Do not force every rule
to rerun merely to obtain the new report.

## Verification and release scope

Publication waits for the exact source commit's Python 3.11/3.12 tests,
offline Chromium report checks and real Conda deployment/recovery checks.
The synthetic Snakemake acceptance test verifies that ordinary workflow
execution produces the readable report and that a report-only refresh leaves
native outputs untouched. Published tags are never moved by the release helper.

This remains an alpha release. These are software and interface checks;
native end-to-end acceptance and independent scientific validation remain
separate requirements in the [acceptance checklist](ACCEPTANCE.md). This
reporting update does not add inferred drug associations, change thresholds,
select new candidates or establish measured susceptibility.
