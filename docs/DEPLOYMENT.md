# Deploy BaSIS 0.2.0rc13

The deployment bundle installs the consolidated application alongside the working pilot setup. It includes a wheel, source distribution, source tree, software-test records and captured environment provenance. It has not been publicly published, and no DOI or software license grant is implied by this bundle.

## Install alongside the pilot environment

From the extracted deployment bundle:

```bash
conda activate basis
python install_local.py
```

The active environment must already provide BaSIS's required Python dependencies. The installer creates a venv using `--system-site-packages`, installs the supplied wheel using offline `--no-deps` installation, and records dependency locations. It does not uninstall or replace the old BaSIS package. Native module environments and database paths remain unchanged.

Default destinations are `.venv` and `installation-checks` below the extracted bundle. Both must be new. To choose explicit destinations:

```bash
python install_local.py --prefix /absolute/path/basis-0.2.0rc13 --checks-dir /absolute/path/basis-acceptance
```

Follow the printed activation command. Preserve the installer JSON record and its synthetic reports. This procedure checks package installation and application behavior while reusing dependencies; it is not a clean-host dependency restoration test.

The installer checks application import/version, packaged-file integrity, a synthetic demonstration, offline rendering of its evidence and control-panel creation. The demonstration does not run native analysis tools, model inference or biological validation.

## Optional Excel reader

At first installation, `python install_local.py --with-excel` opts into downloading openpyxl 3.1.5 and et-xmlfile 2.0.0 into the new venv. The default installer remains offline and records whether an inherited compatible reader is available. The installation record distinguishes dependency reuse from these opt-in installations. CSV/TSV import works without openpyxl. Never install into the launcher currently running acceptance.

## Conventional wheel installation

In a new Python 3.11+ environment:

```bash
python -m pip install /absolute/path/bactromics_basis-0.2.0rc13-py3-none-any.whl
basis --version
basis verify-install
```

Pip may download missing Python dependencies. Native tools and biological resources must be prepared separately. The optional workflow dependency can be installed from the source distribution with `python -m pip install '.[workflow]'`; this requires the configured package sources and may access the network. The default local engine does not require Snakemake.

`basis verify-install` compares installed Python source and bundled assets to the release manifest. It does not verify external databases or prove computational/biological correctness. An edited source checkout may be unsealed or fail this integrity check; keep that development tree distinct from the immutable candidate release.

## Plan tool environments from any workspace

```bash
basis setup-environments --project /absolute/path/workspace
basis setup-environments --project /absolute/path/workspace --env minced
```

These commands print plans only. Add `--create` and explicit `--env` selections when installation is intended. Recipes are bundled with the installed package and are read-only; environment destinations default to the workspace's `.basis-envs`. The helper supports Conda, Mamba and Micromamba, skips existing prefixes, and does not download databases or run analyses.

The eight bundled recipes are starting specifications. To reproduce recorded installed package versions, review the separate captured exports and their limitations in [reproducibility/README.txt](../reproducibility/README.txt). The isolate1 optional-module helper remains study-specific; it is not a general automatic taxonomic configuration service.

## Refresh reports and navigate saved runs

Use new output directories:

```bash
basis report --evidence /absolute/path/run/evidence.json --outdir /absolute/path/report-review
basis control-panel --results /absolute/path/run --outdir /absolute/path/control-panel
```

To link several runs, repeat `--results`. For an existing pilot directory containing `pilot.json`:

```bash
basis control-panel --pilot-dir /absolute/path/pilot --outdir /absolute/path/pilot-panel
```

Reports distinguish consolidated run reports from native tool reports. Overview returns to all linked runs. Native files, alignment files and trees remain accessible through the panel. These commands read saved evidence and preserve its scientific interpretations; they do not rerun tools or retroactively prove that a previous cache is valid for new code.

Retain linked folders in place. Copying only `index.html` or `report.html` does not make every linked native artifact portable. Archive the full study and its dependencies when a self-contained handover is needed.

## Native acceptance and reproducibility boundary

After package acceptance, retain a copy of the prior configuration and set a fresh output directory for any native acceptance run. Preserve input hashes, module selections, thresholds, tool versions and database/model snapshots so differences can be attributed. The packaged source fingerprint now includes runtime assets; prior caches can therefore be invalidated even when biological rules are unchanged.

The release records eleven pilot environments. Ten Conda exports agree with their captured package metadata. The context environment has one missing TensorFlow metadata record; its explicit export is marked incomplete. The audit does not establish package corruption, and the discrepancy does not block installation alongside the existing working environment.

Pip distribution identities are inventories, not a complete set of archived and hashed wheels. Native package archives, original sample inputs, large databases and model weights are outside this bundle. Fully offline restoration and fresh-host native-tool execution remain unverified. GPU and numerical results are not claimed to be bitwise identical across hardware or libraries.

Independent biological validation follows a frozen analysis protocol and separate evaluation data; see [VALIDATION.md](VALIDATION.md).
