# BaSIS AWARE v1

AWARE controls admission to DeepSafe computational follow-up. It evaluates each
tool's findings, merges verified identities, admits candidates with at least one
qualifying source, then applies the existing candidate budgets. Original native
outputs, normalized evidence and confidence labels are retained. Weak findings
do not become qualifying evidence because several tools reported them.

The standard scope is now **`amrfinder-review`**: reported AMRFinder AMR review
cues are selected before budgets. See [AMRFinder review](AMR_REVIEW.md) for exact
methods, retained review obligations, exports and the AFB01 regression. Use
**`all-supported`** explicitly for the previous broad policy described below.

## Configuration

DeepSafe is disabled by default (`deep.enabled: false`). After explicit opt-in,
the existing admission policy defaults to `deep.aware.preset: auto-medium`.
See [reviewed follow-up](REVIEWED_FOLLOWUP.md) for a separate entry point that
consumes saved screening inputs without scheduling screening jobs.
The default is merged even when an older study YAML has no `aware` section.
The total follow-up budget now defaults to **five unique candidates per sample**
(`deep.maximum_candidates: 5`). It applies after identity reconciliation and
admission. It is a computing ceiling, never a quota: fewer qualifying records
produce fewer jobs, and no qualifying records produce no selected-candidate jobs.
An explicit study configuration can override this budget; existing saved results
are not changed by updating the software.

Unselected/deferred records remain visible as **Requires attention**, with their
recorded execution state and admission or budget reason. This is a workflow flag;
it does not assert a biological concern or erase an existing screening call.
The smaller budget does not change admission or ordering, and does not implement
or claim selection by proximity to a tool's decision boundary. That would require
a separately justified, method-specific review policy and the relevant evidence.

Set `deep.aware.enabled: false` explicitly to retain legacy admission. Low-level
legacy API calls without an AWARE block retain legacy behavior; saved historical
calls and analysis results are never changed by report rendering. Current,
version-labelled review cues may be derived from that evidence in refreshed reports.

```yaml
deep:
  aware:
    enabled: true
    scope: amrfinder-review
    preset: auto-medium
    tools:
      vfdb:
        min_identity: 90.0
```

Load the selected versioned preset first, then apply `tools` and `queues`
overrides. There is no adaptive relaxation to fill a candidate limit. Invalid
keys, labels, types, nonfinite values and percentages outside 0–100 stop configuration
loading. `null` disables a numeric admission filter; it does not supply a missing
measurement. Required missing measurements defer the finding with an explicit
reason. RGI's reference length ratio is never substituted for alignment coverage.

See [the commented configuration overlay](../config/aware.example.yaml) for
every supported field. Native screening settings under `modules` and reference
retrieval settings such as `deep.min_identity` remain separate. A looser AWARE
preset cannot recover matches that an upstream search never emitted.

## Preset definitions for all-supported scope

These are transparent **computational workload policies**, not validated
sensitivity/specificity thresholds or organism safety ratings. Their values need
study-specific validation before use as a scientific decision rule.
The AMRFinder review scope uses its documented review methods across all presets;
the AMRFinder row below applies only to `scope: all-supported`.

| Setting | auto-strict | auto-medium | auto-loose |
|---|---|---|---|
| AMRFinder native methods | ALLELE / EXACT, including P/X variants | Supported native methods | Supported native methods |
| RGI cutoff | Perfect | Perfect, Strict | Perfect, Strict, Loose |
| RGI nudged hits | Deferred | Deferred | Allowed in exploratory queue |
| VFDB/BacMet minimum identity | 90% | 80% | 60% |
| VFDB/BacMet minimum reference coverage | 90% | 70% | 50% |
| VFDB/BacMet minimum query coverage | 90% | 70% | 50% |
| VFDB/BacMet maximum E-value | 1e-10 | 1e-5 | 1e-3 |
| Exploratory queue | Off | Off | On |
| Model-attention queue | Off | Off | On |

AMRFinder and RGI use native methods/categories by default, without imposing a
universal similarity cutoff on curated models. Optional identity/reference
coverage filters can be configured per tool. AMRFinder supported methods are
enumerated in `aware.py`, including BLAST, HMM, POINT, partial and disruption
methods. Strict's exact-match subset can defer valid non-exact or variant calls;
it is a deliberately narrow workload, not a claim that those calls are harmless.

The DIAMOND middle preset retains the existing 80% identity/70% reference
coverage baseline, adds a 70% query-coverage admission filter, and uses the
existing 1e-5 search E-value scale. Strict and loose are explicitly uncalibrated
narrower/broader workload alternatives. Their values are not supplied or endorsed
as biological classification rules by the database authors.

The model-attention queue uses a maximum rank (20 by default), not a pathogen
probability or a claim that attention establishes function. Existing
`maximum_attention_candidates`, `maximum_exploratory_candidates`, and
`maximum_candidates` budgets still apply. `include_exploratory: false` still
blocks exploratory follow-up. BacMet stress findings require
`deep.include_stress: true`; this is enforced for exploratory hits too.

Only the five sources that supply gene/protein follow-up candidates have AWARE
tool settings: AMRFinder, RGI, VFDB, BacMet and PathogenFinder2. Other modules
provide context or other evidence and do not receive invented score cutoffs.

## Identity and evidence

Findings are grouped by exact locus or a uniquely resolved protein ID. Validated
annotation coordinates can reconcile an explicit protein ID with a partial
reported span inside its CDS. Both the original source coordinates and the
identity-reconciliation reason are retained. A coordinate-only call can link to
an exact, uniquely annotated CDS. A name or sequence similarity alone never
merges records. Ambiguous or contradictory identity is deferred for review.
Different genomic locations remain separate candidates. Multiple annotated
protein IDs at one locus are not assumed to represent identical sequences.

Every candidate retains all linked source findings, each source's admission
decision, the qualifying finding IDs, and its selection/defer reason. Only
qualifying sources contribute to budget ordering; weak linked hits do not inflate
priority. Eligibility is an OR across independently qualifying sources, never
an averaged score. Budgets apply once per unique eligible candidate.

This release reduces the initial candidate queue and strengthens within-sample
identity reconciliation. It does **not** add a cross-sample sequence cache or
replace the six candidate-stage jobs with batching. Unique-sequence counts in
the audit are an inventory, not a claim of computational reuse. Broad scheduler
dependency narrowing and automatic database-path persistence remain separate
work. The prior compact checkpoint storage remains active.

## Audit and preview

Each prepare job writes `safety/deepsafe/native/prepare/aware_selection.json`.
The same summary is retained in the final DeepSafe evidence and shown in the
HTML report with effective settings, overrides, source counts and reasons.
Counts distinguish received/evaluated/qualifying findings, unique candidates,
eligible/selected candidates, admission deferrals and budget deferrals. Tools
can support the same locus, so per-tool selected-locus counts are not additive.
The existing deferred-candidate table retains candidates excluded by AWARE.

For a read-only comparison using completed saved upstream evidence:

```bash
python scripts/preview_aware.py --isolate /absolute/path/to/isolate
python scripts/preview_aware.py --isolate /absolute/path/to/isolate \
  --configfile config.bioinfo-four.yaml --json
```

The preview retains manual overrides and budgets, never invokes native tools,
and does not extract sequences or modify a run. It checks sample identity and
missing enabled upstream records. Saved evidence may reflect earlier native
screening settings; its checksums and effective admission settings are included
in JSON output. This is not a prediction of runtime or a new native analysis.

Update the checkout only after the running workflow has stopped. Resume normally
with `--rerun-incomplete`; retain normal source/configuration invalidation. Do not
delete screening results or use `--touch` to suppress required recomputation.
New admission settings require DeepSafe reselection, and existing shared setup
dependencies may also schedule other affected work. A completed selected scope
can still have deferred evidence and an incomplete overall review scope.

## Source semantics

- NCBI: [AMRFinderPlus](https://www.ncbi.nlm.nih.gov/pathogens/antimicrobial-resistance/AMRFinder/)
  and [method/output definitions](https://github.com/ncbi/amr/wiki/Interpreting-results).
- CARD: [RGI native cutoff categories and nudging](https://card.mcmaster.ca/analyze/rgi)
  and [RGI documentation](https://github.com/arpcard/rgi/blob/master/docs/rgi_main.rst).

These sources support native output semantics; they do not validate the AWARE
workload presets. Tests exercise synthetic configuration, admission, identity,
reporting and resume behavior, not biological sensitivity/specificity.
