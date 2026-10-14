# AMR and protein-homology adapters

These adapters execute native tools and preserve their outputs. Parser tests use
synthetic native-style fixtures; they do **not** demonstrate that a native tool
installation works or that biological classifications have been validated.

## Modules and databases

| Module | Executable | `database` | Main input | Detection meaning |
|---|---|---|---|---|
| `amrfinder` | `amrfinder` | Prepared, versioned AMRFinderPlus database directory | Genome, proteins and linked GFF together | Native gene/variant calls, separated into AMR, virulence and stress |
| `rgi` | `rgi` | CARD `card.json` file | Proteins by default; configurable contigs | CARD native Perfect/Strict calls; Loose remains exploratory |
| `vfdb` | `diamond` | Prepared VFDB protein `.dmnd` file | Proteins | Virulence-associated reference homology, not demonstrated virulence |
| `bacmet` | `diamond` | Prepared BacMet protein `.dmnd` file | Proteins | Stress/biocide/metal-associated reference homology, not AMR phenotype |

Database download/build is deliberately separate from a sample analysis. Supply
database versions and manifests in configuration. The database identity and
licensing/source should be recorded; choose/document the intended VFDB subset
and experimentally confirmed versus predicted BacMet catalogue. Do not silently
interchange them. `configured_database_version` is user-supplied metadata, not a
claim that an installed version was independently verified.

The shared runner supplies command prefixes, execution logs, error checking and
timeouts. These adapters use argv arrays and never use a shell. Failed processes,
missing outputs and malformed tables propagate errors. Existing native outputs
are rejected rather than silently reused. Root assigns execution status; adapters
only return biological interpretation with its limitations.

## AMRFinderPlus

The command uses `-n`, `-p`, `-g`, `--plus`, `--database`, `--threads`, and `-o`.
Optional `annotation_format` is passed via `--annotation_format` (for example,
`prokka` or `bakta`); omit it for the native default format. Annotation linkage
must match the supplied files. No blanket identity or coverage override is used.
The native curated thresholds and method labels remain authoritative.

Only the sample's explicit `amrfinder_organism` enables `--organism`; no taxon is
guessed or substituted. The configured database must support the requested
organism, otherwise native execution fails. An omitted organism does not mean
all resistance mutations were screened. Presence of genes is not phenotypic
resistance, and a negative screen does not establish susceptibility.

Supported output schemas include current `Element symbol`/`Element name` and
legacy `Gene symbol`/`Sequence name`/`Protein name` headers. Native core/plus scope,
element type, subtype, method and raw fields are retained. `POINT_DISRUPT` is
exploratory because it denotes a putative disruptive variant, not a curated
phenotypic effect. Partial/internal-stop and HMM-only observations retain flags;
they are not automatically discarded or called harmless. A valid header-only
table means no reported native calls; a zero-byte file is an error.

## CARD RGI

Each run first executes `rgi load --card_json PATH --local` in its own output
directory. Then `rgi main` uses `--local` and writes both native TSV and JSON.
The global RGI database is not changed. The JSON must be an object and the TSV
must have the supported native header. Both artifacts are required.

Options:

- `input_type`: `protein` (default) or `contig`. Protein mode retains annotation
  protein IDs and excludes rRNA mutation models. Contig mode uses RGI's ORF
  calls, which may differ from the supplied annotation; its generated ORF IDs
  are preserved separately, not falsely mapped to supplied protein IDs. The
  native `Predicted_Protein` column, when present and alphabet-valid, is retained
  as `attributes.protein_sequence` with explicit source attribution so DeepSafe
  can investigate an unlinked native ORF without inventing a DNA translation.
- `alignment_tool`: `BLAST` (native default) or `DIAMOND`.
- `include_loose`: default `true`, keeping discovery observations visible.
  Loose-only output has interpretation `inconclusive`, not `not_detected`.
- `low_quality`: default `false`; explicitly enable only for suitable inputs.
- `--include_nudge` is never requested. A parsed nudged result, if imported for
  testing or reuse, is marked exploratory even if its native label says Strict.

RGI's `Percentage Length of Reference Sequence` is a protein-length ratio and
can exceed 100%. It is **not** reference alignment coverage. The adapter retains
that ratio separately. Where `Hit_Start`, `Hit_End` and a CARD protein sequence
are available, reference HSP-span coverage is calculated; otherwise normalized
coverage is null. Perfect/Strict model calls do not establish expression,
phenotypic resistance or overall safety. The raw model type and mutation fields
are retained for assessment.

## DIAMOND VFDB and BacMet

The adapter uses `blastp`, explicit output columns, one HSP per query/subject,
and these configurable search settings:

| Option | Default | Units/meaning |
|---|---|---|
| `evalue` | `1e-5` | Search E-value limit |
| `max_target_seqs` | `25` | Returned target cap; `0` means all per native DIAMOND |
| `sensitivity` | `sensitive` | Native sensitivity mode |
| `min_identity` | `80` | Percent amino-acid identity, 0–100 |
| `min_coverage` | `70` | Percent subject/reference coverage per HSP, 0–100 |
| `min_query_coverage` | `0` | Additional percent query coverage per HSP, 0–100 |

The 80/70 values are reporting-screen defaults, **not validated functional or
safety cutoffs**. Configure each module separately. These thresholds are applied
after parsing, so below-threshold alignments remain exploratory evidence with
`passes_threshold: false`. The search E-value/target cap still limits what enters
the native output. One-HSP coverage does not join split genes or multiple HSPs;
partial or discordant observations may require further analysis.

Exact headerless output order:

`qseqid sseqid pident length qlen slen qstart qend sstart send evalue bitscore qcovhsp scovhsp stitle`

Genomic coordinates are left null for protein alignments and resolved by the
shared annotation layer where possible. VFDB/BacMet reference IDs and titles
are retained without guessing gene-family labels from free text. A missing file
is an error. An empty file is native no-hit output, accepted by the execution
adapter only after a successful checked process and a freshness check.

## Dependencies and validation limits

Python parsing uses only the standard library. Native installations must supply
AMRFinderPlus and its BLAST+/HMMER dependencies; CARD RGI and its configured
alignment/ORF dependencies; and DIAMOND. Database indexes must match the installed
tool versions. No native binaries or databases are bundled. Run the actual tools
on their official test data and representative user inputs before claiming
installed-tool compatibility or scientific validation.

Tests cover native-style schema parsing, legacy headers, valid no-hit outputs,
malformed/missing output rejection, unknown values, category separation,
exploratory classification, coverage semantics, command composition, failed
process propagation and stale-output rejection. Fake command runners test the
adapter contract; they are not end-to-end native integration tests.

Primary documentation consulted 2026-10-07:

- https://github.com/ncbi/amr/wiki/Running-AMRFinderPlus
- https://github.com/ncbi/amr/wiki/Interpreting-results
- https://github.com/arpcard/rgi/blob/master/docs/rgi_main.rst
- https://github.com/bbuchfink/diamond/wiki/3.-Command-line-options
