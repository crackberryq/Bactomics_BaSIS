# Collaborator handoff and independent validation

rc10 is a software deployment candidate. Reporting acceptance, reproducibility capture, native execution and biological validation are distinct. Existing isolates1/4/5/6 and the placement development benchmark remain development/pilot material, not an independent evaluation set.

## Freeze before evaluation

Retain the release ZIP and its checksum, installed-package verification, the exact configuration and sample sheet, input-file hashes, database/model snapshots and identities, environment inventories, hardware/runtime information, commands and requested scope. `basis audit-reproducibility` creates a dated record from explicit sources; use `--hash-resources` when a complete current resource inventory is required. Retain the original resource and input bytes independently. A new capture of a path cannot establish that its contents equal those used in a historical run unless their recorded hashes agree.

The release includes a pinned launcher dependency list for the recorded pilot versions. It is not an artifact-hashed lock or offline wheel archive. Captured Conda exports are platform-specific; pip layers and external models remain separate. The main installer reuses existing dependencies and therefore does not demonstrate restoration on a fresh host.

## Deployment acceptance

1. Resolve recorded metadata gaps, retaining both earlier and new audit records. Do not modify installed metadata merely to match counts.
2. Retain exact Conda package and Python wheel/source artifacts, database/model snapshots and applicable redistribution permissions. Verify their recorded hashes.
3. On a fresh compatible machine/environment, install from the retained artifacts and record any resolver changes, platform constraints or missing packages. Record installation failures rather than substituting untracked versions.
4. Run installed software acceptance, then native-tool preflight and a separately identified native acceptance run in fresh output directories. Verify requested modules, failure/zero-hit handling, links and execution logs. Imported or reused records must retain their original identity.
5. Compare expected and observed outputs at the appropriate level; do not assume bitwise GPU/numerical equality across hardware. Document tolerances before evaluating discrepancies.

## Biological evaluation design

Agree the intended claim, evaluation units, inclusion criteria and independent expected labels with the collaborator before seeing evaluation outcomes. Define how uncertain labels, incomplete inputs, failed runs, abstentions and deferred candidates contribute to the denominators. Keep development and evaluation data separate, including related/duplicate cases that would compromise independence.

Report task-specific accuracy with uncertainty and the evaluated scope, alongside coverage, abstention rates, errors and disagreements. Thresholds, references or rules changed after reviewing evaluation outcomes create a new development version; that same set cannot then serve as an untouched independent test.

Trees, motifs, homology, model predictions and experimental evidence remain separate evidence sources. A close relationship or model score alone does not establish a phenotype or an organism safety verdict. No wet-laboratory protocol is prescribed by this deployment package.

## Handoff record

| Record | Present in release or generated | Remaining action |
| --- | --- | --- |
| Source, wheel, sdist and bundle hashes | Release manifests | Preserve the delivered archive |
| Installed software checks | Build-host record; PC checker supplied | Run and retain target-PC acceptance |
| Historical environment capture | `reproducibility/` | Compare updated context metadata |
| Config/sample/evidence freeze | New audit command | Run on explicit validation sources |
| Input/database/model hashes | Audit; resource hashing opt-in | Retain corresponding bytes |
| Complete package artifact archive | Not supplied | Assemble and verify for the target platform |
| Fresh-host native restoration | Not performed | Record a separate acceptance run |
| Independent biological evaluation | Not performed | Collaborator-led held-out evaluation |
