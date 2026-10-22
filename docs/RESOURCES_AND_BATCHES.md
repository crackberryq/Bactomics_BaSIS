# Resource preservation, execution and recovery

This infrastructure update changes preparation, logging and scheduling. Native
screening calls, supplied contig coverage, AWARE admission settings and reporting
interpretation are preserved. Synthetic tests establish software contracts;
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

## CRISPRCasFinder batches

`modules.crisprcasfinder.batch_size` is an integer from 1 to 1024, default 32.
Batches follow the deterministic assembly record order. Each job executes its
contigs sequentially using its allocated threads. There is no nested scheduler
or additional parallel native execution inside a batch.

Small scheduler outputs live at
`safety/native/crisprcasfinder/batches/CCB*.json`. Native contig folders retain
their `CCF*` names. Each completed contig has a record binding its input,
execution settings, resource/environment identity and verified output files.
Shared execution identities are stored once and referenced by those records.
Changing batch size, CPU allocation or receipt timestamps can regroup jobs
without recomputing valid native results. Relevant input or execution changes
invalidate reuse. Collection checks every current contig exactly once.

Static executable/model checks happen once per executed batch. Workers no longer
read the unused complete sample manifest for each contig. Parsing happens once
per newly completed contig; a checksum-bound parsed sidecar is reused during
collection after raw-output verification. Changing the parser or its relevant
execution source invalidates that cache. Native arguments and evidence values
are unchanged.

At size 32, 3,699 contigs produce 116 CRISPR batch jobs, plus preparation and
collection. The same 3,699 native calls remain. The smaller step count measures
scheduling granularity, not a 32-fold runtime improvement. AWARE remains a
separate downstream candidate-admission policy.

## Native results and scoped configuration

Core native jobs publish `safety/completion/<module>.json`. Verified payloads
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

Old CRISPR contig outputs have no complete per-contig execution identity. When
recomputation is required, they are retained under `.previous-contigs/`; they are
not labelled reusable solely because a parser accepts them. Subsequent batch
retries use the new verified completion records. Allow an existing run to finish
before adopting the update if avoiding this migration work is the priority.

The added parsed-cache identity can also require one recomputation of earlier
batch records. Core native outputs created before the result store lack the new
execution identity: on their next required execution, old directories are moved
into `.previous-results/` beside their conventional location. They are preserved,
but are not assumed to be reusable merely because they can be parsed.

Resume normal failed jobs with the same command and `--rerun-incomplete`. If a
managed resource link alone was deleted, the workflow reports the exact outer
receipt to move aside while stopped; the next preparation restores the link and
receipt from the verified cache. If recorded payload files themselves are lost,
restore the matching snapshot instead. Missing CRISPR native folders produce a
targeted batch-receipt/collection repair instruction; verified sibling contigs
are retained and reused.

For a deleted core native link, stop writers and move only its small scheduler
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
