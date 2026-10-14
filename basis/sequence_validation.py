"""Check linkage between an assembly, single CDS features and protein FASTA.

This is a file-consistency test, not gene-function or assembly-quality validation.
Code 11 is the explicit bacterial-workflow default when transl_table is absent;
code 1 is also supported. Unsupported codes/recoding are reported as unchecked.
Genetic-code reference: https://www.ncbi.nlm.nih.gov/Taxonomy/Utils/wprintgc.cgi
"""
from __future__ import annotations

from functools import lru_cache
from itertools import product


_BASES = "TCAG"
_AMINO = "FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG"
_CODE = dict(zip(("".join(codon) for codon in product(_BASES, repeat=3)), _AMINO))
_STARTS = {1: {"TTG", "CTG", "ATG"}, 11: {"TTG", "CTG", "ATT", "ATC", "ATA", "ATG", "GTG"}}
_IUPAC = {
    "A": "A", "C": "C", "G": "G", "T": "T", "R": "AG", "Y": "CT",
    "S": "GC", "W": "AT", "K": "GT", "M": "AC", "B": "CGT", "D": "AGT",
    "H": "ACT", "V": "ACG", "N": "ACGT",
}
_COMPLEMENT = str.maketrans("ACGTRYSWKMBDHVN", "TGCAYRSWMKVHDBN")
_AA_SETS = {letter: {letter} for letter in set(_AMINO)}
_AA_SETS.update({"B": {"D", "N"}, "Z": {"E", "Q"}, "J": {"I", "L"}, "X": set(_AMINO)})
_FALSE = {"false", "no", "0", "00", "none", ".", ""}


def _sequence(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label}: expected a sequence string")
    return "".join(value.split()).upper()


@lru_cache(maxsize=4096)
def _codon_options(codon):
    return frozenset(_CODE["".join(triplet)] for triplet in product(*(_IUPAC[base] for base in codon)))


def _partial_flags(attrs, strand):
    """Return five-prime, three-prime flags in transcription orientation."""
    partial = str(attrs.get("partial", "false")).lower()
    if partial in {"10", "01", "11", "00"}:
        five, three = partial[0] == "1", partial[1] == "1"
    else:
        five = three = partial not in _FALSE
    for key in ("five_prime_partial", "5_prime_partial", "partial_5prime"):
        five |= str(attrs.get(key, "false")).lower() not in _FALSE
    for key in ("three_prime_partial", "3_prime_partial", "partial_3prime"):
        three |= str(attrs.get(key, "false")).lower() not in _FALSE
    left = "." in str(attrs.get("start_range", ""))
    right = "." in str(attrs.get("end_range", ""))
    five |= left if strand == "+" else right
    three |= right if strand == "+" else left
    return five, three


def _skip_reason(attrs, protein):
    for key in ("transl_except", "translation_exception", "exception", "ribosomal_slippage",
                "ribosomal_frameshift", "trans_splicing", "rna_editing"):
        if key in attrs and (key == "transl_except" or str(attrs[key]).lower() not in {"false", "no", "0", "none"}):
            return f"translation/recoding exception tagged by {key}"
    for key in ("pseudo", "pseudogene"):
        if key in attrs and str(attrs[key]).lower() not in {"false", "no", "0", "none"}:
            return f"{key} feature may not have a conventional uninterrupted translation"
    if "U" in protein or "O" in protein:
        return "protein contains recoded U/O residues; explicit recoding validation is unsupported"
    return None


def validate_cds_translations(assembly: dict, proteins: dict, cds_features: list[dict]) -> dict:
    """Return checked/unchecked protein counts and explanatory warning strings.

    Raise ValueError for a confidently incompatible residue or peptide length.
    Validate only single CDS-to-protein mappings. Joined features, unsupported
    genetic codes, recoding, and missing phase are not guessed. Partial CDSs use
    their GFF phase at the biological 5' end and may omit terminal codon bases.
    Ambiguous nucleotide/protein positions are compared by compatible sets;
    uncertainty remains explicit and all-ambiguous comparisons are unchecked.
    Terminal stops are optional in protein FASTA/CDS conventions. Initiator M
    is accepted only for table-specific starts of an intact 5' end with phase 0.
    """
    warnings = []
    checked = unchecked = 0
    mappings = {pid: [] for pid in proteins}
    for feature in cds_features:
        for alias in set(feature.get("aliases", [])):
            if alias in mappings:
                mappings[alias].append(feature)
    assumed_default = False
    for pid, source_protein in proteins.items():
        features = mappings[pid]
        if len(features) != 1:
            unchecked += 1
            reason = "no matching CDS alias" if not features else "multiple/joined CDS features require a joined-feature translator"
            warnings.append(f"{pid}: translation unchecked: {reason}")
            continue
        feature = features[0]
        attrs = {str(key).lower(): value for key, value in feature.get("attributes", {}).items()}
        protein = _sequence(source_protein, pid)
        if not protein or any(residue not in _AA_SETS and residue not in {"U", "O"} for residue in protein):
            raise ValueError(f"{pid}: empty or invalid protein sequence")
        reason = _skip_reason(attrs, protein)
        if reason:
            unchecked += 1
            warnings.append(f"{pid}: translation unchecked: {reason}")
            continue
        table_value = attrs.get("transl_table", attrs.get("translation_table", attrs.get("genetic_code")))
        if table_value is None:
            genetic_code = 11
            assumed_default = True
        else:
            try:
                genetic_code = int(str(table_value))
            except ValueError:
                genetic_code = None
            if genetic_code not in _STARTS:
                unchecked += 1
                warnings.append(f"{pid}: translation unchecked: unsupported genetic code {table_value!r}")
                continue
        phase_value = feature.get("phase")
        if str(phase_value) not in {"0", "1", "2"}:
            unchecked += 1
            warnings.append(f"{pid}: translation unchecked: missing/unsupported GFF phase {phase_value!r}")
            continue
        phase = int(phase_value)
        if "codon_start" in attrs and str(attrs["codon_start"]) != str(phase + 1):
            unchecked += 1
            warnings.append(f"{pid}: translation unchecked: codon_start and GFF phase disagree")
            continue
        contig = feature.get("contig")
        if contig not in assembly:
            raise ValueError(f"{pid}: CDS contig is absent from assembly: {contig!r}")
        sequence = _sequence(assembly[contig], str(contig))
        if not sequence or set(sequence) - set(_IUPAC):
            raise ValueError(f"{contig}: empty or invalid nucleotide sequence")
        start, end, strand = feature.get("start"), feature.get("end"), feature.get("strand")
        if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int)
                or not isinstance(end, int) or start < 1 or end < start or end > len(sequence)
                or strand not in {"+", "-"}):
            raise ValueError(f"{pid}: invalid CDS interval or strand")
        nucleotide = sequence[start - 1:end]
        if strand == "-":
            nucleotide = nucleotide.translate(_COMPLEMENT)[::-1]
        nucleotide = nucleotide[phase:]
        five_partial, three_partial = _partial_flags(attrs, strand)
        remainder = len(nucleotide) % 3
        if remainder and not three_partial:
            # A length not divisible by three without partial annotation does
            # not establish the exporter convention; do not invent a frame.
            unchecked += 1
            warnings.append(f"{pid}: translation unchecked: CDS length after phase is not a multiple of three and its 3' end is not tagged partial")
            continue
        codons = [nucleotide[i:i + 3] for i in range(0, len(nucleotide) - remainder, 3)]
        expected = [set(_codon_options(codon)) for codon in codons]
        observed = protein[:-1] if protein.endswith("*") else protein
        if not expected:
            unchecked += 1
            warnings.append(f"{pid}: translation unchecked: no complete codons after GFF phase")
            continue
        if phase == 0 and not five_partial and codons[0] in _STARTS[genetic_code]:
            # Retain the ordinary amino acid as well: some exporters preserve
            # a raw translation rather than replacing the initial residue by M.
            expected[0].add("M")
        elif phase == 0 and not five_partial and observed.startswith("M"):
            # An ambiguous start can be compatible with an alternative start;
            # it is never replaced unconditionally with methionine.
            possible_codons = {"".join(item) for item in product(*(_IUPAC[base] for base in codons[0]))}
            if possible_codons & _STARTS[genetic_code]:
                expected[0].add("M")
        if expected[-1] == {"*"}:
            expected.pop()
        elif len(expected) == len(observed) + 1 and "*" in expected[-1]:
            expected.pop()
            warnings.append(f"{pid}: terminal ambiguous codon is compatible with a stop; its identity is unresolved")
        if len(observed) != len(expected):
            raise ValueError(f"{pid}: CDS/protein length mismatch: assembly/GFF gives {len(expected)} residues, protein FASTA gives {len(observed)}")
        uncertain = 0
        informative = 0
        for position, (allowed, amino_acid) in enumerate(zip(expected, observed), 1):
            provided = _AA_SETS[amino_acid]
            if not allowed & provided:
                expected_text = "/".join(sorted(allowed))
                raise ValueError(f"{pid}: CDS/protein sequence mismatch at residue {position}: assembly/GFF permits {expected_text}, protein FASTA has {amino_acid}")
            if len(allowed) > 1 or len(provided) > 1:
                uncertain += 1
            if len(allowed) == 1 and len(provided) == 1:
                informative += 1
        if not informative:
            unchecked += 1
            warnings.append(f"{pid}: translation unchecked: no unambiguous amino-acid positions support consistency")
            continue
        checked += 1
        if uncertain:
            warnings.append(f"{pid}: translation compatible but {uncertain} residue positions remain ambiguous or allow initiation alternatives")
        if remainder:
            warnings.append(f"{pid}: compared complete codons; {remainder} terminal nucleotide(s) omitted at annotated partial 3' end")
    if assumed_default:
        warnings.insert(0, "Translation table 11 assumed for CDS features lacking transl_table; this is a bacterial-workflow default, not taxonomic evidence")
    return {"checked": checked, "unchecked": unchecked, "warnings": warnings}
