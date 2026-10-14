# BaSIS 0.1.0 AMR hotfix 1

Date: 2026-10-07. This is a source update to the editable BaSIS 0.1.0 checkout.
The package version remains 0.1.0; the recorded source fingerprint identifies
the changed implementation.

## Why this update is needed

DeepSafe previously gave every AMR-category finding the same priority, including
RGI Loose calls. Large exploratory result sets could crowd native core HMM and
Strict evidence out of the follow-up budget. Disabling exploratory follow-up did
not reliably exclude those AMR-category candidates.

The update assigns queues after locus merging:

1. Nonexploratory AMR/virulence screening evidence, including core AMRFinder HMM
   and unnudged RGI Strict calls.
2. Exploratory evidence, including Loose or nudged RGI calls. The new
   `maximum_exploratory_candidates` default is 20.
3. ML attention-only evidence, subject to its existing separate limit.

All queues also respect `maximum_candidates`. Mixed native plus Loose/ML loci
retain native priority and use one slot. Mixed Loose plus ML loci use the
exploratory limit. Unselected findings remain visible as deferred/inconclusive;
they never become assessed negatives. Deferred work still makes the DeepSafe
assessment incomplete under the existing completeness policy.

`include_exploratory: false` now reliably defers exploratory-only follow-up and
records that reason. Budgets require integer values and the flag requires a
YAML boolean. These are computational resource controls, not biological cutoffs.

Loose-only AMR candidates use the exploratory route. Native exact-call shortcuts
and family routes use nonexploratory AMR evidence; weaker source calls remain in
the evidence record. No phenotype or organism-safety verdict is added.

The default RGI version probe is corrected to `rgi main --version`. The default
AMRFinder version probe now includes an explicitly configured database path.
User-supplied version arguments continue to take precedence.

## Installation

Save `BaSIS_AMR_hotfix1.pyz` in the existing checkout. Activate the launcher
environment that has this checkout installed in editable mode, then run:

```bash
conda activate basis
cd ~/bactomics/BaSIS_v0.1.0
python BaSIS_AMR_hotfix1.pyz .
```

The updater checks original or already-updated file hashes before modifying
anything, backs up changed originals, and prints the backup location. It refuses
unknown local edits. Add `--check` for a read-only preflight. Rerunning an already
applied update is harmless. Its archive contains inspectable source and tests.

Study files, tool environments, databases, and existing results are outside the
update's file list. There is no need to rerun AMRFinder/RGI to inspect the existing
findings. Because BaSIS caches include the source fingerprint, a subsequent
pipeline execution will recompute native modules after this source change.

## Validation

In the development workspace, Python 3.12.14 passed all **146 unit tests**,
including 16 added hotfix regressions. The large-pool synthetic test retained six
core HMM, nine Strict, and one virulence candidate while limiting follow-up to
20 of 3,552 Loose-only candidates. Mixed-locus and ML evidence, disabled
exploration, global/queue limits, invalid configuration types, route selection,
and version-probe overrides were also checked.

The combined local synthetic demo with `-d -e` completed the requested scope.
These checks establish the tested software behavior, not biological accuracy or
native DeepSafe tool compatibility. The actual isolate's cross-tool overlap and
nine Strict RGI findings still require review.
