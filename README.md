# BaSIS AWARE · standalone Snakemake workflow

**Version 0.4.0a4 — alpha prerelease.** BaSIS AWARE collects bacterial genomic screening evidence and focused DeepSafe follow-up in separate offline reports. This edition runs directly from `BaSIS.smk`. Snakemake owns workflow scheduling, tool environments, dependencies and restart decisions. There is no BaSIS package installation or Streamlit launcher in this edition.

This is a separate development line derived from the 0.3.0rc8 scientific and reporting code. Existing RC releases remain available. Infrastructure checks do **not** establish biological accuracy; independent biological validation of this rebuilt workflow has not been completed.

The [acceptance checklist](docs/ACCEPTANCE.md) distinguishes completed software
checks from the native integration evidence still required before beta promotion.
The release branch is `basis-aware`, and the product name is **BaSIS AWARE**,
consistent with **HybAs AWARE**. See the [0.4.0a4 release notes](docs/RELEASE_0.4.0a4.md).
The installation command below selects `basis-aware`; use `--branch v0.4.0a4`
instead to check out the fixed alpha release.

## Install the workflow controller

Use Linux or WSL2 with Conda available. The controller environment runs Snakemake; the Snakefile deploys the selected biological tool environments itself.

```bash
mkdir -p ~/bactomics &&
cd ~/bactomics &&
git clone --branch basis-aware --single-branch \
  https://github.com/crackberryq/Bactomics_BaSIS.git BaSIS-snakemake &&
cd BaSIS-snakemake &&
conda env create --file environment.yml &&
conda activate basis-snake
```

No `pip install basis` step is required. Initial tool installation and database/model preparation can take substantial time and disk space. Downloads and execution logs remain available for inspection.

## Run isolate2

The default annotation layout follows HybAs:

| Input | Expected file |
|---|---|
| Assembly | `~/bactomics/isolate2/annotation/isolate2.fna` |
| Proteins | `~/bactomics/isolate2/annotation/isolate2.faa` |
| Annotation | `~/bactomics/isolate2/annotation/isolate2.gff` |
| GenBank, for modules that need it | `~/bactomics/isolate2/annotation/isolate2.gbk` |

Start from the checkout:

```bash
cd ~/bactomics/BaSIS-snakemake &&
conda activate basis-snake &&
snakemake --snakefile BaSIS.smk \
  --config "isolate=$HOME/bactomics/isolate2" \
  --use-conda --cores 4 --resources mem_mb=20000 gpu=0 \
  --rerun-incomplete
```

This command prepares the requested tools and resources before any sample analysis can start. DeepSafe is disabled by default: review the screening report before requesting follow-up. Completed outputs remain reusable on subsequent invocations. The default uses CPU execution, including PathogenFinder2; see GPU configuration below if a compatible NVIDIA GPU is available.

The main report is:

```text
~/bactomics/isolate2/safety/report.html
```

From Windows Explorer, replace `<distribution>` with your WSL distribution name
and `<user>` with your Linux username:

```text
\\wsl.localhost\<distribution>\home\<user>\bactomics\isolate2\safety\report.html
```

Screening results are written to `safety/report.html`. When DeepSafe is enabled,
its follow-up is written separately to `safety/deepsafe-report/report.html`.
Use the `screening_reports` or `followup_reports` workflow target to request the
corresponding bundle. Screening rendering does not wait for DeepSafe analysis,
although enabled resources still share the setup barrier. See
[separate reports and saved-evidence rendering](docs/REPORT_SEPARATION.md) for
commands, disabled/provisional states and navigation behavior.

The [screening findings reader](docs/READABLE_REPORTS.md) brings full source
descriptions and recorded drug/class labels into the report. Search every saved
finding, expand its original fields, or download the readable findings table.
This format is generated automatically by the normal `BaSIS.smk` run, including
runs launched through Bactologix using this checkout. **Results by tool** appears
before technical input details and distinguishes no findings, inconclusive
results and incomplete assessments. **Tools finished** counts execution only.
No separate report-format setting or manual Python refresh is needed for a new run.

For follow-up that must never schedule screening, use the separate
[`Followup.smk` entry point](docs/REVIEWED_FOLLOWUP.md) after reviewing a completed
screening report. It verifies saved inputs and stops on missing or stale
prerequisites. Its configuration starts disabled; editing a file does not start
a run. Existing explicit `deep.enabled: true` overlays still enable integrated
follow-up in `BaSIS.smk`.

Keep the `safety` folder intact: the reports link to its evidence files, native outputs and exported tree views.

## Prepare first, then run

The normal run already enforces setup. On a new computer, prepare the selected tools and resources without supplying any samples:

```bash
snakemake --snakefile BaSIS.smk setup \
  --config 'isolates=[]' \
  --use-conda --cores 4 --resources mem_mb=20000 gpu=0 \
  --rerun-incomplete
```

The explicit empty isolate list is accepted for setup only. It deploys the configured environments and prepares resources without requiring annotation files or executing sample analyses. A normal analysis run still requires samples. After setup succeeds, run the isolate2 command above. You can also use `setup` with a real sample configuration to prepare only that configured scope.

Inspect the planned jobs without executing them by adding `--dry-run`. Snakemake shows normal job messages and progress by default. Add `--printshellcmds` when debugging; tool output is written under each isolate's `safety/logs/`.

## Configuration and batches

Edit `config.yaml`, or supply an additional YAML file with `--configfile my-study.yaml`. The configured modules, DeepSafe and Extend determine the requested scope. Missing taxonomy is retained as unresolved; HybAs metadata and Kraken outputs are optional for externally annotated inputs. Taxon-specific modules can be explicitly held when applicability is unconfirmed, and the report keeps those holds visible.

For several HybAs-style isolate folders:

```yaml
bactomics_root: ..
isolates: [isolate2, isolate4, isolate5]
threads: 4
memory_mb: 20000
extend: true
deep:
  enabled: false
```

For other layouts, set `samples: config/samples.tsv`. CSV, TSV and `.xlsx` sample sheets are supported; Excel uses the active worksheet. Start with [config/samples.example.tsv](config/samples.example.tsv). `sample_id` and/or `isolate_dir` identify each sample; `assembly`, `proteins`, `gff` and optional `genbank` can override annotation discovery. Unique sample identities and isolate directories are required.

Path rules are explicit:

- YAML resource and sample-sheet paths are relative to the folder containing `BaSIS.smk`.
- An isolate list is resolved relative to `bactomics_root`.
- Paths in a sample-sheet row are relative to that sample sheet's folder.
- Absolute paths and `~` are accepted. Each sample writes into its own `<isolate_dir>/safety/`.
- A command-line `--config isolate=/absolute/path` selects that single isolate, overriding the saved batch list.

Sample sheets can also provide declared `taxon` and `taxon_phylum` fields. Supply supported metadata rather than assigning taxonomy merely to make a held module run.

### DeepSafe family automation

DeepSafe applies the same Snakemake stages to every selected candidate. Family
profiles and placement panels are configuration data; adding an already reviewed
family definition does not require writing a new Snakefile or a gene-specific
runner. Existing selection budgets, sequence checks, reference eligibility and
interpretation gates still apply.

The DeepSafe report's **Family workflow coverage** section shows the matched profile
and placement panel, ambiguous or unmatched routing, each recorded analysis
status, and the reasons an analysis was held. Candidate links open the existing
evidence and tree sections. The JSON and TSV downloads contain **all** candidates,
including deferred candidates. Older evidence without a recorded routing audit
is labelled **not recorded**, rather than being reinterpreted using today's
configuration.

The bundled resources contain **12 broad family profiles and one Cfr/RlmN
development placement panel**. Those are different kinds of evidence coverage.
The workflow is reusable across configured families; this distribution does not
claim universal family coverage or independently validated functional prediction.
Trees and conditional placement weights do not establish a resistance phenotype.

Existing reviewed resources can be selected in a study configuration:

```yaml
deep:
  enabled: true
  profiles_dir: /absolute/path/to/reviewed-family-profiles
  placement_registry: /absolute/path/to/reviewed-placement-registry.json
```

The profile directory and registry must follow the existing validated schemas.
Multiple profiles/panels are supported. Conflicting matches remain ambiguous;
unmatched candidates remain explicit coverage gaps. This update supplies no new
reference labels, sequences, thresholds or biological validation claims.

At preparation, small family resources are copied byte-for-byte into
`safety/deepsafe/native/prepare/family-resources/`. Their source paths, checksums
and configuration identity are recorded and verified before resumed stages use
them. Snakemake tracks edits and additions/removals in the configured family
files. Such changes refresh DeepSafe; reporting-only edits refresh the report.
Completed upstream native analyses have no dependency on these new helpers.
Do not modify source resources or pull code updates during an active run.

Coverage exports are `safety/deepsafe-report/metadata/deep-workflow.json` and
`safety/deepsafe-report/metadata/deep-workflow.tsv`. The final DeepSafe evidence also contains the
same candidate workflow records and the configured family inventory. Reports
remain offline and do not launch analyses or download references.

### Optional GPU execution

CPU is the default. To request one GPU for PathogenFinder2, add these settings to the study YAML:

```yaml
execution:
  device: cuda
modules:
  pathogenfinder2:
    enabled: true
    gpus: 1
```

Run that configuration with `--resources mem_mb=20000 gpu=1`. Requesting GPU execution requires a working compatible CUDA runtime and driver; a Snakemake resource reservation alone does not provide GPU compatibility. The model weights and embeddings have separate recorded provenance.

## Files and restart behavior

| Location | Contents |
|---|---|
| `<isolate>/safety/report.html` | Screening report and native evidence navigation |
| `<isolate>/safety/evidence.json` | Consolidated machine-readable screening evidence |
| `<isolate>/safety/deepsafe-report/` | Separate DeepSafe report, exports and report provenance when enabled |
| `<isolate>/safety/native/` | Native tool outputs, including reusable embeddings |
| `<isolate>/safety/completion/` and `results-cache/` | Core completion receipts, verified payloads and retained failed attempts |
| `<isolate>/safety/deepsafe/` | Selected-candidate stages, native outputs and state |
| `<isolate>/safety/evidence/` | Normalized module records |
| `<isolate>/safety/tables/` | Findings, review priorities, scope and run summaries |
| `<isolate>/safety/metadata/` | Input checks, configuration, provenance and downloadable citations |
| `<isolate>/safety/logs/` and `benchmarks/` | Per-rule logs and timing/resource measurements |
| `<checkout>/resources/` | Shared prepared databases/models and environment inventories |
| `<checkout>/.snakemake/` | Snakemake state, locks and managed Conda environments |

Use one running Snakemake invocation per checkout. All samples in a batch share that invocation and its setup barrier. If separate checkouts are used concurrently, give them separate isolate output folders; the workflow does not coordinate competing writers across different checkouts.

After a failure or shutdown, rerun the **same command** with `--rerun-incomplete`. Snakemake decides which declared outputs are missing, incomplete or outdated; successful unchanged jobs are retained. If it reports a stale lock, first confirm the old workflow has stopped, then unlock using the same checkout and configuration:

```bash
snakemake --snakefile BaSIS.smk \
  --config "isolate=$HOME/bactomics/isolate2" --unlock
```

Then repeat the normal run command. Unlocking removes a workflow lock; the subsequent run handles incomplete work. Snakemake can discard and rebuild an interrupted or failed Conda deployment. An arbitrary failure in a tool whose environment was fully deployed does not imply that the environment will be rebuilt. Do not routinely delete environments, result folders or Snakemake metadata, and do not add `--forceall` to a resume command.

### Database preservation, whole-assembly execution and restart checks

Resource jobs publish small completion records. Prepared databases live in
verified snapshots outside Snakemake's failed-output cleanup, and matching
requests reuse those snapshots. Failed preparation retains its files and
diagnostics. Environment records depend on installation checks rather than
sample policy code. Native command stdout and stderr are logged while running.
geNomad additionally retains validated download archives, checks transfer length
and gzip/tar integrity, and retries transient transfers up to three times.

CRISPRCasFinder now runs the complete assembly in **one native invocation and
one scheduler job per enabled isolate**, restoring the execution granularity of
the original Safety Extend Snakefile. For example, 4,986 contigs produce one
CRISPR assembly job, followed by evidence normalization. Every input record must
appear in the verified output. Pinned versions, native analysis settings and
reporting thresholds are retained; failed or missing results cannot become
negative findings.

The native tool still processes records internally. This removes repeated outer
launches and scheduler overhead; it does not promise a particular completion
time. The old `modules.crisprcasfinder.batch_size` setting is ignored and removed
from effective configuration. `execution_scope` is now `assembly`. AWARE
continues to control later DeepSafe admission separately.

Core native and CRISPR assembly results use verified snapshots, with failed
attempt diagnostics kept outside Snakemake cleanup. Module-specific
configuration prevents a change to a downstream AWARE policy from rerunning
unrelated upstream work. CRISPR retries reuse a matching completed assembly;
a failed assembly is retried as a whole, with its diagnostics preserved.
Configured command timeouts are enforced, with heartbeat and final-status logs;
DeepSafe stages now use their configured CPU and memory allocations.

Inspect configuration, input paths and existing resource receipts without
downloads or changes:

```bash
python scripts/doctor.py --configfile my-study.yaml local-resources.yaml --json
```

Use the same overlays as your run. Optional `--validate-inputs` and
`--verify-resources` perform the more expensive read-only checks. Missing setup
and invalid resources are reported separately.

Read [resource and execution recovery](docs/RESOURCES_AND_BATCHES.md) before updating
an existing run. Finish or stop the running workflow before changing its code.
Older automatically prepared resources without sufficient preparation identity
must be selected through explicit local paths; they are preserved. Existing
per-contig CRISPR outputs are preserved but cannot be reused as a completed
whole-assembly invocation. To start fresh, `scripts/archive_run_results.py`
previews the selected result-folder renames; `--apply` archives them after
checking that workflow processes have stopped. Keep the same ordered configuration
overlays. Database and environment caches remain outside those result folders.

### Linux result storage for WSL

When samples live under `/mnt/c`, retaining and verifying thousands of CRISPR
output files there can dominate runtime after the native command has finished.
The optional `storage.native_results_directory` setting places new CRISPR
payloads on the Linux filesystem while retaining sample-facing receipts and
links. Existing completed results keep their recorded stores.

See [Linux storage and recovery](docs/LOCAL_RESULTS_RECOVERY.md) for the overlay
and stopped-workflow recovery of completed native output still retained in
`/tmp`. Recovery uses the existing verification checks and does not rerun native
analysis or delete the original files.

### CRISPR array boundary review

A saved native array can report an end one base beyond its final repeat while
all repeat/spacer intervals remain contiguous and match the assembly. BaSIS
preserves both bounds and flags that array for review; it does not silently
correct coordinates. The report retains incomplete scope. Other endpoint,
continuity and sequence discrepancies remain errors.

See [saved CRISPR result revalidation](docs/CCF_BOUNDARY_RECOVERY.md) to reuse
the completed native run rejected by the older exact-endpoint check. The helper
also migrates matching completed samples to avoid recomputing them solely
because the parser changed.

### Explicitly skipped IntegronFinder records

When IntegronFinder exits successfully but explicitly skips a record after an
empty-protein-file warning, BaSIS retains the assessed records and marks the
skipped record **not assessed**. The evidence and report retain incomplete scope;
a skipped record is never counted as a negative result. Unexplained omissions,
conflicting summaries, unsupported skips and native errors remain failures.

See [saved IntegronFinder result recovery](docs/INTEGRON_RECOVERY.md) to revalidate
an existing attempt rejected by the older summary-coverage check without running
the native command again. Native search settings are unchanged.

### Shared checkpoint storage and offline compaction

DeepSafe now stores shared JSON context, retrieval audit data and planning metadata
once per content checksum under `safety/deepsafe/state-objects/`. Small v2 checkpoint
envelopes reference those immutable objects. The reader still accepts the older
inline v1 states. Logical input identities, analysis results and interpretation
are unchanged. Keep the entire `deepsafe` directory together when copying a run;
the shared object files are required checkpoint data, not disposable caches.
Missing objects stop dependency loading when the saved state is required;
altered shared data fails checksum verification before a worker consumes it.

Checkpoint hashes and writes normalize JSON keys consistently, including nested
numeric-key mappings. Colliding keys fail explicitly instead of overwriting
values. Existing valid v1/v2 state hashes remain compatible. See the
[JSON serialization audit](docs/JSON_SERIALIZATION_AUDIT.md) for regression
coverage, remaining related risks, and source-invalidation limits when resuming.

For an existing run with large repeated JSON states, stop all writers, update the
checkout, and use the offline compactor. Substitute the isolate directory:

```bash
python scripts/compact_deep_states.py --isolate /absolute/path/to/isolate
python scripts/compact_deep_states.py --isolate /absolute/path/to/isolate --apply
```

The first command reviews storage without replacing state files. `--apply` also
performs full preflight validation, then compacts each state atomically, verifies
lossless hydration, and preserves its original modification timestamp and file
mode. Replacements are atomic per file; interrupted batches can contain both
formats and can be resumed with the same command. Small audit records are saved
under `deepsafe/storage-reviews/`, including original/replacement byte checksums,
logical checksums and measured storage totals. No native tools run, and native
outputs, evidence records, HybAs metadata and Snakemake receipts are not edited.
Temporary space is still required for one state and newly shared objects. The
compactor stops on invalid state, changed provenance or concurrent modifications;
it does not label failed or incomplete work as complete or delete orphaned files.

Normal Snakemake source, input and resource invalidation still applies after a
code update. Compaction itself preserves checkpoint mtimes; it does not suppress
reruns legitimately required by changed code or inputs. Do not roll back to a
reader that only supports v1 after applying compaction.

The HybAs metadata reader also accepts HybAs 9's structured `executed_stages`
list and `skipped_stages` mapping. Other unexpected nested summary fields remain
errors, and isolate identity, duplicate-key and provenance checks remain active.

## BaSIS AWARE: candidate admission and deduplication

The standard follow-up scope is **AMRFinder review**: family-only HMM, partial and
disrupted-sequence calls can receive focused investigation, while other native
AMR calls remain visible for context review. Deferred or inconclusive follow-up
keeps an explicit review notice in the report and exports. See
[AMRFinder review and the AFB01 regression](docs/AMR_REVIEW.md). The previous
broader tool admission is available explicitly as `deep.aware.scope: all-supported`.

DeepSafe now defaults to **auto-medium** admission, with **auto-strict** and
**auto-loose** alternatives plus manual per-tool overrides under `deep.aware`.
Each source is evaluated independently before unique candidates consume budgets.
Verified duplicate identities are merged while all linked findings and deferral
reasons remain visible. Native screening and reference-retrieval settings stay
separate. The presets control computational scope, not organism safety ratings.

See [AWARE definitions and migration notes](docs/AWARE.md) and the
[commented configuration overlay](config/aware.example.yaml). A read-only
comparison on completed saved screening results is available with:

```bash
python scripts/preview_aware.py --isolate /absolute/path/to/isolate
```

The report includes effective settings, qualifying/selected counts and separate
admission/budget deferrals. `deep.aware.enabled: false` retains legacy admission.
The existing candidate stages and compact checkpoint storage remain in use.
Whole-assembly CRISPR execution is separate from DeepSafe candidate stages;
cross-sample sequence caching is not implemented.

## Interpretation and reproducibility

DeepSafe runs configured analyses on selected candidates. It preserves deferred candidates, missing comparison groups and taxonomic review holds. Motifs, domains, similarity and phylogenetic placement are evidence layers; none independently proves a resistance phenotype or organism safety. A completed selected scope can coexist with an incomplete overall scope.

The report exposes citations for **Jimi Goldstein**, ORCID [0000-0002-7829-0090](https://orcid.org/0000-0002-7829-0090), and the run's BaSIS version. Cite the underlying tools and databases as well. BaSIS source is licensed under [MIT](LICENSE); third-party software, models and database terms remain their own.

Record the checkout commit, configuration, sample inputs, prepared-resource provenance and resolved environment inventories when sharing a run. Database downloads and dependency solving can change over time; YAML recipes alone do not guarantee identical data or binaries on another computer. Review [architecture](docs/ARCHITECTURE.md) and [acceptance and validation](docs/ACCEPTANCE.md) before independent validation.
