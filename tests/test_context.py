"""Small native-format fixtures, including failures that must never mean no hits."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from basis.adapters import context as c


VIRUS_HEADER = "seq_name\tlength\ttopology\tcoordinates\tn_genes\tgenetic_code\tvirus_score\tfdr\tn_hallmarks\tmarker_enrichment\ttaxonomy\n"
PLASMID_HEADER = "seq_name\tlength\ttopology\tn_genes\tgenetic_code\tplasmid_score\tfdr\tn_hallmarks\tmarker_enrichment\tconjugation_genes\tamr_genes\n"
INTEGRON_HEADER = "ID_integron\tID_replicon\telement\tpos_beg\tpos_end\tstrand\tevalue\ttype_elt\tannotation\tmodel\ttype\tdefault\tdistance_2attC\tconsidered_topology\n"
SUMMARY_HEADER = "ID_replicon\tCALIN\tcomplete\tIn0\ttopology\tsize\n"
# Native no-hit format from IntegronFinder v2.0.6 scripts/finder.py, lines 673-676.
INTEGRON_NATIVE_ZERO = ("# integron_finder 2.0.6\n"
                        "# cmd: integron_finder isolate1.fna --lin --local-max\n"
                        "# No Integron found\n")
ICE_HEADER = "ICE_IME_id\tGenome_accession\tCategory_of_element\tStart_of_most_upstream_SP\tStop_of_most_downstream_SP\tList_SP_ordered_genomic_position\tComments_regarding_structure\n"


def ice_summary(accession="c1", complete=0):
    return (f"##################### ICEscreen statistics for genome accession {accession} #####################\n"
            f"Complete ICE (4 types of SP):\t{complete}\n"
            "Complete IME (R+I, R+C+I with distance between consecutive SPs <= 14 CDSs):\t0\n"
            "Conjugation module (R+C+V):\t0\n"
            "Mobilizable element (R+C with distance between consecutive SPs <= 14 CDS):\t0\n"
            "Partial ICE (at least V):\t0\n"
            "Other partial element (R+C, R+V, V+C with distance between consecutive SPs > 14 CDS):\t0\n"
            f"Total SPs:\t{complete * 4}\n"
            "Number of segments with exclusively isolated SPs to manually verify:\t0\n")


class ContextParsers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def anti(self, records):
        return self.write("anti.json", json.dumps({"version": "8.0.4", "records": records}))

    def test_antismash_coordinate_conversion_and_record_identity(self):
        path = self.anti([{"id": "c1", "original_id": "chromosome_original",
                           "areas": [{"start": 0, "end": 1500, "products": ["NRPS-like", "terpene"],
                                      "protoclusters": {}, "candidates": [], "subregions": []}]}])
        result = c.parse_antismash(path)
        finding = result["findings"][0]
        self.assertEqual((finding["start"], finding["end"]), (1, 1500))
        self.assertEqual(finding["contig"], "chromosome_original")
        self.assertEqual(finding["family"], "NRPS-like;terpene")
        self.assertEqual(result["interpretation"], "detected")

    def test_antismash_explicit_empty_regions_is_no_detection(self):
        result = c.parse_antismash(self.anti([{"id": "c1", "areas": []}]))
        self.assertEqual(result["interpretation"], "not_detected")

    def test_antismash_circular_region_split_without_count_inflation(self):
        result = c.parse_antismash(self.anti([{"id": "c1", "seq": {"data": "A" * 1000},
                                               "areas": [{"start": 900, "end": 100, "products": ["terpene"]}]}]))
        self.assertEqual([(f["start"], f["end"]) for f in result["findings"]], [(901, 1000), (1, 100)])
        self.assertEqual(result["metrics"]["region_count"], 1)
        self.assertEqual(len({f["attributes"]["native_region_id"] for f in result["findings"]}), 1)

    def test_antismash_missing_areas_is_failure(self):
        with self.assertRaises(ValueError):
            c.parse_antismash(self.anti([{"id": "c1"}]))

    def test_antismash_empty_records_is_failure(self):
        with self.assertRaises(ValueError):
            c.parse_antismash(self.anti([]))

    def test_antismash_skipped_record_is_failure(self):
        with self.assertRaises(ValueError):
            c.parse_antismash(self.anti([{"id": "c1", "areas": [], "skip": "no genes"}]))

    def test_genomad_provirus_maps_to_host_and_keeps_scores(self):
        virus = self.write("v.tsv", VIRUS_HEADER + "chromosome|provirus_101_200\t100\tProvirus\t101-200\t2\t11\t0.9776\tNA\t1\t3.2\tViruses;Duplodnaviria\n")
        plasmid = self.write("p.tsv", PLASMID_HEADER)
        result = c.parse_genomad(virus, plasmid)
        f = result["findings"][0]
        self.assertEqual((f["contig"], f["start"], f["end"]), ("chromosome", 101, 200))
        self.assertTrue(f["attributes"]["integrated"])
        self.assertEqual(f["attributes"]["native"]["virus_score"], "0.9776")

    def test_genomad_nonintegrated_virus_not_called_provirus(self):
        virus = self.write("v.tsv", VIRUS_HEADER + "phage_contig\t100\tDTR\tNA\t2\t11\t0.99\tNA\t1\t3.2\tViruses\n")
        plasmid = self.write("p.tsv", PLASMID_HEADER)
        f = c.parse_genomad(virus, plasmid)["findings"][0]
        self.assertFalse(f["attributes"]["integrated"])
        self.assertEqual(f["contig"], "phage_contig")

    def test_genomad_plasmid_coordinates(self):
        virus = self.write("v.tsv", VIRUS_HEADER)
        plasmid = self.write("p.tsv", PLASMID_HEADER + "p1\t5000\tDTR\t5\t11\t0.99\tNA\t2\t4.5\tNA\tNA\n")
        f = c.parse_genomad(virus, plasmid)["findings"][0]
        self.assertEqual((f["category"], f["start"], f["end"]), ("plasmid", 1, 5000))

    def test_genomad_requires_both_outputs(self):
        with self.assertRaises(ValueError):
            c.parse_genomad(self.write("v.tsv", VIRUS_HEADER), self.root / "absent.tsv")

    def test_genomad_mismatched_provirus_coordinates_fail(self):
        virus = self.write("v.tsv", VIRUS_HEADER + "c|provirus_101_200\t100\tProvirus\t102-201\t2\t11\t0.9\tNA\t1\t2\tViruses\n")
        with self.assertRaises(ValueError):
            c.parse_genomad(virus, self.write("p.tsv", PLASMID_HEADER))

    def test_nonfinite_prediction_score_fails(self):
        virus = self.write("v.tsv", VIRUS_HEADER + "c\t100\tDTR\tNA\t2\t11\tNaN\tNA\t1\t2\tViruses\n")
        with self.assertRaises(ValueError):
            c.parse_genomad(virus, self.write("p.tsv", PLASMID_HEADER))

    def test_mobsuite_retains_mobility_and_chromosome_count(self):
        path = self.write("contig_report.txt", "sample_id\tmolecule_type\tprimary_cluster_id\tcontig_id\tsize\tpredicted_mobility\trep_type(s)\n"
                          "S\tchromosome\t-\tc1\t4000000\t-\t-\n"
                          "S\tplasmid\tAA001\tp1\t12000\tmobilizable\trepA\n")
        result = c.parse_mobsuite(path)
        self.assertEqual(result["metrics"]["chromosome_contigs"], 1)
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["attributes"]["predicted_mobility"], "mobilizable")

    def test_mobsuite_header_only_not_complete_assessment(self):
        with self.assertRaises(ValueError):
            c.parse_mobsuite(self.write("mob.txt", "molecule_type\tcontig_id\tsize\tpredicted_mobility\n"))

    def test_mobsuite_invalid_molecule_does_not_disappear(self):
        with self.assertRaises(ValueError):
            c.parse_mobsuite(self.write("mob.txt", "molecule_type\tcontig_id\tsize\tpredicted_mobility\nunknown\tc1\t1000\t-\n"))

    def test_integron_groups_components_without_count_inflation(self):
        detail = self.write("s.integrons", "# IntegronFinder 2.0.6\n" + INTEGRON_HEADER +
                            "integron_01\tc1\tprotein_1\t101\t600\t1\t1e-20\tintegrase\tintI\tintI\tcomplete\tYes\t10\tlinear\n" +
                            "integron_01\tc1\tattc_1\t700\t760\t1\t1e-10\tattC\tattC\tattC\tcomplete\tYes\t0\tlinear\n")
        summary = self.write("s.summary", SUMMARY_HEADER + "c1\t0\t1\t0\tlinear\t1000\n")
        result = c.parse_integronfinder(detail, summary)
        self.assertEqual(result["metrics"]["integron_count"], 1)
        self.assertEqual((result["findings"][0]["start"], result["findings"][0]["end"]), (101, 760))

    def test_integron_consistent_zero(self):
        result = c.parse_integronfinder(self.write("s.integrons", INTEGRON_HEADER),
                                        self.write("s.summary", SUMMARY_HEADER + "c1\t0\t0\t0\tlinear\t1000\n"))
        self.assertEqual(result["interpretation"], "not_detected")

    def test_integron_native_comment_only_zero(self):
        result = c.parse_integronfinder(self.write("s.integrons", INTEGRON_NATIVE_ZERO),
                                        self.write("s.summary", SUMMARY_HEADER + "c1\t0\t0\t0\tlin\t1000\n"))
        self.assertEqual(result["interpretation"], "not_detected")
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["metrics"]["integron_count"], 0)
        self.assertEqual(result["metrics"]["summary"][0]["ID_replicon"], "c1")

    def test_integron_native_zero_requires_zero_summary_counts(self):
        for counts in ("1\t0\t0", "0\t1\t0", "0\t0\t1"):
            with self.subTest(counts=counts), self.assertRaisesRegex(ValueError, "counts disagree"):
                c.parse_integronfinder(self.write("s.integrons", INTEGRON_NATIVE_ZERO),
                                       self.write("s.summary", SUMMARY_HEADER + f"c1\t{counts}\tlin\t1000\n"))

    def test_integron_native_zero_requires_valid_assessed_summary(self):
        for summary in ("", "# No Integron found\n", SUMMARY_HEADER,
                        SUMMARY_HEADER + "c1\tNA\t0\t0\tlin\t1000\n"):
            with self.subTest(summary=summary), self.assertRaises(ValueError):
                c.parse_integronfinder(self.write("s.integrons", INTEGRON_NATIVE_ZERO),
                                       self.write("s.summary", summary))
        with self.assertRaisesRegex(ValueError, "missing"):
            c.parse_integronfinder(self.write("s.integrons", INTEGRON_NATIVE_ZERO), self.root / "absent.summary")

    def test_integron_missing_empty_or_unrecognized_comments_are_not_zero(self):
        summary = self.write("s.summary", SUMMARY_HEADER + "c1\t0\t0\t0\tlin\t1000\n")
        for detail in ("", "# integron_finder 2.0.6\n# cmd: interrupted\n",
                       "# No Integron found - partial output\n", "# No integron found\n"):
            with self.subTest(detail=detail), self.assertRaises(ValueError):
                c.parse_integronfinder(self.write("s.integrons", detail), summary)
        with self.assertRaisesRegex(ValueError, "missing"):
            c.parse_integronfinder(self.root / "absent.integrons", summary)

    def test_integron_native_zero_marker_cannot_hide_tabular_data(self):
        for data in (INTEGRON_HEADER, INTEGRON_HEADER +
                     "integron_01\tc1\tprotein_1\t101\t600\t1\t1e-20\tintegrase\tintI\tintI\tIn0\tYes\t10\tlin\n"):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, "marker accompanies"):
                c.parse_integronfinder(self.write("s.integrons", INTEGRON_NATIVE_ZERO + data),
                                       self.write("s.summary", SUMMARY_HEADER + "c1\t0\t0\t0\tlin\t1000\n"))

    def test_integron_lost_hits_rejected(self):
        with self.assertRaisesRegex(ValueError, "counts disagree"):
            c.parse_integronfinder(self.write("s.integrons", INTEGRON_HEADER),
                                   self.write("s.summary", SUMMARY_HEADER + "c1\t0\t1\t0\tlinear\t1000\n"))

    def test_circular_integron_not_falsely_linked_to_whole_chromosome(self):
        detail = self.write("s.integrons", INTEGRON_HEADER +
                            "integron_01\tc1\tp1\t10\t100\t1\t1e-20\tintegrase\tintI\tintI\tcomplete\tYes\t10\tcircular\n" +
                            "integron_01\tc1\tattc_1\t970\t990\t1\t1e-10\tattC\tattC\tattC\tcomplete\tYes\t0\tcircular\n")
        summary = self.write("s.summary", SUMMARY_HEADER + "c1\t0\t1\t0\tcircular\t1000\n")
        finding = c.parse_integronfinder(detail, summary)["findings"][0]
        self.assertIsNone(finding["start"])
        self.assertEqual(finding["attributes"]["component_intervals"], [[10, 100], [970, 990]])

    def test_isescan_reads_real_column_names_and_reverse_strand(self):
        p = self.write("s.fna.tsv", "seqID\tfamily\tcluster\tisBegin\tisEnd\tisLen\tstrand\ttype\n"
                       "c1\tIS3\tIS3_1\t100\t1099\t1000\t-\tc\n")
        f = c.parse_isescan(p)["findings"][0]
        self.assertEqual((f["start"], f["end"], f["strand"]), (100, 1099, "-"))

    def test_isescan_length_mismatch_fails(self):
        p = self.write("s.tsv", "seqID\tfamily\tisBegin\tisEnd\tisLen\tstrand\nc1\tIS3\t100\t1099\t999\t+\n")
        with self.assertRaises(ValueError):
            c.parse_isescan(p)

    def test_icescreen_signature_span_is_not_an_element_boundary(self):
        p = self.write("s_detected_ME.tsv", ICE_HEADER + "1\tc1\tICE_complete\t100\t20000\tgene1,gene2\tReview nested element\n")
        f = c.parse_icescreen(p)["findings"][0]
        self.assertIsNone(f["start"])
        self.assertIsNone(f["end"])
        self.assertEqual(f["attributes"]["native"]["Start_of_most_upstream_SP"], "100")
        self.assertTrue(f["attributes"]["manual_review_required"])

    def test_icescreen_explicit_header_only(self):
        self.assertEqual(c.parse_icescreen(self.write("s.tsv", ICE_HEADER))["interpretation"], "not_detected")

    def test_icescreen_native_summary_zero_requires_every_genbank_record(self):
        table = self.write("ice.tsv", ICE_HEADER)
        summary = self.write("ice.summary", ice_summary("c1") + ice_summary("c2"))
        r = c.parse_icescreen(table, summary, {"c1", "c2"})
        self.assertEqual(r["metrics"]["assessed_genome_accessions"], ["c1", "c2"])
        with self.assertRaisesRegex(ValueError, "accessions do not match"):
            c.parse_icescreen(table, summary, {"c1", "c2", "c3"})

    def test_icescreen_summary_detects_lost_element_rows(self):
        table = self.write("ice.tsv", ICE_HEADER)
        summary = self.write("ice.summary", ice_summary(complete=1))
        with self.assertRaisesRegex(ValueError, "summary and element table disagree"):
            c.parse_icescreen(table, summary, {"c1"})

    def test_icescreen_native_positive_counts_and_dash_comment(self):
        table = self.write("ice.tsv", ICE_HEADER + "ID_1\tc1\tComplete ICE (I+R+C+V)\t1\t99\tp1,p2,p3,p4\t-\n")
        summary = self.write("ice.summary", ice_summary(complete=1))
        r = c.parse_icescreen(table, summary, {"c1"})
        self.assertFalse(r["findings"][0]["attributes"]["manual_review_required"])
        self.assertEqual(r["findings"][0]["attributes"]["signature_span"]["start"], 1)
        self.assertIsNone(r["findings"][0]["start"])

    def test_icescreen_nonmonotonic_signature_extremities_need_review(self):
        # Upstream 1.3.3 test3 includes this case; do not assert circularity.
        table = self.write("ice.tsv", ICE_HEADER + "ID_1\tc1\tComplete ICE (I+R+C+V)\t990\t100\tp1,p2\t-\n")
        f = c.parse_icescreen(table)["findings"][0]
        self.assertIsNone(f["start"])
        self.assertNotIn("signature_span", f["attributes"])
        self.assertTrue(f["attributes"]["manual_review_required"])
        self.assertEqual(f["attributes"]["signature_extremities"]["upstream_start"], 990)

    def test_icescreen_rejects_truncated_or_duplicate_summary(self):
        table = self.write("ice.tsv", ICE_HEADER)
        for text in ("analysis finished\n", ice_summary().replace("Total SPs:\t0\n", ""), ice_summary() * 2):
            with self.subTest(text=text), self.assertRaises(ValueError):
                c.parse_icescreen(table, self.write("ice.summary", text), {"c1"})

    def test_icescreen_genbank_annotation_and_record_validation(self):
        genbank = ("LOCUS       c1 99 bp DNA\nACCESSION   AC1\nVERSION     AC1.2\n"
                   "FEATURES             Location/Qualifiers\n     CDS             1..99\n"
                   "ORIGIN\n        1 atg\n//\n")
        self.assertEqual(c._icescreen_input_accessions(self.write("s.gbk", genbank)), {"AC1.2"})
        for malformed in (genbank.replace("     CDS", "     gene"), genbank.replace("ORIGIN", ""),
                          genbank.replace("//", ""), genbank * 2):
            with self.subTest(text=malformed), self.assertRaises(ValueError):
                c._icescreen_input_accessions(self.write("s.gbk", malformed))

    def test_icescreen_preserves_explicit_prefix_environment(self):
        gb = self.write("s.gbk", "LOCUS       c1 9 bp DNA\nFEATURES             Location/Qualifiers\n"
                        "     CDS             1..9\nORIGIN\n        1 atgatgatg\n//\n")
        runner = Mock()
        def output(argv, cwd):
            self.assertIn("--galaxy", argv)
            native = Path(argv[argv.index("-o") + 1])
            self.assertTrue(native.is_dir(), "ICEscreen requires an existing output directory")
            self.assertEqual(list(native.iterdir()), [], "ICEscreen must receive a fresh, empty directory")
            self.write(str(native / "basis_detected_ME.tsv"), ICE_HEADER)
            self.write(str(native / "basis_detected_ME.summary"), ice_summary())
        runner.run.side_effect = output
        result = c.run("icescreen", {"sample_id": "S", "assembly": "/s.fna", "genbank": str(gb)},
                       {"phylum": "bacillota"}, self.root / "run-ice", runner)
        self.assertEqual(result["interpretation"], "not_detected")

    def test_icescreen_refuses_existing_output_directory_and_preserves_results(self):
        for occupied in (False, True):
            with self.subTest(occupied=occupied):
                outdir = self.root / f"run-ice-{occupied}"
                native = outdir / "native"
                native.mkdir(parents=True)
                sentinel = native / "prior-result.txt"
                if occupied:
                    sentinel.write_text("preserved native evidence\n")
                runner = Mock()
                with self.assertRaisesRegex(ValueError, "Refusing stale native results"):
                    c.run("icescreen", {"sample_id": "S", "assembly": "/s.fna"},
                          {"phylum": "bacillota"}, outdir, runner)
                runner.run.assert_not_called()
                self.assertFalse((outdir / "genbank_input").exists())
                if occupied:
                    self.assertEqual(sentinel.read_text(), "preserved native evidence\n")
                else:
                    self.assertEqual(list(native.iterdir()), [])

    def isescan_zero_runner(self, stdout=None, *, unfinished=False, no_proteins=False):
        assembly = self.write("annotation/s.fna", ">c1\nATGATGATG\n")
        native_stdout = ("No IS element was found for ['annotation/s.fna']\n"
                         "End in pred Thu Oct 8 00:00:00 2026\n"
                         "Both complete and partial IS elements are reported.\n"
                         "ISEScan ends at Thu Oct 8 00:00:01 2026\n")
        runner = Mock()
        def output(argv, cwd):
            native = Path(argv[argv.index("--output") + 1])
            self.write(str(native / "proteome/annotation/s.fna.faa"), "" if no_proteins else ">c1_1_9_+\nMMM\n")
            for database in ("clusters.single.faa", "clusters.faa.hmm"):
                self.write(str(native / f"hmm/annotation/{database}.s.fna.faa"),
                           "# HMMER table\n" + ("" if unfinished else "# [ok]\n"))
            return subprocess.CompletedProcess(argv, 0, native_stdout if stdout is None else stdout, "")
        runner.run.side_effect = output
        return assembly, runner

    def test_isescan_native_zero_without_tsv_verified_against_artifacts(self):
        assembly, runner = self.isescan_zero_runner()
        r = c.run("isescan", {"sample_id": "S", "assembly": str(assembly)}, {}, self.root / "run-is", runner)
        self.assertEqual(r["interpretation"], "not_detected")
        self.assertEqual(r["metrics"]["insertion_sequence_count"], 0)
        self.assertTrue(any(p.endswith("isescan_stdout.txt") for p in r["artifacts"]))

    def test_isescan_missing_or_wrong_native_zero_marker_not_negative(self):
        for index, text in enumerate(("", "ISEScan ends at now\n", "No IS element was found for ['other/s.fna']\nEnd in pred now\nISEScan ends at now\n",
                                     "No IS element was found for ['annotation/s.fna']\n")):
            assembly, runner = self.isescan_zero_runner(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                c.run("isescan", {"sample_id": "S", "assembly": str(assembly)}, {}, self.root / f"run-is-{index}", runner)

    def test_isescan_no_table_without_complete_searches_or_proteins_not_negative(self):
        for index, options in enumerate(({"unfinished": True}, {"no_proteins": True})):
            assembly, runner = self.isescan_zero_runner(**options)
            with self.subTest(options=options), self.assertRaises(ValueError):
                c.run("isescan", {"sample_id": "S", "assembly": str(assembly)}, {}, self.root / f"run-is-{index}", runner)

    def test_empty_and_html_do_not_become_no_hits(self):
        for text in ("", "<!DOCTYPE html><html>Error</html>"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                c.parse_isescan(self.write("bad.tsv", text))

    def test_truncated_row_rejected(self):
        p = self.write("s.tsv", "seqID\tfamily\tisBegin\tisEnd\tstrand\nc1\tIS3\t100\n")
        with self.assertRaises(ValueError):
            c.parse_isescan(p)

    def test_native_missing_output_is_failure_even_exit_success(self):
        sample = {"sample_id": "S", "assembly": str(self.root / "s.fna")}
        with self.assertRaises(ValueError):
            c.run("genomad", sample, {"database": "/db"}, self.root / "run", Mock())

    def test_native_genomad_splits_forwarding_and_unchanged_default(self):
        for splits in (None, 8):
            with self.subTest(splits=splits):
                out = self.root / f"run-{splits}"
                runner = Mock()

                def native_outputs(argv, cwd):
                    native = Path(argv[-2])
                    self.write(str(native / "s_summary/s_virus_summary.tsv"), VIRUS_HEADER)
                    self.write(str(native / "s_summary/s_plasmid_summary.tsv"), PLASMID_HEADER)

                runner.run.side_effect = native_outputs
                config = {"database": "/db", "threads": 4}
                expected = ["genomad", "end-to-end", "--threads", "4"]
                if splits is not None:
                    config["splits"] = splits
                    expected += ["--splits", "8"]
                expected += ["/s.fna", str(out / "native"), "/db"]
                result = c.run("genomad", {"sample_id": "S", "assembly": "/s.fna"}, config, out, runner)
                runner.run.assert_called_once_with(expected, cwd=out)
                self.assertEqual(result["interpretation"], "not_detected")

    def test_native_genomad_rejects_invalid_splits_before_execution(self):
        for splits in (0, -1, 1.5, True, False, None, "eight"):
            with self.subTest(splits=splits):
                runner = Mock()
                with self.assertRaisesRegex(ValueError, "genomad.splits"):
                    c.run("genomad", {"sample_id": "S", "assembly": "/s.fna"},
                          {"database": "/db", "splits": splits}, self.root / "run", runner)
                runner.run.assert_not_called()

    def test_native_integron_zero_assesses_all_assembly_replicons(self):
        assembly = self.write("s.fna", ">c1\n" + "A" * 1000 + "\n>c2\n" + "C" * 1000 + "\n")
        runner = Mock()

        def native_outputs(argv, cwd):
            native = Path(argv[argv.index("--outdir") + 1])
            self.write(str(native / "Results_Integron_Finder_s/s.integrons"), INTEGRON_NATIVE_ZERO)
            self.write(str(native / "Results_Integron_Finder_s/s.summary"),
                       SUMMARY_HEADER + "c1\t0\t0\t0\tlin\t1000\nc2\t0\t0\t0\tlin\t1000\n")

        runner.run.side_effect = native_outputs
        result = c.run("integronfinder", {"sample_id": "S", "assembly": str(assembly)},
                       {}, self.root / "run", runner)
        self.assertEqual(result["interpretation"], "not_detected")
        self.assertEqual(len(result["metrics"]["summary"]), 2)

    def test_native_integron_rejects_missing_or_unexpected_assessed_replicons(self):
        assembly = self.write("s.fna", ">c1\n" + "A" * 1000 + "\n>c2\n" + "C" * 1000 + "\n")
        for index, summary_ids in enumerate(("c1", "c2", "c1 c2 other")):
            with self.subTest(summary_ids=summary_ids):
                runner = Mock()

                def native_outputs(argv, cwd):
                    native = Path(argv[argv.index("--outdir") + 1])
                    self.write(str(native / "s.integrons"), INTEGRON_NATIVE_ZERO)
                    self.write(str(native / "s.summary"), SUMMARY_HEADER + "".join(
                        f"{ident}\t0\t0\t0\tlin\t1000\n" for ident in summary_ids.split()))

                runner.run.side_effect = native_outputs
                with self.assertRaisesRegex(ValueError, "summary replicons do not match assembly"):
                    c.run("integronfinder", {"sample_id": "S", "assembly": str(assembly)},
                          {}, self.root / f"run-{index}", runner)

    def test_native_integron_positive_result_also_requires_all_replicons(self):
        assembly = self.write("s.fna", ">c1\n" + "A" * 1000 + "\n>c2\n" + "C" * 1000 + "\n")
        runner = Mock()

        def native_outputs(argv, cwd):
            native = Path(argv[argv.index("--outdir") + 1])
            self.write(str(native / "s.integrons"), INTEGRON_HEADER +
                       "integron_01\tc1\tprotein_1\t101\t600\t1\t1e-20\tintegrase\tintI\tintI\tIn0\tYes\t10\tlin\n")
            self.write(str(native / "s.summary"), SUMMARY_HEADER + "c1\t0\t0\t1\tlin\t1000\n")

        runner.run.side_effect = native_outputs
        with self.assertRaisesRegex(ValueError, "summary replicons do not match assembly"):
            c.run("integronfinder", {"sample_id": "S", "assembly": str(assembly)},
                  {}, self.root / "run", runner)

    def test_native_process_failure_propagates(self):
        runner = Mock()
        runner.run.side_effect = RuntimeError("engine failed")
        with self.assertRaisesRegex(RuntimeError, "engine failed"):
            c.run("isescan", {"sample_id": "S", "assembly": "/s.fna"}, {}, self.root / "run", runner)

    def test_no_stale_results_allowed(self):
        (self.root / "run" / "native").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "stale"):
            c.run("isescan", {"sample_id": "S", "assembly": "/s.fna"}, {}, self.root / "run", Mock())

    def test_icescreen_requires_genbank(self):
        with self.assertRaisesRegex(ValueError, "sample.genbank"):
            c.run("icescreen", {"sample_id": "S", "assembly": "/s.fna"}, {"phylum": "bacillota"}, self.root / "run", Mock())

    def test_unsupported_optional_adapter_explicit(self):
        with self.assertRaises(NotImplementedError):
            c.run("crisprcasfinder", {}, {}, self.root / "run", Mock())


if __name__ == "__main__":
    unittest.main()
