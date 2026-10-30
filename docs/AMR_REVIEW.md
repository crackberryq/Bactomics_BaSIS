# AMRFinder review and targeted DeepSafe follow-up

BaSIS keeps **evidence review** separate from **computational admission**. A finding
can require investigation even when DeepSafe is off, a manual filter excludes it,
its candidate is deferred by a budget, or follow-up fails or is inconclusive.
Completed execution never closes the review question.

See the [end-to-end walkthrough and integration audit](AWARE_TO_REPORT.md) for
the sequence from saved screening through AWARE, DeepSafe and report exports.

This first scope covers **reported AMRFinder AMR findings**. It does not search
again, lower native screening thresholds, recover rejected alignments, infer a
resistance phenotype, or classify an organism as safe. Other modules retain their
existing findings and review sections. No universal “moderate minus strict” band
is introduced.

## Recorded review cues

Policy `basis-amrfinder-review-v1` reads the native method and retained flags:

| Recorded metadata | Review cue | Automatic follow-up in this scope |
|---|---|---|
| HMM | Family assignment needs interpretation | Eligible |
| PARTIAL methods, including contig-end variants | Sequence completeness needs review | Eligible |
| INTERNAL_STOP / FRAME_SHIFT methods | Disruption needs interpretation | Eligible |
| Putative disruptive variant | Retain exploratory qualification | Manual review; existing exploratory gates remain active |
| Missing, unsupported or conflicting method metadata | Reconcile the source record | Deferred for manual review |
| Other supported native AMR calls | Retain context/phenotype review | No redundant sequence follow-up by this scope |

These are **operational review rules**, not calibrated biological risk categories.
In particular an HMM label does not mean resistance is established, and a partial
or disrupted call does not establish loss of function. Scientific review and an
appropriate validation design are still required before claiming sensitivity or
specificity. Native-method semantics come from the
[NCBI AMRFinderPlus interpretation documentation](https://github.com/ncbi/amr/wiki/Interpreting-results).
NCBI does not define or endorse this BaSIS follow-up policy.

## Configuration and compatibility

New standard executions use this scope; DeepSafe remains disabled until explicitly
requested. The reviewed follow-up overlay now declares the scope explicitly:

```yaml
deep:
  enabled: false  # Explicitly enable only for the intended follow-up run.
  maximum_candidates: 5
  aware:
    enabled: true
    scope: amrfinder-review
```

Within this scope, all three workload presets use the same review-method list.
In particular `auto-strict` does not replace it with an exact-only list. Explicit
AMRFinder method/numeric/enabled overrides still apply and their exclusions remain
visible. Other tools cannot admit a candidate through this scope, although their
linked source evidence is preserved. Eligibility is evaluated before identity
deduplication and the existing five-candidate ceiling; no unused slots are filled
with unrelated findings. Ordering within eligible candidates retains the existing
heuristic and is not a biological risk ranking.
An exact/Perfect call from another linked source cannot shortcut an investigation
selected by this review scope.

Use `scope: all-supported` to explicitly request the previous broader admission
policy. Low-level API calls with an AWARE block but no scope keep that legacy
default; standard configuration loading merges the new documented default.
`aware.enabled: false` retains legacy selection. Saved results and frozen run
configurations are never rewritten. Updating code or changing scope requires a
new selection under the new source/configuration identity; inspect the dry run.
Do not overwrite the only copy of an earlier follow-up to compare policies.
The saved-screening follow-up entry point and preview preserve the previous scope
when the saved configuration lacks this field. Use the explicit overlay above to
request AMRFinder review for an older screening run.

Preview without native execution or changes to saved results:

```bash
python scripts/preview_aware.py --isolate /absolute/path/to/isolate \
  --configfile config/deepsafe-followup.example.yaml --json
```

## Reports and exports

Screening and follow-up reports show **AMR evidence review outstanding** when
reported AMR findings remain for review. The expandable table explains the cues,
source completion and linked follow-up states. Its first ten rows are a preview;
`amr-review.tsv` contains all source rows (under `tables/` in workflow reports).
`evidence.json` contains the full `amr_review` accounting. Run-summary JSON, TSV
and terminal output expose review status separately from execution completeness.

Review cues can be derived when refreshing historical reports; they are labelled
with the current review-policy version and do not alter saved calls, interpretations,
candidate selection or analysis results. Linking requires matching sample, finding
ID and saved source metadata. A reused gene label or changed finding is insufficient.

This update does not add an expert-signoff interface or grant deployment permission.
Automatic results never record review closure. An empty review queue describes only
the evidence supplied within this scope; it cannot establish absence of hazards.
Candidate ceilings are not wall-clock deadlines: existing command timeouts remain
per command, and a nonempty native pilot is still required to measure runtime.

## AFB01 regression and beta gate

The owner confirmed `isolate1 = AFB01`. A small fixture preserves the relevant cfr
HMM method, full reference coverage and reported identity from that incident;
identifiers and coordinates are synthetic and no sequence is included. The actual
AMRFinder TSV parser feeds selection and reporting in the test. No cfr-specific
selection exception is used; arbitrary gene labels exercise the same rules.

Software tests must demonstrate that presets, budgets, explicit exclusions, missing
references, failed parents and completed-but-unresolved follow-up cannot remove
review accounting or turn it into clearance. Input evidence must remain unchanged.
These tests do not reanalyse AFB01 or validate its functional interpretation.

Before beta promotion, retain this regression and verify a nonempty native follow-up
through the intended Bactologix version, including saved-screen preservation,
report/export consistency, interruption/resume and unchanged rerun. Scientific
review of the escalation policy remains a separate acceptance item.
