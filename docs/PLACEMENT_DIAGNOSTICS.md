# Placement boundary diagnostics

Update 34 adds automatic descriptive diagnostics to completed DeepSafe family
placements. It does not change the placement scoring policy, thresholds, group
calls, candidate assessments, or benchmark outcomes.

## Read location and function separately

The tree viewer and candidate details distinguish the concentration of placement
likelihood weight on the tree from weight that the existing policy can assign
to a functional group. A high weight on a boundary separating two qualified
groups does not select a function. The report retains the unresolved decision
and displays the reference counts on both sides.

Every reported placement row contributes once to one of five categories:

| Category | Meaning |
| --- | --- |
| Assigned group interior | The unchanged scorer assigns this edge to a group. |
| Artificial root edge | A serialization root edge, excluded from biological split summaries. |
| Competing group boundary | Both cut sides contain different homogeneous qualified groups, each with at least two anchors. |
| Context branch | An unassigned edge has a cut side with no qualified anchor. |
| Other unassigned | No preceding category applies. |

Canonical tip bipartitions combine the two representations of the same
biological split when the tree has a degree-two serialization root. The largest
split weight and the smallest tie-inclusive split set reaching 95% of original
weight describe placement concentration. Weights are never renormalized;
missing weight is explicit. If retained biological weight cannot reach 95%,
the set is labelled insufficient. This is not a confidence interval or a
probability of resistance, gene function, pathogenicity, or organism safety.

## Automatic future runs

The native placement stage writes `analyses.family_placement.diagnostics` for
each successful placement. A diagnostic failure is recorded separately and
cannot turn a completed analysis into a failed scientific call or change its
scores. Other DeepSafe family gates and candidate budgets remain in force.

## Refresh saved reports

From the project directory, run:

```bash
python BaSIS_refresh_placement_report.py
python BaSIS_recover_benchmark.py --run-dir PATH_TO_SAVED_BENCHMARK
```

The first helper selects the latest valid family-placement review by its recorded
finish time. Use `--evidence PATH_TO_EVIDENCE_JSON` to choose another saved bundle.
Both helpers create new report directories and verify native artifact hashes,
query/reference identity, topology, and the complete saved placement score
object. They do not invoke native analyses or GPU inference.

Historical scientific records remain unchanged. Derived diagnostics are saved in
`placement-diagnostics.json`, linked from the report. Missing or inconsistent
native evidence makes diagnostics unavailable; it is never replaced with an
invented tree or score. Original source files and sealed records are checked
again after rendering.

The installed source changes can invalidate caches on a later full pipeline
run. Report refresh does not require that run. The frozen version-1 seed
benchmark remains a development result; a reference expansion requires a new
versioned evaluation rather than rewriting that result.

## Comparator expansion supplied separately

`resources/placement/registry-v2.json` adds three natural, sequence-linked
comparators to the development panel. It contains 13 accession records and 12
unique sequences: six Cfr-family, four alternative RlmN, and two context
sequences. The original v1 entries and default registry are unchanged.

Read `resources/placement/reference-audit-v2.json` for the differing experimental
endpoints, identity links, and unresolved gaps. This is a computational source
audit, not independent expert or classifier validation. The new panel has not
yet been benchmarked. Do not compare a v2 result to the frozen v1 benchmark as
though the reference population were unchanged.

For a separately recorded future development run, set the existing
`deep.placement_registry` config field to the absolute path of
`resources/placement/registry-v2.json`. No change to that field is required to
refresh the existing reports with boundary diagnostics.
