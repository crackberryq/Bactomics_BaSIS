# Saved CRISPR array boundary review and revalidation

CRISPRCasFinder can finish successfully while its reported array end differs
from the end of its final direct repeat. In the observed case, the reported
interval is `20967..21178`, but the contiguous repeat/spacer components cover
`20967..21177`. There are no internal gaps or overlaps. This establishes an
inconsistency in native evidence; it does not establish the correct endpoint.

The pinned [native source](https://github.com/dcouvin/CRISPRCasFinder/blob/c13475ebf983040b3fb0b685380e7da317752002/CRISPRCasFinder.pl)
reads the array bounds from the text report, computes repeat ends using the
repeat length and spacer positions, and writes those values separately into
GFF and JSON. BaSIS does not assume a universal exclusive-end convention or
modify the native JSON to reconcile them.

The parser retains this specific one-base discrepancy only when all existing
sequence, flank, order, count and containment checks pass, the first repeat
starts at the reported array start, components are contiguous, and every repeat
has the declared length. The finding keeps the native start/end and gains
`attributes.boundary_review` with both reported and component intervals.
`metrics.array_boundary_reviews` summarizes the affected arrays. The native
process remains completed, while `scope_incomplete: true` and reason
`crisprcasfinder_boundary_review_required` keep the report incomplete pending
review. Reporting thresholds and native commands are unchanged. Wider endpoint
disagreements, start mismatches, gaps, overlaps and sequence errors still fail.

## Revalidate retained output without running the native command

Stop Snakemake and wait for its workers before updating or applying recovery.
Keep the same ordered configuration overlays, inputs and thread allocation.
Successful recovery prints `resume_threads`, the recorded allocation needed
for cache reuse. Changing the allocation on resume legitimately requires a new
execution identity, even if the requested thread count was previously capped by
Snakemake's available cores.
After updating `snakemake-standalone`, preview using the shell array `cfg`
containing the configuration files used for the run:

```bash
python scripts/revalidate_ccf_results.py --configfile "${cfg[@]}"
```

Apply using the same files:

```bash
python scripts/revalidate_ccf_results.py --configfile "${cfg[@]}" --apply
```

For a configured subset, add `--isolate /absolute/path/to/isolate`. For a
single saved run without configuration files, use `--isolate` and an explicit
Linux-side `--store /absolute/path/to/sample/crisprcasfinder` destination;
that form uses saved sample/module metadata. The batch form reconstructs the
module identity from the current ordered configuration files and existing
resource/environment receipts without performing setup or downloading files.

The helper accepts the exact known legacy parser source hashes, with every
other execution-identity field unchanged. It requires the expected successful
native command and either the specific old array-bounds verification failure
or a verified completed result. It checks saved native source, command logs,
input coverage, runtime-isolation evidence, coordinates and sequences with the
current parser before publishing a receipt. Unexplained failures or identity
drift abort recovery. Samples without saved attempts remain for normal execution.

Recovery preserves original attempts and writes a snapshot under the current
parser identity in the configured Linux result store. On the same filesystem,
unchanged regular files use hardlinks; cross-filesystem transfers use copies.
Derived parsed output is replaced atomically. Treat retained native snapshots
as immutable. The snapshot records its original attempt and identity in
`recovery.json`. No native command is run, no original result is removed, and no
database is downloaded. Progress messages identify each sample and phase;
inventory verification can still take time for large directories, especially
when the original output is on a Windows-mounted filesystem.

All selected samples undergo preflight before any completion receipts are
published. Store locks protect publication, and interrupted snapshot attempts
remain available for inspection; retry selects the original native attempt.
After recovery succeeds, resume the same Snakemake command with
`--rerun-incomplete`. Matching CRISPR results are reused and reports are
regenerated; other unfinished work still runs. Do not delete the result tree or
force a complete rerun for this parser discrepancy.
