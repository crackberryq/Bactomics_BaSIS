"""Batch, external-input, setup-barrier and resource-path contracts.

All sequences are tiny synthetic consistency fixtures. These tests do not run
native biological tools or infer a taxon from sequence content.
"""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from basis.config import ALL_MODULES, default_config
from safety.context import SafetyContext
from safety.setup import validate


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis-context-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.resource_root = self.work / "shared-resources"

    def annotation(self, folder, sample_id=None, *, arbitrary=False, bad_translation=False):
        folder = self.work / folder
        sid = sample_id or folder.name
        annotation = folder / ("custom annotations" if arbitrary else "annotation")
        annotation.mkdir(parents=True, exist_ok=True)
        stem = "draft" if arbitrary else sid
        contents = {"assembly": ("fna", ">contig1\nATGAAATTTTAA\n"),
                    "proteins": ("faa", ">protein1\n" + ("MKY" if bad_translation else "MKF") + "\n"),
                    "gff": ("gff", "##gff-version 3\ncontig1\tfixture\tCDS\t1\t12\t.\t+\t0\tID=protein1;locus_tag=protein1;transl_table=11\n")}
        row = {"sample_id": sid, "isolate_dir": str(folder)}
        for key, (suffix, text) in contents.items():
            path = annotation / f"{stem}.{suffix}"
            path.write_text(text)
            row[key] = str(path)
        return row

    def base_config(self, rows):
        return {"isolates": rows, "extend": False, "deep": {"enabled": False},
                "modules": {name: {"enabled": False} for name in ALL_MODULES},
                "resources": {"directory": str(self.resource_root), "policy": "auto"}}

    def context(self, rows, **changes):
        config = self.base_config(rows)
        config.update(changes)
        return SafetyContext(config, ROOT)

    def test_crispr_runs_whole_assembly_and_ignores_legacy_batch_setting(self):
        cfg = self.base_config([])
        first = SafetyContext(cfg, ROOT).config["modules"]["crisprcasfinder"]
        self.assertEqual(first["execution_scope"], "assembly")
        self.assertNotIn("batch_size", first)
        cfg["modules"]["crisprcasfinder"]["batch_size"] = 32
        self.assertEqual(SafetyContext(cfg, ROOT).config["modules"]["crisprcasfinder"], first)
        cfg["modules"]["crisprcasfinder"]["execution_scope"] = "contig"
        with self.assertRaisesRegex(ValueError, "execution_scope"):
            SafetyContext(cfg, ROOT)

    def test_runtime_durations_are_validated_before_scheduling(self):
        for section in ("minced", "deep"):
            for key in ("timeout_seconds", "heartbeat_seconds"):
                for value in (0, -1, True, float("nan"), float("inf"), "invalid"):
                    cfg = self.base_config([])
                    target = cfg["deep"] if section == "deep" else cfg["modules"][section]
                    target[key] = value
                    with self.subTest(section=section, key=key, value=value):
                        with self.assertRaisesRegex(ValueError, section + "." + key):
                            SafetyContext(cfg, ROOT)
                target[key] = .25
                SafetyContext(cfg, ROOT)

    def test_missing_managed_resource_link_cannot_be_a_silent_noop(self):
        cfg = self.base_config([])
        cfg["modules"]["vfdb"]["enabled"] = True
        payload = self.resource_root / "snapshots/vfdb/key/attempt/data"
        payload.mkdir(parents=True)
        (payload / "vfdb_setA.dmnd").write_text("synthetic database fixture")
        receipt = self.resource_root / "vfdb/provenance.json"
        receipt.parent.mkdir()
        receipt.write_text(json.dumps({"cache_key": "key", "payload_root": str(payload)}))
        with self.assertRaisesRegex(ValueError, "Managed resource link"):
            SafetyContext(cfg, ROOT)
        alias = receipt.parent / "data"
        alias.symlink_to(payload, target_is_directory=True)
        context = SafetyContext(cfg, ROOT)
        self.assertIn("vfdb", context.resource_specs())
        alias.unlink()
        receipt.rename(receipt.with_suffix(".repair-backup"))
        SafetyContext(cfg, ROOT)  # The missing receipt can now schedule recovery.

    def test_csv_tsv_and_xlsx_preserve_ids_and_resolve_relative_paths(self):
        rows = [self.annotation("folder A", "001", arbitrary=True),
                self.annotation("folder B", "sample-B", arbitrary=True)]
        headers = ["sample_id", "isolate_dir", "assembly", "proteins", "gff"]
        values = [[row[key] if key == "sample_id" else str(Path(row[key]).relative_to(self.work))
                   for key in headers] for row in rows]
        for suffix in ("csv", "tsv", "xlsx"):
            with self.subTest(format=suffix):
                sheet = self.work / ("samples." + suffix)
                if suffix == "xlsx":
                    from openpyxl import Workbook
                    book = Workbook()
                    book.active.append(headers)
                    for row in values:
                        book.active.append(row)
                    book.save(sheet)
                    book.close()
                else:
                    with sheet.open("w", newline="") as handle:
                        writer = csv.writer(handle, delimiter="," if suffix == "csv" else "\t")
                        writer.writerow(headers)
                        writer.writerows(values)
                cfg = self.base_config([])
                cfg["samples"] = str(sheet)
                context = SafetyContext(cfg, ROOT)
                self.assertEqual([row["sample_id"] for row in context.samples.values()], ["001", "sample-B"])
                self.assertEqual(set(context.samples), {row["isolate_dir"] for row in rows})
                for original in rows:
                    sample = context.samples[original["isolate_dir"]]
                    self.assertEqual(sample["assembly"], original["assembly"])
                    self.assertEqual(sample["safety_dir"], str(Path(original["isolate_dir"]) / "safety"))

    def test_arbitrary_external_annotations_without_taxonomy_are_valid_inputs(self):
        for mode in ("auto", "external"):
            with self.subTest(mode=mode):
                row = self.annotation("external_folder_" + mode, "independent-sample", arbitrary=True)
                row["input_mode"] = mode
                context = self.context([row])
                sample = context.samples[row["isolate_dir"]]
                self.assertFalse(sample.get("taxon"))
                self.assertFalse(sample.get("taxon_phylum"))
                self.assertNotIn("hybas_dir", sample)
                directory = Path(row["isolate_dir"]) / "safety/metadata"
                validate(sample, context.sample_config(row["isolate_dir"]), directory)
                result = json.loads((directory / "validation.json").read_text())
                self.assertEqual(result["protein_count"], 1)
                self.assertEqual(result["contigs"], 1)

    def test_nonhybas_unknown_taxon_holds_only_scoped_tool(self):
        row = self.annotation("external_ice", "external-ice", arbitrary=True)
        # A matching GenBank is not needed to establish the planning distinction;
        # a present file is sufficient here, and validation remains a separate rule.
        genbank = Path(row["isolate_dir"]) / "custom.gbk"
        genbank.write_text("synthetic placeholder, checked only by validation job\n")
        row["genbank"] = str(genbank)
        cfg = self.base_config([row])
        cfg["modules"]["icescreen"]["enabled"] = True
        cfg["modules"]["minced"]["enabled"] = True
        context = SafetyContext(cfg, ROOT)
        self.assertFalse(context.module_enabled(row["isolate_dir"], "icescreen"))
        self.assertEqual(context.decisions[row["isolate_dir"]]["icescreen"]["reason"], "phylum_scope_requires_review")
        self.assertTrue(context.module_enabled(row["isolate_dir"], "minced"))
        self.assertNotIn("icescreen", context.resource_specs())
        self.assertIn(str(Path(row["isolate_dir"]) / "safety/evidence/icescreen.json"), context.evidence_targets(row["isolate_dir"]))

    def test_mixed_input_consistency_is_checked_in_validation_phase(self):
        good = self.annotation("good")
        bad = self.annotation("bad", bad_translation=True)
        context = self.context([good, bad])
        self.assertEqual(len(context.samples), 2, "DAG configuration must not run sequence validation")
        directory = Path(good["isolate_dir"]) / "safety/metadata"
        validate(context.samples[good["isolate_dir"]], context.sample_config(good["isolate_dir"]), directory)
        self.assertTrue((directory / "validation.json").is_file())
        failed_directory = Path(bad["isolate_dir"]) / "safety/metadata"
        with self.assertRaises(ValueError):
            validate(context.samples[bad["isolate_dir"]], context.sample_config(bad["isolate_dir"]), failed_directory)
        self.assertFalse((failed_directory / "validation.json").exists())
        self.assertFalse((failed_directory / "setup.ready.json").exists())

    def test_every_sample_has_same_global_setup_barrier(self):
        rows = [self.annotation("one"), self.annotation("two")]
        context = self.context(rows)
        expected = {str(Path(row["isolate_dir"]) / "safety/metadata/setup.ready.json") for row in rows}
        for row in rows:
            self.assertEqual(set(context.setup_target(row["isolate_dir"])), expected)
        # Real execution ordering is checked by test_workflow; the barrier may
        # be marked order-only without propagating unrelated config changes.

    def test_disabled_modules_do_not_prepare_resources_or_evidence(self):
        row = self.annotation("disabled")
        context = self.context([row])
        self.assertEqual(context.resource_specs(), {})
        self.assertEqual(context.needed_recipes(), [])
        self.assertEqual(context.environment_receipts(), [])
        self.assertEqual(context.evidence_targets(row["isolate_dir"]), [])
        self.assertEqual(context.report_targets(), [str(Path(row["isolate_dir"]) / "safety/report.html")])

    def test_resource_paths_stable_and_mlst_points_at_bundled_db_root(self):
        row = self.annotation("resourcepaths")
        cfg = self.base_config([row])
        for name in ("amrfinder", "rgi", "vfdb", "bacmet", "mlst", "genomad"):
            cfg["modules"][name]["enabled"] = True
        first, second = SafetyContext(cfg, ROOT), SafetyContext(copy.deepcopy(cfg), ROOT)
        expected = {"amrfinder": "amrfinder/data/database/latest", "rgi": "card/data/card.json",
                    "vfdb": "vfdb/data/vfdb_setA.dmnd", "bacmet": "bacmet/data/bacmet_exp.dmnd",
                    "mlst": "mlst/data/db", "genomad": "genomad/data/genomad_db"}
        for module, relative in expected.items():
            self.assertEqual(first.module_config(row["isolate_dir"], module), second.module_config(row["isolate_dir"], module))
            self.assertEqual(first.module_config(row["isolate_dir"], module)["database"], str(self.resource_root / relative))
        from safety.native import command_argv
        fields = first.module_config(row["isolate_dir"], "mlst")
        command = command_argv("mlst", first.samples[row["isolate_dir"]], fields, self.work / "native", 1)
        self.assertEqual(command[command.index("--datadir") + 1], str(self.resource_root / "mlst/data/db/pubmlst"))
        self.assertEqual(command[command.index("--blastdb") + 1], str(self.resource_root / "mlst/data/db/blast/mlst.fa"))

    def test_complete_manual_deep_resources_require_no_amr_or_antismash_downloads(self):
        row = self.annotation("manual")
        cfg = self.base_config([row])
        cfg["resources"]["policy"] = "manual"
        cfg["deep"] = {"enabled": True, **{name: str(self.work / name) for name in (
            "reference_fasta", "reference_metadata", "domain_database", "profiles_dir",
            "motif_rules", "placement_registry", "prosite_snapshot")}}
        context = SafetyContext(cfg, ROOT)
        specs = context.resource_specs()
        self.assertEqual(set(specs), {"prosite", "deep_catalogue"})
        self.assertTrue(all(spec["mode"] == "external" for spec in specs.values()))
        self.assertEqual(specs["deep_catalogue"]["dependencies"], [])
        self.assertEqual(context.needed_recipes(), ["deepsafe"])

    def test_reference_fasta_and_metadata_must_be_configured_together(self):
        row = self.annotation("partial")
        cfg = self.base_config([row])
        cfg["deep"] = {"enabled": True, "reference_fasta": str(self.work / "references.faa")}
        with self.assertRaisesRegex(ValueError, "configured together"):
            SafetyContext(cfg, ROOT)

    def test_context_preserves_declared_analysis_settings_and_workload_limits(self):
        row = self.annotation("defaults")
        context = self.context([row])
        old = default_config()
        for key in ("maximum_candidates", "maximum_attention_candidates", "maximum_exploratory_candidates",
                    "include_exploratory", "include_stress", "max_references", "min_identity", "min_coverage",
                    "maximum_tree_candidates", "maximum_placement_candidates", "placement_min_query_coverage", "placement_min_columns"):
            self.assertEqual(context.config["deep"][key], old["deep"][key], key)
        for module in ("vfdb", "bacmet"):
            for key in ("min_identity", "min_coverage", "min_query_coverage", "evalue", "max_target_seqs"):
                self.assertEqual(context.config["modules"][module][key], old["modules"][module][key])

    def test_setup_only_inventory_prepares_all_requested_tools_without_samples(self):
        context = SafetyContext({"isolates": [], "deep": {"enabled": True}, "resources": {
            "directory": str(self.resource_root), "policy": "auto"}}, ROOT)
        self.assertEqual(context.samples, {})
        specs = context.resource_specs()
        expected = {"amrfinder", "card", "vfdb", "antismash", "genomad", "mobsuite",
                    "integronfinder", "bacmet", "isescan", "icescreen", "crisprcasfinder",
                    "minced", "mlst", "prott5", "prosite", "deep_catalogue"}
        self.assertEqual(set(specs), expected)
        self.assertEqual(set(context.needed_recipes()), {
            "core-amr", "rgi", "diamond", "antismash", "context", "mobsuite", "isescan",
            "icescreen", "crisprcasfinder", "minced", "mlst", "pathogenfinder2", "deepsafe"})
        self.assertEqual(len(context.environment_receipts()), len(context.needed_recipes()))
        self.assertEqual(specs["prott5"]["targets"], ["pathogenfinder2"])
        self.assertEqual(specs["deep_catalogue"]["targets"], ["deepsafe"])
        self.assertFalse(list(self.work.rglob("safety")), "Planning setup must not create sample results")
        with self.assertRaisesRegex(ValueError, "No samples selected.*setup target"):
            context.report_targets()

    def test_duplicate_names_are_rejected_case_insensitively(self):
        one = self.annotation("a", "sample", arbitrary=True)
        two = self.annotation("b", "SAMPLE", arbitrary=True)
        with self.assertRaisesRegex(ValueError, "Duplicate sample ID"):
            self.context([one, two])


if __name__ == "__main__":
    unittest.main()
