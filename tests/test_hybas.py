import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from basis.hybas import OPERATIONAL_SOURCE, enrich_sample


class HybAsImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "isolate1"
        self.annotation = self.root / "annotation"
        self.annotation.mkdir(parents=True)
        self.metadata = self.root / "metadata"
        self.metadata.mkdir()
        self.reports = self.root / "reports/kraken2"
        self.reports.mkdir(parents=True)
        self.final = self.root / "work/final/assembly.final.fasta"
        self.final.parent.mkdir(parents=True)
        self.final.write_text(">original_a\nATGAAATAA\n>original_b\nCCCCGGGG\n")
        self.sample = {"sample_id": "isolate1", "taxon": "",
                       "amrfinder_organism": ""}
        for key, suffix, content in (
            ("assembly", ".fna", ">annotated_a\nATGAAATAA\n>annotated_b\nCCCCGGGG\n"),
            ("proteins", ".faa", ">protein1\nMK\n"),
            ("gff", ".gff", "##gff-version 3\n"),
        ):
            path = self.annotation / ("isolate1" + suffix)
            path.write_text(content)
            self.sample[key] = str(path)
        self.decision_values = {
            "isolate": "isolate1", "kraken_decision_mode": "auto",
            "selected_target_taxid": "400634", "selected_target_name": "Lysinibacillus",
            "selected_target_rank": "G", "selected_target_source": "inferred_from_kraken",
            "selected_target_percent_for_decision": "95.0",
        }
        self.write_decision()

    def pairs(self, path, header, values):
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(header)
            writer.writerows(values.items())

    def write_decision(self, **overrides):
        values = {**self.decision_values, **overrides}
        self.pairs(self.metadata / "kraken_decision.tsv", ("field", "value"), values)

    def write_summary(self, *, tsv=False, **overrides):
        values = {"isolate": "isolate1", "workflow_version": "test-workflow",
                  "tool_kraken2_version": "2.test", "kraken_db_dir": "/upstream/database"}
        values.update({"kraken_" + key: value for key, value in self.decision_values.items()
                       if key.startswith("selected_target_")})
        values.update(overrides)
        path = self.metadata / ("run_summary.tsv" if tsv else "run_summary.json")
        if tsv:
            self.pairs(path, ("metric", "value"), values)
        else:
            path.write_text(json.dumps(values))
        return path

    def write_report(self, platform="illumina", text=None):
        path = self.reports / (platform + ".report.txt")
        path.write_text(text if text is not None else
                        "95.00\t95\t0\tG\t400634\t  Lysinibacillus\n"
                        "94.00\t94\t94\tS\t123456\t    Lysinibacillus irui\n")
        return path

    def write_checksums(self, rows):
        path = self.metadata / "checksums.tsv"
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(("label", "path", "sha256"))
            writer.writerows(rows)
        return path

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_inferred_genus_and_species_have_operational_provenance(self):
        for rank, name, expected in (("G", "Lysinibacillus", "genus"),
                                     ("S", "Lysinibacillus irui", "species")):
            with self.subTest(rank=rank):
                self.write_decision(selected_target_rank=rank, selected_target_name=name)
                result = enrich_sample(self.sample)
                self.assertEqual(result["taxon"], name)
                self.assertEqual(result["taxon_rank"], expected)
                self.assertEqual(result["taxon_source"], OPERATIONAL_SOURCE)
                self.assertEqual(result["taxon_status"], "read_based_operational_target")
                self.assertEqual(result["hybas_context"]["binding"]["status"], "sequence_verified")
                self.assertEqual(result["amrfinder_organism"], "")
        self.assertEqual(self.sample["taxon"], "")
        self.assertNotIn("hybas_context", self.sample)

    def test_malformed_and_duplicate_metadata_cannot_supply_taxonomy(self):
        decision = self.metadata / "kraken_decision.tsv"
        for suffix in ("isolate\tisolate1\n", "bad row\n", "bad key\tvalue\n"):
            with self.subTest(suffix=suffix):
                self.write_decision()
                with decision.open("a") as handle:
                    handle.write(suffix)
                with self.assertRaises(ValueError):
                    enrich_sample(self.sample)
        self.write_decision()
        summary = self.metadata / "run_summary.json"
        for content in ('{"isolate":"isolate1","isolate":"isolate1"}',
                        '{"isolate":"isolate1","timestamp":1e999}'):
            with self.subTest(content=content):
                summary.write_text(content)
                with self.assertRaises(ValueError):
                    enrich_sample(self.sample)
        summary.unlink()
        for update in ({"selected_target_taxid": "not-a-taxid"},
                       {"selected_target_rank": "family"},
                       {"selected_target_percent_for_decision": "NaN"}):
            with self.subTest(update=update):
                self.write_decision(**update)
                with self.assertRaises(ValueError):
                    enrich_sample(self.sample)

    def test_explicit_root_and_annotation_set_must_match_sample(self):
        self.assertEqual(enrich_sample({**self.sample, "hybas_dir": str(self.root)})["taxon"],
                         "Lysinibacillus")
        for update in ({"hybas_dir": str(self.root.parent)},
                       {"hybas_dir": str(self.root), "sample_id": "other"},
                       {"proteins": str(self.annotation / "other.faa")},
                       {"assembly": str(self.annotation / "other.fna")},
                       {"genbank": str(self.annotation / "other.gbk")}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                enrich_sample({**self.sample, **update})
        unrelated = {**self.sample, "gff": str(self.root / "isolate1.gff")}
        self.assertNotIn("hybas_context", enrich_sample(unrelated))

    def test_all_embedded_isolate_ids_are_required_and_case_sensitive(self):
        for source in ("decision", "json", "tsv"):
            for isolate in ("other", "Isolate1", ""):
                with self.subTest(source=source, isolate=isolate):
                    self.write_decision()
                    for path in self.metadata.glob("run_summary.*"):
                        path.unlink()
                    if source == "decision":
                        self.write_decision(isolate=isolate)
                    else:
                        self.write_summary(tsv=source == "tsv", isolate=isolate)
                    with self.assertRaisesRegex(ValueError, "isolate must match"):
                        enrich_sample(self.sample)

    def test_conflicting_decision_and_summary_are_rejected(self):
        for tsv in (False, True):
            with self.subTest(tsv=tsv):
                for path in self.metadata.glob("run_summary.*"):
                    path.unlink()
                self.write_summary(tsv=tsv, kraken_selected_target_name="Another genus")
                with self.assertRaisesRegex(ValueError, "decision/summary conflict"):
                    enrich_sample(self.sample)

    def test_sequence_binding_ignores_headers_order_and_wrapping(self):
        self.final.write_text(">different_b extra description\nCCCC\nGGGG\n>different_a\natgaaa\ntaa\n")
        result = enrich_sample(self.sample)
        self.assertEqual(result["hybas_context"]["binding"]["status"], "sequence_verified")

    def test_sequence_binding_detects_changes_and_duplicate_multiplicity(self):
        for content in (">a\nATGAAATAG\n>b\nCCCCGGGG\n",
                        ">a\nATGAAATAA\n>b\nCCCCGGGG\n>duplicate\nCCCCGGGG\n"):
            with self.subTest(content=content):
                self.final.write_text(content)
                with self.assertRaisesRegex(ValueError, "sequences do not match"):
                    enrich_sample(self.sample)

    def test_missing_final_assembly_discloses_weaker_binding(self):
        self.final.unlink()
        context = enrich_sample(self.sample)["hybas_context"]
        self.assertEqual(context["binding"]["status"], "sample_id_directory_only")
        self.assertTrue(any("final assembly missing" in item for item in context["warnings"]))

    def test_missing_metadata_does_not_promote_report_species(self):
        (self.metadata / "kraken_decision.tsv").unlink()
        self.write_report()
        result = enrich_sample(self.sample)
        self.assertEqual(result["taxon"], "")
        self.assertEqual(result["hybas_context"]["status"], "not_found")
        self.assertEqual(result["hybas_context"]["read_taxonomy"]["illumina"]["top_species"][0]["name"],
                         "Lysinibacillus irui")

    def test_species_report_does_not_promote_genus_target(self):
        self.write_report()
        result = enrich_sample(self.sample)
        self.assertEqual((result["taxon"], result["taxon_rank"]), ("Lysinibacillus", "genus"))
        self.assertEqual(result["hybas_context"]["read_taxonomy"]["illumina"]["status"], "available")

    def test_summary_tsv_fallback_retains_target_and_upstream_provenance(self):
        (self.metadata / "kraken_decision.tsv").unlink()
        self.write_summary(tsv=True)
        result = enrich_sample(self.sample)
        self.assertEqual(result["taxon"], "Lysinibacillus")
        self.assertEqual(result["hybas_context"]["upstream"]["workflow_version"], "test-workflow")
        self.assertEqual(result["hybas_context"]["sources"][0]["role"], "summary_tsv")

    def test_declared_taxonomy_and_amrfinder_organism_are_preserved(self):
        for name in ("Lysinibacillus irui", "  Manually declared other species  "):
            with self.subTest(name=name):
                result = enrich_sample({**self.sample, "taxon": name,
                                        "taxon_source": "", "taxon_status": "",
                                        "amrfinder_organism": "Explicit supported value"})
                self.assertEqual(result["taxon"], name)
                self.assertEqual(result["taxon_source"], "sample_sheet")
                self.assertEqual(result["taxon_status"], "declared")
                self.assertEqual(result["amrfinder_organism"], "Explicit supported value")
                if name.startswith("  "):
                    self.assertTrue(any("declared taxon retained" in item
                                        for item in result["hybas_context"]["warnings"]))
                else:
                    self.assertFalse(any("differs" in item
                                         for item in result["hybas_context"]["warnings"]))

    def test_manual_targets_and_placeholders_never_supply_taxon(self):
        for source, rank, name in (("manual", "MANUAL", "MANUAL_TARGET"),
                                   ("manual", "S", "Lysinibacillus irui"),
                                   ("inferred_from_kraken", "S", "MANUAL_TARGET_IN_AUTO")):
            with self.subTest(source=source, rank=rank):
                self.write_decision(selected_target_source=source, selected_target_rank=rank,
                                    selected_target_name=name)
                self.assertEqual(enrich_sample(self.sample)["taxon"], "")

    def test_user_confirmation_replaces_old_operational_status(self):
        sample = enrich_sample(self.sample)
        self.assertEqual(sample["taxon_status"], "read_based_operational_target")
        sample.update(taxon="Lysinibacillus irui", taxon_source="user_confirmed",
                      taxon_rank="species")
        result = enrich_sample(sample)
        self.assertEqual(result["taxon"], "Lysinibacillus irui")
        self.assertEqual(result["taxon_source"], "user_confirmed")
        self.assertEqual(result["taxon_rank"], "species")
        self.assertEqual(result["taxon_status"], "declared")

    def test_read_statuses_distinguish_not_found_empty_and_disabled(self):
        context = enrich_sample(self.sample)["hybas_context"]
        self.assertEqual(context["read_taxonomy"]["illumina"]["status"], "not_found")
        self.write_report(text="")
        context = enrich_sample(self.sample)["hybas_context"]
        self.assertEqual(context["read_taxonomy"]["illumina"]["status"], "empty")
        self.write_report()
        self.write_decision(kraken_decision_mode="off")
        result = enrich_sample(self.sample)
        self.assertEqual(result["taxon"], "")
        for platform in ("illumina", "nanopore"):
            evidence = result["hybas_context"]["read_taxonomy"][platform]
            self.assertEqual(evidence["status"], "disabled")
            self.assertEqual(evidence["top_species"], [])

    def test_sources_are_hashed_and_irrelevant_checksum_paths_are_not_opened(self):
        summary = self.write_summary()
        report = self.write_report()
        checksums = self.write_checksums([
            ("final_assembly", "/upstream/moved/assembly.final.fasta", self.digest(self.final)),
            ("run_summary_json", "/upstream/moved/run_summary.json", self.digest(summary)),
            ("raw_illumina_reads", "/unavailable/huge.fastq.gz", "not-read-or-validated"),
        ])
        sources = enrich_sample(self.sample)["hybas_context"]["sources"]
        by_role = {source["role"]: source for source in sources}
        for role, path in (("decision", self.metadata / "kraken_decision.tsv"),
                           ("summary_json", summary), ("illumina_report", report),
                           ("checksums", checksums)):
            self.assertEqual(by_role[role]["path"], str(path))
            self.assertEqual(by_role[role]["sha256"], self.digest(path))

    def test_stale_assembly_and_summary_checksums_are_rejected(self):
        summary = self.write_summary()
        for label, path in (("final_assembly", self.final), ("run_summary_json", summary)):
            with self.subTest(label=label):
                self.write_checksums([(label, str(path), "0" * 64)])
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    enrich_sample(self.sample)


if __name__ == "__main__":
    unittest.main()
