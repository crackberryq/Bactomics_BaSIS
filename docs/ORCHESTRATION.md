# Snakemake orchestration in BaSIS 0.3.0rc1

BaSIS uses one packaged `basis/resources/Snakefile` entry point. The CLI and local
interface select its analysis, environment-setup or resource-preparation mode.
Snakemake owns dependencies and resource scheduling; Python workers implement
scientific parsing, evidence checks and report rendering within its rules.

The Python package installs Snakemake 9.6.2. Conda and, where required, GPU drivers
must be installed separately. Installing the application with pip does not itself
install every native tool or download reference data.

## Analysis sequence

The analysis DAG contains:

1. Per-sample input validation and configured resource checks.
2. Named module rules for the configured screening, context and Extend modules.
   Independent sample/module jobs can run concurrently within the declared budgets.
3. Nine DeepSafe stages per sample: preparation and candidate selection, domains,
   reference retrieval, phylogeny, PROSITE patterns, PROSITE profiles, registry
   motifs, family placement and finalization. Preparation waits for all upstream
   result records, including PathogenFinder2 when enabled.
4. Evidence consolidation, report rendering and final provenance capture.

DeepSafe stages process the selected candidates within each sample. They are
separate Snakemake jobs, but each candidate is not a separate scheduled job.
Eligibility checks still decide which analyses apply; a scheduled stage does not
mean every candidate receives a tree or a functional assignment. Candidate
selection, scientific thresholds and interpretation policies are unchanged.

```bash
basis doctor -c study/config.yaml -d -e -v
basis run -c study/config.yaml -d -e -v --output summary
```

The launcher still parses configuration, builds the plan, starts Snakemake and
returns its recorded outcome. It can produce a failure summary if the scheduler
cannot finish. These controller duties do not implement the normal scientific
sequence outside the DAG. The standalone `doctor`, `report` and `control-panel`
commands remain available for inspection and saved-evidence operations.

## Native tool environments

Analysis rules use native Snakemake `conda:` recipes. With the default
`execution.environment_backend: auto`, a supported enabled native module with no
explicit `command_prefix` gets a managed environment. Snakemake creates it when
needed. An explicitly configured prefix remains external and is not replaced.
An import, excluded module or synthetic demonstration needs no tool deployment.

Managed environments use content-addressed Snakemake directory names. Set their
shared parent in the study configuration:

```yaml
execution:
  environment_backend: auto
  conda_prefix: /home/jimi/bactomics/basis-environments
  cores: 4
  memory_mb: 16000
  gpus: 0
```

The default parent is `.basis-envs` beside the result directory. This is a cache
location, not a request to create a named `basis-TOOL` environment. Before running
native tools, BaSIS checks the active environment's installed recipe constraints
and executable startup and records its identity.

To prepare selected environments ahead of analysis:

```bash
# Read-only preview
basis setup-environments --workspace ~/bactomics/basis-workspace \
  --prefix-root ~/bactomics/basis-environments --env core-amr --env deepsafe

# Explicit preparation and verification through the same Snakefile, in setup mode
basis setup-environments --workspace ~/bactomics/basis-workspace \
  --prefix-root ~/bactomics/basis-environments --env core-amr --env deepsafe --create
```

Setup recognizes existing `basis-RECIPE` directories under the selected parent as
external environments and verifies them without replacing them. New environments
use native Snakemake deployment. Setup records distinguish those backends and
report the actual prefix. A directory alone is never proof of installation.
Environment success does not establish database, model or GPU readiness.

`--engine local` retains the older manager-driven named-prefix setup, including
Mamba/Micromamba selection. Its `--retry-failed` option applies only to eligible
tracked local installations; it does not authorize overwriting unrelated prefixes.
The native Snakemake setup backend uses Conda.

## Explicit resource preparation

Resource administration uses the same main Snakefile in a separate, deliberately
requested mode. Normal analysis never downloads or refreshes databases through
these targets. A dedicated YAML recipe declares the chosen resources, versions,
locations and verification expectations. `basis prepare-resources` previews by
default; `--execute` is required for preparation.

See [complete resource schemas and examples](RESOURCE_SETUP.md). Supported backends in this candidate are:

| Backend | Preparation scope |
| --- | --- |
| `amrfinder` | AMRFinderPlus native database updater |
| `genomad` | geNomad native database download/extraction |
| `antismash` | antiSMASH native database preparation |
| `mobsuite` | MOB-suite native database initialization |
| `bacmet` | Pinned experimentally confirmed BacMet v2 preparation and DIAMOND index |
| `pinned_files` | Copy/download explicitly SHA256-pinned source files; no automatic indexing |

For example, this structural recipe captures an existing reviewed reference file.
Replace the source, version label and SHA256 placeholder with the actual values
before running it; the placeholder is intentionally invalid.

```yaml
schema_version: 1
resources:
  reviewed_reference_files:
    backend: pinned_files
    destination: /absolute/path/databases/reviewed-panel-v1
    version: reviewed-panel-v1
    files:
      - path: proteins.faa
        source: /absolute/path/reviewed/proteins.faa
        sha256: REPLACE_WITH_64_HEX_CHARACTERS_FROM_SHA256SUM
```

```bash
sha256sum /absolute/path/reviewed/proteins.faa
basis prepare-resources -c resource-recipes.yaml --resource reviewed_reference_files
basis prepare-resources -c resource-recipes.yaml --resource reviewed_reference_files --execute
```

A `pinned_files` entry accepts either a local `source` or an HTTPS `url` for each
file. It preserves bytes and checks each SHA256; it does not endorse reference
labels or make a raw FASTA/archive an analysis-ready index. Configure verified
prepared paths in the study and run its readiness checks afterwards.

Native upstream downloaders can choose their current release. Their recipes must
explicitly set `allow_upstream_current: true`, declare a non-moving `version`
label, list `required_paths`, and supply a `version_file` whose trimmed contents
must exactly match that label or `expected_files` with SHA256 content pins.
A mismatch fails preparation. Supply `environment_prefix` to retain an existing
tool environment; otherwise the resource rule uses its managed Conda recipe.
These controls do not turn an upstream latest-only downloader into a historical
release download API.

Snapshots contain file checksums and provenance. A prior snapshot is reused only
when its preparation inputs and recorded contents verify. Changed or partial
destinations are preserved and require a new empty destination. No resource
snapshot is silently updated in place. Not every database/model has a preparation
backend: CARD/VFDB/PROSITE and ProtT5 workflows still need their specific
acquisition, indexing or configuration steps. Generic pinned files cover only
source-file acquisition.

## Migrating from 0.2

The `v0.2.0rc16` release is preserved separately. Install `v0.3.0rc1` alongside it
and retain the old study outputs as historical evidence. Use a new result directory
for the new candidate; a changed source identity can invalidate earlier caches.

Existing study configurations with explicit `command_prefix` lists continue to
use those environments under `auto`. BaSIS does not silently remove prefixes or
route a requested external executable into a new environment. Missing or invalid
external prefixes remain errors to resolve.

To adopt managed deployment for a module, deliberately remove its old
`command_prefix`, use executable names rather than absolute paths into another
environment, and set `environment_backend: managed` in that module if desired.
The same fields apply to `deep`. A managed setting with an explicit command
prefix is rejected as conflicting. A module-level `environment_backend` overrides
the execution default. Use `external` if intentionally relying on an existing
prefix or the current PATH. Unsupported native adapters have no managed recipe;
MLST and CRISPRCasFinder remain normalized-import capabilities.

## Scheduling, recovery and evidence

CPU, memory and GPU settings are scheduler budgets, not operating-system memory
limits. The GUI still allows one job per workspace; that job can contain many
Snakemake tasks. Verbose output streams scheduler/worker activity into the GUI log
and stderr while JSON stdout remains machine-readable.

Run-specific completion receipts are scheduler outputs. Scientific result files
remain evidence and are checked before whole-result cache reuse; scheduling does
not delete them first. Workers verify inputs, resources, tool/source identities
and required artifacts. `--force` explicitly requests recomputation.

DeepSafe stage JSON supports restart within the same workflow attempt. A new
`basis run` gets new scheduler receipts and verifies current identities. This
candidate does not promise automatic reuse of partially completed DeepSafe stages
across separate invocations. Completed whole-module/DeepSafe caches remain usable
when their existing verification checks pass.

Native failures become explicit result records, allowing independent work and
failure-aware reports to complete. Scheduler failures remain incomplete and
cannot promote stale outputs into current evidence. Reports distinguish execution,
assessment coverage and biological interpretation; no-hit, not-assessed and failed
states remain different.

## Compatibility and validation limits

`basis run --engine local` retains the serial execution option. The programmatic
`run_pipeline` and small `basis demo` defaults remain local for compatibility;
select `--engine snakemake` to exercise the DAG. The GUI defaults to Snakemake.
The historical `workflow` extra remains accepted but is redundant.

Software verification uses synthetic inputs, real Snakemake scheduling and
controlled installer/tool doubles. It does not establish live package-download
success or biological accuracy on another computer. Recipe constraints are not
exact solved platform locks. Preserve package inventories and resource snapshots,
then test restoration and independent biological performance separately.
