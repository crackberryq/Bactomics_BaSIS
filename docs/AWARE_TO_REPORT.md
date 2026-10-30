# AWARE to DeepSafe report: end-to-end audit

This walkthrough covers the AMRFinder review scope in PR #16. It describes
software behavior and the remaining integration work; it is not a native AFB01
analysis or a release announcement.

| Step | What happens | What must remain visible |
|---|---|---|
| 1. Saved screening | `Followup.smk` checks report/source hashes, sample identity, annotation checksums and completed setup. It has no screening or database-preparation rules. | Missing, changed or failed prerequisites stop the request; they cannot become negative findings. |
| 2. Explicit scope | `deep.enabled: true` requests follow-up; `deep.aware.scope: amrfinder-review` selects the new policy. Older saved configurations require an explicit scope overlay. | Effective configuration and source hashes are captured separately from screening. |
| 3. Review cues | Reported AMRFinder AMR HMM, partial and disrupted calls require investigation. Unsupported/conflicting method metadata goes to manual review. Other native calls remain for context review. | The review obligation exists even when follow-up is off or excluded. These cues do not recover unreported alignments. |
| 4. Identity and admission | AWARE applies source-specific admission and reconciles protein/locus identity. Multiple tools reporting the same verified locus share a candidate. Conflicting identity is held for review. | Every source remains linked. Other tools cannot admit unrelated candidates into this scope. |
| 5. Budget | Eligible candidates are ordered using the existing heuristic, then the default maximum of five is applied per isolate. | Deferred candidates retain their reasons; this is a computing ceiling, not a validated risk ranking or total runtime deadline. |
| 6. Query and routing | Extract the annotated protein, check sequence integrity, collect genomic context and select an available family profile. | Missing queries, taxonomic concerns and missing/ambiguous family configuration remain explicit. A linked exact call cannot bypass a selected investigation. |
| 7. Evidence layers | Run requested/eligible domain scans and comparator retrieval, then tree planning, phylogeny, PROSITE patterns/profiles, motifs and optional family placement. | Each layer has its own status and reason. Configuration, resources, family coverage and existing eligibility gates determine which analyses actually run. |
| 8. Consolidation | Combine exactly one result per selected candidate and retain all deferred candidates. Preserve source, query and reference provenance. | `completed`, `not_assessed`, `failed` and `deferred` are different states. Completed analyses do not establish function. |
| 9. Report | Render `safety/deepsafe-report/report.html`, full AMR-review TSV, evidence JSON and run summaries. Original screening stays separate. | Candidate review state and assessment limits remain separate from job completion. No automatic result records expert review closure. |

The execution order in step 7 is:

```mermaid
flowchart TD
    A["Prepare: selection, query, integrity, context"] --> B["Per-candidate domains"]
    B --> C["Comparator references and tree budget"]
    C --> D["Per-candidate phylogeny"]
    D --> E["Patterns, profiles and motifs"]
    E --> F["Placement planning and candidate placement"]
    F --> G["Consolidation and separate report"]
```

Domain evidence uses HMMER/Pfam; sequence comparator searches use the configured
BLASTP or DIAMOND path. Eligible trees use MAFFT and IQ-TREE. PROSITE, motif rules
and family placement have separate resource/enablement requirements. Presence of
a stage in the DAG does not mean a native analysis ran: an explicit abstention or
disabled stage still produces a state record. The five-candidate ceiling does not
bound the number of tool invocations per candidate. Current timeouts are per
command (default four hours), not a complete-run deadline.

## Regressions found and repaired during the audit

- The workflow renderer rejected a completed DeepSafe module containing a selected
  candidate with a terminal `not_assessed` result. It now renders that result only
  with an explicit reason and retains incomplete assessment scope. Failed,
  pending, running, unknown and missing selected-candidate states still cannot
  masquerade as a completed module.
- The new AMR-review TSV was not a requested Snakemake output. It is now both a
  declared report output and an explicit workflow target for screening and
  follow-up. Removing that export causes report regeneration from saved evidence
  without repeating DeepSafe analysis or changing screening inputs.

`test_nonempty_amr_review_through_saved_workflow_resume_and_report` uses the actual
`Followup.smk` entry point, candidate rules and renderer. Three synthetic source
findings become two unique candidates, one selected and one budget-deferred. It
stops after tree planning, resumes, checks every selected candidate stage, and
verifies unchanged screening checksums/timestamps and an unchanged no-op rerun.
Missing comparator resources remain `not_assessed`; a metadata-only synthetic
catalogue with no eligible comparators produces `completed` plus `unresolved`.
Both remain in the report and export. No native biological tool runs in this test.
Separate tests exercise failed-stage rejection and per-stage state integrity.

## Bactologix handoff still pending

The inspected Bactologix source was
[`781f83e`](https://github.com/crackberryq/Bactologix/tree/781f83e2a7f7d1ac38132a6bc7059096ae579977).
Its [follow-up adapter](https://github.com/crackberryq/Bactologix/blob/781f83e2a7f7d1ac38132a6bc7059096ae579977/src/bactologix/followup.py)
copies the parent screening workflow and checks an explicit `followup_commits`
list. Its general release-compatibility approval does not automatically extend
that follow-up list. Therefore installing a newer BaSIS version does not upgrade
an existing frozen screening run's follow-up implementation.

Before using the new policy through Bactologix:

1. Merge and publish the tested BaSIS change under an identified version.
2. Test and register its follow-up compatibility in Bactologix, including the new
   scope and unchanged-snapshot checks.
3. Give the follow-up request an explicit, verified implementation version and
   scope. A newer installed checkout must not silently replace the parent's
   frozen workflow. Reusing an older screening bundle with a newer follow-up
   implementation needs a recorded, tested source transition.
4. Verify the real AFB01/isolate1 candidate preview, requested resources, a
   nonempty native run, report/export consistency, restart and unchanged rerun.

The current PR does not implement that Bactologix source transition or validate
native runtimes or family interpretation. Those remain acceptance work before
claiming that the full installed application path is ready for beta. See
[AMRFinder review policy](AMR_REVIEW.md) and [acceptance gates](ACCEPTANCE.md).
