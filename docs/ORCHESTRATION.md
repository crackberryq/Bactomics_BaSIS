# Snakemake orchestration in BaSIS 0.3.0rc6

BaSIS uses one packaged `basis/resources/Snakefile` entry point. The CLI and local interface launch the same analysis workflow. Snakemake owns dependencies, environment deployment and CPU/memory/GPU scheduling. Python workers implement scientific analyses, parsing, evidence checks and report rendering within its rules.

The Python package installs Snakemake 9.6.2. Conda and optional NVIDIA drivers are separate prerequisites. Installing BaSIS with pip installs the controller; the requested first native run prepares its missing tools and resources.

## A normal fresh run

```bash
basis init --directory study --hybas-dir /absolute/path/to/isolate/annotation
basis run -c study/config.yaml -d -e -v --cores 4 --output summary
```

`basis init` defaults to `--preset full`. It enables all 14 native adapters and configures automatic resource preparation. The full preset also enables PROSITE profiles, bounded automatic phylogeny and family placement for DeepSafe when `-d` is requested. `-e` requests Extend. Use `--preset core` for the smaller core module set. Initialization and dry runs do not download resources or execute biological analyses.

New study defaults include:

```yaml
resources:
  policy: auto
  directory: /home/USER/.cache/bactromics-basis
  preset: full
execution:
  environment_backend: auto
  conda_prefix: /home/USER/.cache/bactromics-basis/environments
  device: auto
  cores: 4
```

These are illustrative paths, not values to paste without changing `USER`. Initialization resolves the actual user cache directory, respects `XDG_CACHE_HOME`, and uses up to four available CPU cores by default. Set `--resource-dir /absolute/path/basis-resources` at initialization to select another shared disk. CPU, memory and GPU settings remain scheduler budgets, not operating-system memory limits.

## Analysis sequence

The analysis DAG contains:

1. Input validation, required Conda environment deployment, and resource preparation/checks.
2. A study-wide readiness barrier: all requested tool and resource checks must succeed before any sample analysis starts. Failed setup is retried through the same DAG.
3. Named module rules for screening, context, typing, CRISPR/Cas and Extend. Independent jobs run within the declared budgets after readiness succeeds.
4. DeepSafe preparation and selection wait for upstream result records, including Extend when requested. Snakemake exposes candidate tasks for domain, phylogeny, PROSITE, registry motif and placement work. Reference retrieval/review and deterministic budget planning remain shared prerequisites; candidate results are aggregated for finalization.
5. Evidence consolidation, linked reports and final provenance capture.

Scientific applicability checks determine whether a selected candidate receives a tree, placement or motif observation. Scheduling a candidate does not assert that all analyses apply. Prepared reference catalogues and seed panels do not constitute universally validated family classifiers. Deferred, unsupported and unresolved cases remain visible.

HybAs input resolution imports the recorded target and its rank. An automatically assigned phylum requires a matching selected target, positive support, and consistent phylum ancestry in every available enabled Kraken report. The report paths, hashes and ancestry are retained. Missing or conflicting ancestry is left unresolved; user declarations are preserved. A leading species hit does not become a species call. This allows ICEscreen scope to be resolved automatically where the supplied evidence supports Bacillota; matching GenBank is also required.

The launcher parses configuration, creates the execution plan and starts Snakemake. It can write an incomplete failure summary when the scheduler cannot finish; the normal scientific sequence remains in DAG rules. Standalone `doctor`, `report` and `control-panel` commands remain available for inspection and saved-evidence operations.

## First-use snapshots and reproducibility

With `resources.policy: auto`, the planner identifies missing required resource settings. Resource rules acquire or prepare the needed data and bind the resulting paths to consuming jobs. Already configured external resource paths are retained. A missing or invalid explicit path is not silently replaced with a different database.

Supported preparation covers the core databases, CARD, VFDB, BacMet, bundled context-tool models, MLST/CRISPRCasFinder resources, PROSITE/ProRule, ProtT5/classifier resources, and DeepSafe reference/domain resources. Only requested consumers require their preparation dependencies. Resource and model acquisition does not submit isolate sequences to remote prediction services.

The shared cache stores provider snapshots under `snapshots/<provider>/<deterministic-key>`, alongside preparation locks and staging directories. Each published snapshot has a manifest with provenance and content checksums. Current-run preparation receipts and logs live beneath `results/provenance/scheduler/<run-id>/bootstrap/`. A first-use provider may resolve an upstream current release; its acquired bytes and identity become the retained snapshot. Subsequent runs verify that snapshot and reuse it without a routine refresh. A new computer downloading on a different date is not guaranteed the same upstream data. Preserve and restore the recorded snapshots for an exact resource match.

Failed partial preparations remain in staging with a failed receipt. Missing or modified published snapshot contents fail verification and are not implicitly refreshed. Preparation failures are visible with logs; they cannot become negative biological results. Full resource preparation is distinct from native analysis acceptance and from independent biological validation.

The full DeepSafe resource set combines bundled source-reviewed seed panels and the existing motif/placement registries with the screening reference catalogue and PROSITE resources. Imported screening references remain unreviewed comparators; source-reviewed seed panels retain their recorded coverage gaps. Automatic acquisition does not turn either group into a validated functional classifier.

`basis doctor` is read-only. It identifies planned automatic preparation as planned, rather than claiming that missing tools/databases are installed. Its success does not replace the native run's preparation and verification checks.

## Native environments and device selection

Rules use native Snakemake `conda:` recipes. Under `execution.environment_backend: auto`, a supported native module without an explicit `command_prefix` uses a managed environment. An explicit prefix remains external. Imports, excluded modules and synthetic demonstrations do not need native tool deployment.

Managed environment directories are content-addressed Snakemake caches. New automatic studies place them beneath their shared resource directory. For existing manual configurations without `execution.conda_prefix`, the default parent remains `.basis-envs` beside the result directory. Startup checks record the environment's installed constraints and executable identity. A directory alone is not proof of installation, and recipe constraints are not solved package locks.

New studies use `execution.device: auto`. The launcher checks available NVIDIA hardware for scheduling, then PathogenFinder2 checks CUDA availability in its actual runtime. Auto mode can fall back to CPU. `device: cpu` requests CPU; `device: cuda` requires usable CUDA and fails if it is unavailable. `device: configured` retains the explicit GPU-budget behavior of older configurations. BaSIS does not install or repair GPU drivers.

## Optional preparation before analysis

Environment preparation remains available through the same Snakefile's setup mode:

```bash
basis setup-environments --workspace ~/bactomics/basis-workspace \
  --prefix-root ~/bactomics/basis-resources/environments --env core-amr --env deepsafe
basis setup-environments --workspace ~/bactomics/basis-workspace \
  --prefix-root ~/bactomics/basis-resources/environments --env core-amr --env deepsafe --create
```

The first command previews; the second installs and verifies. Existing named `basis-RECIPE` directories are verified as external prefixes without replacing them. New environments use native Snakemake deployment. Set the study's `execution.conda_prefix` to the same parent if sharing these environments.

For custom resource snapshots, the explicit administrative mode remains available:

```bash
basis prepare-resources -c resource-recipes.yaml --resource chosen_database
basis prepare-resources -c resource-recipes.yaml --resource chosen_database --execute
```

This is an advanced separate interface with its own recipe schema, supported backends and version/content requirements; see [resource administration](RESOURCE_SETUP.md). It is not a prerequisite command sequence for a new automatic full study. A generic pinned-file recipe acquires source files; it does not automatically build an analysis-ready database.

## Migrating from 0.2

A clean `basis init` study is the recommended starting point for a fresh native evaluation. Earlier installations and results can remain untouched. Existing configurations without an automatic resource policy remain manual; their scientific settings, explicit prefixes and resource paths are not rewritten when a new package is installed.

To keep an existing environment, retain its argument-list `command_prefix` under `auto`. To adopt managed deployment deliberately, remove that prefix and any absolute executable paths into another environment, then optionally set the module's `environment_backend: managed`. A managed backend with an explicit prefix is rejected. `external` intentionally uses an existing prefix or PATH. The same routing rules apply to DeepSafe.

Do not copy sample-specific imports, exclusions, embedding caches, reviewed query trees or circularity declarations into an unrelated study. A fresh import without a base configuration uses the full automatic defaults; importing with `--base-config` intentionally retains that configuration and needs review.

## Scheduling, recovery and evidence

The GUI allows one job per workspace; that job can contain many Snakemake tasks. The normal analysis path launches one Snakemake process. Native Snakemake output is forwarded to stderr and saved to `snakemake.log`. The `-v` flag also enables Snakemake's `--verbose`, `--printshellcmds` and `--show-failed-logs`, with live native-tool diagnostics. JSON stdout remains machine-readable. Output explicitly captured for FASTA, JSON or other data files stays separate from terminal diagnostics. The duplicate parent scheduler/launcher heartbeat timers are removed.

Completion receipts use a stable `provenance/scheduler/state-<plan-hash>` directory for an unchanged plan. Every launch also retains its own logs, context and reporting history. Scientific result files remain evidence and are verified before reuse. Inputs, resources, source/tool identities and required artifacts must match. `--force` explicitly requests recomputation; it is not a database refresh operation.

Repeating the same study configuration resumes valid completed Snakemake tasks, including candidate tasks. Missing or invalid completion evidence is invalidated for Snakemake to rerun with its dependants. A changed source release or execution plan creates a new state namespace and can require recomputation; this is deliberately conservative. Run history is retained separately from reusable work.

Stable native reuse requires a fully fingerprinted Conda environment and supported resource dependencies. Tools outside that boundary, unrecognized custom resource structures, or relative paths to reviewed tree artifacts receive fresh checks and disabled native cache reuse on each launch. This avoids reusing results when BaSIS cannot verify their full dependency identity.

Preparation and native worker failures return unsuccessful exit codes to Snakemake. Independent jobs may finish under `--keep-going`; dependent jobs are blocked. Successful normal reporting remains a workflow target. If the DAG cannot reach that target, the launcher writes an incomplete failure summary from attested current-run diagnostics without changing failure into success. Reports distinguish failed, blocked and not-started execution from completed or reused evidence. A deferred candidate or unresolved scientific interpretation is not, by itself, a native command failure.

## Compatibility and validation limits

Public analysis, demo and environment-setup commands use Snakemake exclusively. The internal serial implementation is retained for software tests and older programmatic integrations; it is not a GUI or CLI engine choice. The small synthetic demo does not establish fresh-machine native readiness. The historical `workflow` extra remains accepted but is redundant.

Software checks exercise synthetic inputs, native schemas, scheduling and controlled installer/tool doubles. They do not establish that every live package download succeeds, every tool runs correctly on a collaborator's computer, or a biological classifier is accurate. Full clean-machine native acceptance and independent biological validation remain pending. Preserve actual environment exports and resource snapshots, then test restoration and biological performance separately.

## Inspecting and stopping an older running invocation

On Linux/WSL, the read-only inspection helper identifies a same-user BaSIS launcher by its exact configuration path and displays its process descendants:

```bash
python -I -m basis.run_control -c study/config.yaml
```

The stop helper uses Linux pidfds even when a Conda/Python build omits their Python bindings. It first probes kernel support without stopping any process. Unsupported kernels or denied syscalls stop recovery before any target signal is sent.

Use the explicit `--stop` option to stop that invocation and its verified descendants before upgrading or retrying. It does not use broad process-name kills. If the launcher has already disappeared, the helper cannot establish ownership of an orphan by ancestry and will not guess. The current implementation must not be used to stop unrelated HybAs jobs.

Install the new release in a separate Python environment, stop the old invocation, then rerun the same study configuration. Keep the resource cache and prior evidence in place. Compatible completed resource snapshots are verified and reused, and interrupted ProtT5 downloads retain their persistent download cache. Native analysis caches still require matching source, configuration, input, tool and artifact identities; a source release change can require recomputation. Do not run the rc2-only resource recovery helper against rc3 evidence.
