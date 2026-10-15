# Batch inputs in BaSIS 0.3.0rc5

The batch importer creates a new study with `samples.tsv`, `config.yaml`, an original-sheet snapshot and `import-audit.json`. It never runs annotation or analysis tools. One sample sheet can contain several isolates; the normal run produces a consolidated report.

## Preview before creating a study

```bash
basis import-samples --file samples.xlsx --root /absolute/path/to/bactomics --dry-run -v
```

The read-only preview reports `ready_for_import` (exit 0) or `blocked` (exit 2), the number of ready/blocked rows, and each sample's resolved paths and file errors. It creates no study or snapshot. `--directory` is optional during preview; when supplied, an existing destination blocks import. Use the same `--base-config`, `--sheet` and `--input-mode` settings you intend to import with.

File errors are collected across structurally readable rows. Structural errors such as duplicate IDs, formulas or malformed tables block the sheet before row resolution; those previews have zero row counters and a top-level error. Shared required input files block every affected row, with grouped diagnostics. Global configuration errors can block import even when individual rows are ready. Inspect both top-level and per-row errors.

`-v` prints progress and heartbeats to stderr; JSON remains on stdout, so `> preview.json` can save it. A preview hashes input files and may take time on large sheets or network drives. Import rechecks the sheet and files before committing. Preview does not check sequence consistency, tool/database readiness, biological suitability or all destination filesystem permissions. Run `basis doctor` on the imported study for the separate analysis preflight.

## Start with sample names

Use `examples/batch/samples.xlsx` or `sample_names.tsv`. The required column is `sample_id`; keep IDs as text, particularly when they contain leading zeros. The Excel example has one `Samples` worksheet and no formulas. Replace the example IDs with the desired sample names.

```bash
basis import-samples --file samples.xlsx \
  --root /absolute/path/to/bactomics \
  --directory study-batch
basis doctor -c study-batch/config.yaml -d -e
```

The destination must not exist. `--base-config` reuses resolved tool/resource configuration but assigns fresh sample, result and placement-cache paths. It preserves declared thresholds and module selections, including import-mode settings. Without a base config, the importer creates the full preset with automatic first-run resources. Import itself downloads nothing; the normal Snakemake run prepares missing requested resources. Existing sample-specific exclusions must name samples present in the new sheet.

The importer refuses to transfer circular-contig declarations or a circular IntegronFinder topology from a base configuration. Those concern a particular assembly and need separate review for new samples. A default linear setting is not confirmation of physical chromosome topology.

Names-only discovery checks exactly these three layouts for every ID:

| Layout | Required files for sample `example` |
| --- | --- |
| HybAs-shaped | `ROOT/example/annotation/example.fna`, `.faa`, `.gff` |
| Sample folder | `ROOT/example/example.fna`, `.faa`, `.gff` |
| Flat folder | `ROOT/example.fna`, `.faa`, `.gff` |

An optional `.gbk` or `.gbff` with the same stem is included. Files in multiple layouts, or both GenBank extensions, require explicit paths. The importer never chooses an arbitrary recursive match.

## Explicit paths and external annotation

See [external inputs](EXTERNAL_INPUTS.md) for the external-mode sequence check, provenance fields and focused PC acceptance command.

A sheet can instead supply `sample_id`, `assembly`, `proteins` and `gff`, plus optional `genbank`. Explicit paths are relative to the original sheet, or absolute; `--root` is for discovery only. Each row must supply all three required paths or use names-only discovery.

| `input_mode` | Behavior |
| --- | --- |
| `auto` | Preserves existing path-based HybAs discovery when applicable. |
| `hybas` | Requires one matching isolate/annotation set and an identifiable HybAs root. Missing taxonomy is still unknown. |
| `external` | Reads the supplied annotation without consulting adjacent HybAs metadata. A declared `hybas_dir` conflicts with this mode. |

The command's `--input-mode` supplies a default; a nonblank row value takes precedence. External input needs matching nucleotide FASTA, protein FASTA and GFF CDS identifiers and translations. GenBank is optional generally but required for native ICEscreen. An assembly alone receives an `annotation_required` error; annotation is not automatically generated. No Kraken output is required for external annotated input, and absent taxonomy is never inferred from a file name.

Optional metadata columns are `taxon`, `taxon_rank`, `taxon_source`, `amrfinder_organism`, `taxon_phylum`, `phenotypes` and `hybas_dir`. `phenotypes` is a path to the existing JSON format. A supplied `taxon_phylum` remains a declaration. For HybAs inputs, a blank phylum can be filled from consistent, exact selected-target ancestry in available enabled Kraken reports, with source hashes recorded. Species is never inferred from a genus target.

## Excel and table validation

`.xlsx`, UTF-8 CSV and UTF-8 TSV are accepted. For multiple worksheets add `--sheet Samples`. Legacy `.xls` must be saved as `.xlsx` or delimited text. Formula/error cells, merged cells, numeric/date/boolean values, malformed rows, unknown columns, duplicate IDs (including case variants), missing inputs and shared required input files are rejected. Excel scientific notation or lost leading zeros cannot be recovered; enter IDs as text before saving.

Excel requires `openpyxl==3.1.5`. The deployment installer offers `--with-excel` to install it and `et-xmlfile==2.0.0` into the new venv; this explicitly permits a dependency download. The current clean deployment installer downloads Python dependencies; source checkouts use pip. Older frozen release installers may differ. A conventional wheel installation can select the optional extra:

```bash
python -m pip install '/path/to/bactromics_basis-0.3.0rc5-py3-none-any.whl[excel]'
```

CSV/TSV import works without the Excel dependency. BaSIS reads formulas as formulas so it can reject them; it does not trust Excel's cached computed values. The original sheet is copied, checksummed and made read-only. Import validates table structure and file identity; `basis validate` or `basis doctor` performs sequence consistency checks. Import and execution each record their own hashes.

## Per-sample module scope

The [per-sample preflight report](PREFLIGHT.md) displays independent input failures, resource-check outcomes and explicit exclusions before execution. Add `--report-dir NEW_DIR` to `validate` or `doctor`.

Global module settings remain in `modules`. An explicit exclusion for an individual sample uses:

```yaml
sample_module_overrides:
  example:
    pathogenfinder2:
      enabled: false
      reason: "Withheld under the study's declared input eligibility criteria"
```

These overrides only exclude modules and require a reason. They cannot activate a globally disabled module or alter thresholds. Unknown sample/module IDs fail validation. A deliberate exclusion reduces requested scope. A requested module held because prerequisites are missing remains incomplete.

Unknown taxonomy produces an advisory for requested broad screenings, not a universal block. Native ICEscreen requires matching GenBank and an in-scope sample phylum supplied by the user or resolved from verified HybAs selected-target ancestry. Missing or conflicting scope information yields a recorded hold; a genus name alone is not mapped to a lineage. This is prerequisite and declared-scope checking, not a general biological suitability classifier.

`basis doctor`, local execution and Snakemake use the same decisions. `--force` does not bypass holds. The report's **Inputs and module scope** section shows provenance, planned action, actual execution and reasons. `module-scope.tsv` exports those fields. Saved historical evidence without decisions is labeled as not recorded; report refresh does not invent a new assessment.

## Run and reproduce

After reviewing preflight for the desired scope:

```bash
basis run -c study-batch/config.yaml -d -e -v
```

The default Snakemake engine prepares automatic first-run resources and schedules jobs within the configured CPU, memory and GPU budgets. The local compatibility engine processes samples serially and requires existing tools and manually prepared resources. Keep the configuration, sample manifest, import audit, original sheet, software identity and resource snapshots with the outputs. Module completion, deferred candidates and unresolved interpretation remain separate report states.

This candidate is installed alongside earlier releases. The recovered pilot integration check is separate from independent biological validation. Software checks do not establish biological accuracy.

## Earlier rc5 batch acceptance checkpoint

The rc5 deployment bundle supplied `batch-check-inputs/pilot456.xlsx` and the equivalent CSV, containing only the sample IDs `isolate4`, `isolate5`, `isolate6`. With the rc5 environment active:

```bash
python -I BaSIS_check_batch_import.py --root ~/bactomics
```

The checker verifies the installed package, previews and imports both formats into fresh subdirectories, checks file identities and CSV/Excel agreement, and verifies duplicate/missing-sample rejection. It exports `batch-import-check-summary.zip` without sequence files. Source preservation is checked from the first file-hash observation onward. The new configurations contain resource placeholders and are not execution-ready. No `doctor`, native tools or GPU inference are run. The target-PC rc5 batch checkpoint passed for isolates 4, 5 and 6. Native batch scheduling and biological validation remain separate. The rc6 external-input checkpoint also passed on the target PC. The rc7 per-sample preflight checkpoint also passed. The rc8 bundle supplies the unified-run checker described in [RUN_CONTROLS.md](RUN_CONTROLS.md).
