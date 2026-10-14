# Automated motif assessment

DeepSafe evaluates applicable rules for selected candidates after domain analysis. The same engine supports any family for which an explicit, sourced rule is supplied. Registry coverage is finite: the initial registry has one documented shared radical-SAM motif (PF04055), not a universal functional classifier.

Defaults: `deep.motifs: true`; `deep.motif_rules: null` uses the bundled registry. Set `motifs: false` to disable, or set `motif_rules` to an absolute JSON registry path. The registry contents are fingerprinted for DeepSafe cache reuse.

Rules select candidates by a matching GA-qualified Pfam accession (without its version suffix) or a listed profile ID. Profile selection alone cannot supply domain coordinates: a matching GA-qualified domain is still required to evaluate the motif. Exact matches must fit entirely within that domain's aligned query interval. Coordinates are 1-based and inclusive. Multiple and overlapping matches are retained.

The deliberately bounded pattern syntax supports canonical amino acids, residue sets such as `[ST]`, wildcard `x`, and fixed repetitions such as `x(3)`, separated by hyphens. General regex, variable-length repeats, anchoring and full PROSITE syntax are not supported. This is not a ScanProsite implementation.

Every rule must state its sources, evidence basis, limitations, domain selectors and domain-model coverage requirement for reporting absence. All rules must declare `function_established: false`. Custom rules are user-supplied hypotheses until independently evaluated; registering one does not validate it.

Results:
- `present`: exact motif found within an eligible domain; no functional conclusion.
- `not_detected_in_assessed_region`: no exact match in the assessed domain interval, with sufficient model coverage, canonical residues and no recorded integrity flags. This is not a claim that the protein is inactive or safe.
- `not_assessable`: missing/partial domain information, ambiguous residues or integrity concerns prevent the absence interpretation.
- `no_applicable_rule`: registry coverage does not include this candidate.
- `not_assessed`: candidate deferred, motifs disabled, or query unavailable.
- `failed`: query hash does not match recorded evidence.

The shipped CxxxCxxC rule is shared by Cfr, RlmN and other radical-SAM proteins. Noncanonical exceptions exist. It cannot distinguish Cfr from RlmN or establish resistance. Motif and domain evidence are correlated; no score or confidence bonus is added. Existing candidate selection, trees, functional interpretation and follow-up decisions are unchanged.

`python BaSIS_review_motifs.py` evaluates saved queries and domain results, verifies query hashes, and writes a separate timestamped report and JSON. It launches no native tools and preserves the original results. It is a reporting review, not retroactive execution of the updated pipeline. Deferred candidates remain deferred.

Automatically learning novel signatures from alignments is not implemented. It would require independent positive and related-negative evaluation before interpretation.
