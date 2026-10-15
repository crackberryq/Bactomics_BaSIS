# Start with BaSIS 0.3.0rc3

Install the application, initialize a fresh study, and run it. Snakemake prepares missing tools and requested resources as part of the first native run.

Use Linux x86-64 or WSL2 with Python 3.11+, Git and Conda available on PATH:

```bash
python3 -m venv basis-0.3.0rc3
source basis-0.3.0rc3/bin/activate
python -m pip install 'bactromics-basis[ui,excel,extend] @ git+https://github.com/crackberryq/Bactomics_BaSIS.git@v0.3.0rc3'
basis verify-install
basis init --directory study --hybas-dir /absolute/path/to/isolate/annotation
basis run -c study/config.yaml -d -e -v --cores 4 --output summary
```

Use a new study directory. The default full preset enables all 14 native adapters, with DeepSafe requested by `-d` and Extend by `-e`. Scope and evidence requirements still apply to each sample. You do not need an older configuration, migration helper, or a sequence of manual database-install commands for this route.

The first run downloads and indexes substantial resources. Keep network access available and allow space for native environments, database/model snapshots and results. Progress is visible with `-v`. New studies share verified resources under `~/.cache/bactromics-basis` by default; choose a different disk with `basis init --resource-dir /absolute/path/basis-resources ...`. Existing snapshots are verified and reused without an automatic refresh.

CPU execution is supported. New studies use automatic device selection for Extend: a suitable NVIDIA GPU and working tool runtime can be used; otherwise execution falls back to CPU. BaSIS does not install GPU drivers.

## Open the interface

```bash
basis gui --workspace ~/bactomics/basis-workspace
```

Open **http://localhost:8501**. The interface and CLI use the same workflow. Follow saved jobs and logs, then open linked results. Closing the browser does not stop a running job; a computer reboot interrupts it.

## Other inputs and reproducibility

For a batch, use [Excel/CSV/TSV import](docs/BATCH_INPUTS.md). For annotations produced outside HybAs, use [external input mode](docs/EXTERNAL_INPUTS.md). Matching assembly FASTA, protein FASTA and GFF are required; BaSIS does not annotate an assembly. HybAs/Kraken is optional. Unknown taxonomy remains unknown.

Existing manual configurations keep their explicit resources and environments. Installing this candidate does not rewrite them. See [installation](docs/INSTALL.md) and [orchestration](docs/ORCHESTRATION.md) for resource policies, optional preflight checks and recovery boundaries.

This installs from GitHub, not a claimed PyPI release. Preserve the exact installed commit and resource snapshots as described in [GitHub quickstart](docs/GITHUB_QUICKSTART.md). Full clean-machine native acceptance and independent biological validation remain separate work.

Author: Jimi Goldstein, [ORCID 0000-0002-7829-0090](https://orcid.org/0000-0002-7829-0090). BaSIS uses the [MIT license](LICENSE); external resources retain their own licenses.
