"""Selected-target lineage import, including native ICEscreen scope routing."""
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from basis.config import default_config
from basis.hybas import PHYLUM_SOURCE, enrich_sample
from basis.inputs import load_samples
from basis.sample_policy import plan_sample_modules


class HybAsPhylumTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "sampleA"
        (self.root / "annotation").mkdir(parents=True)
        (self.root / "metadata").mkdir()
        self.reports = self.root / "reports/kraken2"
        self.reports.mkdir(parents=True)
        self.sample = {"sample_id": "sampleA", "taxon": "", "input_mode": "hybas"}
        for key, suffix, text in (("assembly", ".fna", ">1\nATGAAATAA\n"),
                                  ("proteins", ".faa", ">p1\nMK\n"),
                                  ("gff", ".gff", "##gff-version 3\n"),
                                  ("genbank", ".gbk", "LOCUS sample\n")):
            path = self.root / "annotation" / ("sampleA" + suffix)
            path.write_text(text)
            self.sample[key] = str(path)
        self.summary = self.root / "metadata/run_summary.json"
        self.summary.write_text(json.dumps({"isolate": "sampleA", "target_taxid": "400634", "run_kraken": True}))

    def report(self, platform="illumina", *, phylum="Bacillota", phylum_id="1239",
               target=True, indentation=True, positive=True, name="Lysinibacillus"):
        path = self.reports / (platform + ".report.txt")
        rows = [("100", "100", "0", "D", "2", "Bacteria"),
                ("90", "90", "0", "P", "1224", "  Pseudomonadota"),
                ("90", "90", "90", "S", "777", "    Leading unrelated species")]
        if phylum:
            rows.append(("10", "10", "0", "P", phylum_id, "  " + phylum))
        else:
            rows = rows[:1]
        if target:
            rows.append(("10", "10" if positive else "0", "0", "G", "400634",
                         ("    " if indentation else "") + name))
            rows.append(("1", "1", "1", "S", "888", "      Lysinibacillus unconfirmed_species"))
        with path.open("w", newline="") as stream:
            csv.writer(stream, delimiter="\t").writerows(rows)
        return path

    def test_exact_target_not_leading_species_supplies_phylum_and_provenance(self):
        path = self.report()
        result = enrich_sample(self.sample)
        self.assertEqual(result["taxon"], "Lysinibacillus")
        self.assertEqual(result["taxon_rank"], "genus")
        self.assertEqual(result["taxon_phylum"], "Bacillota")
        self.assertEqual(result["taxon_phylum_taxid"], "1239")
        self.assertEqual(result["taxon_phylum_source"], PHYLUM_SOURCE)
        scope = result["hybas_context"]["phylum_resolution"]
        self.assertEqual(scope["status"], "resolved")
        self.assertEqual(scope["evidence"][0]["source"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(scope["evidence"][0]["source"]["path"], str(path))
        self.assertEqual(scope["evidence"][0]["ancestors"][-1]["taxid"], "400634")
        self.assertEqual(scope["ignored_reports"][0]["status"], "not_found")
        self.assertNotIn("taxon_phylum", self.sample)

    def test_generic_other_phylum_is_retained_without_genus_lookup(self):
        self.report(phylum="Actinomycetota", phylum_id="201174", name="Prescottella")
        result = enrich_sample(self.sample)
        self.assertEqual(result["taxon"], "Prescottella")
        self.assertEqual(result["taxon_phylum"], "Actinomycetota")
        self.assertEqual(result["taxon_phylum_taxid"], "201174")

    def test_available_report_without_matching_target_blocks_assignment(self):
        self.report()
        self.report("nanopore", target=False)
        result = enrich_sample(self.sample)
        self.assertNotIn("taxon_phylum", result)
        self.assertEqual(result["hybas_context"]["phylum_resolution"]["reason"],
                         "selected_target_missing_from_available_report")

    def test_conflicting_enabled_reports_block_assignment_and_clear_previous_auto_value(self):
        self.report()
        first = enrich_sample(self.sample)
        self.report("nanopore", phylum="Actinomycetota", phylum_id="201174")
        result = enrich_sample(first)
        self.assertNotIn("taxon_phylum", result)
        self.assertNotIn("taxon_phylum_source", result)
        self.assertEqual(result["hybas_context"]["phylum_resolution"]["reason"],
                         "phylum_ancestry_conflict_between_reports")

    def test_missing_or_flat_ancestry_and_zero_support_remain_unknown(self):
        for options, reason in (({"phylum": ""}, "selected_target_phylum_ancestor_missing"),
                                ({"indentation": False}, "selected_target_phylum_ancestor_missing"),
                                ({"positive": False}, "selected_target_without_positive_clade_support")):
            with self.subTest(options=options):
                self.report(**options)
                result = enrich_sample(self.sample)
                self.assertNotIn("taxon_phylum", result)
                self.assertEqual(result["hybas_context"]["phylum_resolution"]["reason"], reason)

    def test_no_reports_and_disabled_reports_do_not_supply_phylum(self):
        result = enrich_sample(self.sample)
        self.assertNotIn("taxon_phylum", result)
        self.report()
        self.summary.write_text(json.dumps({"isolate": "sampleA", "target_taxid": "400634", "run_kraken": False}))
        result = enrich_sample(self.sample)
        self.assertNotIn("taxon_phylum", result)
        self.assertEqual(result["hybas_context"]["phylum_resolution"]["evidence"], [])
        self.assertTrue(all(r["status"] == "disabled" for r in result["hybas_context"]["phylum_resolution"]["ignored_reports"]))

    def test_explicit_phylum_and_declared_species_are_never_overridden(self):
        self.report()
        result = enrich_sample({**self.sample, "taxon": "Lysinibacillus irui", "taxon_rank": "species",
                                "taxon_source": "user_confirmed", "taxon_phylum": "User phylum"})
        self.assertEqual(result["taxon_phylum"], "User phylum")
        self.assertEqual(result["taxon_phylum_source"], "sample_sheet")
        self.assertEqual(result["taxon"], "Lysinibacillus irui")
        self.assertEqual(result["taxon_rank"], "species")
        self.assertTrue(any("explicit declaration was retained" in warning for warning in result["hybas_context"]["warnings"]))

    def test_declared_other_taxon_does_not_inherit_target_phylum(self):
        self.report()
        result = enrich_sample({**self.sample, "taxon": "Prescottella", "taxon_source": "user_confirmed"})
        self.assertNotIn("taxon_phylum", result)
        self.assertEqual(result["hybas_context"]["phylum_resolution"]["sample_assignment"],
                         "held_declared_taxon_differs_from_selected_target")

    def test_known_phylum_synonyms_can_agree_without_external_lookup(self):
        self.report()
        self.report("nanopore", phylum="Firmicutes")
        self.assertEqual(enrich_sample(self.sample)["taxon_phylum"], "Bacillota")

    def test_sample_loader_and_icescreen_policy_use_enrichment_automatically(self):
        self.report()
        manifest = self.root.parent / "samples.tsv"
        with manifest.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.sample), delimiter="\t")
            writer.writeheader()
            writer.writerow(self.sample)
        sample = load_samples(manifest)[0]
        config = default_config()
        config["modules"]["icescreen"]["enabled"] = True
        decision = plan_sample_modules(config, sample, extend=False)["icescreen"]
        self.assertEqual(decision["action"], "run")
        self.assertEqual(sample["taxon_phylum_source"], PHYLUM_SOURCE)
        self.assertEqual(sample["taxon_rank"], "genus")


if __name__ == "__main__":
    unittest.main()
