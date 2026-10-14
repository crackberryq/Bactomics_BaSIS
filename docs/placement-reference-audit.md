# Functional placement reference audit

The first registry supports the Cfr/RlmN methyltransferase family. It contains
10 accession records, representing nine distinct protein sequences: four
characterized Cfr sequences, three experimentally supported RlmN-like sequences,
and two unlabelled contextual sequences. Other families require their own
source-audited panels and should report `no_applicable_panel`.

The packaged registry is `resources/placement/registry-v1.json`, mirrored in the
Python package. Every sequence has a SHA-256 digest, exact accession or sequence
version, public retrieval URL, and an accession-to-experiment explanation. The
companion audit JSON records exclusions and source-file digests. Bundled sequences
allow reproducible offline use; their official URLs support verification or
retrieval when a sequence is not already available.

| Reference | Comparison role | Source linkage |
| --- | --- | --- |
| Q9FBG4 and A5HBL2 | Cfr | Accession-specific experimental evidence in UniProt; identical sequences count once. |
| YP_001420189.1, YP_002773985.1 | Cfr | Exact accessions in [PMID 22547628](https://pubmed.ncbi.nlm.nih.gov/22547628/), Table 1. |
| YP_003243447.1 | Cfr | GI 261407206 in [PMID 23752511](https://pubmed.ncbi.nlm.nih.gov/23752511/) resolves to this archived accession. |
| P36979 | RlmN-like | Gene-linked biochemical evidence, including [PMID 18025251](https://pubmed.ncbi.nlm.nih.gov/18025251/). |
| YP_002773208.1 | RlmN-like | GI 226313314 in PMID 23752511 resolves to this accession. |
| A6QGB8 | RlmN-like | Exact Newman locus NWMN_1128 investigated in PMID 18025251. |
| O34617 | Context | Accession annotation is based on homology; no experimental anchor status asserted. |
| ZP_02993180.1 | Context | GI 187776707 in PMID 23752511; activity remains unresolved beyond the tested host. |

The two RlmN-like references from Brevibacillus and Newman have gene-linked
evidence for rRNA modification, while precise C2 chemistry is inferred in their
source studies. They are comparators for a competing enzyme family, **not universal
non-AMR or susceptible controls**. RlmN can affect antibiotic susceptibility.
Likewise, Cfr biochemical activity does not establish expression or an isolate's
resistance phenotype.

The Bacillus clausii accession YP_174574.1 is excluded: the paper's assay strain
is described as *domuvar*, whereas the linked archived accession is KSM-K16.
Exact correspondence of the assayed construct and accession was not resolved
in this audit. A negative heterologous-host result for the Clostridium protein
does not make it a negative functional anchor.

The normalized NCBI FASTA sequences were compared with their archived GenPept
ORIGIN sequences. UniProt sequence fields were checked against their accession
metadata. No residue substitutions, engineered variants, or artificial negative
examples were introduced. Duplicate accessions retain provenance but cannot
increase a group's distinct-reference count.

This is an AI-assisted identity and source audit, not independent expert review
or classifier validation. The panel is small and taxonomically uneven. Placement
weights describe relationship to this reference set under the fitted model;
they are not calibrated probabilities of function. No outgroup or phylogenetic
root is asserted. Broader panels, independent review, and leakage-controlled
evaluation remain necessary before any functional probability can be reported.
