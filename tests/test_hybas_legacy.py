"""Legacy HybAs targets resolve from exact report taxids, never abundance guesses."""
from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from basis.hybas import OPERATIONAL_SOURCE, enrich_sample


class HybasLegacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis_hybas_legacy_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "isolate1"
        self.annotation = self.root / "annotation"
        self.annotation.mkdir(parents=True)
        self.metadata = self.root / "metadata"
        self.metadata.mkdir()
        self.reports = self.root / "reports" / "kraken2"
        self.reports.mkdir(parents=True)
        self.sample = {"sample_id": "isolate1", "taxon": "", "amrfinder_organism": ""}
        for key, extension, contents in (
            ("assembly", "fna", ">c1\nATGAAATTTTAA\n"),
            ("proteins", "faa", ">p1\nMKF\n"),
            ("gff", "gff", "##gff-version 3\nc1\tProkka\tCDS\t1\t12\t.\t+\t0\tID=p1;transl_table=11\n"),
        ):
            path = self.annotation / f"isolate1.{extension}"
            path.write_text(contents, encoding="utf-8")
            self.sample[key] = str(path)
        final = self.root / "work" / "final" / "assembly.final.fasta"
        final.parent.mkdir(parents=True)
        final.write_text(">original\nATGAAATTTTAA\n", encoding="utf-8")
        self.summary()

    def pairs(self, path, header, values):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(header)
            writer.writerows(values.items())

    def summary(self, *, tsv=False, **overrides):
        values = {"isolate": "isolate1", "workflow_version": "HybAs-8.7-ieee-batch",
                  "target_taxid": "400634", "run_kraken": True, "tool_kraken2_version": "2.synthetic"}
        values.update(overrides)
        path = self.metadata / ("run_summary.tsv" if tsv else "run_summary.json")
        if tsv:
            self.pairs(path, ("metric", "value"), values)
        else:
            path.write_text(json.dumps(values), encoding="utf-8")
        return path

    def decision(self, **fields):
        self.pairs(self.metadata / "kraken_decision.tsv", ("field", "value"),
                   {"isolate": "isolate1", **fields})

    def report(self, platform="illumina", *, name="Lysinibacillus", rank="G",
               include_target=True, clade_reads=1):
        # Synthetic distractors make highest abundance and top-three shortcuts fail.
        text = ("60.0\t60\t60\tS\t101\t  Highest abundance species\n"
                "25.0\t25\t25\tS\t102\t  Second abundance species\n"
                "10.0\t10\t10\tS\t103\t  Third abundance species\n"
                "4.0\t4\t4\tS\t104\t  Fourth abundance species\n")
        if include_target:
            text += f"{float(clade_reads)}\t{clade_reads}\t0\t{rank}\t400634\t    {name}\n"
        path = self.reports / f"{platform}.report.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_exact_legacy_taxid_resolves_beyond_top_three_with_qualified_provenance(self):
        for tsv in (False, True):
            for rank, name, expected_rank in (("G", "Lysinibacillus", "genus"),
                                               ("S", "Lysinibacillus irui", "species")):
                with self.subTest(tsv=tsv, rank=rank):
                    for path in self.metadata.glob("run_summary.*"):
                        path.unlink()
                    summary = self.summary(tsv=tsv)
                    report = self.report(rank=rank, name=name)
                    result = enrich_sample(self.sample)
                    context = result["hybas_context"]
                    target = context["selected_target"]
                    self.assertEqual(context["taxonomy_status"], "resolved")
                    self.assertEqual((target["taxid"], target["name"], target["rank"]),
                                     ("400634", name, rank))
                    self.assertEqual(target["source"], "legacy_run_summary_target")
                    self.assertEqual(target["selection_method"], "not_recorded")
                    self.assertEqual(target["resolution"], "kraken_report_exact_taxid")
                    self.assertTrue(target["positive_clade_support"])
                    self.assertNotIn("awareness_state", target)
                    self.assertEqual((result["taxon"], result["taxon_rank"]), (name, expected_rank))
                    self.assertEqual(result["taxon_source"], OPERATIONAL_SOURCE)
                    self.assertEqual(result["taxon_status"], "read_based_operational_target")
                    reads = context["read_taxonomy"]["illumina"]
                    self.assertEqual(reads["target_match"]["taxid"], "400634")
                    self.assertNotIn("400634", {row["taxid"] for row in reads["top_species"]})
                    sources = {Path(source["path"]): source for source in context["sources"]}
                    for path in (summary, report):
                        self.assertEqual(sources[path]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
                    self.assertEqual(result["amrfinder_organism"], "")
        self.assertEqual(self.sample["taxon"], "")

    def test_declared_species_survives_resolved_legacy_genus(self):
        self.report()
        result = enrich_sample({**self.sample, "taxon": "Lysinibacillus irui", "taxon_rank": "species",
                                "taxon_source": "user_confirmed", "amrfinder_organism": "Explicit setting"})
        self.assertEqual((result["taxon"], result["taxon_rank"], result["taxon_source"]),
                         ("Lysinibacillus irui", "species", "user_confirmed"))
        self.assertEqual(result["taxon_status"], "declared")
        self.assertEqual(result["amrfinder_organism"], "Explicit setting")
        self.assertEqual(result["hybas_context"]["selected_target"]["name"], "Lysinibacillus")

    def test_modern_selection_and_explicit_no_selection_block_legacy_fallback(self):
        self.report()
        modern = {"selected_target_taxid": "999", "selected_target_name": "Modern selected species",
                  "selected_target_rank": "S", "selected_target_source": "inferred_from_kraken"}
        for source in ("decision", "summary"):
            for selection in (modern, {"selected_target_taxid": "NA"}, {"selected_target_taxid": ""}):
                with self.subTest(source=source, selection=selection):
                    (self.metadata / "kraken_decision.tsv").unlink(missing_ok=True)
                    self.summary(**({"kraken_" + key: value for key, value in selection.items()}
                                    if source == "summary" else {}))
                    if source == "decision":
                        self.decision(**selection)
                    result = enrich_sample(self.sample)
                    target = result["hybas_context"]["selected_target"]
                    self.assertNotEqual(target["source"], "legacy_run_summary_target")
                    self.assertNotEqual(target["taxid"], "400634")
                    if selection is modern:
                        self.assertEqual(result["taxon"], "Modern selected species")
                        self.assertEqual(target["taxid"], "999")
                    else:
                        self.assertFalse(result.get("taxon"))
                        self.assertEqual(result["hybas_context"]["taxonomy_status"], "unresolved")

    def test_conflicting_legacy_json_and_tsv_targets_are_rejected(self):
        self.report()
        self.summary(tsv=True, target_taxid="999")
        with self.assertRaisesRegex(ValueError, "target_taxid"):
            enrich_sample(self.sample)

    def test_missing_target_row_retains_taxid_without_guessing_report_species(self):
        for report_exists in (False, True):
            with self.subTest(report_exists=report_exists):
                if report_exists:
                    self.report(include_target=False)
                result = enrich_sample(self.sample)
                context = result["hybas_context"]
                self.assertFalse(result.get("taxon"))
                self.assertEqual(context["taxonomy_status"], "unresolved")
                self.assertEqual(context["selected_target"]["taxid"], "400634")
                self.assertEqual(context["selected_target"]["source"], "legacy_run_summary_target")
                self.assertFalse(context["selected_target"]["name"])
                self.assertFalse(context["selected_target"]["rank"])
                self.assertIsNone(context["read_taxonomy"]["illumina"]["target_match"])
                self.assertTrue(context["warnings"])

    def test_conflicting_platform_names_or_ranks_prevent_auto_taxonomy(self):
        self.report()
        for name, rank in (("Different genus", "G"), ("Lysinibacillus", "S")):
            with self.subTest(name=name, rank=rank):
                self.report("nanopore", name=name, rank=rank)
                result = enrich_sample(self.sample)
                context = result["hybas_context"]
                self.assertFalse(result.get("taxon"))
                self.assertEqual(context["taxonomy_status"], "unresolved")
                self.assertEqual(context["selected_target"]["taxid"], "400634")
                self.assertTrue(context["warnings"])
                for platform in ("illumina", "nanopore"):
                    self.assertEqual(context["read_taxonomy"][platform]["target_match"]["taxid"], "400634")

    def test_zero_read_target_is_resolved_but_cannot_supply_taxonomy(self):
        self.report(clade_reads=0)
        result = enrich_sample(self.sample)
        context = result["hybas_context"]
        self.assertFalse(result.get("taxon"))
        self.assertEqual(context["taxonomy_status"], "resolved")
        self.assertEqual(context["selected_target"]["name"], "Lysinibacillus")
        self.assertFalse(context["selected_target"]["positive_clade_support"])
        self.assertEqual(context["read_taxonomy"]["illumina"]["target_match"]["clade_reads"], 0)
        self.assertTrue(context["warnings"])

    def test_disabled_legacy_or_explicit_modern_off_never_autofills_taxonomy(self):
        self.report()
        for disabled in (False, "false", "modern_off"):
            with self.subTest(disabled=disabled):
                (self.metadata / "kraken_decision.tsv").unlink(missing_ok=True)
                self.summary(run_kraken=True if disabled == "modern_off" else disabled)
                if disabled == "modern_off":
                    self.decision(kraken_decision_mode="off")
                result = enrich_sample(self.sample)
                context = result["hybas_context"]
                self.assertFalse(result.get("taxon"))
                self.assertEqual(context["taxonomy_status"], "disabled")
                self.assertEqual(context["read_taxonomy"]["illumina"]["status"], "disabled")
                self.assertIsNone(context["read_taxonomy"]["illumina"]["target_match"])


if __name__ == "__main__":
    unittest.main()
