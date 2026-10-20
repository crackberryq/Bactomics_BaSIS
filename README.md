# BaSIS · standalone Snakemake workflow

**Version 0.4.0a1 — development alpha.** BaSIS collects bacterial genomic screening evidence and focused DeepSafe follow-up in an offline report. This edition runs directly from `BaSIS.smk`. Snakemake owns workflow scheduling, tool environments, dependencies and restart decisions. There is no BaSIS package installation or Streamlit launcher in this edition.

This is a separate development line derived from the 0.3.0rc8 scientific and reporting code. Existing RC releases remain available. Infrastructure checks do **not** establish biological accuracy; independent biological validation of this rebuilt workflow has not been completed.

## Install the workflow controller

Use Linux or WSL2 with Conda available. The controller environment runs Snakemake; the Snakefile deploys the selected biological tool environments itself.

```bash
mkdir -p ~/bactomics &&
cd ~/bactomics &&
git clone --branch snakemake-standalone --single-branch \
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
  --config isolate=/home/jimi/bactomics/isolate2 \
  --use-conda --cores 4 --resources mem_mb=20000 gpu=0 \
  --rerun-incomplete
```

This command prepares the requested tools and resources before any sample analysis can start. Completed outputs remain reusable on subsequent invocations. The default uses CPU execution, including PathogenFinder2; see GPU configuration below if a compatible NVIDIA GPU is available.

The main report is:

```text
/home/jimi/bactomics/isolate2/safety/report.html
```

From Windows Explorer on the `Ubuntu-22.04` WSL distribution:

```text
\\wsl.localhost\Ubuntu-22.04\home\jimi\bactomics\isolate2\safety\report.html
```

Keep the `safety` folder intact: the main report links to its evidence files, native outputs and exported tree views.

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
  enabled: true
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

The main report's **Family workflow coverage** section shows the matched profile
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

Coverage exports are `safety/metadata/deep-workflow.json` and
`safety/metadata/deep-workflow.tsv`. The final DeepSafe evidence also contains the
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
| `<isolate>/safety/report.html` | Main offline report and evidence navigation |
| `<isolate>/safety/evidence.json` | Consolidated machine-readable evidence |
| `<isolate>/safety/native/` | Native tool outputs, including reusable embeddings |
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
  --config isolate=/home/jimi/bactomics/isolate2 --unlock
```

Then repeat the normal run command. Unlocking removes a workflow lock; the subsequent run handles incomplete work. Snakemake can discard and rebuild an interrupted or failed Conda deployment. An arbitrary failure in a tool whose environment was fully deployed does not imply that the environment will be rebuilt. Do not routinely delete environments, result folders or Snakemake metadata, and do not add `--forceall` to a resume command.

## Interpretation and reproducibility

DeepSafe runs configured analyses on selected candidates. It preserves deferred candidates, missing comparison groups and taxonomic review holds. Motifs, domains, similarity and phylogenetic placement are evidence layers; none independently proves a resistance phenotype or organism safety. A completed selected scope can coexist with an incomplete overall scope.

The report exposes citations for **Jimi Goldstein**, ORCID [0000-0002-7829-0090](https://orcid.org/0000-0002-7829-0090), and the run's BaSIS version. Cite the underlying tools and databases as well. BaSIS source is licensed under [MIT](LICENSE); third-party software, models and database terms remain their own.

Record the checkout commit, configuration, sample inputs, prepared-resource provenance and resolved environment inventories when sharing a run. Database downloads and dependency solving can change over time; YAML recipes alone do not guarantee identical data or binaries on another computer. Review [architecture](docs/ARCHITECTURE.md) and [acceptance and validation](docs/ACCEPTANCE.md) before independent validation.
