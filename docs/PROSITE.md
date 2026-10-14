# Curated PROSITE pattern assessment

PROSITE complements the small family-specific motif registry with a local scan against its curated pattern collection. `deep.prosite_snapshot` is optional and independent of `deep.motifs`. A configured snapshot enables scanning of selected candidates with available, hash-verified queries during `basis run -d`. Deferred candidates remain deferred. No query sequence is transmitted online.

`BaSIS_prepare_prosite.py` downloads unmodified `prosite.dat`, `prosite.aux`, `prosite.doc`, `ps_reldt.txt` and the official standalone `ps_scan.pl` from SIB. It records URLs, UTC time, release and SHA256 checksums and rejects mismatched releases. It writes a timestamped configuration based on `study-isolate1/config.deep-auto.yaml`; existing configurations remain unchanged. If a download fails, rerun preparation: a fresh snapshot is used. It does not install packages or download model weights.

`BaSIS_review_prosite.py` uses the newest timestamped PROSITE configuration by default, or accepts `--config PATH`. It verifies saved query hashes and runs only ps_scan through the configured DeepSafe command prefix. Previous domain, reference and tree computations are not rerun. It creates a separate timestamped report and evidence JSON. The native command, input FASTA, raw GFF and scan metadata are retained. Errors fail visibly rather than becoming negative scans.

Native options are `-r -s --nopp -x 0 -o gff`. This is an exploratory **pattern-only** scan: PROSITE profiles, evaluator miniprofiles, ProRule and post-processing are not applied. Patterns with SKIP-FLAG=TRUE are excluded. The official scanner owns pattern syntax and matching behaviour; BaSIS does not translate the full syntax into its own regex implementation.

The scanner may pad variable-length pattern matches with dots. These display placeholders are removed only for comparison with the input subsequence; the original native attributes are preserved.

Each match records its accession, description, source pattern, positions, matched sequence, documentation link, raw scanner attributes, available auxiliary reference-database statistics and taxonomy range. GA domain overlaps are reported as context, not used as independent confirmations or automatic family validation. Taxonomic applicability is explicitly not automatically validated. Reference-database false-positive/negative counts are not confidence values for this query. Zero matches do not establish safety, protein inactivity or absence of a family.

Only a single terminal stop marker is removed with an explicit normalization audit. Internal or repeated stops are rejected. Ambiguous residues in a returned matched segment are flagged. Coordinates refer to the normalized sequence (unchanged residue numbering).

The snapshot is content-fingerprinted for DeepSafe caching. The Perl executable must belong to the fingerprinted conda environment for native cache reuse; otherwise analyses may run, but reuse is conservatively disabled.

Data attribution: SIB Swiss Institute of Bioinformatics, PROSITE. The database files state CC BY-NC-ND 4.0; retain their notices and review https://prosite.expasy.org/prosite_license.html for deployment/distribution. BaSIS does not redistribute database files in its patch. The scanner carries a separate GPL notice. Documentation: https://prosite.expasy.org/prosuser.html and https://prosite.expasy.org/scanprosite/scanprosite_doc.html .

Coverage is finite. This integration does not generate new signatures, validate protein function, alter screening labels, escalate candidates, or replace wet-lab confirmation.

## Profiles and residue observations (update 17)

Enable `deep.prosite_profiles: true` with `deep.executables.pfscan: pfscanV3`
(pftools 3.2.12). The snapshot must additionally contain release-matched,
checksum-recorded `prorule.dat`. `BaSIS_prepare_prosite_profiles.py` creates a
new snapshot from the existing verified files; it does not modify them.
`BaSIS_review_prosite_profiles.py` scans only selected saved queries and writes
an independent review. Future DeepSafe runs execute both configured scans.

The native engine scans all MATRIX entries at level 0. Extended PSA alignments
map profile positions to query residues: uppercase match, lowercase insertion,
and dash deletion. Full profile-coordinate length and exact query sequence
are checked before residue mapping. Unmappable sites remain not assessable.

Only explicit single-position residue conditions (one canonical amino acid or
an allowed residue set) are evaluated. Source features, groups and unsupported
conditions are retained. This is not a complete ProRule interpreter: case logic,
taxonomic applicability, size, fusion, repeats and functional annotation are
not evaluated. No match does not establish absence of a function. Pattern,
profile, domain and custom-motif evidence remain distinct and may describe the
same biological feature; their counts are not independent evidence votes.

Validation used native pfscanV3 on seven uploaded queries: four profile hits.
PS51918/PRU01266 mapped the radical-SAM candidate's three cysteines to positions
110, 114 and 117 without a Cfr-specific motif rule. This shared feature does not
distinguish Cfr from RlmN or establish resistance activity.
