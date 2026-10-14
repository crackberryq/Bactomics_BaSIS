# DeepSafe family placement — update 29

This adds an automatic reference-panel audit, reference-only phylogeny and
EPA-ng placement layer to the normal `basis run -c CONFIG -d -e -v` workflow.
It is enabled by `deep.family_placement: true`. Existing motif, Pfam, PROSITE,
context and exploratory-tree evidence remains available.

## What is automated

1. Route selected candidates using qualified screen labels. Weak or loose
   alternate labels cannot select a panel. Mutation-dependent models and
   taxonomic or sequence-integrity concerns are held for the appropriate review.
2. Resolve a source-audited registry panel, use pinned local sequences, or
   download missing public reference accessions. Downloads never submit query
   sequences. Expected accession and sequence hashes must match.
3. Collapse identical sequences across accessions while retaining their sources.
   Conflicting labels, missing competing groups and insufficient distinct
   experimental anchors cause an explicit hold.
4. Build a reference-only MAFFT alignment and IQ-TREE tree, then fit the query to
   that fixed alignment. Every removed insertion is recorded; inadequate query
   coverage stops placement. Reference trees are cached by panel, settings and
   executable identity, with file checksums verified before reuse.
5. Run EPA-ng over all reference-tree edges. Sum raw likelihood-weight mass on
   the interiors of homogeneous labelled clades. Boundary edges, branches
   carrying context alone and singleton groups remain unresolved. Omitted mass
   is shown separately and is never renormalized into confidence.
6. Add candidate-level placement evidence, panel provenance and visible holds
   to the HTML report and consolidated JSON.

The initial model is fixed `LG+G4`. IQ-TREE's fitted gamma shape is transferred
to EPA-ng using a validated model descriptor. The exact report, its checksum,
reported parameter precision, native commands and native outputs are retained.
IQ-TREE branch support is not multiplied into placement weight.

## Initial biological coverage

The included pilot panel covers the Cfr/RlmN methyltransferase family: four
distinct characterized Cfr proteins, three experimentally supported RlmN-like
comparators and two unlabelled contextual proteins. Ten accessions collapse to
nine sequences. See `placement-reference-audit.md` and the packaged registry for
the accession-to-publication evidence and specific limitations.

RlmN-like denotes a competing biochemical family. It does not mean an organism
is susceptible or safe. Some sources infer the precise C2 chemistry by analogy;
that limitation remains attached to those references.

The engine is generic, but reference curation is not universal. Other families
produce an explicit unsupported-panel hold until an appropriate reviewed panel
is registered. This is preferable to labelling downloaded annotations as
experimentally confirmed or comparing a query only with AMR-positive proteins.

## Meaning of a score

`group_support` is the sum of EPA-ng likelihood weights on qualifying edges for
a comparison group. It is conditional on this particular panel, alignment,
reference topology and substitution model. It is **not** a calibrated
probability of protein function, expression, isolate resistance or safety.
`function_probability` is always `null` in this release. A high score can still
be misleading with sparse references or an incorrect topology. Reference
diversity, alternative models, alignment sensitivity and independent biological
validation remain research requirements.

The minimum two distinct anchors per required group is an eligibility rule,
not a scientifically validated sample-size threshold. Source review means
an AI-assisted identity and literature audit, not independent expert approval.

## Configuration

```yaml
deep:
  family_placement: true
  placement_registry: /absolute/path/to/basis/resources/placement/registry-v1.json
  placement_cache_dir: /absolute/path/to/study/placement-cache
  placement_allow_download: true
  maximum_placement_candidates: 20
  placement_model: LG+G4
  placement_min_query_coverage: 0.7
  placement_min_columns: 50
```

MAFFT, IQ-TREE 2 and EPA-ng run in the existing DeepSafe command-prefix
environment. EPA-ng must be at least 0.3.4; this release was tested with 0.3.8.
No GPU is required. The exact software and reference panel are recorded.

For the existing isolate1 study, `python BaSIS_run_family_placement.py` finds
the unique expanded native configuration and reconciled evidence bundle,
verifies saved input/query checksums, and runs only the new layer. It writes a
fresh report directory and an integrated configuration for subsequent full
runs. It preserves original reports, native results and prior interpretations.
Use `--config` and `--evidence` when multiple matching bundles exist.
`--prepare-only` writes the integrated configuration without running analyses.

## Validation and benchmarking

Software tests cover malformed native outputs, tree-root invariance, missing
weight, duplicate/conflicting references, provenance tampering, cache corruption,
query mapping, operational failures, biological holds and report rendering.
Real native integration was exercised with MAFFT 7.526, IQ-TREE 2.4.0 and
EPA-ng 0.3.8. Those checks establish adapter behavior, not classifier accuracy.

`python -m basis.placement_benchmark --manifest MANIFEST.json
--predictions PREDICTIONS.json --out NEW_EVALUATION.json` audits declared
sequence/cluster/clade partitions and reference-panel membership before
computing coverage, selective accuracy, confusion and abstention-aware macro-F1.
Its JSON schemas are documented in `basis/placement_benchmark.py`. Ground-truth
labels must come from independent experimental evidence; clustering and clade
assignments must be produced externally and are not proved correct by this audit.
The evaluator rejects train/test/reference leakage, requires complete held-out
case accounting and a frozen decision policy, and refuses to overwrite outputs.

No biological benchmark has yet established incremental accuracy or calibrated
probabilities for this release. Before such claims, compare placement against
AMRFinder/RGI and sequence-only baselines on cluster/clade-held-out cases, measure
abstention, and evaluate alignment/reference/model sensitivity. Bootstrap values,
likelihood weights and database identity must not be presented as independently
validated resistance probabilities.
