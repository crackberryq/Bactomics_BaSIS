# Snakemake orchestration

BaSIS 0.2.0rc16 uses Snakemake 9.6.2 by default for command-line analysis,
GUI analysis jobs and explicit tool-environment installation. The Python package
installs Snakemake; Conda, Mamba or Micromamba must be available separately.

## Setup, then analysis

1. Preview the selected tool-environment recipes. A preview performs no downloads.
2. Explicitly create missing environments. Snakemake coordinates the independent
   environment tasks; the selected package manager installs each recipe.
3. Verify installed package metadata and executable probes, and write a setup
   summary. An existing directory alone is never a successful installation.
4. Configure database/model paths and run study preflight.
5. Run the analysis graph: independent sample/module jobs first, then each sample's
   DeepSafe job after its upstream result records, including Extend when enabled.
6. The BaSIS launcher consolidates results and writes the final report after
   scheduling finishes, including recorded module failures and incomplete scope.

Setup and analysis are separate workflow invocations. DeepSafe's internal
candidate/domain/reference/motif/tree steps remain inside its worker; they are not
individual Snakemake jobs. Scientific selection and interpretation rules are unchanged.

```bash
# Read-only preview
basis setup-environments --workspace ~/bactomics/basis-workspace \
  --env core-amr --env diamond --env deepsafe

# Explicit installation and verification; one environment task at a time by default
basis setup-environments --workspace ~/bactomics/basis-workspace \
  --env core-amr --env diamond --env deepsafe --create

# Configure exact database/model paths in the study before these commands
basis doctor -c study/config.yaml -d -e -v
basis run -c study/config.yaml -d -e -v --output summary
```

Tool setup is not complete database provisioning. The setup summary separately
labels databases and models as not assessed. No analysis starts from environment
installation alone, and the GUI does not label that outcome as analysis-ready.
Automatic acquisition/configuration of every database/model remains future work.

## Existing environments and retries

Setup preserves existing environment directories and validates them. Incompatible
or incomplete pre-existing environments require attention instead of being silently
overwritten. Explicit `--retry-failed` repairs only eligible failed installations
tracked by this setup workflow with the same recipe. It does not authorize replacing
an unrelated environment. Every installation attempt has its own logs and records.

Recipe constraints describe allowed packages. They are not exact solved platform
locks. Retain installed package inventories, exact database/model releases and file
checksums when freezing a validation environment; reproducibility across computers
still requires a restoration test.

## Scheduling and evidence

The configured CPU, memory and GPU budgets are passed to Snakemake. Independent
sample/module jobs can run concurrently within those budgets. A workspace still
allows one GUI job at a time; that job can contain multiple scheduled tasks.

BaSIS workers check input, tool, resource and source identities before cache reuse.
Snakemake re-enters the workers to perform these checks. This is distinct from
`--force`, which explicitly requests recomputing the analyses.

Native analysis failures are recorded as evidence; dependent DeepSafe analysis can
then report upstream incompleteness. Independent work is allowed to continue.
Scheduler infrastructure errors are reported conservatively and cannot promote
old result files into successful current-run evidence.

`-v` streams scheduler/worker output to stderr and the GUI job log while retaining
`snakemake.log`. JSON stdout remains machine-readable. Reports retain separate
execution, assessment coverage and biological interpretation.

## Compatibility

`basis run --engine local` keeps the serial execution option. The programmatic
`run_pipeline` default and the small synthetic `basis demo` default remain local
for compatibility; use `basis demo --engine snakemake` to exercise the scheduler.
The GUI's synthetic demonstration uses Snakemake. The historical `workflow` extra
remains accepted but is redundant because Snakemake is now a base dependency.

Software tests use synthetic inputs and controlled tool-installation doubles.
They establish orchestration behavior, not biological accuracy or success of
every live upstream package download on a new computer.
