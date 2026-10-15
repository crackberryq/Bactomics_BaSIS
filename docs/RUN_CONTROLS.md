# Run controls and batch summaries

One launcher handles base, DeepSafe and Extend modes for all samples in the configured sample sheet. The flags select existing analysis layers; this release changes how the run is controlled and explained.

| Command flags | Requested layers |
| --- | --- |
| none | Configured base modules |
| `-d` | Base and DeepSafe |
| `-e` | Base and configured Extend module |
| `-d -e` | Base, configured Extend and DeepSafe |

`-e` still requires PathogenFinder2 to be enabled and eligible in the configuration. Per-sample exclusions and requested prerequisite holds retain their existing meanings. `-d` assesses the selected DeepSafe queue; it does not select every deferred candidate.

## Readable terminal output

```bash
basis run -c study/config.yaml -d -e -v --output summary
```

`-v` shows stages, sample progress, module outcomes, cache reuse, command-log paths and 30-second heartbeats on stderr. The final summary lists each sample's execution counts and scope state, with selected/deferred DeepSafe counts shown separately. It includes the report and evidence paths. Detailed diagnostic messages remain in the evidence and logs.

Snakemake is the execution engine. The launcher forwards scheduler and worker output and records it in the launch log. Native analyses wait for the global setup barrier, then independent sample/module and DeepSafe candidate tasks run within the declared budgets. Repeating an unchanged study resumes verified completed tasks. See [orchestration](ORCHESTRATION.md).

## Machine-readable output

The default remains JSON. Progress and the compact completion summary go to stderr, keeping stdout parseable:

```bash
basis run -c study/config.yaml -d -e -v > run-result.json 2> run-progress.log
```

Use `--output summary` for human text on stdout. This option is available for `run` and `demo`. `demo` now also accepts `-v`.

Verbose mode applies only for the current CLI call, while respecting an existing `BASIS_VERBOSE` setting. A verbose call does not permanently change subsequent calls made from the same Python process.

## Equivalent flag forms

These commands request the same modes:

```bash
basis run -c study/config.yaml -d -e -v
basis -vde -c study/config.yaml
basis --config=study/config.yaml --deep --extend --verbose
```

Root `--help` and `--version` retain their usual behavior. Unknown options still fail rather than being silently ignored.

## Plan without running analyses

```bash
basis --dry-run -vde -c study/config.yaml --output summary
```

The output is explicitly labelled as a plan. Input checks and planning metadata can be written, but native/GPU analysis and completed-result generation are not requested. A valid plan does not establish that external tools or databases will execute successfully. Use `basis doctor` for the existing resource preflight checks.

`--force` retains its existing meaning during an actual run: selected work is recomputed rather than reused when possible. It does not override exclusions or prerequisite holds. During dry-run it describes the planned cache policy only.

## Saved summaries and outcomes

Actual runs add `run-summary.json` and `run-summary.tsv` to the output directory and run history. They summarize the same records as the main report and evidence. The JSON result links both files through `artifacts` and includes `summary`; `run.batch_summary` preserves it in the evidence.

Requested-module completion, explicit exclusions, disabled modules, held work and deferred DeepSafe candidates are distinct. The overall scope is copied from existing pipeline accounting. A completed selected queue can therefore coexist with an incomplete overall scope. Imported evidence and cache reuse are identified separately from newly produced native results.

Exit code 0 retains its existing meaning for completed requested scope or a successful plan. Exit code 2 remains an input/configuration error or incomplete requested scope. Expected error cases in the software checker are successful tests when they return 2; the checker's final status states whether all scenarios passed.

## Check the installed release

```bash
basis verify-install
basis demo --outdir synthetic-check -d -e -v --output summary
```

The first command checks packaged source/assets. The second creates a fresh demonstration with small artificial fixtures through Snakemake. It performs no native biological analysis, model download or GPU inference. Historical study-specific checkers remain associated with their original release versions.
