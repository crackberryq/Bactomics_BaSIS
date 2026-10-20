# Standalone workflow architecture

BaSIS 0.4.0a1 uses a directly runnable `BaSIS.smk`, organized in the style of HybAs. The workflow checkout contains code, recipes and shared prepared resources. Each isolate contains its own `safety/` result bundle. Existing annotation files are inputs and are not overwritten.

## Execution ownership

| Responsibility | Owner |
|---|---|
| Select ready jobs, allocate cores/memory/GPU reservations | Snakemake |
| Create and activate tool environments | Rule `conda:` declarations and Snakemake deployment |
| Essential installation checks | Matching `envs/*.post-deploy.sh` hooks |
| Require setup before sample analysis | `sample_setup_ready` dependency on all selected environments/resources |
| Execute native analysis | Native shell rules or a synchronous helper inside one rule |
| Parse scientific evidence and retain applicability holds | Reused scientific code plus standalone helpers |
| Retry, resume and detect outdated outputs | Snakemake's declared inputs, outputs and metadata |
| Render visualizations and citations | Saved-evidence reporting rule |

There is no inner BaSIS scheduler, environment activation, detached job launcher or cache controller on the active path. The small `DirectRunner` boundary runs commands synchronously in the environment already activated by Snakemake. It rejects attempts to invoke another `conda`, `mamba`, `micromamba`, `snakemake` or `basis` launcher.

This does not mean every operating-system subprocess is a separate DAG node. Some scientific stages need a small ordered sequence of commands, such as an alignment and bounded tree follow-up. Those stages remain inside one Snakemake job. A failure exits that job, and the rule owns the corresponding native directory and state output. The documentation does not claim finer-grained restartability than those rule boundaries provide.

## Rule groups

- **Setup and resources:** validate the annotation set; deploy and inventory selected tool environments; prepare resources in dependency order; expose the common setup barrier. Explicitly configured external resources are verified and remain read-only.
- **Native screening:** run supported screening modules into `safety/native/<module>/`; normalize their outputs into `safety/evidence/<module>.json`. Missing output and verified no-hit output are distinguished.
- **Extend:** preserve an explicit boundary between full-proteome embeddings, classifier inference and output collection. A classification failure can be retried without recreating valid upstream embeddings.
- **DeepSafe:** a checkpoint discovers the selected candidates after upstream evidence exists. Candidate layers, shared reference retrieval, tree planning, placement planning and final consolidation are separate dependent jobs. Deferred candidates remain in the saved evidence.
- **Report:** consume normalized records and saved candidate evidence. Export the main report, searchable evidence navigation, available tree/alignment views, tables, citations and a native-file index. Rendering does not launch native analyses.

The `setup` target also accepts an explicit empty isolate list for installation on a new computer, without annotation inputs. Normal analysis targets require at least one sample. The all-tools-ready barrier is global to the configured invocation, including a batch. This intentionally prevents one sample's analyses from beginning while another requested tool or resource is still unprepared. A module explicitly disabled or held by its applicability policy is recorded separately; it is not silently treated as a completed negative screen.

## Layout and provenance

`workflow/rules/` declares the DAG. `workflow/lib/safety/` contains small standalone stage helpers. `workflow/vendor/basis/` preserves the reused scientific and rendering implementation from rc8; its original version identity is retained rather than relabeling it as newly written code. `envs/` contains native recipes and deployment checks. The controller recipe is `environment.yml`.

Prepared data default to the checkout's `resources/`. Snakemake's working-directory lock protects that checkout's workflow; use one invocation per checkout. External database paths may be supplied explicitly, with their identity and checksums recorded. Separate checkouts must not write to the same isolate's `safety/` concurrently.

Module records carry sample identity, execution status, normalized findings, metrics and declared artifacts. Inputs and effective configuration are captured under `safety/metadata/`. The reporting layer verifies that selected module evidence is present and belongs to the expected sample. It does not turn a missing, failed or unstarted record into a completed result.

## Completion has two meanings

Execution answers whether a requested job finished and produced acceptable outputs. Interpretation answers what those outputs support. They remain separate throughout the report.

A completed DeepSafe module may have all selected candidates completed while retaining deferred candidates and an incomplete overall scope. Missing comparator groups, unresolved taxonomy, unsupported family rules and uncertain tree evidence remain explicit. Placement weights are not calibrated probabilities of resistance, and an organism-level safety verdict is not generated.

## Restart contract

Snakemake owns resumability at declared rule boundaries. Unchanged completed upstream outputs are reusable; failed or incomplete outputs are rerun. `--rerun-incomplete` permits incomplete jobs to be attempted again. `--unlock` removes a stale workflow lock after the prior invocation has stopped; it is not a general tool-repair command.

An interrupted Conda deployment without its completion marker, or a deployment whose post-deploy checks fail, is handled through Snakemake's environment lifecycle. A fully deployed environment that later encounters a runtime or input error is a different case. Its cause must be resolved; environment deletion is not the default response.

The active architecture is tested separately from biological validity. See [ACCEPTANCE.md](ACCEPTANCE.md).

### PF2 classification configuration and recovery

The classification rule uses `safety.pf2_classify`, which invokes the pinned
PF2 CLI's default pretrained prediction configuration. In this PF2 release,
passing `--config` selects a schema that requires training parameters even for
prediction. BaSIS checks each of the four bundled classifier weights against
the prepared copies before prediction, and records the actual bundled paths
and both sets of checksums. No training settings or model changes are needed.

This helper is a source dependency of classification only. The earlier
`safety.extend classify` implementation is superseded on the active workflow
path; the shared `extend.py` is deliberately unchanged in this repair so
completed embeddings and CRISPR jobs retain their existing input provenance.
Removing that legacy entrypoint is deferred to a planned source refactor.

After updating the checkout, rerun the original Snakemake command with
`--rerun-incomplete`. No output deletion, timestamp changes, or relaxed rerun
triggers are required. Unchanged embeddings are read and verified before the
classifier retry; prediction and downstream jobs then resume normally.
