# Native screening pilot: 2026-10-29

**Scope: one AMRFinder-only workflow and an unchanged rerun, invoked through
Bactologix.** This is a scoped acceptance record for BaSIS v0.4.0a2, source
`14f161a1f252a712053892961edf2275469c8dab`. It does not complete the full
[beta promotion gate](../ACCEPTANCE.md#beta-promotion-gate).

The maintainer supplied the original execution log and the second invocation's
log text for inspection. The two records identify the same saved run and source
commit. This public summary omits local paths, sample/run identifiers and host
names. The original logs and result bundle remain with the maintainer.

## Native execution

The original execution started at `2026-10-29T14:04:55+00:00`. Its supplied log
is 9,561 bytes with SHA-256:

```text
95c5e4602b22c44d12c931fe969ed628f61b4aee3e33d997ee2549c33988402b
```

The recorded DAG contains one native analysis rule, `amrfinder`, alongside
configuration capture, input validation, environment/resource verification,
setup, evidence normalization and screening report generation. It finishes
`10 of 10 steps (100%) done`. No error is present in the supplied log.

| Outcome | Observation |
|---|---|
| Input validation and setup | `validate_inputs`, `capture_environment`, `prepare_resource` and `sample_setup_ready` completed |
| Native AMRFinder execution | `amrfinder` completed after the native-output verification step was invoked |
| Normalized evidence | `normalize_native` completed |
| Report generation | `safety_report` completed and the final `all` target completed |

These observations establish logged execution success for this selected scope.
The ten workflow steps are not ten analysis tools. The native output contents,
finding counts, report links and scientific interpretation were not established
by inspecting this execution log.

## Unchanged rerun

The second invocation started at `2026-10-29T15:10:57+00:00`. It identifies the
same run, source commit, saved configuration path and execution command. After
building the DAG, Snakemake reports:

```text
Nothing to be done (all requested files are present and up to date).
```

**Result: the native unchanged-rerun scheduling check passed for this run.**
No workflow jobs were scheduled. This supports reuse of the completed requested
work; it does not establish byte-for-byte preservation of every output because
no before/after file inventory was supplied. Both invocations recorded the
policy to reuse matching installed environments with automatic installation
and repair disabled.

## Remaining scope

Other native analysis modules, clean installation, controlled native
interruption/resume, report-only refresh on this native bundle, detailed report
contents/links, optional follow-up, and measured CPU/memory/storage use remain
outside these two logs' verified scope. Input checking was described in the
application log as size/mtime checking; input content hashes were not inspected
here. Neither log establishes scientific accuracy or organism safety.

Keep these observations distinct from synthetic CI coverage and from later
tests. A final beta release still needs matching release metadata, exact-commit
CI and Bactologix adapter checks, together with the applicable native acceptance
evidence recorded in [beta preparation](../BETA_READINESS.md).
