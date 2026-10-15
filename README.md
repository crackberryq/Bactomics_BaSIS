# BaSIS

**Bacterial Adaptive Safety Investigation System**  
A reproducible workflow for bacterial genomic safety investigation, with a local browser interface and linked evidence reports.

BaSIS combines screening tools, genomic context, selected-protein follow-up and optional machine-learning evidence. It accepts matching assembly and annotation files from HybAs or external workflows, and supports Excel, CSV and TSV sample sheets.

**Current candidate: 0.3.0rc4.** A new full study prepares its required tool environments, databases and models through the normal Snakemake analysis workflow. The first verified resource snapshots are recorded and reused without an automatic refresh. Independent biological validation and full native acceptance on a clean collaborator computer remain pending.

## Install and run

Use Linux x86-64 or WSL2 with Python 3.11+, Git and Conda available on PATH. Create a fresh application environment:

```bash
python3 -m venv basis-0.3.0rc4
source basis-0.3.0rc4/bin/activate
python -m pip install 'bactromics-basis[ui,excel,extend] @ git+https://github.com/crackberryq/Bactomics_BaSIS.git@v0.3.0rc4'
basis verify-install
basis init --directory study --hybas-dir /absolute/path/to/isolate/annotation
basis run -c study/config.yaml -d -e -v --cores 4 --output summary
```

`pip` installs the application and Snakemake. `basis init` creates the sample manifest and full configuration. `basis run` installs missing native environments, prepares missing requested resources, checks inputs, and runs the analysis. Initialization itself performs no downloads or biological analyses. Existing installations and studies can remain in place; this clean-start route needs no study migration or helper script.

The first run needs network access, disk space for substantial databases/models and native environments, and time for downloads and indexing. Later runs verify and reuse those snapshots. CPU execution is supported; new studies automatically check whether an NVIDIA GPU can be used for Extend, with a CPU fallback when it cannot. Drivers are not installed by BaSIS. See [installation details](docs/INSTALL.md).

This installs from the GitHub release tag. **No PyPI publication is claimed.** Record the full installed commit and actual resource snapshots for reproducibility; see [GitHub quickstart](docs/GITHUB_QUICKSTART.md). Optional extras add the interface, Excel import and controller-side Extend dependencies.

## Local interface

```bash
basis gui --workspace ~/bactomics/basis-workspace
```

Open **http://localhost:8501**. The optional Streamlit interface offers setup, sample import, run configuration, persistent jobs, live logs and report downloads. It calls the same Snakemake workflow as the CLI. Closing the browser does not stop a running job. It is intended for one trusted local user, with one job per workspace. See [the interface guide](docs/LOCAL_INTERFACE.md).

## Analysis layers

| Layer | What it provides | Interpretation |
| --- | --- | --- |
| Base | AMR and homology screening, biosynthetic and mobile-element context, typing and CRISPR/Cas analyses in the full preset | Database matches and predictions are evidence requiring interpretation. |
| DeepSafe, `-d` | Selected-candidate domains, motifs, PROSITE patterns/profiles, reference comparisons and eligible phylogenetic analyses | Comparator coverage and applicability determine follow-up; unresolved and deferred candidates stay visible. |
| Extend, `-e` | PathogenFinder2 predictions and an exploratory attention queue | Model outputs describe model evidence, not measured exposure risk. |

The full preset enables 14 native adapters: AMRFinderPlus, CARD/RGI, VFDB, BacMet, antiSMASH, geNomad, MOB-suite, IntegronFinder, ISEScan, ICEscreen, MinCED, MLST, CRISPRCasFinder and PathogenFinder2. `-e` requests the Extend module. Sample prerequisites and tool scope still apply: enabling a module does not guarantee an interpretable result for every organism. Native adapter availability is separate from independent biological validation.

HybAs imports preserve the recorded taxonomy rank. Where available Kraken reports agree on the selected target's phylum ancestry, BaSIS imports that phylum with source hashes for module scope checks. A leading species hit does not become a species identification. Explicit user taxonomy is preserved. ICEscreen requires matching GenBank and supported phylum evidence; unresolved scope remains visible.

## Inputs and resource policy

Matching nucleotide FASTA, protein FASTA and GFF are required. An assembly alone is not automatically annotated. For external annotations, use [external input mode](docs/EXTERNAL_INPUTS.md); Kraken is not required. Unknown taxonomy stays unknown and can limit taxon-dependent modules.

The default `basis init --preset full` stores managed resources under `~/.cache/bactromics-basis`, or under the user cache directory selected by `XDG_CACHE_HOME`. Choose another shared location with `--resource-dir /absolute/path/basis-resources`. Use `--preset core` when requesting the smaller core screen. New full studies enable automated DeepSafe follow-up, PROSITE profiles and family placement; eligibility gates still apply.

For several isolates, preview and import a [batch sample sheet](docs/BATCH_INPUTS.md):

```bash
basis import-samples --file samples.xlsx --root /absolute/path/to/samples --dry-run -v
basis import-samples --file samples.xlsx --root /absolute/path/to/samples --directory study-batch
basis run -c study-batch/config.yaml -d -e -v --cores 4 --output summary
```

Fresh imports without a base configuration use the automatic full-study defaults. Existing manually configured resource paths and explicit tool prefixes retain their meaning; BaSIS does not replace them just because a newer package is installed. See [orchestration and resource policy](docs/ORCHESTRATION.md).

## Explore saved results

Reports link findings, selected-protein evidence, available trees, native outputs and logs. Build a new report or a control panel without rerunning analyses:

```bash
basis report --evidence /path/to/results/evidence.json --outdir /path/to/new-report
basis control-panel --results /path/to/results --outdir /path/to/new-panel
```

Repeat `--results` to link multiple runs. Keep linked study directories in place; the control panel is not a self-contained copy of every linked artifact. See [report navigation](docs/REPORT_NAVIGATION.md).

Execution, assessment coverage and biological interpretation are reported separately. Completed selected candidates can coexist with deferred candidates and an incomplete overall scope. Failed or unassessed modules are not negative findings. Genomic findings, tree support and model scores do not certify organism safety or establish a calibrated probability of pathogenicity.

## Reproduce, validate and cite

Retain the exact source commit or release artifact, installed-package verification, resolved configuration, input hashes, tool versions, database/model snapshots and native outputs. First-use acquisition may resolve an upstream current release; the recorded snapshot identifies what was actually used. Reproducing that run requires those exact resources, not another unpinned first download on a different date.

[Reproducibility capture](docs/REPRODUCIBILITY.md) records identities and gaps; an inventory alone does not prove successful restoration. Development pilots, synthetic checks and parser tests do not constitute independent biological validation. The public repository excludes private study inputs and native run outputs.

- [Run controls](docs/RUN_CONTROLS.md) and [DeepSafe](docs/DEEPSAFE.md)
- [Collaborator validation](docs/COLLABORATOR_VALIDATION.md)
- [Publishing and release checks](docs/PUBLISHING.md)
- [Citation metadata](CITATION.cff)

**Author:** Jimi Goldstein · [ORCID 0000-0002-7829-0090](https://orcid.org/0000-0002-7829-0090). Reports include citation downloads. Please also cite the underlying tools and databases used in your analysis.

BaSIS is distributed under the [MIT license](LICENSE). External tools, datasets and model weights retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
