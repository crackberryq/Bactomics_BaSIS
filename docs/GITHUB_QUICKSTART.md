# GitHub installation and collaborator quickstart

Repository: https://github.com/crackberryq/Bactomics_BaSIS  
Package: `bactromics-basis` · Command: `basis` · Candidate: `0.3.0rc1`

Use Linux or WSL2, Python 3.11+ and Git. Native scientific tools and resource databases are separate from the Python application. This guide installs from GitHub and does not imply a PyPI release exists.

## Install an exact source revision

Clone the repository and record the full commit before installing:

```bash
git clone --branch v0.3.0rc1 https://github.com/crackberryq/Bactomics_BaSIS.git
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

Keep `installed-source-commit.txt` with the run record and share that exact SHA with collaborators. The clone selects `v0.3.0rc1`; the installation uses its resolved full commit. Keep the preceding `v0.2.0rc16` installation separate if it is your validation baseline. A dependency inventory is useful provenance; it is not a complete artifact-hashed dependency lock.

`ui` adds Streamlit and `excel` adds `.xlsx` import. Snakemake is a base dependency and is the default analysis scheduler; the `workflow` extra remains a compatible alias. CSV/TSV and the CLI do not require the first two extras.

## Confirm the application works

From a fresh output location:

```bash
basis demo --outdir synthetic-demo -d -e --engine snakemake
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

Conda must be available separately. Analysis rules deploy their supported Conda recipes automatically when no explicit module command prefix is configured. To prepare environments ahead of analysis, preview a plan:

```bash
basis setup-environments --workspace ~/bactomics/basis-tools --env core-amr
```

Add `--create` to install and verify the selected recipes. This uses the same workflow entry point as analysis, in setup mode. Existing named prefixes are verified and preserved; newly managed prefixes use Snakemake cache names. Recipes are package specifications rather than solved locks. Each enabled module also needs its configured database/model paths and applicable sample metadata. Use `basis prepare-resources --help` for explicit supported resource snapshots; normal analysis does not refresh them. See [orchestration and migration](ORCHESTRATION.md), [installation](INSTALL.md), [external inputs](EXTERNAL_INPUTS.md) and [batch inputs](BATCH_INPUTS.md).

Create a fresh study or import a sheet, review its configuration, and run:

```bash
basis doctor -c /absolute/path/to/study/config.yaml -d -e -v
basis run -c /absolute/path/to/study/config.yaml -d -e -v --output summary
```

Preflight checks are distinct from successful native execution. Copying another computer's configuration without updating its paths is insufficient. The GUI calls the same engine, retains exact commands and separates execution failures, deferred scope and unresolved interpretation.

## Freeze a validation run

Keep the source revision, Python installation record, configs/sample sheets, input hashes, resource/model snapshots and original native outputs. Capture declared dependency identities with `basis audit-reproducibility`; see [its limits and options](REPRODUCIBILITY.md). Use predefined independent evaluation cases and expected labels for biological validation. Do not treat the development pilot, synthetic checks or software test suite as independent biological validation.

Historical deployment archives and study-specific evidence are not included in this source repository. Request an explicitly identified release/checkpoint separately if those records are needed for a collaboration.
