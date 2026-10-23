# Saved IntegronFinder result recovery

The old standalone verifier required one summary row for every input record.
IntegronFinder can exit successfully after explicitly skipping a record because
its native protein file is empty. Such a record has not been assessed; it is not
a negative finding and the warning alone does not establish why the file is empty.

The dedicated worker reconciles summary rows, processing and result-writing
messages, and paired empty-file/skip warnings for the same record and attempt.
It records coverage under `metrics.contig_assessment`, retains findings from
assessed records, and sets `scope_incomplete: true` for partial coverage. With no
findings in a partial result, interpretation remains `inconclusive`. The report
lists the module as incomplete even though native execution completed. Arbitrary
missing rows, malformed tables, unknown skips and native errors still fail.
An entirely unassessed run with no summary rows remains an error.

## Recover an existing run

Stop Snakemake and wait for its workers before updating or applying recovery.
Keep the original ordered configuration overlays and thread allocation.
Use the current `snakemake-standalone` branch, then preview:

```bash
python scripts/recover_integron_results.py --configfile "${cfg[@]}"
```

Here `cfg` is the shell array containing the configuration files used for this
run, in their original order. To publish the verified saved results:

```bash
python scripts/recover_integron_results.py --configfile "${cfg[@]}" --apply
```

For one sample, use `--isolate /absolute/path/to/isolate` instead of `--configfile`.
The batch command also migrates existing completed IntegronFinder results to the
new worker identity. This avoids rerunning those samples solely because the
verifier changed. Samples with no saved attempt are left for normal execution.

Recovery verifies the original successful command, saved input/configuration,
executable, environment, resource receipts and unchanged legacy source hashes.
It accepts the specific old summary-coverage failure or a verified completed
result, then applies the new checks. It does not execute native commands,
download resources, delete original results or bypass unexplained failures.
Configuration files select the batch; saved per-module metadata supplies the
execution identity. Changes to inputs, settings or allocation on resume can
legitimately require new work.

A compact snapshot contains final tables, stdout/stderr, the recorded command
and new parsed evidence. All original intermediate files remain in the original
attempt, whose path and identity are recorded in `recovery.json`. Publication is
protected by the existing result-store lock. A failed copy leaves originals and
existing completion receipts intact and can be retried. The report is regenerated
by the ordinary workflow, not by the recovery script.

After successful recovery, resume the original Snakemake command with
`--rerun-incomplete`. Retain its existing cores/resources/configuration options.
No results-directory cleanup, forced full rerun, or database reinstallation is
needed for this specific verifier defect. Other unfinished jobs will still run.
