# GitHub installation and collaborator quickstart

Repository: https://github.com/crackberryq/Bactomics_BaSIS  
Package: `bactromics-basis` · Command: `basis` · Candidate: `0.2.0rc15`

Use Linux or WSL2, Python 3.11+ and Git. Native scientific tools and resource databases are separate from the Python application. This guide installs from GitHub and does not imply a PyPI release exists.

## Install an exact source revision

Clone the repository and record the full commit before installing:

```bash
git clone https://github.com/crackberryq/Bactomics_BaSIS.git
cd Bactomics_BaSIS
basis_commit=$(git rev-parse HEAD)
printf '%s\n' "$basis_commit" > installed-source-commit.txt
python3 -m venv .venv
source .venv/bin/activate
python -m pip install "bactromics-basis[ui,excel] @ git+https://github.com/crackberryq/Bactomics_BaSIS.git@$basis_commit"
python -m pip check
basis --version
basis verify-install
python -m pip freeze --all > installed-python-packages.txt
```

Keep `installed-source-commit.txt` with the run record and share that exact SHA with collaborators. Installing from `main` is convenient for trying the current development candidate but does not pin future installations. A dependency inventory is useful provenance; it is not a complete artifact-hashed dependency lock.

`ui` adds Streamlit, `excel` adds `.xlsx` import, and `workflow` adds optional Snakemake scheduling. CSV/TSV and the CLI do not require the first two extras.

## Confirm the application works

From a fresh output location:

```bash
basis demo --outdir synthetic-demo -d -e
basis report --evidence synthetic-demo/results/evidence.json --outdir synthetic-report
basis control-panel --results synthetic-demo/results --outdir synthetic-panel
basis gui --workspace ~/bactomics/basis-workspace
```

Open **http://localhost:8501**. The demo uses synthetic fixtures and does not run native biological analysis or GPU inference. From the checkout, the optional installed-interface helper adds automated interface and detached-job checks:

```bash
python -I BaSIS_check_interface.py
```

It creates a new `interface-checks/` directory and `interface-checks/interface-summary.zip`; preserve that result before repeating the check. Its success establishes the tested software behavior, not biological accuracy.

## Prepare native analysis

Conda must be available separately. Start by previewing an environment plan:

```bash
basis setup-environments --workspace ~/bactomics/basis-tools --env core-amr
```

Inspect `basis setup-environments --help` for explicit creation options. Recipes are package specifications rather than solved locks. Each enabled module also needs its configured database/model paths and applicable sample metadata. See [installation](INSTALL.md), [external inputs](EXTERNAL_INPUTS.md) and [batch inputs](BATCH_INPUTS.md).

Create a fresh study or import a sheet, review its configuration, and run:

```bash
basis doctor -c /absolute/path/to/study/config.yaml -d -e -v
basis run -c /absolute/path/to/study/config.yaml -d -e -v --output summary
```

Preflight checks are distinct from successful native execution. Copying another computer's configuration without updating its paths is insufficient. The GUI calls the same engine, retains exact commands and separates execution failures, deferred scope and unresolved interpretation.

## Freeze a validation run

Keep the source revision, Python installation record, configs/sample sheets, input hashes, resource/model snapshots and original native outputs. Capture declared dependency identities with `basis audit-reproducibility`; see [its limits and options](REPRODUCIBILITY.md). Use predefined independent evaluation cases and expected labels for biological validation. Do not treat the development pilot, synthetic checks or software test suite as independent biological validation.

Historical deployment archives and study-specific evidence are not included in this source repository. Request an explicitly identified release/checkpoint separately if those records are needed for a collaboration.
