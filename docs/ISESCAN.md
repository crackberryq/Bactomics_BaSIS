# ISEScan zero-result parsing

The supported native environment pins ISEScan 1.7.3. That version can finish
successfully without creating a result TSV when its protein searches yield no
significant hits. A missing TSV alone does not establish either success or failure.

## Correct warning inventory

Upstream [`pred.py` at v1.7.3](https://github.com/xiezhq/ISEScan/blob/v1.7.3/pred.py)
groups the raw HMMER rows by contig before printing individual no-significant-hit
warnings. The final warning lists those same raw-hit contigs. Contigs with no raw
hits do not appear in either warning inventory. If both searches return no rows,
the final warning has an empty contig list.

The previous BaSIS check incorrectly required warnings and raw hits for every
input contig. It could therefore reject a successful native run with
`ISEScan missing table: no-hit transcript does not cover every input contig`.

The corrected parser matches warnings to the raw search rows, while verifying
the completed searches, their target proteome and provenance, the successful
translation record and the ordered terminal messages. Missing, truncated,
conflicting or mismatched evidence remains an error. Native command failure
remains a failure even if zero-result text was written.

## Reported result and scope

- Verified zero findings are retained as a completed execution with count zero.
- Contigs with generated proteins but no raw hits require no warning.
- Input contigs without generated proteins are listed separately; a zero-result
  payload with this coverage gap is inconclusive with incomplete scope, rather
  than a failed process or an unqualified assembly-wide negative.
- Existing positive TSVs, header-only TSVs and the later native no-IS terminal
  marker remain supported.

`tests/test_isescan.py` covers these cases using synthetic files and a synthetic
executable. It also checks that an unchanged repeat uses the saved completion
without invoking the executable again. These are software-contract tests.

## Existing failed attempts

Keep the failed attempt, `commands.json`, logs and native search outputs. A
parser correction does not by itself repair a saved completion receipt. Verify
the recorded native exit status and the corrected parser against that attempt
before deciding how to resume it. Do not fabricate an empty TSV or touch a
completion file to bypass verification.

For the specific legacy warning-coverage failure, after updating this checkout,
preview a single sample using its frozen metadata:

```bash
python scripts/recover_isescan_results.py --isolate /path/to/isolate --refresh-report
```

Apply the same verified recovery and render the screening report:

```bash
python scripts/recover_isescan_results.py --isolate /path/to/isolate --apply --refresh-report
```

This command reads the sample's `safety/metadata` and local
`safety/results-cache/isescan` store. It does not load or edit study YAML, run
ISEScan, launch other native tools, activate environments, or invoke Snakemake.
It requires stopped workflow workers, the exact reviewed legacy parser failure,
a successful recorded native command, unchanged inputs/settings/resources and
executable identity, and complete supporting native output. Other requested
report evidence must exist and match the saved input hashes before publication.

Recovery copies the original raw files into a new verified cache snapshot and
retains their original bytes and path-bearing transcripts. A `recovery.json`
audit binds these copies to the original attempt and parser identity. The new
`parsed.json` points to the copied artifacts. Repeated recovery and subsequent
reuse by the native worker do not run ISEScan. A gap in generated-protein coverage
remains inconclusive in the refreshed report. An unsupported or changed attempt
stops for diagnosis rather than falling back to native execution.

The script repairs the ISEScan receipt and normalized evidence and optionally
refreshes its sample's report. It does not rewrite Snakemake's scheduler history
or migrate other tools' cache identities. The recovered files can finish a saved
report without resubmitting the whole workflow; a future general resume still
needs the dry-run review below.

This shared adapter is included in native source fingerprints. Updating it may
invalidate cached jobs that record those fingerprints; inspect the workflow's
dry-run before resuming an existing batch. The unchanged-repeat test does not
claim that an update from an older parser will avoid recomputation.
