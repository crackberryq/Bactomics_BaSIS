# Resource preservation, execution and recovery

This infrastructure update changes preparation, logging and scheduling. Native
analysis settings, supplied contig coverage, AWARE admission settings and reporting
interpretation are preserved. CRISPRCasFinder now receives the entire assembly
in one outer invocation, as in the original Safety Extend Snakefile. Synthetic tests establish software contracts;
they do not measure biological accuracy or promise a particular runtime gain.

## Resource lifecycle

The resource rule declares only `<resources>/<name>/provenance.json` as its
output. New automatic preparations use unique payload paths below
`<resources>/snapshots/<name>/<request-key>/attempt-*/data`. A completed cache
record is published only after verification. A managed `<resources>/<name>/data`
link points to the completed payload, preserving the native command paths.
Failed jobs cannot delete the snapshot through Snakemake output cleanup.

Reuse requires matching preparation settings, provider implementation, explicit
source files, relevant dependency declarations and semantic environment content.
Allocation changes and environment capture timestamps do not change that
identity. A source edit to shared setup/configuration code no longer recaptures
every environment. Provider implementation or installation changes can still
invalidate a snapshot; whole-file provider source hashes are conservative.

A repeated resource job verifies the saved inventory, including checksums and
directory membership, then restores its original completion record. A corrupted
snapshot fails verification. Rebuilding a different request never overwrites a
previous successful payload. Failed attempts retain their request, files and
failure diagnostic. Resource locks prevent competing publication for one name.

External paths continue to work with `resources.policy: manual`. Source files
are read and verified in place. This policy disables automatic resource
preparation downloads; it is not a network firewall for Conda or arbitrary
third-party tools. Optional DeepSafe reference retrieval is separately governed
by `deep.placement_allow_download`.

## Downloads and logs

The runner streams captured stdout and stderr into its command log. Core native
commands retain their existing redirections: their stdout/stderr files update
during execution and are copied into the command log on exit. Heartbeats remain
live in that log. Stdout stays separate when a parser needs the return text, and
commands that write stdout into a result file keep that behaviour. A log
write failure, elapsed-time timeout or controller interruption stops the native
process group created for that command, including descendants holding its pipes.
Snakemake owns the enclosing worker and workflow shutdown.

`timeout_seconds` and `heartbeat_seconds` are finite positive module settings,
defaulting to 14,400 and 30 seconds. Logs record command start, PID, periodic
elapsed-time heartbeats and final status. A heartbeat confirms that the worker
is waiting; it is not a biological progress estimate. Core native jobs retain
`commands.log` and `commands.json` beside each attempt's `data` directory.
PathogenFinder2 embedding and classification have separate outer deadlines,
including their in-process Python work, and `.runtime.log`/`.runtime.json`
diagnostics in `safety/logs/`. DeepSafe native commands use its configured runtime
settings. Deadlines apply to commands, not to the total workflow duration.

geNomad uses its installed version and the official compatibility table to
select the database release. Its downloader checks advertised transfer length,
reads the complete gzip stream and tar inventory, checks the selected version,
and validates extraction paths. A completed archive and its checksum record
are published together under `<resources>/downloads/genomad/`. A verified
archive is reusable after extraction failure. New automatic preparation may
still request the small compatibility table.

Transient transfer failures receive at most three attempts. Partial files and
diagnostics are retained; retries begin from zero. HTTP byte-range resumption is
not implemented. Permission, disk-full and unsafe-archive errors are not treated
as transient retries. MOB-suite still uses its own native initializer and mirror
logic; this update does not promise faster remote servers or change its retry
policy. Durable snapshot reuse prevents unnecessary repeated initialization.

## Execution units and remaining job counts

The old standalone rules split CRISPRCasFinder into one invocation per contig.
The other screening modules already receive the full selected sample input:

| Modules | Input per analysis invocation |
| --- | --- |
| antiSMASH, geNomad, MOB-suite, IntegronFinder, ISEScan, MinCED, MLST | Complete multi-record assembly |
| AMRFinder | Complete assembly, proteins and GFF |
| RGI | Complete proteins, or complete assembly when `input_type: contig` |
| VFDB, BacMet | Complete proteins |
| ICEscreen | Complete annotated GenBank |
| PathogenFinder2 | Complete proteome embedding, then sample-level classification |
| CRISPRCasFinder, after this update | Complete multi-record assembly |
| DeepSafe | Selected candidate across six scheduled phases |

RGI's `contig` input type does not mean one scheduled job per contig. Native
programs can still loop over records internally. DeepSafe's six candidate phases
are domains, phylogeny, patterns, profiles, motifs and placement; some record a
skip without a native call. For 100 selected candidates these produce 600 phase
jobs plus sample-level preparation and consolidation. That is a separate source
of job volume, unaffected by whole-assembly CRISPR execution. Its contribution to
elapsed runtime needs measurement before changing phase granularity.

See the [native command builder](../workflow/lib/safety/native.py),
[Extend rules](../workflow/rules/extend.smk) and
[DeepSafe rules](../workflow/rules/deep.smk) for the execution boundaries.

## Whole-assembly CRISPRCasFinder execution

One `crisprcasfinder_assembly` job stages the complete multi-FASTA assembly with
safe identifiers and invokes the pinned native program once per enabled isolate.
The native program still processes records internally, including its individual
Prodigal/MacSyFinder calls. There is no nested scheduler. This removes thousands
of outer worker launches and scheduler jobs for fragmented inputs; it does not
remove all record-level native work or establish an end-to-end runtime bound.

`modules.crisprcasfinder.execution_scope` is `assembly`. An old `batch_size`
setting is ignored and removed from effective configuration. Legacy batch helpers
remain for historical fixture coverage but are not part of the active DAG.

Verification requires exact input/output record coverage, matching sequences and
lengths, per-record native artifacts and counts, and one matching aggregate count.
Every actual MacSyFinder invocation has a separate isolation audit bound to its
input and output. A missing, duplicated or failed record cannot silently become a
negative result. The existing scientific parsers, source/model pins, thresholds
and coordinate checks are retained. The old Snakefile's failure-suppression
behaviour is not restored.

A successful assembly publishes `safety/completion/crisprcasfinder.json` and a
verified payload under `safety/results-cache/crisprcasfinder/`. The conventional
`safety/native/crisprcasfinder` path points to that payload, including `parsed.json`.
A matching completed assembly is reusable after receipt deletion. A failed
assembly retains diagnostics and is rerun as a whole; old individual contig
outputs are not promoted into a completed assembly result.

The configured `timeout_seconds` now bounds the single whole-assembly native
command, rather than one contig invocation. Its default remains 14,400 seconds
(four hours). Adjust it explicitly if a measured assembly run requires a longer
deadline. Heartbeats report elapsed time, not percentage completion. AWARE remains
a separate downstream admission policy.

## Native results and scoped configuration

Core native and CRISPR assembly jobs publish `safety/completion/<module>.json`. Verified payloads
live in `safety/results-cache/<module>/<identity>/attempt-*/data`, with their
ordinary `safety/native/` locations pointing to those payloads. Reuse checks
sample inputs, the module configuration, relevant sources, the executable,
environment/resource receipts and output inventory. Missing scheduler receipts
can be restored without rerunning a matching verified command. Existing payloads
are verified during DAG construction as well as before reuse/normalization; this
adds file-reading cost to a restart but detects changed outputs on an idle run.

New attempts never replace a previous successful payload. Failed attempts retain
their logs and partial files. `modules.<name>.failed_attempts_to_keep` keeps the
newest three failed attempts by default, and accepts integers from 1 to 100.
Only failed attempts in that module's owned store are pruned. Successful and
legacy payloads are not automatically deleted. `cache: false` prevents verified
reuse when the job executes; it does not force an otherwise up-to-date Snakemake
job to run.

RGI database loading is independent of sample sequences. Analysis uses a private
writable copy so generated indexes cannot alter the verified loader snapshot.
The private copy is removed after successful verification and retained on
failure. A reused result requires neither a new copy nor a native call.

Each worker now consumes `safety/metadata/configs/<module>.json`. Changes to a
downstream AWARE policy do not invalidate completed core, CRISPR or embedding
work. Changes to a module's own settings, inputs, resources, environment or code
still invalidate its work. Full configuration remains in metadata for reports.
DeepSafe's scheduled stages use `deep.threads` and `deep.memory_mb`, subject to
the overall allocation, rather than fixed rule allocations. Configuration
separation does not change the selected analysis scope.

## Read-only preflight

From the checkout, inspect the same ordered configuration overlays as the run:

```bash
python scripts/doctor.py --configfile study.yaml local-resources.yaml --json
python scripts/doctor.py --configfile study.yaml local-resources.yaml \
  --validate-inputs --verify-resources
```

The doctor reads inputs, paths and existing receipts; it does not run tools,
download, change configuration or prepare missing resources. The optional full
checks read more data and can take longer. A path merely existing is not reported
as a verified compatible resource. Missing setup is distinguished from invalid
data. `--outdir /absolute/path/to/new-review-directory` exports JSON, HTML and TSV
to a new directory outside isolate and resource folders; omit it for no writes.

## Updating and recovering an existing checkout

Finish or stop the current workflow before updating code. Keep the original
study, strict/CUDA and local-resource configuration files in the resume command.
The current user-specific manual-resource overlay remains usable.

Old automatic receipts do not record enough preparation identity to prove
compatibility with arbitrary current settings. The update preserves those
receipts/data and stops with local-path migration guidance instead of silently
downloading again. Select verified old resource paths explicitly, using a
separate receipt directory when the paths are under the old resource directory.
The update does not recursively search disks or guess among database versions.

Old CRISPR contig outputs do not prove completion of the new whole-assembly
invocation. On required execution, the prior native directory is preserved under
`.previous-results/` beside its conventional location. Core native outputs made
before the result store also need one recomputation when scheduled. Existing
completed reports may be regenerated as execution/configuration sources change.

For an explicitly fresh run, archive selected `safety` folders after stopping the
workflow and updating the checkout. The helper uses the same configuration merge
and sample resolution as the workflow:

```bash
python scripts/archive_run_results.py --configfile study.yaml strict-cuda.yaml local-resources.yaml
python scripts/archive_run_results.py --configfile study.yaml strict-cuda.yaml local-resources.yaml --apply
```

The first command is read-only. `--apply` renames each selected folder in place
to `safety.before-<UTC timestamp>-<unique suffix>` and writes an archive journal.
It refuses active workflow processes, overlapping/unsafe folders, inputs or
resources inside a selected result folder, and absolute symlinks that would
break on relocation. All selected folders are checked before the first rename.
Missing result folders are skipped. A partial rename failure is recorded; files
are never deleted. This is for Linux/WSL and is not a lock against independently
started writers: do not launch another workflow during the operation.

Annotations, configuration, repository resources and Conda environments are
retained. Archived files still occupy disk space; renaming avoids a second copy.
Historical reports can contain original absolute paths, so archives preserve
files but are not automatically relocated runnable report bundles. Archiving all
`safety` folders deliberately starts sample analyses fresh; it is not a normal
resume or a promise to reuse their partial results. Do not archive again before
an ordinary retry.

Resume normal failed jobs with the same command and `--rerun-incomplete`. If a
managed resource link alone was deleted, the workflow reports the exact outer
receipt to move aside while stopped; the next preparation restores the link and
receipt from the verified cache. If recorded payload files themselves are lost,
restore the matching snapshot instead. CRISPR assembly native links follow the same receipt recovery procedure as
core native links below.

For a deleted core or CRISPR assembly native link, stop writers and move only its small scheduler
receipt aside; the next scheduled job verifies the saved payload and restores
the link and receipt. Do not delete the result store. If payload bytes are
missing or changed, verification stops with the affected path; restore the
recorded files before retrying. Deliberate recomputation requires moving aside
the affected `safety/completion/<module>.json` and its matching identity folder's
`cache.json` while stopped; retain the payload for inspection. A corrupted
payload is never silently relabelled complete.

Do not delete referenced snapshots, completion records or shared execution
identity files as ordinary cache cleanup. Retained failed downloads, older
snapshots and previous contigs consume space; this update does not automatically
purge them. Review their provenance and active references before any later
cleanup. Conda deployment recovery remains Snakemake's responsibility.
