# Linux-side CRISPR result storage and interrupted publication recovery

The whole-assembly worker executes its native command in `/tmp`, then retains
the raw files in its result store and verifies them. When the store is under a
Windows-mounted sample directory, that retention step becomes a recursive
cross-filesystem copy. Subsequent verification also opens many small files on
the Windows filesystem. A native command can finish while the enclosing
Snakemake job remains busy copying or verifying its output.

`commands.log` records native-command completion, not completion of the entire
Snakemake job. A successful native exit still requires the existing output
checks before a scheduler completion receipt is published. The worker retains
its declared scheduler resources throughout these phases.

## Select a Linux result store

Append an overlay such as `config/local-results.example.yaml` after the existing
configuration files:

```yaml
storage:
  native_results_directory: ~/bactomics/basis-native-results
```

The configured root contains a SHA256 namespace derived from each canonical
isolate-directory path, then a `crisprcasfinder` subdirectory. Samples with the
same basename in different directories have different stores. Relative roots
are resolved against the workflow root; `~` expands to the current user's home.
The helper does not move existing result folders.

This setting currently controls the CRISPR assembly result store only. Shared
databases, Conda environments, input annotations and other modules retain their
existing locations. The small sample-facing completion receipt and native-result
link remain in the sample's `safety` directory. The retained payload itself is
on Linux, so moving it from `/tmp` can use a same-filesystem rename when both
locations share a filesystem. Verification then reads the Linux payload.

Without the overlay, existing storage behavior is preserved. An existing
completion receipt pins its recorded store, including on later retries, so
adding this overlay does not itself copy or recompute a completed result.
New placement applies to samples without a completion receipt, or to results
explicitly recovered into the new store. The top-level storage setting is not
part of effective scientific configuration or execution identity.

## Recover a completed native command whose publication was interrupted

First stop the workflow and wait for all its worker processes to exit. Do not
delete the sample's `safety` folder, the partial attempt, or the matching
`/tmp/basis-ccf-assembly-*` directory. Do not reboot before recovery; the raw
temporary directory may be removed during a reboot. Update the checkout only
after workers have stopped: execution identities bind source-file contents.

Use exactly the same ordered overlays for recovery and the subsequent workflow
resume, including the Linux result-store overlay. Preview selected samples:

```bash
python scripts/recover_ccf_run.py --configfile \
  study.yaml strict-cuda.yaml local-resources.yaml local-results.yaml
```

Apply the recovery after reviewing the preview:

```bash
python scripts/recover_ccf_run.py --configfile \
  study.yaml strict-cuda.yaml local-resources.yaml local-results.yaml --apply
```

The helper checks the original attempt and successful command record, matches
the current inputs and execution identity, and verifies the raw output using
the unchanged verifier. It copies recoverable raw output into the Linux store
and publishes a completion receipt only after verification succeeds. It never
invokes the native analysis program. Recovery reports its stages as they run.
The original temporary directory and partial Windows copy remain untouched.

The helper refuses active writers, incompatible inputs or code, unsafe storage
locations, and incomplete raw output. It does not convert these failures into
successful or negative findings. Missing native completion is reported for the
normal workflow to handle; it is not silently replaced by an older attempt.
Already completed results remain in their recorded stores. Do not combine
pieces from the temporary and partial destination trees manually.

Resume with the same configuration files and normal `--rerun-incomplete` flag.
Snakemake may schedule a recovered owner rule because of incomplete metadata or
changed storage parameters. A matching recovered cache is then verified and
reused without repeating the native command. Independently interrupted or
never-started jobs still need to run. No blanket deletion or metadata cleanup is
required.

For one explicitly identified attempt, `scripts/recover_ccf_results.py --help`
provides the lower-level path arguments. Recovery is intentionally limited to
retained raw output from the unchanged whole-assembly execution contract; it
does not adopt older per-contig results or bypass provenance checks.
