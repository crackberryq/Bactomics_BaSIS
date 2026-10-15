# Install and run BaSIS 0.3.0rc5

Use Linux x86-64 or Ubuntu inside WSL2, Python 3.11+, Git and Conda on PATH. The GitHub source release is pip-installable; no public PyPI release is claimed. NVIDIA hardware and drivers are optional for Extend acceleration.

## 1. Install the application

```bash
python3 -m venv basis-0.3.0rc5
source basis-0.3.0rc5/bin/activate
python -m pip install 'bactromics-basis[ui,excel,extend] @ git+https://github.com/crackberryq/Bactomics_BaSIS.git@v0.3.0rc5'
python -m pip check
basis verify-install
```

Keep earlier installations and studies intact. This route installs the package from the release tag and needs no historical deployment ZIP or helper. Record the full installed source commit and Python inventory using [the GitHub quickstart](GITHUB_QUICKSTART.md).

Snakemake 9.6.2 is a base dependency. Conda must already be installed; pip does not install Conda or system GPU drivers. The optional `ui`, `excel` and `extend` extras supply application-side dependencies. Native analysis tools are isolated in their own environments and deployed by the workflow.

## 2. Initialize and run

For an existing HybAs annotation set:

```bash
basis init --directory study --hybas-dir /absolute/path/to/isolate/annotation
basis run -c study/config.yaml -d -e -v --cores 4 --output summary
```

Initialization defaults to the full preset. It enables all 14 native adapters and automatic resource preparation. The normal Snakemake run creates missing environments, acquires/prepares required databases and models, checks inputs and performs the requested analyses. `-d` enables DeepSafe and `-e` enables Extend. No migration helper or copied old-study configuration is required.

The first run needs network access and disk space for substantial downloads, database indexes, model weights, native environments and output files. It can take considerably longer than later analysis-only runs. `-v` shows progress and logs. The first verified snapshots are retained and checked before reuse; ordinary runs do not silently update them.

Choose a shared cache on another disk when initializing:

```bash
basis init --directory study-on-data-disk \
  --hybas-dir /absolute/path/to/isolate/annotation \
  --resource-dir /absolute/path/basis-resources
```

Without this option, new studies use `~/.cache/bactromics-basis`, respecting `XDG_CACHE_HOME`. Managed environments live under its `environments` directory. `--preset core` selects the smaller core module set. Full-preset settings enable PROSITE profiles and eligible automatic phylogeny/placement; applicability checks still control which candidates can receive those analyses.

For a batch, `basis import-samples --file samples.xlsx --directory study-batch` without a base configuration creates the same automatic full-study defaults. Supply `--root` for relative input paths. See [batch import](BATCH_INPUTS.md).

## 3. Input and scope requirements

Matching assembly FASTA, protein FASTA and GFF are required. An assembly alone is not annotated by BaSIS. Matching GenBank is required for ICEscreen. For external annotations, use [external input mode](EXTERNAL_INPUTS.md); Kraken/HybAs is not required.

HybAs imports retain the selected target's rank. Consistent selected-target ancestry in available enabled Kraken reports can supply phylum automatically, with report hashes and lineage evidence. Leading species hits are not species identifications. Explicit user taxonomy is preserved. Missing or conflicting phylum evidence holds phylum-dependent analyses until reviewed.

| Analysis | Native capability and scope |
| --- | --- |
| AMRFinderPlus, RGI | AMR screening; organism-specific models still depend on applicable taxonomy/settings |
| VFDB, BacMet | Protein homology screening against prepared references |
| antiSMASH, geNomad, MOB-suite | Biosynthetic, viral/proviral and plasmid-context predictions |
| IntegronFinder, ISEScan, ICEscreen | Integron, insertion-sequence and ICE/IME context; ICEscreen has a phylum/GenBank scope gate |
| MinCED, CRISPRCasFinder | CRISPR arrays; CRISPRCasFinder also assesses Cas systems |
| MLST | Supported typing schemes; an unassigned scheme or sequence type is completed, inconclusive typing |
| PathogenFinder2 | Optional model inference and exploratory attention ranking |
| DeepSafe | Candidate-specific domains, motifs/patterns, comparator retrieval and eligible phylogeny/placement |

All 14 module adapters are native-capable in this candidate. That does not mean every sample is in every tool's scope, that every comparator family is validated, or that all tools have passed a live clean-machine acceptance run.

The MLST adapter uses version 2.35.0 and its bundled PubMLST snapshot; it does not silently update to the live PubMLST collection. CRISPRCasFinder uses pinned 4.3.2 source and CASFinder 3.1.0 models on Linux x86-64. The Conda recipe pairs MacSyFinder 2.0 with its supported HMMER 3.3.2 dependency. Its report separates Cas component candidates from typed systems and records the algorithm mode. Short contigs retain upstream unordered mode; the circular-genome assumption in ordered mode does not establish physical chromosome circularity.

## 4. Optional preflight and interface

```bash
basis doctor -c study/config.yaml -d -e -v --report-dir preflight-study
basis run -c study/config.yaml -d -e --cores 4 --dry-run
basis gui --workspace ~/bactomics/basis-workspace
```

Doctor and dry-run planning perform no resource downloads or biological analyses. Missing resources scheduled under the automatic policy are described as planned, not installed. Preflight is useful for input and scope problems, but it does not replace native setup/analysis verification. Open the interface at **http://localhost:8501**.

CPU execution is supported. New studies use `execution.device: auto`: NVIDIA hardware is checked for scheduler allocation, and CUDA availability is checked inside the actual PathogenFinder2 runtime before inference. Auto mode can fall back to CPU. Choose `cpu` to force CPU or `cuda` to require GPU availability. Existing configurations without this field retain their configured GPU budgets. BaSIS does not install drivers.

An incomplete requested scope exits with status 2 and retains its report. Completed selected DeepSafe candidates can coexist with deferred candidates and incomplete scope. A failed, held or absent analysis is never reported as evidence of absence.

## 5. Existing environments and resource paths

Existing manual configurations retain their explicit paths and scientific settings. Under the default environment routing policy, a module's explicit argument-list prefix remains external:

```yaml
modules:
  amrfinder:
    enabled: true
    executable: amrfinder
    command_prefix: [conda, run, --no-capture-output, -p, /absolute/path/basis-core-amr]
    database: /absolute/path/databases/amrfinder/EXACT_RELEASE
```

This does not recreate or repair that prefix. A missing explicit database path is not silently replaced with a new download. Use a fresh initialized study for the automatic clean-start route, or deliberately update the old configuration after reviewing its sample-specific imports and settings.

To adopt managed environments in an existing configuration, remove the explicit prefix and absolute executable paths into another environment. An optional module-level `environment_backend: managed` makes that choice explicit. A managed setting combined with a prefix is rejected. Use `external` when deliberately relying on an existing prefix or PATH. The same rules apply to `deep`.

Conda recipes are package constraints, not solved platform locks. Preserve the actual environment exports after native acceptance:

```bash
conda list --prefix /absolute/path/recorded-prefix --explicit > tool-linux64.lock.txt
conda env export --prefix /absolute/path/recorded-prefix > tool-resolved.yml
```

Optional environment preinstallation and custom resource-administration commands remain available; see [orchestration](ORCHESTRATION.md) and [resource administration](RESOURCE_SETUP.md). They are not required preparatory commands for the normal automatic study.

## 6. Reproduce and validate

First-use acquisition may resolve a current upstream database release. The prepared snapshot records its sources and content hashes and is then reused without a refresh. Freeze the actual snapshot, tool environments, resolved run configuration, input hashes and native artifacts. Reinstalling the application alone cannot reproduce different upstream data fetched later.

Normalized imports remain available for independently completed analyses that match the current sample hashes. They must retain their original provenance and be labeled imported. Raw tool tables and missing output files are not completed no-hit evidence. See [the evidence schema](REPORTING.md).

Before biological validation, check that each requested module has either a completed result or a visible failure/hold, and compare reported findings with native outputs. Review known-positive and completed no-hit cases, sequence IDs, coordinates, alignments and available trees. Record measured phenotype evidence only when it exists.

Full native acceptance on a clean collaborator computer and independent biological validation remain pending. Software tests, synthetic demonstrations and successful environment preparation do not establish biological accuracy. See [validation](VALIDATION.md), [collaborator validation](COLLABORATOR_VALIDATION.md) and [reproducibility capture](REPRODUCIBILITY.md).

BaSIS is MIT-licensed. Native tools, databases and model weights retain their own licenses and conditions. The full preset includes separately acquired CRISPRCasFinder (GPL-3.0-or-later), CASFinder models (CC BY-NC-SA 4.0) and Vmatch (separate license); those resources are not relicensed under MIT. See [third-party notices](../THIRD_PARTY_NOTICES.md).
