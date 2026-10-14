# DeepSafe follow-up in BaSIS 0.1

DeepSafe converts existing normalized screening findings into bounded, inspectable
candidate investigations. It is a research evidence workflow, not a validated
functional or organism-safety classifier. Enabling it does not create a new
phenotype measurement or validate an isolate for a particular use.

## What runs automatically

1. Select AMR and virulence findings, eligible exploratory findings, and a bounded
   set of ML attention candidates. Nonexploratory AMR/virulence screening evidence
   has priority over exploratory-only and attention-only candidates, in that order.
   Core AMRFinder HMM and unnudged RGI Strict calls retain screening priority;
   RGI Loose/nudged calls remain exploratory. Multiple calls at the same locus retain all finding
   IDs; distinct genomic copies remain separate, even when their sequences match.
2. Record an analysis plan and decision trace. An unambiguous native exact
   AMRFinder core or CARD RGI Perfect protein-homologue call, with 100% identity
   and reference coverage and no integrity/conflicting-gene flags, takes a
   context-review route by default. Ambiguous AMR, virulence homology and
   attention-only candidates have distinct exploratory routes.
3. Extract the query from the supplied protein FASTA by its exact identifier, or
   use an explicit `attributes.protein_sequence` supplied by a normalized source
   finding. Preserve a locus-context nucleotide FASTA when coordinates exist.
   Coordinates alone are not sufficient evidence to invent a translated protein.
4. Search the configured local reference catalogue once with BLASTP or DIAMOND,
   then automatically assemble a reference set for **each candidate**. A reviewed
   family profile can add explicit comparator anchors. The optional `metadata`
   mode selects matching catalogue family labels without a sequence search; that
   mode is recorded and must not be represented as similarity evidence.
5. Optionally scan a configured, indexed HMM database with HMMER `hmmscan`.
   Preserve native tables and validate numeric fields, query IDs and completion
   markers. A profile can explicitly request native GA, TC or NC model cutoffs.
6. Write reference selection evidence and sequence hashes. Prepare one FASTA
   containing the query and chosen comparators. If requested and sufficiently
   informative, run MAFFT and IQ-TREE with a recorded seed and thread budget.
7. Validate query retention in the alignment and tree. Preserve the tree and
   labels for review. No nearest-tip functional assignment is performed.

This release follows normalized input findings. It does **not** independently run
an assembly-wide TBLASTN discovery screen or predict missing ORFs. Optional domain
scanning requires a configured HMM resource and is not claimed when unconfigured
or disabled. Existing domain/context calls remain inspectable source evidence.
The core workflow must supply raw-genome candidate records if that evidence is to
be followed here. Local proximity to an IS, integron or plasmid-associated finding
is context evidence, not proof of transferability.

## Configuration

Place these keys in the DeepSafe configuration mapping:

```yaml
profiles_dir: profiles
reference_fasta: /absolute/path/references.faa
reference_metadata: /absolute/path/references.tsv
reference_search_tool: blastp  # blastp, diamond, or metadata
maximum_candidates: 40
maximum_attention_candidates: 5
maximum_exploratory_candidates: 20
include_exploratory: true
max_references: 30
search_max_targets: 100
min_identity: 0
min_coverage: 0
min_reference_coverage: 0
retrieval_evalue: 0.00001
phylogeny: false
investigate_exact_calls: false
domain_scan: true
domain_database: null  # optional hmmpress-indexed HMM database
phylogeny_seed: 42
threads: 2
context_flank: 20000
circular_contigs: []
executables:
  blastp: blastp
  diamond: diamond
  mafft: mafft
  iqtree: iqtree
  hmmscan: hmmscan
```

Identity and coverage values are percentages, not fractions. These defaults are
exploratory retrieval settings, **not biologically validated decision thresholds**.
The search retains each reference's highest-scoring individual HSP and records
query and reference coverage for that HSP; it does not join disconnected HSPs into
an apparent full-length match. The search shortlist and final-reference budgets
limit what can be assessed. Explicit profile anchors supplement sequence shortlists.

`maximum_candidates` is the global ceiling. `maximum_exploratory_candidates`
separately bounds exploratory-only follow-up; its default of 20 is an operational
budget, not a biological threshold. `include_exploratory: false` defers those
candidates with reason `exploratory_followup_disabled`, without removing evidence.
Candidates supported only by Loose AMR calls take `exploratory_family_investigation`.
`maximum_attention_candidates` applies only to attention-only candidates. A locus
with both native screening and Loose/ML evidence retains screening priority and
uses one slot. A locus with Loose plus ML evidence uses the exploratory queue and
cannot bypass its limit. Queue assignment and limits apply after locus merging.
Candidate
selection respects the PF2 adapter's supplied rank, then maximum attention score
when rank is unavailable; final ties use stable candidate IDs. This ordering is
prioritization, not risk scoring. Candidate
budgets never erase findings: candidates outside the budget remain in the manifest
as `deferred`. Deferred candidates still make DeepSafe's assessment incomplete;
they are never reported as assessed negatives. Configure topology explicitly per sample or globally; do not infer
circularity from a one-contig assembly. When known circular, context wraps around
the origin. Linear or unspecified topology produces a recorded truncated window
near a contig end. Origin-spanning CDS coordinates are currently rejected for
manual normalization rather than silently mis-extracted.

`investigate_exact_calls: true` overrides the context-only shortcut. Internal stop
positions, terminal stops, ambiguous residues and native partial/disruption flags
remain in `analyses.integrity`. Native partial/disruption flags or internal stops
prevent that shortcut. No expression or activity is inferred from these checks.

The supplied runner handles command prefixes, timeout enforcement and logs.
Its execution environment needs BLAST or DIAMOND, and MAFFT/IQ-TREE when phylogeny
is enabled, and HMMER when a domain database is configured. Python needs only the
standard library and PyYAML. Executable names are
configurable because installations may expose `iqtree`, `iqtree2`, or `iqtree3`.

## Reference catalogue contract

FASTA identifiers must be unique and match `reference_id` in the TSV. Every FASTA
record needs metadata and every metadata record needs a sequence. Required TSV
columns:

| Column | Meaning |
|---|---|
| `reference_id` | Exact FASTA identifier, without `>` or whitespace |
| `accession` | Source accession; versioned accessions are preferred |
| `family` | Explicit family label used for metadata selection |
| `comparison_group` | Reviewable comparator-group label |
| `role` | `reference`, `anchor`, or `outgroup` |
| `review_status` | `reviewed`, `unreviewed`, or `rejected` |
| `source` | Source URL or other inspectable provenance |

Optional `sequence_sha256` is verified when supplied and computed when absent.
Optional `retrieved_at` records acquisition time. Retrieval does not establish the
correctness of functional labels. `rejected` references cannot be selected.

No verified sequences, accessions, or biological decision cutoffs are invented or
bundled. Prepare a local catalogue from inspected source records. A versionless
accession remains usable for exploratory work, with a warning and sequence hash.

`basis.references.prepare_accession_catalog(requests_tsv, out_fasta, out_metadata)`
is an explicit opt-in helper for **public protein accession retrieval** from NCBI.
It sends accession IDs only; it does not upload isolate sequences. The input TSV
requires `accession` and can additionally supply `reference_id`, `family`,
`comparison_group`, `role`, and `review_status`. Missing review status defaults to
`unreviewed`. The helper verifies each returned identifier/sequence and records the
resolved accession, source URL, retrieval time and hash. Retrieval/validation
failures raise an error. The default request budget is 100 accessions; no requests
are made merely by enabling DeepSafe.

## Family profiles and validation status

Profiles are YAML files with `schema_version: 1`, unique `profile_id`,
`candidate_aliases`, `required_comparison_groups`, `anchor_reference_ids`,
`validation_status`, and `interpretation_policy: exploratory_only`.

- `unvalidated_template`: configuration scaffolding only; no classifier validation.
- `reviewed`: requires reviewer, review date, evidence citation, required groups,
  and explicit anchors. This records a user's review claim; BaSIS does not
  independently certify the reviewer or evidence.
- `validated`: requires the same provenance fields. This is still a user-supplied
  status claim, and BaSIS 0.1 still produces exploratory evidence only.
- Unsupported or ambiguously matched families use the `generic` fallback.

The supplied Cfr profile is explicitly an **unvalidated template**, with no anchors
or verified reference sequences. It lists Cfr/RlmN comparator groups to make a
missing comparison visible. Shared motifs or a tree's nearest neighbour are not
used as proof of resistance or absence of resistance. Missing reviewed comparator
groups or anchors remain explicit warnings; they cannot produce a categorical
negative result.

Optional profile `analyses` restricts investigations to a subset of `integrity`,
`context`, `references`, `domains`, and `phylogeny`. Basic integrity/context checks
remain available; global `phylogeny: false` or `domain_scan: false` always wins.
For a family-specific HMM resource, add:

```yaml
resources:
  domains:
    database: /absolute/path/family.hmm
    native_cutoff: ga  # optional: ga, tc, nc; must exist in the actual model
```

Relative resource paths resolve against the profile file's directory. The model
and all `.h3f/.h3i/.h3m/.h3p` indices must already exist; DeepSafe does not modify a
shared database. The HMM file hash, applied native cutoff and every domain remain
in evidence. Missing configured databases or failed scans are failures;
unconfigured/disabled scans are explicitly `not_assessed`.

## Outputs and execution states

`candidates.json` retains the complete selected/deferred candidate ledger, source
finding IDs, selection reasons, queries, profile status, references, context and
analysis plans/decision traces and states. IDs include sample identity to prevent
cross-sample collisions. Per-candidate outputs include `query.faa`, `locus_context.fna`,
`reference_manifest.json`, `query_plus_references.faa`, and
`phylogeny_labels.json`; successful phylogenies additionally provide alignment and
native IQ-TREE outputs. Original reference labels are mapped to simple tool-safe
identifiers. Identical **reference** sequences may be collapsed with every alias
retained; the query is never removed, even when identical to a reference.
Configured domain scans add `domains.domtblout` and `domains.hmmscan.txt`.

| Candidate execution | Meaning |
|---|---|
| `completed` | Requested available steps executed; scientific result may remain inconclusive |
| `not_assessed` | Required query sequence or configured catalogue unavailable |
| `deferred` | Explicit resource budget prevented investigation |
| `failed` | Malformed data, invalid coordinates, tool failure or lost-query check |

No eligible comparator is a reference-coverage limitation, not a negative AMR
result. Fewer than four distinct query-plus-reference sequences produces an
inconclusive phylogeny state. Search failures raise to the parent workflow; local
candidate failures remain recorded and set `complete: false`. The parent report
must propagate failure, not-assessed and deferred counts into its completeness
summary. Biological interpretation remains `inconclusive`; a sample with no
eligible candidates returns `not_applicable` for this follow-up stage.

## Validation performed by this release

`python -m unittest discover -s tests -p test_deep.py -v` runs synthetic software
checks for candidate selection/copies, sample-specific IDs, adaptive routes,
integrity flags, evidence merging, ML budgets, search error
handling, missing outputs, automatic reference assembly, exact-match query
retention, reference alias provenance, dropped-query detection, insufficient
references, circular context, profile metadata, and public-accession response
verification, native HMM cutoffs and HMM failure/zero-hit separation. These are
contract/integrity checks, **not biological validation**.
External tools and public retrieval are mocked in these tests.
# Building a search catalogue from an existing database

For broad exploratory reference retrieval, an existing protein FASTA release can be converted without hand-writing one metadata row per sequence:

```bash
basis prepare-references --source-fasta /path/to/reference-proteins.faa \
  --source-name "database-name and release" \
  --fasta /path/to/basis-references.faa --metadata /path/to/basis-references.tsv
```

This preserves original header annotations and sequence hashes. It deliberately labels every imported reference `unreviewed` with comparison group `unknown`; a database header or reviewed database record is not proof that its biological function has been validated for this investigation. Set `deep.reference_fasta` and `deep.reference_metadata` to these products. BaSIS then chooses comparator sequences for each selected candidate automatically. Reviewed family anchors can be added subsequently with their supporting evidence.
