# Consolidated DeepSafe reporting

Every normal DeepSafe run now writes per-candidate `consolidated_evidence`, a
selected-candidate `consolidated_evidence.json` artifact and an integrated HTML
summary. No manual review script is required for subsequent configured runs.

The summary preserves each layer's execution status and recorded reason.
Connected query-interval overlaps group Pfam GA hits, custom motifs, PROSITE
patterns and profiles. These reporting groups are not physical-domain counts,
independent votes, or resolved functional classifications. Nonoverlapping repeat
copies remain separate. Identical annotations are deduplicated for display;
raw evidence is unchanged. Residue observations share a row by query position
and observed residue while retaining every distinct rule, feature and condition.
Unmapped and unsupported conditions remain separate and visible. No full ProRule
logic, taxonomy or functional annotation is asserted.

`BaSIS_consolidate_deep.py` prepares `study-isolate1/config.final.yaml` from the
native `config.core.yaml` plus the latest prepared DeepSafe profile config. It
requires matching sample-manifest paths and all seven core modules enabled in
native mode. An existing differing final config is preserved and causes a stop.
Outputs go to `results-final`, preserving earlier study results. The script also
refreshes the small selected-query pattern/profile/motif scans and generates a
historical-evidence consolidation without rerunning earlier domain/tree work.

Run the final pipeline with:

    basis run -c study-isolate1/config.final.yaml -d

This runs enabled core screens (or verifies their native caches), candidate
selection, configured DeepSafe analyses and the consolidated report. Motifs,
PROSITE patterns and profiles are enabled in this generated configuration.
Resource versions and cache integrity are still checked. Candidate budgets,
reviewed-reference gates, taxonomy review and bounded tree policies remain in
force. Deferred candidates are not silently assessed or promoted. Overall
incomplete scope can therefore persist even when all selected candidates finish.
Extend (`-e`) still requires its separate environment and model setup.
