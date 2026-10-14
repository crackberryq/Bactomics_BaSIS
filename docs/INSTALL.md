# Install and test BaSIS 0.3.0rc1

Use Linux x86-64 or Ubuntu inside WSL2, Python 3.11+ and Git. Install Conda
separately for native tool deployment. Keep the preceding `0.2.0rc16` installation
and its studies intact when adopting this candidate. The GitHub source release
is pip-installable; no public PyPI release is claimed.

## 1. Install the application

```bash
python3 -m venv basis-0.3.0rc1
source basis-0.3.0rc1/bin/activate
python -m pip install 'bactromics-basis[ui,excel] @ git+https://github.com/crackberryq/Bactomics_BaSIS.git@v0.3.0rc1'
basis verify-install
basis demo --outdir demo-0.3.0rc1 -d -e --engine snakemake
basis gui --workspace ~/bactomics/basis-workspace
```

The demo uses synthetic fixtures. It does not run native biological analyses or
verify your GPU. Record the full installed source commit and Python dependencies;
see [GitHub quickstart](GITHUB_QUICKSTART.md). Deployment ZIPs with a bundle manifest
can use `install_local.py`; a plain source checkout does not contain that bundle.

Snakemake 9.6.2 is a base dependency and the default analysis scheduler. The CLI
and GUI launch the same workflow. Input/resource checks, native module execution,
nine DeepSafe stages per sample, reports and provenance are DAG stages. The serial
`--engine local` compatibility option does not deploy missing rule environments.

## 2. Native environments

Native rules deploy supported Conda recipes automatically when an enabled module
has no explicit command prefix. Conda must already be on PATH. Existing explicit
prefixes remain external under the default `auto` policy. See
[environment routing and migration](ORCHESTRATION.md#migrating-from-02).

| Recipe | Tools | Separate data preparation |
| --- | --- | --- |
| `core-amr` | AMRFinderPlus | Compatible AMRFinder database |
| `rgi` | CARD/RGI | Exact CARD `card.json` |
| `diamond` | DIAMOND | VFDB/BacMet protein sources and indexes |
| `context` | geNomad, IntegronFinder | geNomad database; packaged IntegronFinder models |
| `deepsafe` | MAFFT, IQ-TREE 2, EPA-ng, BLAST+, HMMER, SeqKit, pftools, Perl | References, domain and PROSITE resources as configured |
| `antismash` | antiSMASH | Databases compatible with its release |
| `mobsuite` | MOB-suite | Initialized plasmid and taxonomy resources |
| `isescan` | ISEScan | Packaged models |
| `icescreen` | ICEscreen | Packaged models and matching GenBank |
| `minced` | MinCED and OpenJDK | No reference database required |
| `pathogenfinder2` | Pinned PathogenFinder2 source/dependencies | ProtT5 and classifier resources; runtime/GPU checks |

To prepare environments before a study:

```bash
basis setup-environments --workspace ~/bactomics/basis-workspace \
  --prefix-root ~/bactomics/basis-environments --env core-amr --env deepsafe
basis setup-environments --workspace ~/bactomics/basis-workspace \
  --prefix-root ~/bactomics/basis-environments --env core-amr --env deepsafe --create
```

The first command previews; the second installs and verifies. New managed
prefixes have Snakemake cache names. Existing named `basis-RECIPE` folders are
verified and preserved. Setup records the actual prefix and whether it was
managed or external. Set `execution.conda_prefix` to the same parent to share
managed environments with subsequent analysis. Without this setting, analysis
uses `.basis-envs` beside its configured result directory.

`basis setup-environments --engine local` retains the older named-prefix
installer and supports Conda/Mamba/Micromamba. Its explicit `--retry-failed` repairs
only eligible tracked installations. No database download or analysis is part of
environment setup.

Recipes are package constraints, not solved platform locks. After native
acceptance, retain exact exports from the actual recorded prefix:

```bash
conda list --prefix /absolute/path/actual-prefix --explicit > tool-linux64.lock.txt
conda env export --prefix /absolute/path/actual-prefix > tool-resolved.yml
```

## 3. Reuse an existing execution environment

An existing module can retain its argument-list `command_prefix`:

```yaml
modules:
  amrfinder:
    enabled: true
    executable: amrfinder
    command_prefix: [conda, run, --no-capture-output, -p, /absolute/path/basis-core-amr]
    database: /absolute/path/databases/amrfinder/RELEASE
    database_version: REPLACE_WITH_ACTUAL_RELEASE
    threads: 4
```

This does not recreate or repair the external prefix. Missing or invalid external
prefixes need attention. To adopt managed deployment, deliberately remove the
prefix, remove absolute executable paths into other environments, and optionally
set `environment_backend: managed` in that module. Configuring both managed and
an explicit prefix is rejected. The same approach applies to `deep`.

For a tool intentionally available on PATH, set `environment_backend: external`
and use an empty command prefix. Command prefixes are lists of executable
arguments, not shell strings; do not embed `conda activate`, redirects or pipes.
For an external DeepSafe environment, check whether its tree executable is named
`iqtree` or `iqtree2` and set `deep.executables.iqtree` accordingly.

MLST and CRISPRCasFinder have normalized-import support, not native adapters.
MinCED has a native adapter but does not assess Cas systems. Use module scope and
exclusions deliberately rather than treating unsupported analysis as a negative.

## 4. Prepare resources explicitly

Database/model preparation is separate from pip installation and ordinary
analysis. The same main Snakefile has explicit resource targets for AMRFinderPlus,
geNomad, antiSMASH, MOB-suite, pinned experimental BacMet v2, and SHA256-pinned raw
files. Read [resource recipe schemas and examples](RESOURCE_SETUP.md).

```bash
basis prepare-resources -c resource-recipes.yaml --resource chosen_database
basis prepare-resources -c resource-recipes.yaml --resource chosen_database --execute
```

The default is a read-only plan. Execution writes an immutable snapshot with
provenance/checksums. Native downloaders require explicit acknowledgement when
they choose upstream content, plus declared version/content expectations. Existing
changed or partial snapshots are preserved; choose a fresh destination. No
ordinary analysis silently refreshes these resources. Raw pinned files are not
automatically extracted, indexed or configured for their consuming module.

| Module | Resource requirement |
| --- | --- |
| AMRFinderPlus | Exact compatible database directory; do not use a moving `latest` link for a frozen run |
| RGI | Exact nonempty CARD `card.json`; the adapter loads it locally in its output directory |
| VFDB/BacMet | Chosen protein dataset and DIAMOND index; record source and build identity |
| geNomad | Extracted database directory and actual release |
| antiSMASH | Its compatible prepared database collection |
| MOB-suite | Initialized plasmid/reference and taxonomy collection |
| IntegronFinder/ISEScan/ICEscreen | Packaged models and their required dependencies |
| PathogenFinder2 | Configured ProtT5 and classifier weights; see [Extend](EXTEND.md) |
| DeepSafe | Reference FASTA/metadata, domain database and applicable PROSITE/motif/placement resources |

Not all resources have an automatic preparation backend. Follow their dedicated
guides where extraction, indexing or model preparation remains necessary.
Configure actual snapshot paths and releases in the study, then run `doctor`.
A successful environment/resource preparation is not a successful biological run.

Reference acquisition remains available separately:

```bash
basis prepare-references --requests reference_requests.tsv \
  --fasta references/proteins.faa --metadata references/catalogue.tsv --maximum-accessions 100
```

This retrieves explicitly requested public protein accessions without submitting
isolate sequences. See [DeepSafe](DEEPSAFE.md) for request columns and reviewed
labels. Creating a reference catalogue does not validate a family classifier.

## 5. Configure and run the isolates

```bash
basis init --directory study
# Edit study/config.yaml and study/samples.tsv with actual inputs and resources.
basis doctor -c study/config.yaml -d -e -v
basis run -c study/config.yaml -d -e --cores 4 --dry-run
basis run -c study/config.yaml -d -e --cores 4 -v --output summary
```

Matching assembly FASTA, protein FASTA and GFF are required. Matching GenBank is
required for ICEscreen; its sample phylum must also be explicit. Paths in the
configuration resolve relative to that file; sample-file paths resolve relative
to the execution sheet. Excel/CSV/TSV batches and external annotations are supported;
see [batch inputs](BATCH_INPUTS.md) and [external inputs](EXTERNAL_INPUTS.md).
Kraken/HybAs taxonomy is useful when available, but unknown taxonomy stays unknown.

Use a new output directory for this candidate. Select modules and scope
deliberately. Preflight and dry-run checks do not establish native tool correctness.
An incomplete requested scope exits with status 2 and retains its report. A failed
or missing analysis is never evidence of absence.

For GPU-enabled PF2, both `execution.gpus` and `modules.pathogenfinder2.gpus` must
allow a GPU. Model/runtime compatibility must be verified separately; scheduler
budgets are not device-driver installation or operating-system memory limits.
Whole-result cache reuse requires verified inputs, resource/model identities,
source identity and required artifacts. A new run does not automatically reuse
partially completed DeepSafe stages from a different scheduler attempt; see
[recovery boundaries](ORCHESTRATION.md#scheduling-recovery-and-evidence).

## Import previously completed evidence

Normalized imports are supported for completed analyses when rerunning the upstream tool is unnecessary. They must refer to exactly the current sample inputs. For example, enable MinCED import inside `modules`:

```yaml
modules:
  minced:
    enabled: true
    mode: import
    result: imports/{sample_id}.minced.json
```

Obtain the expected per-sample hashes with:

```bash
basis validate -c study/config.yaml > study/input-validation.json
```

The JSON output contains a `checks` list. Copy the matching sample's complete `input_hashes` mapping into the import record, including `genbank` when present. An import requires `schema_version: 1`, the exact `sample_id` and module name, and `execution_status: completed`. It also needs a findings list and a valid interpretation. Minimal structural example for an independently verified completed no-hit result:

```json
{
  "schema_version": 1,
  "sample_id": "AFB02",
  "module": "minced",
  "execution_status": "completed",
  "input_hashes": {
    "assembly": "COPY_ACTUAL_SHA256_FROM_VALIDATE",
    "proteins": "COPY_ACTUAL_SHA256_FROM_VALIDATE",
    "gff": "COPY_ACTUAL_SHA256_FROM_VALIDATE"
  },
  "interpretation": "not_detected",
  "findings": [],
  "metrics": {},
  "artifacts": ["/absolute/path/to/original/minced-output.txt"],
  "notes": ["Retain the actual upstream command, executable version and completion log with this evidence."]
}
```

This is a schema example, not a result for AFB02. Do not create a no-hit import merely because an output is absent. Positive findings must use BaSIS categories and fields, 1-based inclusive coordinates, and numbers or JSON `null` for unknown numeric values. Keep native outputs, execution logs and database/model provenance. The report labels the evidence as imported: BaSIS did not run the upstream tool in that run. Raw tool tables are not accepted directly by this normalized JSON interface. Refer to [REPORTING.md](REPORTING.md) for the evidence schema.

## 6. First-run acceptance

1. The configured samples are present and their input identifiers reconcile.
2. Each requested tool has a completed status or a visible failure with its log. No failed/missing output is labeled not detected.
3. The software records the configured database releases and preserves native output files.
4. Manually inspect at least one known positive and one completed no-hit result for every enabled parser against its native output.
5. Confirm any deep-analysis query appears in its alignment and, when produced, its tree. Confirm candidate IDs link back to the source finding.
6. Open the HTML report and compare its counts and locations with the TSV/JSON and native artifacts. Enter phenotype evidence only when it has actually been measured.

See [VALIDATION.md](VALIDATION.md) for the distinction between software checks and biological validation.

## Official installation references

Consulted 7 October 2026. Tool releases and installation instructions can change; record what you actually installed.

- [Snakemake 9.6.2 package metadata](https://pypi.org/project/snakemake/9.6.2/) and [installation guide](https://snakemake.readthedocs.io/en/stable/getting_started/installation.html)
- [AMRFinderPlus](https://github.com/ncbi/amr) and [database updater](https://github.com/ncbi/amr/wiki/amrfinder_update)
- [CARD/RGI installation](https://github.com/arpcard/rgi) and [database loading](https://github.com/arpcard/rgi/blob/master/docs/rgi_load.rst)
- [DIAMOND installation](https://github.com/bbuchfink/diamond/wiki/2.-Installation)
- [geNomad installation](https://portal.nersc.gov/genomad/installation.html)
- [antiSMASH installation](https://docs.antismash.secondarymetabolites.org/install/)
- [MOB-suite](https://github.com/phac-nml/mob-suite)
- [IntegronFinder](https://github.com/gem-pasteur/Integron_Finder)
- [ISEScan](https://github.com/xiezhq/ISEScan)
- [ICEscreen installation](https://icescreen.migale.inrae.fr/run_icescreen/install_run_locally/install_from_conda/)
