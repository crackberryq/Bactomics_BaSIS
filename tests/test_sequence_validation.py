import unittest

from basis.sequence_validation import validate_cds_translations


def feature(length, *, strand="+", phase="0", attrs=None, start=1, pid="p1"):
    return {"contig": "c1", "start": start, "end": start + length - 1,
            "strand": strand, "phase": phase, "aliases": [pid], "attributes": attrs or {}}


class TranslationConsistencyTests(unittest.TestCase):
    def check(self, dna, protein, **kwargs):
        return validate_cds_translations({"c1": dna}, {"p1": protein}, [feature(len(dna), **kwargs)])

    def test_standard_and_bacterial_translation(self):
        for table in ("1", "11"):
            with self.subTest(table=table):
                result = self.check("ATGAAATTTTAA", "MKF", attrs={"transl_table": table})
                self.assertEqual(result, {"checked": 1, "unchecked": 0, "warnings": []})

    def test_same_ids_mixed_sequences_fail(self):
        with self.assertRaisesRegex(ValueError, "residue 2"):
            self.check("ATGAAATTTTAA", "MQF")
        with self.assertRaisesRegex(ValueError, "length mismatch"):
            self.check("ATGAAATTTTAA", "MK")

    def test_terminal_stop_fasta_conventions(self):
        for dna in ("ATGAAATTTTAA", "ATGAAATTT"):
            for protein in ("MKF", "MKF*"):
                self.assertEqual(self.check(dna, protein)["checked"], 1)
        with self.assertRaises(ValueError):
            self.check("ATGAAATTTTAA", "MKF**")

    def test_alternative_start_methionine_code11(self):
        for codon in ("GTG", "TTG", "CTG", "ATT", "ATC", "ATA"):
            self.assertEqual(self.check(codon + "AAATTCTAA", "MKF")["checked"], 1)
        with self.assertRaisesRegex(ValueError, "residue 1"):
            self.check("GTGAAATTCTAA", "MKF", attrs={"transl_table": "1"})

    def test_no_automatic_methionine_for_stop_or_nonstart(self):
        for codon in ("TAA", "TGA", "TAG", "GCG"):
            with self.subTest(codon=codon), self.assertRaisesRegex(ValueError, "residue 1"):
                self.check(codon + "AAATTCTAA", "MKF")

    def test_reverse_complement_coordinates(self):
        # Reverse complement of ATGAAATTCTAA is TTAGAATTTCAT.
        self.assertEqual(self.check("TTAGAATTTCAT", "MKF", strand="-")["checked"], 1)
        assembly = {"c1": "CCCC" + "TTAGAATTTCAT" + "GGGG"}
        result = validate_cds_translations(assembly, {"p1": "MKF"}, [feature(12, strand="-", start=5)])
        self.assertEqual(result["checked"], 1)

    def test_phase_applied_at_biological_five_prime_end(self):
        self.assertEqual(self.check("AATGAAATTT", "MKF", phase="1", attrs={"partial": "10"})["checked"], 1)
        # Reverse complement of AATGAAATTT.
        self.assertEqual(self.check("AAATTTCATT", "MKF", strand="-", phase="1", attrs={"partial": "10"})["checked"], 1)

    def test_partial_5prime_does_not_invent_initiator(self):
        self.assertEqual(self.check("GTGAAATTT", "VKF", attrs={"partial": "10"})["checked"], 1)
        with self.assertRaisesRegex(ValueError, "residue 1"):
            self.check("GTGAAATTT", "MKF", attrs={"partial": "10"})

    def test_partial_3prime_trailing_bases(self):
        result = self.check("ATGAAATTTA", "MKF", attrs={"partial": "01"})
        self.assertEqual(result["checked"], 1)
        self.assertTrue(any("terminal nucleotide" in item for item in result["warnings"]))
        self.assertEqual(self.check("ATGAAATTTA", "MKF")["unchecked"], 1)

    def test_range_partial_flags_follow_reverse_strand(self):
        # Reverse complement of ATGAAATTTA; incomplete biological 3' end is
        # the low-coordinate end for a minus-strand CDS.
        result = self.check("TAAATTTCAT", "MKF", strand="-", attrs={"start_range": ".,1"})
        self.assertEqual(result["checked"], 1)

    def test_ambiguous_dna_compatibility_not_blanket_masking(self):
        self.assertEqual(self.check("ATGGCNTAA", "MA")["checked"], 1)
        with self.assertRaisesRegex(ValueError, "residue 2"):
            self.check("ATGGCNTAA", "MV")
        result = self.check("ATGNNNTAA", "MK")
        self.assertEqual(result["checked"], 1)
        self.assertTrue(any("remain ambiguous" in x for x in result["warnings"]))
        self.assertEqual(self.check("NNNNNN", "XX")["unchecked"], 1)

    def test_ambiguous_protein_codes(self):
        self.assertEqual(self.check("ATGGATGAATTA", "MBZJ")["checked"], 1)
        with self.assertRaises(ValueError):
            self.check("ATGGCT", "MB")

    def test_unannotated_internal_stop_mismatch_and_actual_stop(self):
        with self.assertRaisesRegex(ValueError, "residue 2"):
            self.check("ATGTGAAAATAA", "MWK")
        self.assertEqual(self.check("ATGTGAAAATAA", "M*K")["checked"], 1)

    def test_unsupported_code_recoding_pseudogene_are_explicitly_unchecked(self):
        for attrs in ({"transl_table": "4"}, {"transl_table": "garbage"},
                      {"transl_except": "(pos:4..6,aa:Sec)"}, {"exception": "ribosomal slippage"},
                      {"pseudo": "true"}):
            with self.subTest(attrs=attrs):
                result = self.check("ATGTGAAAATAA", "MWK", attrs=attrs)
                self.assertEqual((result["checked"], result["unchecked"]), (0, 1))
                self.assertTrue(result["warnings"])
        self.assertEqual(self.check("ATGTGAAAATAA", "MUK")["unchecked"], 1)

    def test_missing_phase_and_conflicting_codon_start(self):
        self.assertEqual(self.check("ATGAAATTT", "MKF", phase=".")["unchecked"], 1)
        self.assertEqual(self.check("ATGAAATTT", "MKF", attrs={"codon_start": "2"})["unchecked"], 1)

    def test_missing_and_joined_mapping_not_guessed(self):
        result = validate_cds_translations({"c1": "ATGAAA"}, {"p1": "MK"}, [])
        self.assertEqual(result["unchecked"], 1)
        result = validate_cds_translations({"c1": "ATGAAA"}, {"p1": "MK"}, [feature(3), feature(3, start=4)])
        self.assertEqual(result["unchecked"], 1)

    def test_invalid_coordinate_and_alphabet_raise(self):
        with self.assertRaises(ValueError):
            validate_cds_translations({"c1": "ATG"}, {"p1": "M"}, [feature(6)])
        with self.assertRaises(ValueError):
            self.check("AT-", "M")
        with self.assertRaises(ValueError):
            self.check("ATG", "M-")

    def test_code_default_is_disclosed(self):
        result = self.check("ATGAAATTT", "MKF")
        self.assertTrue(any("table 11 assumed" in item for item in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
