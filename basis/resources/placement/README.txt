BaSIS Cfr/RlmN comparator resource v2 — optional development expansion

13 accession records / 12 unique natural sequences:
6 qualified Cfr-family sequences, 4 alternative RlmN sequences, 2 context sequences.
Three new unique sequences: AKV84429.1, CCL89685.1, AEA94342.1.
All ten v1 reference entries are preserved unchanged, including the two accessions
sharing the original Cfr sequence and the context-only Bacillus subtilis entry.

This resource is optional. Preparing it does not change the default v1 registry,
old benchmark, classifier, thresholds, any isolate result, or safety interpretation.
Use the explicit v2 path only for a separately recorded development run.

Evidence differs by comparator. Cfr(B) AKV84429.1 has gene-linked heterologous
susceptibility evidence; direct C8 chemistry for that exact variant is not claimed.
Cfr(C) CCL89685.1 is linked by the published 100% identity statement to the HON10
enzyme with HPLC-supported C8 RNA methylation. OG1RF AEA94342.1 is linked to native
rlmN deletion causing loss of m2A in rRNA/tRNA. Detailed source identity, assays,
limitations and primary URLs are recorded in reference-audit-v2.json.

Bacillus subtilis O34617 remains context-only pending exact linkage to the 2016
wild-type assay sequence. Cfr(E) DF11 remains held pending a pinned tested native
allele sequence. These gaps were not filled by homology inference.

The new references were inspected for method development. They are not untouched
independent validation samples. No performance gain or calibrated probability is
claimed. Alternative RlmN membership is not a universal non-resistance/safety label.

Files
registry-v2.json: compatible with the existing BaSIS placement registry validator;
                 embeds immutable natural protein sequences for offline preparation.
references-v2.faa: 13 accession records; exact duplicates remain visible and the
                  existing panel preparer deduplicates them to 12 sequence anchors.
reference-audit-v2.json: identity and primary experimental evidence audit plus gaps.
*-source-downloads.json: provenance for the research source cache. Cached publisher
                        articles are not distributed; inspect cited source URLs.
checksums-v2.json: SHA256 digests of delivered resources (excluding this checksum file).
