# Annotation integrity and coverage

BaSIS checks agreement between supplied exports before starting analysis. Passing
these checks means the supported files are consistent; it does not establish that
the annotation found every gene, that the assembly is correct, or that an organism
is safe. No missing sequence or feature is inferred by these checks.

## Protein FASTA and GFF

Every non-pseudogene GFF CDS must map to exactly one protein FASTA identifier by
`ID`, `protein_id` or `locus_tag`. Every FASTA identifier must also map to one CDS.
Partial CDSs still require their supplied partial protein sequence. A partial
flag is not permission to omit a protein from screening silently.

Explicit `pseudo` or `pseudogene` attributes permit a CDS to have no protein
record. Values `false`, `no`, `0`, `none`, `null` and `.` do not enable this exception. Excluded
pseudogene counts and examples are recorded as warnings; protein-based analyses
cannot assess missing proteins. Multiple CDS rows mapping to one protein and
multiple FASTA identifiers mapping to one CDS remain unsupported and are rejected
as ambiguous; joined features are not silently flattened.

## Optional GenBank export

The GenBank nucleotide records must match the supplied assembly. Each eligible
CDS must also match a GFF CDS's contig, single interval and strand. Non-pseudogene
CDSs cannot be missing, duplicated or added only in one export. The GenBank
`translation` must match the supplied protein (an optional terminal stop is
ignored). Partial intervals are permitted when their recorded boundaries and
peptides agree. Joined, remote and unknown locations are rejected explicitly
because this input contract does not implement a joined-feature translator.

Contig aliases are resolved through sequence agreement and unambiguous record
identities. Gene identifiers may differ across exporters if the exact location
and peptide agree; an identifier that instead points to another supplied protein
is a conflict. GenBank pseudogene features must correspond to GFF pseudogenes;
their exclusion from eligible-CDS coverage remains visible.

Previously accepted incomplete exports now fail input validation. Export a
matching assembly/protein/GFF/GenBank set from the same annotation source; do not
interpret a subset as complete annotation coverage. A new validation code input
in the workflow ensures these checks are rerun when the coverage implementation
changes.

## Native coordinates and reports

Known protein identifiers cannot erase contradictory native coordinates.
Unresolved contigs, opposite strands, malformed intervals and spans outside the
annotated CDS reject before normalization. Declared contig aliases, unknown
locations and contained partial alignments remain supported. Original reported
coordinates are retained, including when normalized evidence is loaded again.

The HTML overview now shows recorded input warnings alongside execution results.
The Inputs and module scope section retains all warnings, annotation coverage,
translation checks and limitations. The first five warnings per sample are shown
in the overview with an explicit remaining count; JSON and report details retain
the complete list. A successful tool execution does not cancel input limitations.

These changes repair file integrity and presentation behavior. Scientific
performance validation and the native acceptance requirements in
[ACCEPTANCE.md](ACCEPTANCE.md) remain separate release gates.
