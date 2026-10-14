# Start with BaSIS 0.2.0rc16

The GitHub repository contains the application source. The optional local Streamlit interface and CLI use the same analysis engine.

## From GitHub

On Linux or WSL2, with Python 3.11+ and Git:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'bactromics-basis[ui,excel] @ git+https://github.com/crackberryq/Bactomics_BaSIS.git@main'
basis verify-install
basis gui --workspace ~/bactomics/basis-workspace
```

Open **http://localhost:8501**. Use a full source commit SHA instead of `main` when freezing a validation installation. See [GitHub quickstart](docs/GITHUB_QUICKSTART.md) for an exact-commit workflow and a synthetic smoke test. Public PyPI publication remains a separate step.

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

Configure native tool environments, databases and models. The interface can plan, create and verify tool environments through Snakemake, then run study preflight. Database/model acquisition is not yet fully automated. See [orchestration](docs/ORCHESTRATION.md). Existing resource configurations can be reused with their paths reviewed for the current computer.

Run **Setup → preflight**, then choose your study configuration in **Run**. Use the **Samples** page for Excel/CSV/TSV batches. Follow jobs in **Jobs**, and open linked reports through **Results & export**. Jobs survive browser closure; a computer reboot interrupts them.

See [local interface behavior](docs/LOCAL_INTERFACE.md), [batch inputs](docs/BATCH_INPUTS.md) and [external annotation inputs](docs/EXTERNAL_INPUTS.md). The rc13 baseline is preserved. Independent biological validation remains pending.

Author: Jimi Goldstein, [ORCID 0000-0002-7829-0090](https://orcid.org/0000-0002-7829-0090). BaSIS uses the [MIT license](LICENSE); external resources have their own licenses.
