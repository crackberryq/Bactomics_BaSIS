## rc12 environment handling

The rc11 launcher passed nine top-level checks on the target PC using Python 3.11.17; all command log hashes and 97 installed package identities were verified. Four historical snapshots, sixteen inputs and sixty-six resources were captured separately. Those records remain historical acceptance evidence, not independent biological validation.

rc12 corrects the ordinary-venv Conda metadata warning and avoids native fingerprint probes for synthetic fixtures. The focused environment checker validates this software behavior using the installed package. Native Conda fingerprints remain required for native cache reuse; an unsupported environment disables reuse instead of authorizing it from version strings alone.

# rc11 inventory and restoration preparation

Current results and precise software-test scope are recorded in the deployment bundle RELEASE_CHECKS.json. The source inventory fix preserves physical installation identity and retains alias provenance. The new helpers separate historical-evidence capture, current resource inventory and isolated Python-launcher restoration. Target-PC checks and full native restoration require their own records. No new biological interpretation is introduced.

# Historical rc10 reporting and reproducibility update

The current release checks are in the bundle's `RELEASE_CHECKS.json`. They cover installed-package software behavior, report/source preservation and read-only provenance capture. Historical pilot and rc9 PC acceptance records are preserved separately. The rc10 target-PC reporting acceptance subsequently passed on October 13. Its context audit matched 228 metadata records to 228 explicit-export digests; the original uploaded capture and review are preserved under reproducibility/.

Read [collaborator validation](COLLABORATOR_VALIDATION.md) for the next deployment and independent-evaluation gates. Native methods, scientific resources, selection rules, thresholds and interpretation logic are unchanged by rc10.

# Historical rc9 reporting update

Current rc9 software checks are recorded in the deployment bundle RELEASE_CHECKS.json. They cover saved-evidence navigation and source preservation using synthetic examples. Existing native pilot evidence remains historical; this update adds no independent biological validation.

# Historical validation status — 0.2.0rc8

This release changes launcher output and run-summary presentation. Module decisions, native analysis algorithms, thresholds, reference/model resources, cache eligibility and exit-code policy remain unchanged from rc7. Current software counts and exact evidence are in `RELEASE_CHECKS.json` at the deployment bundle root.

Feature 1 (rc5 batch import) and Feature 2 (rc6 external annotated inputs) passed their target-PC checks. Those checks covered input handling, not native biological execution.

Feature 3 (rc7 per-sample preflight and exclusions) passed on the target PC using Python 3.11.17. All eight synthetic scenarios behaved as intended. The checker, six recorded runtime-code fingerprints and release manifest matched the delivered rc7 package; installed integrity covered 92 files. Twenty-eight report-artifact hashes, 21 local download links and saved table/command outcomes were consistent. The checker recorded 41 synthetic input/config files unchanged. Expected error cases returned exit code 2. No native analysis or GPU inference was performed.

Feature 4 (rc8 unified run controls) has a separate installed-command checker using artificial imported evidence. It exercises the four deep/extend combinations, verbose progress, parseable JSON, concise summaries, imported-module cache reuse, dry-run preservation, stale optional-result exclusion and failed-input evidence reporting. The subsequent target-PC summary passed and is retained under reproducibility/rc8-run-controls-check-summary.zip.

The rc4 cache checkpoint and older native pilot results remain historical evidence attached to their recorded versions. No release is presented as independent biological validation. Files under source/validation and reproducibility describe their historical checkpoints unless identified as current.

A summary describes the recorded requested scope. Completing selected candidates does not imply that deferred candidates were assessed. Imported and reused records must not be described as newly executed native analyses. Input consistency, resource preflight, native execution and biological accuracy remain separate scopes. Full fresh-host restoration remains a separate check.

## Historical validation status — 0.2.0rc1

BaSIS 0.2.0rc1 is a deployment candidate. Three kinds of evidence are kept separate:

| Evidence | Current status | Supported conclusion |
| --- | --- | --- |
| Captured 0.1.0 pilot source and native runs | Earlier isolate1 and isolate4/5/6 work is preserved; the supplied pilot reconciliation was audited | Those recorded configurations/tools produced the saved outputs on the pilot PC. |
| 0.2.0rc1 software acceptance | Packaging, integrity, fixture and installed-command checks are recorded with the candidate | The tested application behavior and distribution contents meet the recorded software checks. |
| Independent biological validation | Not performed for this release | No validated sensitivity, specificity, functional probability or organism-safety claim follows from this candidate. |

Read the candidate's release-check record for exact commands, runtime, counts and outcomes. Older `validation/` records describe earlier development checkpoints unless their version and package fingerprint explicitly match this candidate. No historical test count is adopted as a new-release result.

## What the native pilot establishes

The reconciled three-isolate pilot records completed requested native modules for isolate4 (12/12), isolate5 (11/11) and isolate6 (10/10). Selected DeepSafe execution completed for 35 isolate4 candidates and 28 isolate5 candidates. Isolate6 selected no DeepSafe candidates; that is not a completed biological assessment of every gene. Deferred candidates remain visible: 1,926, 4,065 and 18, respectively.

The isolate6 ISEScan retry recorded a completed three-contig zero-hit analysis; the original parser failure remains historical evidence. The reconciliation did not rerun the other tools. PathogenFinder2 was withheld for isolate6 under its recorded input restrictions. These runs are development/pilot evidence and are not an independent accuracy benchmark.

A source-capture identity and preserved logs make these records auditable. They do not prove that all native tools will execute correctly on a fresh machine or that all biological classes are covered.

## Repeat application acceptance

After installing the candidate:

```bash
basis --version
basis verify-install
basis demo --outdir validation-demo -d -e
basis report --evidence validation-demo/results/evidence.json --outdir validation-report
basis control-panel --results validation-demo/results --outdir validation-panel
```

Use new output directories. The fixtures are synthetic and require no native analyses. The deployment bundle's `install_local.py` performs its recorded acceptance sequence and retains a JSON record. Inspect that record and the rendered artifacts rather than treating an exit code alone as sufficient evidence.

From the source tree, the automated suite runs with:

```bash
python -m unittest discover -s tests -v
```

Some checks depend on optional test packages or runtimes. Report skips and failures with the Python/platform identity. A parser fixture establishes behavior for the tested schema; it does not establish biological accuracy or compatibility with every future tool version.

## Check the target native installation

```bash
basis doctor -c study/config.yaml -d -e
basis validate -c study/config.yaml
basis run -c study/config.yaml -d -e --dry-run
```

These are preflight checks. They do not establish native execution or functional correctness. Test the packaged release in a fresh output directory using retained configurations, input hashes and database/model identities. Inspect the requested modules' native logs and normalized outputs, including completed zero-hit cases and failures.

Keep the expected and observed results, installation records, configuration, source manifest and native artifacts together. Ensure failures, not-assessed modules, deferred candidates and unresolved interpretations remain distinct. Verify coordinates, identifiers and linked file paths against the source outputs. Imported evidence must retain its exact sample/input identity and original provenance.

The deployment installer reuses dependencies from the active pilot environment. The historical context metadata gap has been reconciled; full native package/model restoration remains unverified. Fresh-host restoration and numerical repeatability require their own recorded checks.

## Independent biological validation

Before collecting the independent evaluation results, freeze the intended claim, software candidate, database/model snapshots, inclusion criteria, analysis settings and handling of abstentions. Keep development cases separate from evaluation cases, including closely related or duplicate inputs that could leak across the split.

Use independently reviewed expected labels and phenotype evidence where phenotype claims are intended. Report task-specific performance, uncertainty, coverage and abstention rates, together with disagreements. Stratify results by the scope that matters to the stated claim, such as taxonomic representation and assembly quality. Do not tune rules on the evaluation set and then describe that same set as independent validation.

Trees, domain/motif matches, catalogue labels, model scores and attention rankings remain different evidence types. Agreement among them does not turn them into independent experimental confirmation. A completed selected-candidate workflow can still return an unresolved interpretation, and a whole-run incomplete scope can persist because candidates were deliberately deferred.

Biological validation outcomes should be linked to the exact release and retained as a separate validation artifact. Genomic findings alone do not certify organism safety or establish a calibrated probability of harm for a proposed use.
