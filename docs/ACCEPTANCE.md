# Acceptance and independent validation

**Status: development alpha.** The rebuilt workflow is intended for infrastructure acceptance before broader biological validation. Neither parser tests nor synthetic execution tests establish diagnostic accuracy or organism safety.

## Test layers

| Layer | What it checks | What it does not establish |
|---|---|---|
| Unit and record-contract tests | Parsing, identity, no-hit handling, scope holds, file integrity, report links | Real-tool performance across all inputs |
| Synthetic Snakemake acceptance | Setup gate, a real DAG, intentional job failure, selective resume, no-op rerun, report-only refresh | Biological correctness of synthetic tool outputs |
| Real Conda deployment test | Snakemake deployment, failed post-deploy checks, incomplete-environment recovery | Every biological tool's complete runtime behavior |
| Native pilot on annotated isolates | Installed tools, configured resources and complete workflow integration | Independent sensitivity, specificity or calibration |
| Independent biological validation | Agreement with reviewed external evidence and appropriate measured phenotypes | Generalization beyond the validation design |

The native and independent biological validation stages have **not** been completed for this standalone alpha. CI results should be checked for the exact commit being tested; passing results from an older RC do not automatically validate this rebuild.

## Local software checks

From the checkout, with `basis-snake` active, install the test dependencies before
running the full software suite:

```bash
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
```

`requirements-test.txt` includes `h5py`, which the workflow-controller environment
does not require for normal orchestration. Without it, HDF5-specific tests can be
skipped; a successful run with skipped tests is not the same coverage as the full
CI suite. Record the test count and skips alongside the commit and dependency
versions. This test setup does not install the native analysis environments.

The orchestration fixtures use clearly synthetic annotations and temporary fake tools. They run Snakemake directly and deliberately test failure recovery. They are not reported as native biological validation.

The real environment-recovery test needs Conda and network access, creates small temporary Python environments, and performs no biological analysis:

```bash
python scripts/check_environment_recovery.py
```

The script writes `summary.json` and scenario logs to the temporary test directory it prints. It checks two distinct cases:

1. An incomplete environment is recreated while a completed synthetic job's output is preserved.
2. A failing post-deploy check prevents job execution and does not publish a complete environment; the same recipe succeeds once the check can pass.

It does not intentionally damage a user's existing environment or claim that arbitrary native-tool errors trigger automatic rebuilds.

Some restricted containers expose child PIDs inconsistently to `psutil`, preventing Snakemake benchmarking. `BASIS_TEST_NO_BENCHMARK=1` is a test-only fallback that removes benchmark directives from a temporary workflow copy. A run using that fallback does not verify production benchmarking. Normal Linux/WSL acceptance and CI should exercise the original rules unchanged.

## Native pilot acceptance

Use an unchanged annotated isolate and the documented standalone command. Preserve any previous `safety/` bundle separately before selecting the same output location for a new comparative study; do not overwrite a previous version's only evidence copy.

Check these outcomes:

- Selected environments and resources finish before any sample analysis starts.
- Every requested executable module produces normalized evidence or a clearly recorded applicability hold. A tool failure causes a nonzero workflow exit and identifies the failed rule/log.
- The main report is at `<isolate>/safety/report.html`; citations, tables, native files and available tree views open through its links.
- Sample identifiers, source hashes, database/model provenance and effective settings match the intended inputs.
- A second unchanged invocation reports no work to do. Its native output hashes and modification times remain unchanged.
- Report refresh with unchanged analysis inputs does not repeat native analyses.
- Selected candidate completion, deferred scope and interpretation holds remain separately visible.
- A separately requested `Followup.smk` run completes its configured follow-up
  scope while preserving the saved screening files and their modification times.
  Record whether any candidates were actually processed; a zero-candidate run
  does not exercise native follow-up execution.
- An interrupted invocation resumes without repeating successful unchanged jobs.
  Already prepared, unchanged resources are reused without downloading them again.

A failed pilot should be diagnosed from the named Snakemake rule and its log. Do not delete successful outputs or change scientific thresholds merely to obtain a green report.

## Beta promotion gate

This gate is **pending**. Passing software CI or updating the checklist does not
complete the native acceptance work.

The current feature freeze, recorded software evidence and remaining native
acceptance work are tracked in [beta preparation](BETA_READINESS.md).

- Integrate the release changes into the documented installation branch and
  record the exact release commit. Installation instructions, version metadata
  and release notes must describe that same build.
- Resolve confirmed user-visible correctness defects and retain regression tests.
- Preserve the [AFB01 AMRFinder review regression](AMR_REVIEW.md): preset changes,
  budget deferral, missing references and inconclusive or failed follow-up must
  retain review accounting. A nonempty native pilot and scientific review of
  the escalation policy remain pending independently of software-test success.
- Complete the [Bactologix follow-up source handoff](AWARE_TO_REPORT.md): a newer
  installation must not be mistaken for an upgrade of a frozen screening run.
  Record the verified follow-up implementation and explicit review scope.
- Preserve clean-install and native pilot evidence for each platform and
  configuration advertised as tested. Untested configurations remain explicit.
- Verify setup, screening, both report bundles, opt-in follow-up, unchanged rerun,
  interruption recovery and reuse of existing resources as described above.
- Record hardware, operating system, storage location, elapsed time, peak memory
  and disk use. State known limitations rather than promising an unmeasured
  end-to-end runtime.
- Publish the validation bundle and clearly label optional follow-up as
  experimental, including its resource and family-coverage limits.

Independent scientific validation can continue during a clearly labelled research
beta. Any accuracy or functional-interpretation claims require their own supporting
evidence. Stable promotion additionally needs broader native compatibility and
input coverage, a reproducible environment/resource baseline, and external user
acceptance; the number of passing software tests alone does not establish these.

## Reproducible validation bundle

Preserve these together when sharing validation results:

| Item | Purpose |
|---|---|
| Checkout commit and unmodified source | Exact workflow implementation |
| Controller and tool environment inventories | Resolved runtime provenance |
| Study configuration and sample sheet | Scope, path mapping and resource choices |
| Annotation files and recorded checksums | Input identity |
| Prepared database/model snapshots and provenance | Reference identity, not just database names |
| Complete `safety/` folder for each isolate | Native outputs, records, reports, logs and benchmarks |
| Independent reference labels and phenotype records | External evidence for biological comparison |

Recipe files pin important versions but are not complete cross-platform environment locks. Automatic downloads may resolve to a newer upstream snapshot on a fresh installation. Reusing and recording the same verified resources is necessary when the objective is an exact reference-data comparison.

Keep performance benchmarking, workflow reproducibility and biological accuracy as separate outcomes. A successful software run supports the first two only to the extent actually tested; it does not validate the biological interpretations by itself.
