# Start with BaSIS 0.3.0rc1

The GitHub repository contains the application source. The optional local Streamlit interface and CLI use the same analysis engine.

## From GitHub

On Linux or WSL2, with Python 3.11+ and Git:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'bactromics-basis[ui,excel] @ git+https://github.com/crackberryq/Bactomics_BaSIS.git@v0.3.0rc1'
basis verify-install
basis gui --workspace ~/bactomics/basis-workspace
```

Open **http://localhost:8501**. The command selects the `v0.3.0rc1` tag. Record the full source commit SHA when freezing a validation installation. See [GitHub quickstart](docs/GITHUB_QUICKSTART.md) for an exact-commit workflow and a synthetic smoke test. Public PyPI publication remains a separate step.

## From a packaged deployment ZIP

If you received a BaSIS deployment bundle containing `BUNDLE_MANIFEST.json` and `dist/`, extract it into a new directory and run:

```bash
python install_local.py --with-ui --with-excel
source .venv/bin/activate
python -I BaSIS_check_interface.py
basis gui --workspace ~/bactomics/basis-workspace
```

The bundle installer creates a clean `.venv` and downloads Python dependencies. It verifies the bundle and preserves earlier installations. It is for deployment ZIPs; do not use it on a plain Git checkout without the bundle manifest and built distributions.

## Before a native study

Install Conda and configure the study databases/models. When an enabled native module has no explicit command prefix, Snakemake deploys its supported tool recipe automatically. Existing explicit prefixes are preserved. Environment preparation and supported database preparation can also be run deliberately before analysis through the same workflow entry point. See [orchestration and migration](docs/ORCHESTRATION.md); not every database/model has a preparation backend.

Use **Tool environments** and **Databases & models** for setup, **Add samples** for Excel/CSV/TSV batches, and **Readiness & run** to inspect and launch the study. Follow **Progress & jobs** and open linked reports through **Explore results**. Jobs survive browser closure; a computer reboot interrupts them.

See [local interface behavior](docs/LOCAL_INTERFACE.md), [batch inputs](docs/BATCH_INPUTS.md) and [external annotation inputs](docs/EXTERNAL_INPUTS.md). The preceding `0.2.0rc16` release is preserved separately. Independent biological validation remains pending.

Author: Jimi Goldstein, [ORCID 0000-0002-7829-0090](https://orcid.org/0000-0002-7829-0090). BaSIS uses the [MIT license](LICENSE); external resources have their own licenses.
