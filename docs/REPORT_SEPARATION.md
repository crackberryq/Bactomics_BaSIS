# Screening and DeepSafe reports

The workflow renders screening evidence and DeepSafe follow-up into separate
offline reports. Report separation changes presentation and report dependencies;
it does not change screening settings, candidate selection or analysis methods.

| Path under an isolate | Contents |
|---|---|
| `safety/report.html` | Screening results, requested module scope and native evidence |
| `safety/evidence.json`, `safety/tables/`, `safety/metadata/` | Screening exports and provenance |
| `safety/deepsafe-report/report.html` | Separate DeepSafe follow-up, with screening evidence retained as source context |
| `safety/deepsafe-report/evidence.json`, `safety/deepsafe-report/tables/`, `safety/deepsafe-report/metadata/` | Follow-up exports and provenance, including family workflow coverage |
| `safety/deepsafe/` | Existing analysis states and native follow-up artifacts; not moved by rendering |

Keep the entire `safety` directory together when sharing local reports. Both
bundles link to saved evidence within that directory. The separate follow-up
bundle is not a self-contained replacement for its referenced analysis files.

## Workflow targets

DeepSafe is disabled by default. For an explicit follow-up that cannot rebuild
screening inputs, use [Followup.smk](REVIEWED_FOLLOWUP.md). The targets below
belong to the integrated `BaSIS.smk` workflow and can build upstream work.

Run from the checkout with the same configuration files and resource limits as
the original run. For example, with a study configuration named `my-study.yaml`:

```bash
snakemake screening_reports --snakefile BaSIS.smk \
  --configfile my-study.yaml \
  --use-conda --cores 4 --resources mem_mb=20000 gpu=0 \
  --rerun-incomplete
```

Use `followup_reports` in place of `screening_reports` to request the separate
DeepSafe report when DeepSafe is enabled. The default target requests screening
reports for all selected isolates, plus separate follow-up reports when enabled.
The explicit follow-up target does not require the screening HTML to exist first.

These are workflow targets: Snakemake can schedule missing or outdated upstream
evidence. They are not render-only commands. Put the target before `--configfile`
so it cannot be consumed as another configuration filename. Add `--dry-run` to
inspect the planned jobs before executing them. The `setup` target still supports
environment/resource preparation without selected samples; report targets require
at least one selected sample.

To disable DeepSafe, use an overlay such as `config.no-deep.yaml`:

```yaml
deep:
  enabled: false
```

Place that overlay last in the configuration list:

```bash
snakemake screening_reports --snakefile BaSIS.smk \
  --configfile my-study.yaml config.no-deep.yaml \
  --use-conda --cores 4 --resources mem_mb=20000 gpu=0 \
  --rerun-incomplete
```

With DeepSafe disabled, the default target requests only screening reports and
`followup_reports` has no work. The screening report omits DeepSafe sections and
exports. Historical DeepSafe files are not deleted and do not become current
screening evidence. To request follow-up later, restore the intended enabled
configuration; an explicitly disabled configuration cannot render a DeepSafe
report.

## Dependency and failure behavior

The screening report has no dependency on DeepSafe evidence or the follow-up
report. Once its own screening evidence and metadata are ready, it can render
while DeepSafe analysis is pending.

There is still a shared setup barrier. `sample_setup_ready` requires every
resource and environment selected by the effective configuration and produces
the metadata configuration used by both reports. Native analysis rules also wait
for setup receipts for every selected isolate. Consequently, a fresh
`screening_reports` invocation with DeepSafe enabled can still prepare DeepSafe
resources and its environment. Report separation does not split that installation
barrier. A final no-DeepSafe overlay removes those DeepSafe-only setup requests.

By default, Snakemake can stop scheduling new jobs after a job fails, including
otherwise independent pending report jobs. Add `--keep-going` to a combined run
to allow independent ready jobs to continue. It does not bypass a failed
prerequisite. A later explicit `screening_reports` invocation can finish the
screening report when its own required evidence is available.

## Render only from saved evidence

The `safety.reporting` CLI reads saved JSON and artifacts and writes report files.
It does not invoke analysis tools, install environments or download resources.
Do not run it concurrently with a workflow writing the same report bundle.

The following Bash example assumes the saved configuration requests only the
two illustrated screening evidence files. Replace the paths and the array with
the exact normalized evidence files requested by your saved configuration:

```bash
safety_root=/absolute/path/to/isolate/safety
screening_evidence=(
  "$safety_root/evidence/amrfinder.json"
  "$safety_root/evidence/minced.json"
)

PYTHONPATH="$PWD/workflow/lib:$PWD/workflow/vendor" python -m safety.reporting \
  --sample "$safety_root/metadata/sample.json" \
  --config "$safety_root/metadata/config.json" \
  --evidence "${screening_evidence[@]}" \
  --outdir "$safety_root" --view screening --evidence-root "$safety_root"
```

Run from the checkout in the workflow controller environment. Supply all required
screening evidence; missing requested evidence is an error. Do not glob every
historical JSON file or include `deepsafe.json` in the screening view.

For an existing DeepSafe evidence record and an enabled saved configuration:

```bash
PYTHONPATH="$PWD/workflow/lib:$PWD/workflow/vendor" python -m safety.reporting \
  --sample "$safety_root/metadata/sample.json" \
  --config "$safety_root/metadata/config.json" \
  --evidence "${screening_evidence[@]}" "$safety_root/evidence/deepsafe.json" \
  --outdir "$safety_root/deepsafe-report" --view deep \
  --evidence-root "$safety_root"
```

The CLI retains `--view combined` as its compatibility default. Use the explicit
`screening` or `deep` view shown above for separate reports. The workflow rules
already do so. For a separate follow-up bundle, the evidence root must be its
parent `safety` directory; arbitrary external artifact roots are rejected.

## Status and interpretation

The follow-up budget defaults to five unique candidates per sample. Unselected
records remain in a **Requires attention** section with their recorded execution
state and the reason they were deferred. A budget deferral means follow-up was
not scheduled; it is not a negative screening result. See [AWARE](AWARE.md) for
the independent admission policy and configuration overrides.

The reports preserve requested, disabled, not assessed, incomplete and failed
states. A disabled or unassessed module is not a negative finding. The screening
renderer rejects failed, blocked or not-started requested screening evidence
instead of presenting it as a completed result.

For review of saved partial work, the separate DeepSafe renderer can accept a
failed, blocked or not-started DeepSafe parent record. Child evidence from a
noncompleted parent remains visibly provisional; successful child output does
not make its parent complete. This requires an existing valid DeepSafe evidence
record, and does not synthesize one from an interrupted directory. The normal
Snakemake target may still require completion of its upstream jobs.

Completion describes the requested computational scope. Neither report issues a
scientific safety verdict, confirms a resistance phenotype, or authorizes field
deployment. Findings and follow-up evidence are supplied for qualified review.

## Links between the two reports

At render time, a report links to an already-rendered counterpart only when its
sample/configuration hashes and screening evidence hashes match. The counterpart
HTML and evidence must also match their recorded hashes. Missing, stale, edited
or malformed counterparts receive no link.

This check does not add a dependency between reports. If the other report appears
later, re-render the first report to refresh its navigation; an existing report
does not automatically update. The two report paths remain usable directly.

Reviewed `Followup.smk` executions deliberately use a separate effective
configuration. Its hash differs from the original screening configuration, so
the optional matching-configuration cross-link is absent. Both report paths
remain valid, and `safety/deepsafe/review/config.json` records the source snapshot.
