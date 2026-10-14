# Local BaSIS interface (rc14)

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

1. **Overview:** run a clearly labelled synthetic demo.
2. **Setup:** preview or create isolated Conda environments. Existing prefixes
   are preserved. Recipes are specifications, not solved platform locks.
   Run preflight against your study's actual database/model configuration.
3. **Samples:** preview/import Excel, CSV or TSV using existing batch rules.
   A local sheet retains its original relative-path base. An uploaded sheet
   needs absolute input paths, or exact sample-name discovery with an explicit
   annotation root. Select the worksheet for multi-sheet workbooks.
4. **Run:** select a config, review module modes/exclusions, choose DeepSafe and
   Extend, and review CPU/RAM/GPU budgets loaded from the config. Tool-specific
   device, threads and memory settings remain in that config. A new job gets a
   resolved config snapshot, private results directory and private placement cache.
5. **Jobs:** reconnect to saved jobs, read updating logs and inspect the exact
   command, version, source-config checksum and timestamps. One job runs per
   workspace. A second identical submission reconnects; select **New attempt**
   to retry a finished job or recheck changed inputs/resources. Config/sheet
   hashes alone are not proof that referenced resources remain unchanged.
6. **Results & export:** open existing rich reports and download evidence,
   tables, methods and citations. For older control panels, explicitly add
   the related result directories so sibling links can be served. Broad
   parent folders are unnecessary. A new control panel can be built from an
   explicit list of results. The report server never derives permissions from
   saved paths in evidence files.

Execution outcomes preserve BaSIS semantics: `incomplete` may include deferred
candidates. The report separately shows selected-candidate execution and
interpretation. Model scores and phylogenetic support are not safety verdicts.

## Installation and resource boundaries

`pip` installs Python components. It does not install Conda, GPU drivers,
native tools, databases or models. Setup supports environment creation and
resource preflight; automatic acquisition/configuration of every database is
not included in rc14. Existing configured resources and captured restoration
procedures remain usable. No downloads occur simply by opening the interface.
The RGI, antiSMASH and MOB-suite recipes are now included in the wheel.
Unsupported MLST/CRISPRCasFinder adapters remain explicit scope holds.

Closing the interface does not stop jobs. Keep the computer awake; a reboot
interrupts running analyses. Interrupted jobs are recorded as interrupted,
never as successfully completed. This version offers new-attempt retries,
not process checkpoint restoration. The local engine remains serial and
budgets are scheduling declarations, not operating-system memory limits.

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

The rc13 software-validation baseline is preserved. rc14 adds the interface
and deployment plumbing; it does not add independent biological validation.
