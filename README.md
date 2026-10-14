# BaSIS

**Bacterial Adaptive Safety Investigation System**  
A reproducible workflow for bacterial genomic safety investigation, with a local browser interface and linked evidence reports.

BaSIS combines screening tools, genomic context, selected-protein follow-up and optional machine-learning evidence. It accepts matching assembly and annotation files from HybAs or external workflows, and supports Excel, CSV and TSV sample sheets.

**Current candidate: 0.3.0rc1.** Snakemake now owns input/resource checks, named analysis rules, nine DeepSafe stages per sample, evidence consolidation, reporting and provenance. The same workflow entry point also provides explicit environment and resource preparation modes. Scientific methods, selection rules and interpretation thresholds are unchanged. Independent biological validation is pending.

## Install and open

Use Linux or WSL2 with Python 3.11+ and Git. From a new working directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'bactromics-basis[ui,excel] @ git+https://github.com/crackberryq/Bactomics_BaSIS.git@v0.3.0rc1'
basis verify-install
basis gui --workspace ~/bactomics/basis-workspace
```

Open **http://localhost:8501**. The optional Streamlit interface offers setup, sample import, run configuration, persistent jobs, live logs, and report downloads. Closing the browser does not stop a running job. It is intended for one trusted local user, with one job per workspace.

This command installs from GitHub. **No PyPI publication is claimed.** The command selects the `v0.3.0rc1` release tag; record its full commit SHA for a reproducible installation, as explained in [GitHub quickstart](docs/GITHUB_QUICKSTART.md). Omit `[ui,excel]` for the base CLI package, or choose either extra independently.

`pip` installs the Python application and Snakemake. Install Conda separately; native analysis rules can then create their required tool environments automatically. Existing explicit environment prefixes remain usable. Database preparation is an explicit administrative action with supported backends; model setup and GPU drivers still require their own preparation. Start with [the quickstart](docs/GITHUB_QUICKSTART.md), [local interface guide](docs/LOCAL_INTERFACE.md), or [tool installation](docs/INSTALL.md).

## Analysis layers

| Layer | What it provides | Interpretation |
| --- | --- | --- |
| Base | AMR and homology screening, biosynthetic and mobile-element context | Database matches and predictions are evidence requiring interpretation. |
| DeepSafe, `-d` | Selected-candidate domains, motifs, PROSITE patterns/profiles, reference comparisons and eligible phylogenetic analyses | Resource and comparator requirements determine available follow-up; unresolved and deferred candidates stay visible. |
| Extend, `-e` | Optional PathogenFinder2 predictions and an exploratory attention queue | Model outputs describe model evidence, not measured exposure risk. |

Native adapters cover AMRFinderPlus, CARD/RGI, VFDB, BacMet, antiSMASH, geNomad, MOB-suite, IntegronFinder, ISEScan, ICEscreen, MinCED and PathogenFinder2. MLST and CRISPRCasFinder currently support normalized imports. The configured modules define the requested scope; an available adapter does not mean its prerequisites are installed.

## Run from the command line

```bash
basis init --directory study --hybas-dir /absolute/path/to/hybas/project
# Review study/config.yaml and study/samples.tsv; configure resources first.
basis doctor -c study/config.yaml -d -e
basis run -c study/config.yaml -d -e -v --output summary
```

For external annotations, use [external input mode](docs/EXTERNAL_INPUTS.md). Matching nucleotide FASTA, protein FASTA and GFF are required; an assembly alone is not automatically annotated. Kraken output is not required. Unknown taxonomy stays unknown and can limit taxon-dependent modules.

For several isolates, preview and import a [batch sample sheet](docs/BATCH_INPUTS.md):

```bash
basis import-samples --file samples.xlsx --root /absolute/path/to/samples --dry-run -v
basis import-samples --file samples.xlsx --root /absolute/path/to/samples \
  --directory study-batch --base-config /absolute/path/to/config.yaml
```

The CLI and GUI launch the same Snakemake workflow, using declared CPU, memory and GPU budgets. DeepSafe follows completed upstream records, including Extend when enabled. Scientific processing remains in Python called by the rules. The serial `--engine local` option remains for compatibility. See [orchestration and verified setup](docs/ORCHESTRATION.md).

## Explore saved results

Reports link findings, selected-protein evidence, available trees, native outputs and logs. Build a new report or a control panel without rerunning analyses:

```bash
basis report --evidence /path/to/results/evidence.json --outdir /path/to/new-report
basis control-panel --results /path/to/results --outdir /path/to/new-panel
```

Repeat `--results` to link multiple runs. Keep linked study directories in place; the control panel is not a self-contained copy of every linked artifact. See [report navigation](docs/REPORT_NAVIGATION.md).

Execution, assessment coverage and biological interpretation are reported separately. Completed selected candidates can coexist with deferred candidates and an incomplete overall scope. Failed or unassessed modules are not negative findings. Genomic findings, tree support and model scores do not certify organism safety or establish a calibrated probability of pathogenicity.

The preceding `0.2.0rc16` release is preserved separately. See [migration and environment routing](docs/ORCHESTRATION.md#migrating-from-02) before moving an existing study to the new workflow.

## Reproduce, validate and cite

Retain the exact source commit or release artifact, installed-package verification, resolved configuration, input hashes, tool versions, database/model snapshots and native outputs. [Reproducibility capture](docs/REPRODUCIBILITY.md) records identities and gaps; a captured inventory is not proof of successful restoration. Development pilot results and software checks do not constitute independent biological validation.

The public source repository excludes private study inputs, native run outputs and historical validation/reproducibility archives. Some detailed guides retain explicitly versioned development history; those records are not supplied as public validation evidence.

- [Run controls](docs/RUN_CONTROLS.md) and [DeepSafe](docs/DEEPSAFE.md)
- [Collaborator validation](docs/COLLABORATOR_VALIDATION.md)
- [Publishing and release checks](docs/PUBLISHING.md)
- [Citation metadata](CITATION.cff)

**Author:** Jimi Goldstein · [ORCID 0000-0002-7829-0090](https://orcid.org/0000-0002-7829-0090). Reports include citation downloads. Please also cite the underlying tools and databases used in your analysis.

BaSIS is distributed under the [MIT license](LICENSE). External tools, datasets and model weights retain their own licenses.
