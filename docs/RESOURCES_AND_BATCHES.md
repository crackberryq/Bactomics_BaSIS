# Resource preservation and contig scheduling

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

Both command streams reach the command log during execution. Stdout remains
separate from stderr when a parser needs the command's return text, and commands
that deliberately write stdout into a result file keep that behaviour. A log
write failure or controller interruption kills and reaps the immediate child.
Snakemake continues to own the process group and workflow shutdown.

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

At size 32, 3,699 contigs produce 116 CRISPR batch jobs, plus preparation and
collection. The same 3,699 native calls remain. The smaller step count measures
scheduling granularity, not a 32-fold runtime improvement. AWARE remains a
separate downstream candidate-admission policy.

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

Resume normal failed jobs with the same command and `--rerun-incomplete`. If a
managed resource link alone was deleted, the workflow reports the exact outer
receipt to move aside while stopped; the next preparation restores the link and
receipt from the verified cache. If recorded payload files themselves are lost,
restore the matching snapshot instead. Missing CRISPR native folders produce a
targeted batch-receipt/collection repair instruction; verified sibling contigs
are retained and reused.

Do not delete referenced snapshots, completion records or shared execution
identity files as ordinary cache cleanup. Retained failed downloads, older
snapshots and previous contigs consume space; this update does not automatically
purge them. Review their provenance and active references before any later
cleanup. Conda deployment recovery remains Snakemake's responsibility.
