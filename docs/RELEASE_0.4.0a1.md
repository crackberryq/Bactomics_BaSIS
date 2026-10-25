# BaSIS AWARE 0.4.0a1

**Alpha prerelease — 2026-10-25.** Release branch: `basis-aware`. Tag: `v0.4.0a1`.
The product name is **BaSIS AWARE**, consistent with **HybAs AWARE**.

This is the first named BaSIS AWARE release of the standalone Snakemake edition.
It includes the tested changes from [PR #8](https://github.com/crackberryq/Bactomics_BaSIS/pull/8),
with release naming and documentation on a separate branch. Earlier releases and
the `snakemake-standalone` branch remain available.

## Included changes

- Screening runs with DeepSafe disabled by default. Existing explicit opt-in
  configurations retain their behavior.
- `Followup.smk` provides separately requested follow-up from verified saved
  screening inputs. It stops on missing, stale or failed prerequisites and does
  not schedule screening or database preparation.
- Screening and follow-up have separate report bundles. Deferred and unselected
  records remain visible with their workflow reasons.
- The default follow-up computing ceiling is five unique candidates per sample
  after deduplication; this is a limit, not a quota. Explicit overrides remain
  effective. Candidate eligibility and ordering are unchanged.
- Completion reporting distinguishes original execution records from completion
  qualified by the enclosing run status. Incomplete enclosing runs retain
  provisional results without silently presenting them as complete.
- Edits limited to the shared completion/reference-summary helper refresh
  finalization and reports without invalidating earlier analysis stages.
- Installation instructions use portable paths, and local tests and CI use the
  same test dependency file.

## Verification and limits

The implementation is unchanged from commit
`a551939f0c16baa56d4918d3e8869974193a2ec5`; release changes affect documentation and
citation metadata only. [Its CI run](https://github.com/crackberryq/Bactomics_BaSIS/actions/runs/40223828645)
passed 503 software tests on each of Python 3.11 and 3.12 and genuine small-environment
Conda deployment/recovery checks on Conda 25.9.1 and 26.1.1. The local 503-test run
also passed with the documented container-only benchmark fallback.

Native end-to-end acceptance, a nonempty native follow-up pilot, measured runtime
and resource limits, and independent scientific validation remain incomplete.
This release retains alpha status; it does not establish biological function,
diagnostic accuracy or organism safety. See [ACCEPTANCE.md](ACCEPTANCE.md).

## Installation and migration

Use the source checkout and controller environment described in [README.md](../README.md).
This edition runs `BaSIS.smk` directly; it is not installed as the older Python
package and does not include that edition's Streamlit launcher.

For a fixed source checkout:

```bash
git clone --branch v0.4.0a1 --single-branch \
  https://github.com/crackberryq/Bactomics_BaSIS.git BaSIS-aware-0.4.0a1
```

Keep existing configurations and successful results. Inspect a dry run before
resuming: changes to shared workers since an older checkout may invalidate
affected downstream stages. `--rerun-incomplete` does not prevent legitimate
reruns caused by changed source inputs.

Prepared resources are required for isolated follow-up. Conda environment
installation and existing analysis-level reference retrieval may still occur;
the isolated workflow is not an unconditional offline mode. See
[REVIEWED_FOLLOWUP.md](REVIEWED_FOLLOWUP.md) and [REPORT_SEPARATION.md](REPORT_SEPARATION.md).
