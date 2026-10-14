# Explicit resource preparation

BaSIS 0.3.0rc1 uses the same packaged Snakefile for analysis, environment setup,
and resource preparation. Resource preparation is an administrative target:
an ordinary `basis run` checks configured resources and does not download a new
database release. The commands below do not change study configurations.

Create a separate YAML file with `schema_version: 1` and a `resources` mapping.
Each named resource has its own immutable snapshot destination. A plan is
read-only; `--execute` explicitly enables download/build work:

```bash
basis prepare-resources -c resources.yaml --resource amrfinder
basis prepare-resources -c resources.yaml --resource amrfinder --execute --cores 4
```

Repeat `--resource` to select additional resources. `--report-dir` selects a new
attempt directory; otherwise records are placed beside the resource YAML under
`.basis-resources/attempts/`. `--conda-prefix` selects the Snakemake environment
cache parent. When a resource has no `environment_prefix`, its rule uses the
packaged Conda recipe. Supply `environment_prefix` to verify and reuse an
existing tool environment.

## Supported backends

| Backend | Action | Release constraint |
| --- | --- | --- |
| `amrfinder` | `amrfinder_update --database DESTINATION` | Explicit upstream-current acknowledgement; expected version file or file hashes |
| `genomad` | `genomad download-database DESTINATION` | Same |
| `antismash` | `download-antismash-databases --database-dir DESTINATION` | Same |
| `mobsuite` | `mob_init --database_directory DESTINATION` | Same |
| `bacmet` | Pinned experimental BacMet v2 FASTA validation and DIAMOND indexing | Built-in source SHA256 and expected record count |
| `pinned_files` | Copy/download individually hash-pinned files | Every source requires a SHA256 |

The first four native downloaders choose the content offered by their upstream
software. BaSIS does not pretend they can fetch an arbitrary historical release.
They require `allow_upstream_current: true`, a specific `version` label, and
either `version_file` or `expected_files`. A version file must contain exactly
the declared version after surrounding whitespace is removed. Expected-file
checks validate those exact files; they do not independently prove every
component belongs to the declared release. A mismatch fails preparation and
preserves the downloaded material for inspection.

For example, this requests the upstream AMRFinder database only if its version
matches the chosen release. Replace the version with the release you intend to
obtain. If upstream has moved on, this deliberately fails instead of silently
substituting another release.

```yaml
schema_version: 1
resources:
  amrfinder:
    backend: amrfinder
    destination: ./db/amrfinder-2026-08-07.1
    version: "2026-08-07.1"
    environment_prefix: /home/jimi/bactomics/BaSIS_v0.1.0/.basis-envs/basis-core-amr
    allow_upstream_current: true
    version_file: latest/version.txt
    required_paths:
      - latest/AMRProt.fa
      - latest/AMRProt.fa.phr
      - latest/AMR.LIB
    threads: 4
```

For resources with several component versions, use `expected_files`, mapping
relative source/version-file paths to known SHA256 values, plus the required
database artifacts. Do not invent hashes or use the hash of an unrelated file.
Record the version label as a bundle identifier when that is what it represents.

The BacMet backend requires only its destination and optional existing DIAMOND
environment; its version and source pin are built in:

```yaml
schema_version: 1
resources:
  bacmet:
    backend: bacmet
    destination: ./db/bacmet-experimental-v2
    threads: 4
```

`pinned_files` can capture already selected CARD, VFDB, PROSITE or ProtT5 files.
It does **not** perform their database-specific extraction, conversion, indexing,
tokenizer checks or model acceptance. Its result is labelled `source_files_only`.
A pin must be recorded from the intended release before execution:

```yaml
schema_version: 1
resources:
  reference_sources:
    backend: pinned_files
    destination: ./db/references-release-1
    version: "release-1"
    files:
      - path: references.faa
        source: ./reviewed-inputs/references.faa
        sha256: REPLACE_WITH_THE_64_CHARACTER_SHA256
```

Use `url: https://...` instead of `source:` for a remote file. A file must have
exactly one source. HTTPS certificate validation remains enabled, and redirects
to insecure HTTP are rejected. Archives are saved as raw files; there is no
automatic extraction or execution of downloaded content. Unknown backends and
arbitrary shell-command settings are rejected.

## Records, reuse and failures

Every completed snapshot contains `provenance.json` with the normalized recipe,
recipe hash, declared version, version-evidence type, every produced file's
SHA256, internal symlink targets, and native environment verification where
applicable. Native tool commands and verification logs are kept in the attempt
directory. `summary.json` records each resource's outcome. `ready` means this
resource preparation contract passed; the summary still reports
`analysis_readiness: not_assessed` until study preflight checks configured paths.

Rerunning an identical resource recipe verifies its existing snapshot's bytes
and reuses it. An existing unowned, changed, or incomplete destination is never
overwritten. For a retry after failure, inspect the log and select a new empty
destination. Snapshot locks serialize competing writers. Use each returned
concrete database path in the study configuration, then run `basis doctor`.

Software tests exercise source-pin failure, existing-directory preservation,
version mismatch, escaped symlinks, receipt integrity and a real Snakemake
workflow using local fixture files. They do not constitute live upstream
download acceptance or biological validation.
