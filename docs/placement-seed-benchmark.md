# DeepSafe internal placement benchmark

Run `python BaSIS_benchmark_placement.py` from the BaSIS installation. The helper
uses `.basis-envs/basis-deepsafe`, prints progress, and writes a fresh timestamped
folder beneath `study-isolate1/placement-benchmarks/`. It prints the HTML report
and an upload ZIP when finished. Existing isolate results are preserved.

This is a separate development benchmark, not another isolate safety test. It
does not run automatically with every `basis run -d -e`. No GPU, training of a
machine-learning classifier, database download, or isolate reanalysis is needed.
The environment must contain Python, BLASTP, MAFFT, IQ-TREE and EPA-ng; update 30
provides the corresponding DeepSafe environment specification.

## What it measures

The default Cfr/RlmN panel contains nine unique sequences after exact sequence
deduplication: seven source-labelled seeds and two unlabelled context sequences.
Source audits and experimental citations remain in the panel manifest. The
same source labels are used for internal scoring; they are not independently
adjudicated external test truth. In particular, the alternative group includes
RlmN-like rRNA methylation evidence and must not be interpreted as a universal
non-resistance or safety label.

Before any predictions, the runner freezes the following research settings:

| Setting | Value |
| --- | --- |
| Similarity-component edge | At least 70% identity and 80% span coverage of each sequence |
| Component rule | Either reported alignment direction establishes an undirected edge |
| Exclusion | Entire connected component, including context sequences |
| Remaining anchors | At least two unique anchors in every labelled group |
| Remaining reference sequences | At least four |
| Placement assignment | Group likelihood-weight mass at least 0.80, margin at least 0.20 |
| BLAST baseline eligibility | Identity at least 30%, query and reference coverage at least 70%, E-value at most 1e-5 |
| BLAST assignment | Best eligible bitscore; cross-group ties and context-only best hits abstain |

All-versus-all BLAST uses one HSP per pair and a target budget equal to the
complete reference count. Full-length self hits are required. Coverage uses
sequence-coordinate spans, not gapped alignment length. Discovery is permissive
(`E=1000`, word size 2, SEG off, composition adjustment off); baseline assignment
has the stricter separate E-value gate. Search settings and executable identity
are recorded. Missing alignments do not prove absence of homology.

The 70% threshold was chosen from label-blind seed-sequence geometry before
prediction outcomes; at 80%, this small panel produces singleton components.
Similarity blocking is not independently established clade disjointness. The
method is a simple connected-component holdout, not an implementation of
GraphPart or an external benchmark dataset.

Each eligible fold gets a new training-only MAFFT alignment and IQ-TREE reference
tree with fixed LG+G4 settings. Model fitting and tree construction exclude all
held-out proteins. The query is added to the fixed reference alignment and
placed with EPA-ng. Query group and accession labels are not supplied to native
inference. Reference trees are reused only within the same verified training
fold. BLAST is rerun against exactly those remaining references, including
context proteins, rather than searched against the full original catalogue.

## Interpretation and reporting

Every planned query has a result for both methods. The report distinguishes:

- Assigned correctly or incorrectly relative to the registry label.
- Completed analysis that abstains under the frozen decision rule.
- Held analysis because the remaining comparison panel is inadequate.
- Native execution or audit failure.

Held cases and failures remain in the all-query denominator. Accuracy among
accepted assignments is shown together with assignment coverage, correct/all
queries, and raw counts. Zero accepted assignments have undefined accepted
accuracy. The paired comparison additionally shows queries completed by both
methods, retaining their abstentions.

Per-query elapsed time includes process startup and auditing. The first placement
in each fold includes tree construction; subsequent queries reuse that tree.
Shared preparation time is recorded separately. These timings are not a
hardware-independent speed benchmark. No calibration, confidence intervals,
functional probabilities, isolate resistance phenotype, or safety verdict is
derived from this small seed-panel exercise.

Installed AMRFinder/CARD databases are not treated as an independent baseline:
they may contain the held-out seed proteins. A future external comparison must
audit reference overlap, independent labels, related-sequence leakage and
taxonomic coverage for every method.

The HTML report provides per-query tree links, exact JSON/TSV downloads, and
fold exclusions. Keep the report folder together when sharing. The archive
includes native audit files and may contain local filesystem paths. The
`frozen-protocol.json` file is preserved separately from the report's exported
`protocol.json`; its original bytes are checksummed in `benchmark.json`.

`--prepare-only` runs the clustering search and freezes folds but does not run
predictions. `--threads`, `--env-prefix`, `--registry`, `--panel`, and `--outdir`
can be specified. A target output directory must not already exist. Native
failures produce exit code 2; abstentions and adequacy holds are valid benchmark
outcomes and do not by themselves make the execution fail.

## Initial local smoke run

The native smoke run used the embedded panel, BLASTP 2.17.0, MAFFT, IQ-TREE and
EPA-ng. It produced four labelled folds: five eligible queries and two adequacy
holds. Both methods completed all five eligible queries. BLAST assigned three
correctly and abstained on two; placement abstained on all five under the frozen
rule. This is not evidence that placement outperforms sequence search. The next
scientific work is to expand and independently review the comparison panel and
evaluate a separately defined validation dataset, not to retune thresholds on
these seven seeds and report them as independent test performance.

## Method references

- NCBI BLAST command-line options: https://www.ncbi.nlm.nih.gov/books/NBK279684/
- EPA-ng: https://github.com/Pbdas/epa-ng
- IQ-TREE: https://iqtree.github.io/doc/
- GraphPart, related-sequence partitioning discussion: https://doi.org/10.1093/nargab/lqad088

These references describe underlying tools and methodological considerations;
they do not constitute validation of the BaSIS placement decision rule.
