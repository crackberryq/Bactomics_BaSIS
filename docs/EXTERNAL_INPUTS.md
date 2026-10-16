# External annotated input — 0.3.0rc8

BaSIS can use a bacterial assembly and matching annotations produced outside HybAs. Kraken output is not required. Set `input_mode` to `external` to skip adjacent HybAs metadata, even when the files happen to use an `isolate/annotation/` folder layout.

## Required inputs

Supply nucleotide FASTA (`assembly`), protein FASTA (`proteins`) and GFF (`gff`). The GFF must link the protein identifiers to CDS features on the supplied assembly. The sequence check verifies coordinates, identifiers and CDS translations using the existing validation rules. GenBank is optional for general input validation; individual requested modules can require it separately.

An assembly alone is not accepted for this annotated-input workflow. The command returns `annotation_required` and names the missing annotation inputs. BaSIS does not generate annotation automatically.

## Import and validate

A CSV, TSV or Excel sheet can contain explicit paths and the required `sample_id` column. For example:

```csv
sample_id,assembly,proteins,gff,input_mode
sampleA,annotations/assembly.fasta,annotations/proteins.fasta,annotations/features.gff3,external
```

Explicit paths are relative to the source sheet, or absolute; file stems and extensions need not follow HybAs naming. Names-only discovery retains the exact layouts in [batch inputs](BATCH_INPUTS.md).

```bash
basis import-samples --file samples.csv --input-mode external --dry-run -v
basis import-samples --file samples.csv --input-mode external --directory study-external
basis validate -c study-external/config.yaml -v
basis doctor -c study-external/config.yaml -d -e -v
```

Use the same row/command options for preview and import. A nonblank per-row `input_mode` overrides the command default. Omitting `--base-config` creates the full preset with automatic first-run resources. Import downloads nothing; the normal Snakemake run prepares missing requested environments, databases and models. A base configuration retains its declared modules, thresholds and import modes; review them for the new sample.

The steps answer different questions:

| Command | What it checks |
| --- | --- |
| `import-samples --dry-run` | Sheet structure and file identity; creates no study. |
| `import-samples` | Creates a new manifest, source-sheet snapshot and audit; does not run analyses. |
| `validate` | Existing sequence consistency checks and declared module decisions; does not check installed tools or resources. |
| `doctor` | Checks requested prerequisites and existing explicit resources; automatic first-run resources are reported as planned. It does not download resources or execute native analyses. |

`-v` adds stages and 30-second heartbeats to stderr. JSON stays on stdout.

## Taxonomy and provenance

Preview rows include `input_context`. `validate` and `doctor` return a top-level `input_context` mapping keyed by sample ID. It shows input mode, recorded taxon/rank/source/status and HybAs metadata policy/status. For external mode, `hybas_metadata_status` is `not_consulted` and `taxonomic_classification_performed` is false.

Missing taxonomy stays unavailable. Blank entries and markers such as `NA`, `N/A`, `None`, `null`, `unknown`, `unresolved`, `not resolved`, `not_recorded` and `not supplied` are displayed with `taxon_status: not_recorded`; the raw entry is preserved. This status describes missing information, not an attempted classification that failed. The existing missing-taxonomy advisory is retained for requested modules without changing their planning actions.

If you supply a taxon, it remains a declaration with its recorded source. BaSIS does not infer taxonomy from filenames, manufacture a Kraken result or verify the declaration during input validation. `external` conflicts with `hybas_dir` and with `taxon_source=hybas_kraken_operational_target`, including when the taxon field is blank. Both imported sheets and directly supplied TSV manifests enforce that provenance restriction.

Existing module prerequisites still apply. Requested work held for missing information remains incomplete; an input validation pass does not establish that every requested analysis can run.

## Target-PC feature check

With the rc6 environment active:

```bash
python -I BaSIS_check_external_inputs.py --annotation-dir ~/bactomics/isolate4/annotation
```

The supplied checker hashes the original required files, makes isolated copies, and treats those copies as external input. It places deliberately invalid HybAs metadata only beside the copies, then verifies that external preview/import and sequence validation ignore it. It also checks missing/declaration-only taxonomy, assembly-only rejection, conflicting provenance and a mismatched-ID negative fixture. It records package identity, commands and source preservation in a separate summary ZIP.

This uses an existing annotation set to exercise external-mode software behavior. It is not evidence of compatibility with every annotation producer. No native tools, GPU inference or database downloads are invoked. The copied annotation contains no added GenBank or Kraken input. Keep the original annotation and completed analysis results in place.
