# Preparing BaSIS AWARE 0.4.0b1

**Status: beta preparation, not a published beta. Updated 2026-10-29.**

The beta scope is the existing v0.4.0a2 workflow and readable reports. Batch
comparison and additional reporting features are deferred. Changes during this
phase address confirmed defects, acceptance evidence and release packaging.
The [beta promotion gate](ACCEPTANCE.md#beta-promotion-gate) remains applicable.

## Recorded evidence

The published baseline is v0.4.0a2 at
`14f161a1f252a712053892961edf2275469c8dab`. Bactologix v0.1.0a44 supports that
exact commit. Its installation compatibility does not establish native tool
execution or the source version used by an older saved run.

| Check | Evidence available | Remaining acceptance work |
|---|---|---|
| Source, citation and release identity | [BaSIS release](https://github.com/crackberryq/Bactomics_BaSIS/releases/tag/v0.4.0a2) | Record the final beta commit and matching metadata |
| Python 3.11/3.12, report browser and Conda deployment/recovery | [BaSIS checks](https://github.com/crackberryq/Bactomics_BaSIS/actions/runs/40883511632) passed on the baseline | Repeat required CI on the final beta commit |
| Workflow failure/resume, unchanged rerun and report-only refresh | Covered by synthetic orchestration acceptance in the checks above | Retain the corresponding native pilot records |
| Bactologix adapter, application browser and environment deployment | [Bactologix checks](https://github.com/crackberryq/Bactologix/actions/runs/40885417060) passed with the baseline | Support and test the exact beta release commit |
| Clean installation and complete native pilot | No reviewable native acceptance bundle is recorded here | Retain platform, run commit, configuration, setup/execution logs, results and resource records |
| Native no-op rerun, interruption/resume and resource reuse | Not established by the synthetic fixtures | Retain before/after observations and execution logs from the pilot |
| Separate screening/follow-up reports and optional follow-up | Software checks exist; native follow-up acceptance remains unrecorded | Record exercised scope and preserve screening files; label experimental coverage explicitly |

A successful application update, a report that opens, or a report-only refresh
is useful evidence for that operation. It does not by itself demonstrate a
complete native run on the new source. Record the exact run commit and distinguish
user observations from inspected execution evidence.

## Complete the acceptance record

Use the existing [native pilot checklist](ACCEPTANCE.md#native-pilot-acceptance).
Keep the pilot configuration fixed and retain the original result bundle.
Record which platform and optional modules were exercised; untested scope stays
explicit. Do not report a zero-candidate follow-up as native follow-up coverage.

For a Bactologix pilot, the completed execution job's reviewed support snapshot
and log are the starting point. The snapshot must identify the selected job and
its saved run commit. If its bounded excerpt omits setup or completion details,
retain the relevant job log as well. The configured current installation can
differ from the one that produced a saved run. Keep raw sample data local;
publish only the reviewed validation material intended for release evidence.

Record each outcome as passed, failed or not yet checked, with its evidence
location. Fix a confirmed software defect and repeat the affected check before
counting it as passed. The reproducible validation bundle is described in
[acceptance](ACCEPTANCE.md#reproducible-validation-bundle).

## Publish after acceptance

Once the documented gate is met, update the standalone version, runtime metadata,
citation, vendored-source metadata and release-specific notes to `0.4.0b1`.
Review the acceptance evidence with that release change. The publication helper
supports explicitly versioned `0.4.0bN` releases, names them **Research beta**
and keeps GitHub's prerelease flag enabled. It runs only after required CI and
retains the exact-commit and immutable-tag checks used for alpha publication.
The helper checks release identity; it does not independently certify a native
pilot or scientific validation. This preparation does not bump the version or
publish a beta automatically.

After publication, update Bactologix's reviewed adapter reference and run its
checks against the exact beta commit before publishing the compatible app
update. Existing frozen runs retain their original source bindings.

Independent scientific validation can continue during a clearly labelled
research beta, as described in the existing acceptance policy. Software beta
status does not establish diagnostic accuracy or organism safety.
