# Core, Extend and DeepSafe integration

Run `BaSIS_prepare_combined.py` after update 21 and successful Extend recovery.
It checks input compatibility and recorded artifact hashes, previews candidate
queues and writes two configurations without overwriting differing files:

* `config.combined.yaml`: native Core and Extend followed by DeepSafe, writing
  `results-combined`. Run with `basis run -c ... -d -e -v`.
* `config.combined-validation.yaml`: imports the verified historical core and
  recovered Extend evidence and runs DeepSafe, writing
  `results-combined-validation`. No embedding or classifier inference repeats.

Both use the same deep configuration, including the explicit opt-in
`attention_queue_for_mixed_evidence: true`. A protein with attention evidence
and only exploratory screening evidence is routed to the attention queue.
Its evidence stays exploratory. Native-screen evidence retains priority and
shared proteins are deduplicated under the existing locus rules. Older configs
retain their previous routing unless this option is enabled.

The attention budget is 20; the helper ensures the total budget can hold all
current native-screen candidates plus 20 attention candidates. The general
exploratory setting and tree budget remain unchanged. Incomplete scope may
persist because other exploratory candidates remain deferred. A queue records
why a protein was selected, not its biological function or risk.

The local pipeline already executes enabled modules before DeepSafe; the
Snakemake deep rule depends on all module outputs. Both therefore provide
Extend findings to candidate selection when `-d -e` is requested. Candidate
reports and consolidated evidence include queue, attention rank and score.
Attention-derived follow-up remains subject to the usual reference, taxonomy,
domain, motif and phylogeny gates.

This study helper targets the current single-isolate study. The pipeline itself
retains support for multi-sample configurations. Native Extend cache remains
disabled in these configs, matching the tested validation configuration.
