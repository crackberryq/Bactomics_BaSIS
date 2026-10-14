# Install and test BaSIS 0.2.0rc2

For an alongside installation on the existing pilot PC, start with [DEPLOYMENT.md](DEPLOYMENT.md). The deployment bundle supplies the wheel and a local installer; the instructions below cover separate application/tool setup and configuration.

For a GPU-enabled PF2 run, set both `execution.gpus: 1` and `modules.pathogenfinder2.gpus: 1`. With the module value `0`, BaSIS hides CUDA devices from that module and runs its CPU path. This release schedules at most one GPU job at a time; it does not allocate multiple GPU devices. Set a realistic `memory_mb` budget independently of GPU memory.

Whole-result PF2 caching requires explicit local ProtT5 and classifier-weight paths, valid artifact checksums and an observed tool version; landscape mapping disables this cache. Otherwise use the adapter's checked embedding-reuse inputs described in `EXTEND.md`. DeepSafe supports configured cache reuse when the recorded inputs, configuration, resources, source identity and required artifacts pass its checks. A completed historical record is not automatically a valid current cache. The 0.2.0rc2 source fingerprint includes bundled runtime assets, so this packaging update can invalidate older cache identities. Native raw outputs and prior integrated evidence snapshots remain retained.

These instructions target Linux x86-64 or Ubuntu inside WSL2 on Windows. A Linux terminal is required for the native tool stack; Windows PowerShell is not a substitute for the commands below. Keep working files in the WSL Linux filesystem when practical and use Linux paths in configuration. The recipes are starting specifications, not solved platform lockfiles. Earlier native pilot executions and captured environment identities are retained separately; the full native stack has not been freshly restored during this candidate's software tests. See the deployment bundle's `reproducibility/README.txt` for exact-export and pip-artifact limitations.

## 1. Install the BaSIS application first

Use an existing Miniforge/Conda installation, or install Miniforge from its [official distribution](https://github.com/conda-forge/miniforge). Do not install into the existing HybAs environment.

```bash
conda create -n basis -c conda-forge --strict-channel-priority python=3.11 pip
conda activate basis
```

From the directory containing `pyproject.toml`:

```bash
python -m pip install -e .
basis --help
basis demo --outdir demo-base
basis demo --outdir demo-deep-extend -d -e
```

The default local engine runs jobs serially and does not require Snakemake. For optional Snakemake scheduling of independent jobs:

```bash
python -m pip install -e '.[workflow]'
snakemake --version
```

In rc16, Snakemake is a base dependency and the default scheduler. Use `--engine local` explicitly for serial execution. The workflow baseline is Snakemake 9.6.2. Its upstream package requires Python 3.11 or newer. The Python environment contains BaSIS and its Python dependencies; native biological tools and databases are separate.

## 2. Choose native tools, then create their environments

Eight unchanged starting recipes are bundled with the Python package and source distribution:

| Recipe | Environment directory name | Tools | Separate data preparation |
| --- | --- | --- | --- |
| `envs/core-amr.yml` | `basis-core-amr` | AMRFinderPlus | Compatible AMRFinder database |
| `envs/diamond.yml` | `basis-diamond` | DIAMOND | VFDB and/or BacMet protein databases |
| `envs/context.yml` | `basis-context` | geNomad, IntegronFinder | geNomad database; IntegronFinder resources supplied by its package |
| `envs/deepsafe.yml` | `basis-deepsafe` | MAFFT, IQ-TREE 2, EPA-ng, BLAST+, HMMER, SeqKit | Reviewed reference FASTA and metadata; configured domain resources |
| `envs/isescan.yml` | `basis-isescan` | ISEScan and pinned interface dependencies | Packaged ISEScan resources |
| `envs/icescreen.yml` | `basis-icescreen` | ICEscreen and pinned interface dependencies | Packaged ICEscreen resources |
| `envs/minced.yml` | `basis-minced` | MinCED and OpenJDK | No reference database required |
| `envs/pathogenfinder2.yml` | `basis-pathogenfinder2` | Pinned PathogenFinder2 source and dependencies | ProtT5 and classifier resources; runtime compatibility verification |

The helper prints a plan by default:

```bash
basis setup-environments --project /absolute/path/workspace
```

Explicitly create only the selected environments:

```bash
basis setup-environments --project /absolute/path/workspace --create --env core-amr --env diamond
basis setup-environments --project /absolute/path/workspace --create --env context --env deepsafe
```

The default destination is `.basis-envs/` inside the selected workspace; without `--project`, the workspace is the current directory. Recipes are read from the installed package and are not modified. The source-tree wrapper `bash scripts/setup_envs.sh` accepts the same options. Use `--prefix-root /absolute/path/basis-envs` to change it, or `--manager mamba`/`--manager micromamba` for an installed alternative. No database download or genome analysis is part of this helper. In rc16, selected environments are installed and verified through Snakemake. Existing prefixes are checked and preserved; an incomplete prefix produces a failed verification. See [orchestration and retry behavior](ORCHESTRATION.md).

Version ranges allow a package solver to select releases satisfying those ranges; this is not a compatibility guarantee. They do not demonstrate compatibility with every future tool schema. Once a combination passes the native acceptance checks, export exact package records and retain those with the run:

```bash
conda list --prefix /absolute/path/basis-envs/basis-core-amr --explicit > basis-core-amr-linux64.lock.txt
conda env export --prefix /absolute/path/basis-envs/basis-core-amr > basis-core-amr-resolved.yml
```

If a recipe fails to solve, do not relax all version constraints silently. Capture the solver error and solve the affected tool in its own environment. Update that module's prefix and repeat its parser/native acceptance check.

## 3. Install additional selected modules separately

Use isolated environments for packages with large or conflicting dependency stacks:

```bash
conda create -n basis-rgi -c conda-forge -c bioconda --strict-channel-priority rgi
conda create -n basis-antismash -c conda-forge -c bioconda --strict-channel-priority antismash
conda create -n basis-mobsuite -c conda-forge -c bioconda --strict-channel-priority mob_suite
```

Run only commands for modules you intend to use. These commands create packages, not a fully configured analysis. MOB-suite has its own older numerical-library constraints in the upstream installation requirements; allow its environment to select a compatible Python rather than forcing the main application's Python 3.11 into it. antiSMASH and PathogenFinder2 can require substantial separate data/model installations.

CRISPRCasFinder and MLST use normalized evidence imports; native execution for these two modules is not included. Install and run those tools separately only when they are in scope, then prepare the import described below. MinCED has a native adapter. Use its bundled recipe or [the optional setup helper](OPTIONAL_MODULES.md), which remains specific to the isolate1 study and accepts an explicit workspace. MLST without an applicable scheme does not provide a strain-level safety conclusion. Keep PathogenFinder2's model environment separate from the application and record its model version as well as its code version. BaSIS's optional `extend` Python extra supplies NumPy for reading supported exported arrays; it does not install PathogenFinder2 or its model stack. Follow the separate [PathogenFinder2 module guide](EXTEND.md) and supplied `envs/pathogenfinder2.yml` recipe.

## 4. Configure executable prefixes

BaSIS executes an argument list without a shell. `command_prefix` is a YAML list of command tokens, not one shell command string. For example, in an AMRFinder module block:

```yaml
enabled: true
executable: amrfinder
command_prefix:
  - conda
  - run
  - --no-capture-output
  - -p
  - /absolute/path/basis-envs/basis-core-amr
database: /absolute/path/databases/amrfinder/RELEASE
database_version: "replace-with-the-actual-release"
threads: 4
```

For a named environment, use `command_prefix: [conda, run, --no-capture-output, -n, basis-rgi]`. The `conda` executable itself must be visible from the activated BaSIS environment. A program already in the active `PATH` can use an empty prefix. Use the module names and configuration nesting generated by `basis init`; the block above illustrates module fields only.

Every deep-analysis command needs access to its external programs. Set the deep layer's `command_prefix` to the `basis-deepsafe` environment. Check whether the installed IQ-TREE program is named `iqtree` or `iqtree2`; set the deep layer's `executables.iqtree` override accordingly. The default reference search uses BLASTP. If selecting DIAMOND search instead, make DIAMOND available in that same execution environment or provide its explicit executable path. Avoid embedding `conda activate`, pipelines, redirects, or `$VARIABLE` substitutions in a command-prefix list; these are shell constructs, not executable arguments.

## 5. Prepare and record databases

Database installation can be much larger than the application. Choose a data directory with adequate free disk space, a stable release identifier and a backup plan. Review data licensing and availability at its original provider. No bundled fixture represents a complete public database.

| Module | What must exist before a real run | Preparation notes |
| --- | --- | --- |
| AMRFinderPlus | A database compatible with the installed executable | `amrfinder_update --database DIR` creates versioned directories. Configure an exact release directory, not a moving `latest` link. |
| RGI | A nonempty CARD `card.json` file | Configure its exact path as `database`. The adapter performs `rgi load --card_json ... --local` inside the module's output directory; manual global loading is unnecessary. |
| VFDB | Protein FASTA and its DIAMOND `.dmnd` index | Record VFDB set, download date, source, FASTA checksum and index-building DIAMOND version. |
| BacMet | Protein FASTA and DIAMOND index | Identify the experimentally confirmed or predicted set explicitly; their evidence status is different. |
| geNomad | Extracted geNomad database directory | `genomad download-database DIR` downloads the database; record the actual database release. |
| antiSMASH | Databases installed for the chosen antiSMASH release | Run its `download-antismash-databases` program in the antiSMASH environment and retain the reported versions. |
| MOB-suite | Initialized plasmid/reference and taxonomy resources | Run `mob_init` in the MOB-suite environment before analysis and confirm successful completion. |
| IntegronFinder, ISEScan, ICEscreen | Their packaged models/resources and dependencies | Run each upstream installation check or example locally; verify package/model versions and output schemas. |
| PathogenFinder2 | Configured model ensemble and required resources | Follow its module documentation. Model scores remain model outputs. |
| Deep layer | Reference FASTA, metadata catalogue and optional reviewed family profiles | Use the release's reference preparation interface; retain accession/version, provenance and checksum information. |

Example commands, run deliberately after selecting each tool:

```bash
conda run --no-capture-output -p /absolute/path/basis-envs/basis-core-amr amrfinder_update --database /absolute/path/databases/amrfinder
conda run --no-capture-output -p /absolute/path/basis-envs/basis-context genomad download-database /absolute/path/databases/genomad
conda run --no-capture-output -n basis-antismash download-antismash-databases
conda run --no-capture-output -n basis-mobsuite mob_init
```

Build a local DIAMOND database from an already acquired FASTA:

```bash
conda run --no-capture-output -p /absolute/path/basis-envs/basis-diamond diamond makedb --in /absolute/path/databases/vfdb/proteins.faa --db /absolute/path/databases/vfdb/vfdb
```

Configure the resulting `.dmnd` file using the adapter's generated settings. Repeat with the chosen BacMet FASTA if enabled. Record the source FASTA checksum separately because an index name alone does not identify its contents.

For deep reference preparation:

```bash
basis prepare-references --help
basis prepare-references --requests reference_requests.tsv --fasta references/proteins.faa --metadata references/catalogue.tsv --maximum-accessions 100
```

This command retrieves the requested public protein accessions from NCBI and writes a local catalogue. It does not submit isolate sequences. Follow [DEEPSAFE.md](DEEPSAFE.md) for the accession-request columns, reference labels and family-profile format. Reference preparation must use your intended source catalogue; do not promote illustrative demo reference labels into verified biological annotations. Profiles included with this release carry an explicit validation status. Creating a profile does not validate its family assignments or its decision rules.

## 6. Configure the actual isolates

```bash
basis init --directory study
```

Edit the generated configuration and sample manifest. Configuration paths are resolved relative to the configuration file, and sample paths relative to the sample TSV. Required sample columns are `sample_id`, `assembly`, `proteins` and `gff`. Optional columns include `genbank`, `taxon`, `amrfinder_organism` and `phenotypes`; the latter is a path to phenotype JSON, not a free-text cell. For each sample, assembly FASTA, protein FASTA and GFF must refer to the same version of the annotation. If supplied, GenBank sequences must match the assembly; ICEscreen requires this input. Keep AFB02 and SLG04 as explicit sample IDs rather than relying on an undocumented `isolate1`/`isolate2` mapping. Record the species identification and isolate accession if available. Only use an AMRFinder organism option that the installed AMRFinder database actually supports.

Use absolute paths when first configuring the study. Set module `enabled` flags deliberately, record tool/database versions and set a fresh output directory. A module disabled because it is outside scope should appear as not assessed; a requested module that fails requires attention before a negative conclusion can be supported. Set per-module threads conservatively and begin with one sample and four total cores. Hardware needs depend heavily on enabled tools and databases; a successful demo does not establish memory capacity for native analysis.

```bash
basis doctor -c study/config.yaml
basis validate -c study/config.yaml
basis run -c study/config.yaml --cores 4 --dry-run
basis run -c study/config.yaml --cores 4
```

Review status and native evidence for the base run. After preparing the additional resources:

```bash
basis doctor -c study/config.yaml -d -e
basis run -c study/config.yaml -d --cores 4
basis run -c study/config.yaml -d -e --cores 4
basis run -c study/config.yaml -d -e --engine snakemake --cores 4
```

These are alternative run scopes, not four commands that must all be executed. Retain each run in a separate configured output directory if changing its scope. `doctor` checks inputs, declared paths and executable/prefix availability; with a `command_prefix`, it verifies that the prefix program exists, not the executable inside that environment. `validate` provides input checks and hashes. Neither command establishes native tool correctness. A run with an incomplete requested scope exits with status 2 and retains its report so the failures can be examined.

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

## 7. First-run acceptance

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
