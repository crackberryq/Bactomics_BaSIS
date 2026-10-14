# Batch sample templates

Edit `samples.xlsx` in Excel, or edit the matching `sample_names.tsv` in a text editor. Each row identifies one sample. Replace the two example names with your own and keep the first-row headers unchanged. The workbook has one sheet, `Samples`, with instructions in its header comments.

Keep `sample_id` values as **text**, especially IDs with leading zeros. Use a unique ID beginning with a letter or digit, followed by letters, digits, underscores, hyphens or dots. Blank taxonomy fields mean that you have not supplied a taxonomic declaration; they do not mean that the sample is taxonomically resolved.

The importer searches these exact locations below the root directory for matching annotation files:

- `<root>/<sample_id>/annotation/<sample_id>.*`
- `<root>/<sample_id>/<sample_id>.*`
- `<root>/<sample_id>.*`

It requires the matching `.fna`, `.faa` and `.gff` files and can include a matching `.gbk` or `.gbff` file. Multiple competing matches require explicit file paths. An assembly alone needs annotation before it can be used by the current pipeline.

`input_mode` accepts `auto`, `hybas` or `external`. Use `auto` for automatic HybAs discovery, `hybas` for HybAs outputs, and `external` for annotated inputs that should not inherit HybAs metadata. Declare `taxon`, its `taxon_rank` and `taxon_phylum` only when supported by your taxonomic evidence. Leave unresolved fields blank rather than guessing; a missing or incompatible phylum can withhold a module that requires it.

Import into a new study using an existing configuration with your tool and database locations:

```bash
basis import-samples \
  --file examples/batch/samples.xlsx \
  --root /path/to/bactomics \
  --directory new-study \
  --base-config /path/to/config.yaml
```

You can supply `--sheet Samples` explicitly. The command prepares the study files; review its import audit and resolved paths before running the study. Do not add formulas, merged header cells or extra instruction sheets to the input workbook.
