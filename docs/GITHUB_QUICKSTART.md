# GitHub installation and collaborator quickstart

Repository: https://github.com/crackberryq/Bactomics_BaSIS  
Package: `bactromics-basis` · Command: `basis` · Candidate: `0.3.0rc4`

Use Linux x86-64 or WSL2, Python 3.11+, Git and Conda on PATH. The application is installed from GitHub; this guide does not claim a PyPI release exists.

## Install and start a fresh study

```bash
python3 -m venv basis-0.3.0rc4
source basis-0.3.0rc4/bin/activate
python -m pip install 'bactromics-basis[ui,excel,extend] @ git+https://github.com/crackberryq/Bactomics_BaSIS.git@v0.3.0rc4'
python -m pip check
basis --version
basis verify-install
basis init --directory study --hybas-dir /absolute/path/to/isolate/annotation
basis run -c study/config.yaml -d -e -v --cores 4 --output summary
```

The default full study enables 14 native adapters. Snakemake creates missing native environments and prepares required database/model snapshots before their consuming analyses. `init` itself does not download resources or run biology. Each module retains its sample applicability and evidence gates; every enabled analysis need not produce a positive finding or resolved interpretation.

The first run needs network access and enough disk space for substantial downloads, prepared resources, native environments and outputs. Shared resources default to `~/.cache/bactromics-basis` (`XDG_CACHE_HOME` is respected). To select another disk, add `--resource-dir /absolute/path/basis-resources` to `basis init`. The verified first snapshot is retained and reused without a silent update.

`ui` adds Streamlit, `excel` adds `.xlsx` import, and `extend` adds controller-side Extend dependencies. Native PathogenFinder2 and its runtime are deployed separately by the workflow. Snakemake is a base dependency; the `workflow` extra remains a compatible alias. Conda and optional NVIDIA drivers must already be installed.

## Record the exact installation

The installation command selects the release tag. Capture the full commit recorded by pip and the installed Python package inventory:

```bash
python -c 'from importlib.metadata import distribution; print(distribution("bactromics-basis").read_text("direct_url.json"))' > installed-source.json
python -m pip freeze --all > installed-python-packages.txt
```

Keep these with the run record. For a collaborator repeating the same source revision, replace `v0.3.0rc4` in the installation URL with the full `commit_id` from `installed-source.json`. A dependency inventory is useful provenance; it is not a complete artifact-hashed dependency lock.

First-use downloads can resolve upstream current releases. Reproducibility requires the actual recorded snapshots and native environment identities, not just reinstalling the same application tag and downloading again later.

## Optional software check and interface

From fresh output locations:

```bash
basis demo --outdir synthetic-demo -d -e --engine snakemake
basis report --evidence synthetic-demo/results/evidence.json --outdir synthetic-report
basis control-panel --results synthetic-demo/results --outdir synthetic-panel
basis gui --workspace ~/bactomics/basis-workspace
```

Open **http://localhost:8501**. The demo uses synthetic fixtures and performs no native biological analysis or GPU inference. Its success establishes the exercised software behavior, not biological accuracy.

A read-only study preflight is also available:

```bash
basis doctor -c study/config.yaml -d -e -v --report-dir preflight-study
```

For automatic studies, missing resources scheduled for first-use preparation are described as planned. A preflight pass does not mean they have already been installed or that native analyses have succeeded. Preserve the report before rerunning with another output directory.

## Batch and external inputs

```bash
basis import-samples --file samples.xlsx --root /absolute/path/to/annotations --directory study-batch
basis run -c study-batch/config.yaml -d -e -v --cores 4 --output summary
```

A fresh import without `--base-config` receives automatic full-study defaults. Matching assembly FASTA, protein FASTA and GFF are required. For external annotations, select the appropriate input mode and provide taxonomy only when supported. See [batch inputs](BATCH_INPUTS.md), [external inputs](EXTERNAL_INPUTS.md), [installation](INSTALL.md) and [orchestration](ORCHESTRATION.md).

Existing manual configurations preserve their resource paths and explicit environment prefixes. Copying another computer's old configuration does not make those paths valid on the new computer. The fresh-study route above avoids inheriting old imported results, sample exclusions and caches.

## Freeze and validate

Keep the source revision, Python installation record, configuration/sample sheet, input hashes, resource/model snapshots and original native outputs. Capture dependency identities with `basis audit-reproducibility`; see [its limits and options](REPRODUCIBILITY.md).

Full native acceptance on a clean collaborator machine remains to be demonstrated. Evaluate biological performance on predefined independent cases with expected labels. Development pilots, synthetic checks, parser tests and successful downloads are not independent biological validation. Genomic evidence and model scores are not organism-safety certification.
