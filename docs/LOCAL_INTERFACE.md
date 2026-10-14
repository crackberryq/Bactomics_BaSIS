# Local BaSIS interface (0.3.0rc1)

The optional interface runs on Linux or WSL2. Analyses use the same installed
BaSIS CLI and adapters. The browser is a control panel for this computer, not
a cloud computation service. Close and reopen it without restarting analyses.

Install the release wheel with the `ui` and `excel` extras, then run:

```bash
basis gui --workspace ~/bactomics/basis-workspace
```

Open `http://localhost:8501`. The service binds only to loopback. This release
is intended for one trusted local user; it is not a multi-user hosted service.
Do not expose it through a public proxy. Configuration files are trusted local
analysis instructions, including executable and command-prefix settings.

## Workflow

1. **Start here:** follow four guided steps or run a labelled synthetic demo through Snakemake.
2. **Tool environments:** preview, create and verify isolated Conda environments
   through the main Snakemake workflow in setup mode. Existing named prefixes are
   preserved and checked; new managed environments use Snakemake cache names.
   Recorded verification is distinguished from unverified folder presence. Recipes
   are specifications, not solved platform locks.
3. **Databases & models:** select a dedicated resource recipe and its named
   resources, preview the plan, then explicitly allow preparation. Inspect the
   supported backend and immutable destination for each selected resource.
4. **Add samples:** preview/import Excel, CSV or TSV using existing batch rules.
   A local sheet retains its original relative-path base. An uploaded sheet
   needs absolute input paths, or exact sample-name discovery with an explicit
   annotation root. Select the worksheet for multi-sheet workbooks.
5. **Readiness & run:** select a config, review module modes/exclusions, choose DeepSafe and
   Extend, and review CPU/RAM/GPU budgets loaded from the config. Tool-specific
   device, threads and memory settings remain in that config. A new job gets a
   resolved config snapshot, private results directory and private placement cache.
   Snakemake owns input/resource checks, module execution, nine DeepSafe stages per
   sample, consolidation, reporting and provenance. The serial local alternative
   is under Advanced.
6. **Progress & jobs:** reconnect to saved jobs, read live scheduler/worker logs and inspect the exact
   command, version, source-config checksum and timestamps. One job runs per
   workspace. A second identical submission reconnects; select **New attempt**
   to retry a finished job or recheck changed inputs/resources. Config/sheet
   hashes alone are not proof that referenced resources remain unchanged.
7. **Explore results:** open existing rich reports and download evidence,
   tables, methods and citations. For older control panels, explicitly add
   the related result directories so sibling links can be served. Broad
   parent folders are unnecessary. A new control panel can be built from an
   explicit list of results. The report server never derives permissions from
   saved paths in evidence files.

Execution outcomes preserve BaSIS semantics: `incomplete` may include deferred
candidates. The report separately shows selected-candidate execution and
interpretation. Model scores and phylogenetic support are not safety verdicts.

## Installation and resource boundaries

`pip` installs Python components, including Snakemake. Conda and GPU drivers are
separate prerequisites. Supported native rules automatically deploy their Conda
recipe when no explicit command prefix is set. Existing prefixes remain external
under the default `auto` policy. Use the setup environment parent for existing
named environments, or configure `execution.conda_prefix` as the analysis cache
parent; actual verified prefixes are shown in setup records.

Resource preparation requires an explicit recipe and execution request. Supported
backends and their version/checksum requirements are documented in
[resource setup](RESOURCE_SETUP.md). Normal analysis never refreshes databases;
opening the interface performs no installation or download. Not every database or
model is automatically prepared, and raw pinned files are not analysis-ready
indexes. Run readiness checks with the study's actual paths after preparation.
MLST/CRISPRCasFinder remain normalized-import capabilities.

Closing the interface does not stop jobs. Keep the computer awake; a reboot
interrupts running analyses. Interrupted jobs are recorded as interrupted,
never as successfully completed. This version offers new-attempt retries,
not restoration of a process after reboot. Deep stage JSON supports restart
within one workflow attempt; a new GUI submission does not promise partial-stage
reuse across attempts. Completed whole-result caches are verified separately.
Snakemake schedules independent modules and
samples within declared budgets; the local alternative remains serial. Budgets
are scheduling declarations, not operating-system memory limits. See [orchestration](ORCHESTRATION.md).

## Reproducibility and publication

Use the reproducibility-audit action to capture the selected original config
and its resource identities/gaps. The action does not copy large resources or
prove restoration on a new machine. Run records carry the actual version and
configuration provenance. Citations include Jimi Goldstein's user-supplied,
checksum-valid ORCID: https://orcid.org/0000-0002-7829-0090.

The release is locally pip-installable. Public PyPI publication is pending
publisher configuration and upload. The approved repository name is
`crackberryq/Bactomics_BaSIS`; BaSIS software uses the MIT license.
Do not describe `pip install bactromics-basis` as available on PyPI until a
release has actually been published and independently installed from there.

The `0.2.0rc16` baseline is preserved separately. Version `0.3.0rc1` changes
workflow ownership while retaining scientific thresholds and interpretation
policies. It does not establish independent biological validation.
