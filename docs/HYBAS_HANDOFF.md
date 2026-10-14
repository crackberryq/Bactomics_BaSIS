# HybAs metadata handoff

BaSIS imports taxonomy context from HybAs 8.7 summaries and the inspected HybAs
AWARE output layout.
The supplied workflow header identifies `8.8-ieee-batch`; its configuration
identifies `HybAs-9.0.0-AWARE`. BaSIS records the version in each run summary
when available rather than assuming a version from the filename.
The user's existing isolate1 output records `HybAs-8.7-ieee-batch` and a generic
`target_taxid`, without an AWARE decision table. Update 3 adds this older contract.

## Automatic discovery

For sample `isolate1`, the expected root is the isolate directory, for example
`/home/jimi/bactomics/isolate1`. It contains:

| Path below the isolate root | Use |
| --- | --- |
| `annotation/isolate1.fna`, `.faa`, `.gff`, optional `.gbk` | Matching annotation input set |
| `metadata/kraken_decision.tsv` | AWARE selected target, rank, method and decision evidence |
| `metadata/run_summary.json` or `.tsv` | Target fallback, consistency checks and upstream version details |
| `reports/kraken2/illumina.report.txt`, `nanopore.report.txt` | Read classification summaries, including up to three leading species per platform |
| `work/final/assembly.final.fasta` | Sequence-level binding to the annotation assembly |
| `metadata/checksums.tsv` | Validate available final-assembly and run-summary checksums |

`basis init --directory STUDY --hybas-dir ROOT` discovers matching annotation
sets and imports available taxonomy context. ROOT may be the isolate directory,
its annotation directory, or a parent containing several isolates. Duplicate
annotation sets for one sample ID are rejected.

Existing sample sheets are enriched on every load: `validate`, `doctor`, planning
and execution all use the current matching metadata. The `hybas_dir` sample-sheet
column can also explicitly identify the isolate root. This release requires the
matching original annotation layout; arbitrary relocated annotation sets are
not silently linked by filename alone.

## Taxonomy semantics

The inspected HybAs workflow selects an operational genus or species target from
Kraken classifications of reads. It does **not** run a separate final-assembly
species classifier. Prokka annotation filenames and its generic organism fields
are not treated as species evidence.

If no taxon has been declared, an inferred Kraken target can populate `taxon`,
with `taxon_rank` set to `genus` or `species`, `taxon_source` set to
`hybas_kraken_operational_target`, and runtime `taxon_status` set to
`read_based_operational_target`. A genus remains a genus. The leading species in
a read report is retained as supporting evidence and is never promoted to an
isolate identification. Manual target placeholders and disabled Kraken runs do
not create a taxon assignment.

### HybAs 8.7 compatibility

When newer selected-target fields are absent, BaSIS reads the recorded
`target_taxid` from the run summary and finds that exact taxid in the original
Kraken reports. These are the upstream classification data used in the inspected
MultiQC workflow. Matching is independent of abundance and works at any reported
rank, including a genus target outside the list of leading species. HTML scraping
and live taxonomy lookups are not required.

The legacy target has `source: legacy_run_summary_target` and
`selection_method: not_recorded`. Its selection is not retrospectively attributed
to AWARE. The matching name, rank, counts and percentage are retained separately
for each platform. A consistent genus/species target with positive clade-read
support can populate a blank operational taxon; a declared species is preserved.
Leading read species remain separate evidence even when the target is a genus.

Newer selected-target fields take precedence, including explicit no-selection
and disabled states. Disagreeing legacy target IDs in JSON and TSV summaries are
rejected. Missing target rows or conflicting report names/ranks retain the target
ID, produce a warning and do not assign an operational taxon. Zero-read matches
can resolve a name/rank but do not support an automatic taxon assignment.

`hybas_status: imported` means metadata were read. The separate
`taxonomy_status` distinguishes `resolved`, `unresolved`, `not_recorded`, and
`disabled`. Resolution means a target name/rank was recovered; it is not a
biological identity validation. Sync output also shows each platform's leading
species, and the HTML report presents those read classifications directly.

A declared taxon is retained. Its source is `sample_sheet` unless explicitly
provided, or `user_confirmed` when recorded with `--species`. The upstream target
remains separately available in `hybas_context.selected_target`. A disagreement
produces a visible warning. A declared species within the selected genus is
compatible with that genus target.

Automatically sourced taxa refresh when the upstream target changes; they are
cleared when its evidence is no longer available or valid. User declarations are
not overwritten. BaSIS does not fill `amrfinder_organism` automatically: that
field selects AMRFinder's supported organism-specific models and is not a free
text synonym for the isolate taxon. Importing taxonomy also does not by itself
validate the applicability of a CARD mutation model across species or relabel
native RGI results.

## Update an existing study

To write the imported values into an existing sample sheet:

```bash
basis sync-hybas -c study-isolate1/config.amr.yaml
```

To record an independently established species identity while importing the
upstream evidence, specify one sample explicitly. For the user's confirmed
identification of this study's isolate1:

```bash
basis sync-hybas -c study-isolate1/config.amr.yaml \
  --sample isolate1 --species "Lysinibacillus irui"
```

The species above is a user declaration, not a conclusion computed by BaSIS.
Use `--dry-run` to preview either operation. The entire sample sheet is checked
before writing. Sync preserves unrelated columns and values, creates a sibling
backup containing the original bytes, and replaces the sample sheet atomically.
It leaves native result records unchanged and does not run biological tools.
Configurations sharing that sample sheet receive the same metadata on next load.

## Identity, provenance and failures

Decision and summary records must identify the same isolate as the sample sheet.
Conflicting target fields, malformed tables, duplicate keys, incompatible input
paths, checksum mismatches and unequal final-assembly sequences raise errors.
Sequence binding tolerates renamed contig headers and FASTA wrapping/order
differences, but preserves sequence multiplicity. It does not establish species.

Missing metadata, intentionally empty Kraken reports and absent optional files
are reported as unavailable. If the final assembly is absent, a warning states
that binding relies on the sample ID and directory layout only. A declared taxon
survives missing upstream metadata.

The imported context records source paths and SHA-256 hashes, available workflow
and Kraken versions, the database path, target rank/method, decision evidence,
read summaries, assembly binding and warnings. It does not read raw sequencing
files or hash a Kraken database. HTML reports distinguish the declared taxon from
the operational target; `evidence.json` preserves the complete imported context.

Existing reports remain historical snapshots. The next normal run creates a
report with the new context. Installing this source update changes the BaSIS
source fingerprint and can invalidate native cache reuse on that next run.

## Validation scope

The update is tested against synthetic fixtures representing this source
contract, including missing, conflicting and tampered metadata. These checks do
not substitute for native acceptance on the user's actual HybAs output directory.
