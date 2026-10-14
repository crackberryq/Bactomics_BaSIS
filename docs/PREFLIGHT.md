# Per-sample preflight and exclusions

BaSIS 0.2.0rc7 adds a saved, offline preflight report and structured diagnostics for each readable sample row. It also corrects the existing report's scope labels: globally disabled modules are no longer counted as explicit study exclusions.

## Review before execution

```bash
basis validate -c study/config.yaml -e -v --report-dir preflight-inputs
basis doctor -c study/config.yaml -d -e -v --report-dir preflight-resources
```

Each report directory must be new. Open its `index.html`. The directory contains the JSON check record and tabular exports for sample outcomes and module scope. The CLI still writes JSON to stdout and progress to stderr. Exit code 2 means preflight found errors; a report is still written for an inspectable failed preflight. Existing report destinations are refused.

`validate` checks annotation consistency and declared scope. `doctor` adds the existing checks for executable or command-prefix availability, declared resource paths and configured resource budgets. A found environment launcher does not verify the tool inside that environment. Neither command runs a native analysis, downloads resources or certifies biological suitability. Optional `-d` checks the configured DeepSafe resources; `-e` includes the extension's declared scope.

## Interpret the states

| Scope state | Meaning |
| --- | --- |
| Planned run | The module is requested for this sample. This is not evidence that it has executed or that resource checks passed. |
| Explicit exclusion | An active per-sample declaration removes this module from requested scope and retains a reason. |
| Held — requested | The module remains requested, but a prerequisite or supported-adapter condition prevents execution. Requested scope remains incomplete. |
| Disabled / extension not requested | The global configuration or omitted extension flag makes the module inactive. This is not an explicit sample exclusion. |
| Not recorded | Saved evidence lacks the relevant decision. Reporting does not invent one. |

Input status and resource-check status are separate from those scope decisions. A failed sample input is visibly failed even when its module configuration says planned run. Runnable modules under `validate` have resource status `not_checked`. Shared errors appear separately and can fail the overall preflight even when an individual sample's inputs passed.

## Deliberate exclusions

Exclusions remain explicit YAML declarations in the study configuration:

```yaml
sample_module_overrides:
  sampleB:
    pathogenfinder2:
      enabled: false
      reason: "Excluded under this study's declared input eligibility criteria"
```

Only `enabled: false` and a nonempty reason are accepted. Unknown sample/module IDs, attempts to enable a module, and added threshold overrides are rejected. The report preserves a stored declaration even when a globally disabled module or an omitted `-e` flag makes it inactive. The report does not activate that declaration or rewrite the configuration.

No sample or module is automatically excluded because a check failed. An explicit exclusion changes the study's requested scope; it is not a successful analysis or a negative biological finding.

## Independent sample diagnostics

The diagnostic reader uses the same row resolver as execution. During preflight, a row with missing files, invalid local metadata or a sequence mismatch is recorded as failed while independent rows are still checked. Invalid or duplicate IDs and malformed table structure remain global failures because the manifest cannot safely identify the work. Invalid exclusion configuration also blocks checks globally.

Execution loading remains strict: this diagnostic continuation does not authorize a partial run or skip bad samples. Correct inputs and rerun preflight before requesting execution. Sequence warnings are shown alongside each sample instead of being buried in a successful record. Missing taxonomy stays unknown.

## Focused PC check

The rc7 target-PC checkpoint passed all eight scenarios. To repeat that historical check, use the rc7 deployment folder and its activated venv:

```bash
python -I BaSIS_check_sample_scope.py
```

The helper creates tiny artificial inputs in a new directory and exercises mixed valid/invalid rows, scope labels, inactive declarations, failed-preflight reporting and rejection cases. It verifies the installed rc7 package, checks that the synthetic source files remain unchanged, and prints `sample-scope-check-summary.zip` for review. It does not use or alter the isolate studies or execute native tools. The report is a software fixture, not a biological result.

The current rc8 bundle supplies a separate run-control checker; see [RUN_CONTROLS.md](RUN_CONTROLS.md). The preflight commands and per-sample report remain available.
