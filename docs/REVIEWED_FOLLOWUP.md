# Explicit follow-up from saved screening results

Standard runs default to `deep.enabled: false`. Existing study overlays that
explicitly enable it retain their behavior. To prevent an older overlay from
enabling DeepSafe during screening, place a final `deep: {enabled: false}` overlay
last in the configuration list. Disabling it does not delete historical results.

`Followup.smk` is a separate, single-isolate workflow. It includes DeepSafe stages
and the separate follow-up renderer, but no native screening, input-generation,
screening-report, setup, or resource-preparation rules. A missing screening
prerequisite cannot cause a screening tool to run.

## Review and launch

1. Complete screening with `BaSIS.smk` and review `safety/report.html`.
2. Copy `config/deepsafe-followup.example.yaml` to a study-specific configuration.
   It starts disabled. Review the inherited settings and explicitly set
   `deep.enabled: true` when requesting follow-up. Maximum numeric thresholds
   are not an off switch. Existing admission settings and the default
   five-candidate computing ceiling remain unchanged.
3. Supply already prepared resource paths or existing `resource_receipts` when
   absent from the saved configuration. Preparation remains a separate setup
   operation. This entry point does not download or rebuild databases.
   Snakemake's `--use-conda` can still install the DeepSafe environment if absent,
   and existing analysis-level reference retrieval behavior remains governed by
   the inherited configuration.
4. Start an explicit command. Editing YAML alone does not launch anything.

From the checkout in the controller environment:

```bash
snakemake --snakefile Followup.smk \
  --configfile config.my-followup.yaml \
  --config isolate=/absolute/path/to/reviewed-isolate \
  --use-conda --cores 4 --resources mem_mb=20000 \
  --rerun-incomplete
```

Add `--dry-run` to validate prerequisites and inspect the initial DAG. Candidate
jobs are discovered after the existing preparation checkpoint. Use a separate
invocation for each reviewed isolate. Do not mix screening study YAML into this
command: unsupported top-level settings are rejected. Supported keys are
`isolate`, `deep`, `resource_receipts`, `threads`, and `memory_mb`. Paths are
relative to the checkout.

## Reuse and provenance contract

The entry point requires a screening-only report produced by this renderer,
its navigation manifest, normalized source evidence, sample/configuration
metadata, input validation, and completed setup receipt. It verifies report and
source hashes, exact module/sample identity, requested scope, and current
annotation checksums. Missing, stale, failed or incompatible prerequisites stop
the invocation. Explicit assessment holds remain visible. Older report bundles
must be refreshed separately with the saved-evidence renderer.

Original screening reports, native results and screening configuration are
read-only inputs. Effective follow-up configuration and source checksums are
recorded separately in:

- `safety/deepsafe/review/config.json`
- `safety/deepsafe/review/inputs.json`

The report remains `safety/deepsafe-report/report.html`; analysis states remain
under `safety/deepsafe/` and final evidence at `safety/evidence/deepsafe.json`.
These follow-up outputs are refreshed when configuration changes; this is not a
versioned archive of every prior follow-up. Screening results are not deleted.
Do not edit source inputs/configuration or update code during an active run.

The configuration is an explicit research choice, not validation of the selected
policy. Eligibility, ordering and scientific analysis are unchanged by this
entry point. It introduces no tool-margin ranking or new gene-specific rules.
Unselected records retain their evidence and `Requires attention` reasons.
