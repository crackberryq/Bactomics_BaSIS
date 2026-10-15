# Local BaSIS interface (0.3.0rc4)

The optional interface runs on Linux or WSL2. Analyses use the same installed
BaSIS CLI, Snakemake workflow and adapters. The browser controls this computer;
it is not a cloud computation service.

Install BaSIS with the `ui`, `excel` and `extend` extras as described in
[installation](INSTALL.md), then start the interface:

```bash
basis gui --workspace ~/bactomics/basis-workspace
```

Open `http://localhost:8501`. The service binds to loopback and is intended for
one trusted local user. Configuration files are trusted local analysis
instructions, including executable and command-prefix settings.

## Four-step workflow

**Start here** shows the four steps and an optional labelled synthetic demo.
A fresh study does not need a separate tool-installation or database-setup step.

1. **Add samples:** preview/import Excel, CSV or TSV. A local sheet retains its
   original relative-path base. An uploaded sheet needs absolute input paths,
   or exact sample-name discovery with an explicit annotation root. Select the
   worksheet for multi-sheet workbooks. A fresh import without a base config
   creates the full preset with automatic first-run resources. Existing base
   configurations retain their declared resources, module modes and thresholds.
2. **Run analysis:** choose the study config and whether to include DeepSafe and
   Extend. **Start analysis** runs input checks and lets Snakemake prepare missing
   requested environments, databases and models before the dependent analyses.
   **Check readiness** is an optional read-only preview; resources awaiting
   preparation are reported as planned, not downloaded or verified. Advanced
   settings expose CPU/RAM/GPU scheduling budgets, deployment and engine choices.
3. **Progress & jobs:** follow live scheduler/worker logs and inspect the exact
   command, software version, source-config checksum and timestamps. One job runs
   per workspace. Repeated identical submissions reconnect to the saved job;
   choose another attempt explicitly to retry finished work. Each analysis gets
   its own resolved config snapshot, results directory and placement cache.
4. **Explore results:** open rich reports and download evidence, tables, methods
   and citations. Build a control panel from an explicit list of results. For
   older panels, add the related result directories to serve sibling links;
   broad parent folders are unnecessary. The report server never derives access
   permissions from paths stored in evidence files.

Snakemake owns input/resource checks, native module execution, nine DeepSafe
stages per sample, consolidation, reporting and provenance. Completion and
interpretation remain separate: an overall `incomplete` scope can include
intentionally deferred candidates, while all selected candidates completed.
Model scores and phylogenetic support are not organism-safety verdicts.

## Automatic first-run preparation

`pip` installs the Python application and Snakemake. Conda must already be
available; GPU drivers are installed outside BaSIS. Merely opening the interface
or importing samples performs no installation or biological analysis.

For a fresh full-preset study, starting an analysis authorizes the normal workflow
to deploy required Conda environments and prepare requested resources. Initial
downloads and indexing can need substantial network access, disk space and time.
The resource cache defaults to `~/.cache/bactromics-basis` (or the corresponding
`XDG_CACHE_HOME` location); `resources.directory` selects another location.
Source identities, resolved versions and file checksums are recorded on first
successful preparation. Verified snapshots are reused without automatic updates;
missing or altered published snapshot files fail verification rather than silently
refreshing. This records the downloaded content, not independent certification
of its scientific curation.

Explicit tool prefixes and resource paths in existing configurations remain in
use. Existing manual configurations are not silently converted to automatic
resources. The full preset includes native MLST and CRISPRCasFinder alongside
the other modules; their input and scope requirements still apply. MLST with no
assigned scheme or sequence type is a completed, inconclusive typing result.
CRISPRCasFinder distinguishes component candidates from typed Cas systems.

With Extend selected, a full automatic study prepares the pinned local ProtT5
model and classifier resources. The default `execution.device: auto` probes
available NVIDIA capacity, then verifies CUDA inside the inference environment;
CPU fallback is recorded when CUDA is unavailable. GPU scheduling budgets alone
do not establish the device used. Inspect the inference-device record and logs.
BaSIS does not install or upgrade GPU drivers.

**Advanced · tool environments** and **Advanced · databases & models** remain
available for explicit preparation, custom resources and inspection. They are
optional administration pages, not prerequisites for the four-step workflow.
See [resource setup](RESOURCE_SETUP.md) for the separate recipe-driven commands.
The serial local engine requires existing tools and manually prepared resources;
automatic first-run preparation requires Snakemake.

## Persistent jobs and reproducibility

Closing the browser does not stop a job. A reboot interrupts running analyses;
such jobs are recorded as interrupted, never completed. New-attempt retries do
not restore a terminated process. Deep stage JSON supports restart within one
workflow attempt; a new GUI submission does not promise partial-stage reuse
across attempts. Completed whole-result caches are verified separately.

Snakemake schedules independent modules and samples within declared budgets.
Those budgets are scheduling declarations, not operating-system memory limits.
See [orchestration](ORCHESTRATION.md).

The reproducibility-audit action captures the selected original configuration
and its resource identities or gaps. It does not copy large resources or prove
restoration on another computer. Run records retain actual versions and config
provenance. Citations include Jimi Goldstein's supplied ORCID:
https://orcid.org/0000-0002-7829-0090.

BaSIS uses the MIT license; third-party tools, models and databases retain their
own terms. GitHub installation instructions are in [installation](INSTALL.md).
Do not assume a PyPI release exists unless it has been published and verified.
The rc2 software checks and earlier pilot runs are separate from clean-machine
native acceptance and independent biological validation, which remain pending.
