"""HybAs manifest integration uses synthetic local metadata, never native tools."""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from basis import cli
from basis.inputs import load_samples, validate_sample


class HybasIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis_hybas_integration_")
        self.root = Path(self.temp.name)
        self.hybas_root = self.root / "hybas"
        self.study = self.root / "study"
        self.study.mkdir()
        self.manifest = self.study / "samples.tsv"
        self.config = {"samples": str(self.manifest)}

    def tearDown(self):
        self.temp.cleanup()

    def annotation(self, sample_id="isolate1", *, standalone=False):
        directory = (self.root / "standalone" if standalone else self.hybas_root / sample_id) / "annotation"
        directory.mkdir(parents=True, exist_ok=True)
        paths = {key: directory / f"{sample_id}.{suffix}" for key, suffix in
                 (("assembly", "fna"), ("proteins", "faa"), ("gff", "gff"))}
        paths["assembly"].write_text(">c1\nATGAAATTTTAA\n", encoding="utf-8")
        paths["proteins"].write_text(">p1\nMKF\n", encoding="utf-8")
        paths["gff"].write_text(
            "##gff-version 3\nc1\tProkka\tCDS\t1\t12\t.\t+\t0\tID=p1;locus_tag=p1;transl_table=11\n",
            encoding="utf-8")
        return {"sample_id": sample_id,
                **{key: os.path.relpath(path, self.study) for key, path in paths.items()}}

    def metadata(self, sample_id="isolate1", *, name="Lysinibacillus", rank="G", summary_only=False):
        directory = self.hybas_root / sample_id / "metadata"
        directory.mkdir(parents=True, exist_ok=True)
        target = {"name": name, "rank": rank, "taxid": "400634",
                  "source": "inferred_from_kraken"}
        if summary_only:
            path = directory / "run_summary.json"
            path.write_text(json.dumps({"isolate": sample_id, "workflow_version": "synthetic",
                **{f"kraken_selected_target_{key}": value for key, value in target.items()}}),
                encoding="utf-8")
        else:
            path = directory / "kraken_decision.tsv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle, delimiter="\t")
                writer.writerow(["field", "value"])
                writer.writerow(["isolate", sample_id])
                writer.writerows((f"selected_target_{key}", value) for key, value in target.items())
        return path

    def write_manifest(self, rows, fields=None):
        if fields is None:
            fields = list(dict.fromkeys(key for row in rows for key in row))
        with self.manifest.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)

    def read_manifest(self, path=None):
        with Path(path or self.manifest).open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            return reader.fieldnames, list(reader)

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def test_old_annotation_only_manifest_imports_taxonomy_without_rewriting(self):
        row = self.annotation()
        metadata = self.metadata()
        self.write_manifest([row])
        original = self.manifest.read_bytes()
        sample = load_samples(self.manifest)[0]
        self.assertEqual(sample["taxon"], "Lysinibacillus")
        self.assertEqual(sample["taxon_rank"], "genus")
        self.assertEqual(sample["taxon_source"], "hybas_kraken_operational_target")
        self.assertEqual(sample["taxon_status"], "read_based_operational_target")
        self.assertEqual(sample["hybas_context"]["status"], "imported")
        source = next(s for s in sample["hybas_context"]["sources"] if s["role"] == "decision")
        self.assertEqual(Path(source["path"]), metadata)
        self.assertEqual(source["sha256"], hashlib.sha256(metadata.read_bytes()).hexdigest())
        self.assertEqual(validate_sample(sample)["protein_count"], 1)
        self.assertEqual(self.manifest.read_bytes(), original)

    def test_initialize_imports_metadata_and_writes_taxonomy_columns(self):
        self.annotation()
        self.metadata()
        self.annotation("isolate2")
        self.metadata("isolate2", name="Lysinibacillus irui", rank="S", summary_only=True)
        result = cli.initialize(self.root / "initialized", self.hybas_root)
        fields, rows = self.read_manifest(result["samples"])
        self.assertTrue({"hybas_dir", "taxon", "taxon_rank", "taxon_source"}.issubset(fields))
        by_id = {row["sample_id"]: row for row in rows}
        self.assertEqual(set(by_id), {"isolate1", "isolate2"})
        self.assertEqual(by_id["isolate1"]["taxon"], "Lysinibacillus")
        self.assertEqual(by_id["isolate1"]["taxon_rank"], "genus")
        self.assertEqual(by_id["isolate2"]["taxon"], "Lysinibacillus irui")
        self.assertEqual(by_id["isolate2"]["taxon_rank"], "species")
        for sample_id, row in by_id.items():
            self.assertEqual(Path(row["hybas_dir"]), self.hybas_root / sample_id)
            self.assertEqual(row["taxon_source"], "hybas_kraken_operational_target")
        for sample in load_samples(result["samples"]):
            self.assertEqual(validate_sample(sample)["translation_validation"]["checked"], 1)

    def test_sync_explicit_species_preserves_paths_extra_columns_other_rows_and_backup(self):
        first = self.annotation()
        second = self.annotation("isolate2")
        self.metadata()
        self.metadata("isolate2")
        (self.study / "phenotypes.json").write_text("[]\n", encoding="utf-8")
        first.update(taxon="", amrfinder_organism="Escherichia", phenotypes="phenotypes.json",
                     genbank="", notes="  retain whitespace and punctuation; exact  ")
        second.update(taxon="Bacillus subtilis", amrfinder_organism="", phenotypes="",
                      genbank="", notes="untouched row")
        original_rows = [first, second]
        self.write_manifest(original_rows)
        original_bytes = self.manifest.read_bytes()
        result = cli.sync_hybas(self.config, sample_id="isolate1", species="Lysinibacillus irui")
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["samples"][0]["taxonomy_status"], "resolved")
        self.assertEqual(result["samples_path"], str(self.manifest))
        fields, rows = self.read_manifest()
        self.assertEqual([r["sample_id"] for r in rows], ["isolate1", "isolate2"])
        self.assertTrue(set(first).issubset(fields))
        taxonomy_fields = {"taxon", "taxon_rank", "taxon_source", "taxon_status", "hybas_dir"}
        for before, after in zip(original_rows, rows):
            for key, value in before.items():
                if key not in taxonomy_fields or before["sample_id"] == "isolate2":
                    self.assertEqual(after[key], value, (before["sample_id"], key))
        self.assertEqual(rows[0]["taxon"], "Lysinibacillus irui")
        self.assertEqual(rows[0]["taxon_rank"], "species")
        self.assertEqual(rows[0]["taxon_source"], "user_confirmed")
        self.assertEqual(rows[1]["taxon"], "Bacillus subtilis")
        self.assertTrue(Path(result["backup"]).is_absolute())
        self.assertEqual(Path(result["backup"]).read_bytes(), original_bytes)
        loaded = load_samples(self.manifest)[0]
        self.assertEqual(loaded["taxon"], "Lysinibacillus irui")
        self.assertEqual(loaded["taxon_source"], "user_confirmed")
        self.assertEqual(loaded["taxon_status"], "declared")
        self.assertEqual(loaded["phenotype_source"], str(self.study / "phenotypes.json"))

    def test_sync_rejects_unknown_sample_and_species_without_id_before_mutation(self):
        self.write_manifest([self.annotation()])
        self.metadata()
        before = self.snapshot()
        for arguments in ({"sample_id": "missing"},
                          {"sample_id": "missing", "species": "Lysinibacillus irui"},
                          {"species": "Lysinibacillus irui"}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    cli.sync_hybas(self.config, **arguments)
                self.assertEqual(self.snapshot(), before)

    def test_sync_legacy_native_shape_shows_resolved_target_and_read_species(self):
        row = self.annotation()
        row.update(taxon="Lysinibacillus irui", taxon_rank="species", taxon_source="user_confirmed")
        self.write_manifest([row])
        metadata = self.hybas_root / "isolate1" / "metadata"
        metadata.mkdir()
        (metadata / "run_summary.json").write_text(json.dumps({
            "isolate": "isolate1", "workflow_version": "HybAs-8.7-ieee-batch",
            "target_taxid": 400634, "run_kraken": True,
            "kraken_illumina_report_present": True, "kraken_ont_report_present": True,
            "tool_kraken2_version": "2.1.3"}), encoding="utf-8")
        reports = self.hybas_root / "isolate1" / "reports" / "kraken2"
        reports.mkdir(parents=True)
        for name in ("illumina.report.txt", "nanopore.report.txt"):
            (reports / name).write_text(
                "96.00\t960\t100\tG\t400634\tLysinibacillus\n"
                "70.00\t700\t700\tS\t999999\tLysinibacillus fixture species\n", encoding="utf-8")
        result = cli.sync_hybas(self.config, sample_id="isolate1")
        imported = result["samples"][0]
        self.assertEqual(imported["taxonomy_status"], "resolved")
        self.assertEqual(imported["selected_target"]["name"], "Lysinibacillus")
        self.assertEqual(imported["selected_target"]["source"], "legacy_run_summary_target")
        self.assertEqual(imported["read_classification"]["illumina"]["leading_species"]["taxid"], "999999")
        self.assertEqual(imported["taxon"], "Lysinibacillus irui")
        self.assertEqual(imported["taxon_source"], "user_confirmed")
        self.assertEqual(load_samples(self.manifest)[0]["hybas_context"]["upstream"]["workflow_version"],
                         "HybAs-8.7-ieee-batch")

    def test_sync_invalid_later_metadata_cannot_partially_update_manifest(self):
        self.write_manifest([self.annotation(), self.annotation("isolate2")])
        self.metadata()
        invalid = self.metadata("isolate2")
        invalid.write_text(invalid.read_text().replace("isolate\tisolate2", "isolate\twrong_isolate"),
                           encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            cli.sync_hybas(self.config)
        self.assertEqual(self.snapshot(), before)

    def test_sync_dry_run_and_cli_dry_run_leave_all_files_unchanged(self):
        self.write_manifest([self.annotation()])
        self.metadata()
        config_path = self.study / "config.yaml"
        config_path.write_text("samples: samples.tsv\noutdir: results\n", encoding="utf-8")
        before = self.snapshot()
        result = cli.sync_hybas(self.config, sample_id="isolate1", species="Lysinibacillus irui", dry_run=True)
        self.assertEqual(result["status"], "dry_run")
        self.assertIsNone(result["backup"])
        self.assertEqual(self.snapshot(), before)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["sync-hybas", "-c", str(config_path), "--sample", "isolate1",
                             "--species", "Lysinibacillus irui", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(self.snapshot(), before)

    def test_hybas_taxonomy_refreshes_but_declared_taxonomy_is_preserved(self):
        first = self.annotation()
        first.update(taxon="Stale genus", taxon_rank="genus", taxon_source="hybas_kraken_operational_target")
        second = self.annotation("isolate2")
        second.update(taxon="User declared species", taxon_rank="species", taxon_source="user_confirmed")
        self.write_manifest([first, second])
        self.metadata()
        self.metadata("isolate2")
        initial = load_samples(self.manifest)
        self.assertEqual(initial[0]["taxon"], "Lysinibacillus")
        self.assertEqual(initial[1]["taxon"], "User declared species")
        self.metadata(name="Lysinibacillus irui", rank="S")
        refreshed = load_samples(self.manifest)
        self.assertEqual(refreshed[0]["taxon"], "Lysinibacillus irui")
        self.assertEqual(refreshed[0]["taxon_rank"], "species")
        self.assertNotEqual(initial[0]["hybas_context"]["sources"], refreshed[0]["hybas_context"]["sources"])
        self.assertEqual(refreshed[1]["taxon"], "User declared species")
        self.assertEqual(refreshed[1]["taxon_source"], "user_confirmed")

    def test_standalone_annotation_loads_without_fabricated_taxonomy(self):
        row = self.annotation(standalone=True)
        self.write_manifest([row])
        original = self.manifest.read_bytes()
        sample = load_samples(self.manifest)[0]
        self.assertFalse(sample.get("taxon"))
        self.assertFalse(sample.get("taxon_species"))
        self.assertFalse(sample.get("taxon_source"))
        self.assertNotEqual((sample.get("hybas_context") or {}).get("status"), "imported")
        for key in ("assembly", "proteins", "gff"):
            self.assertEqual(Path(sample[key]), (self.study / row[key]).resolve())
        self.assertEqual(validate_sample(sample)["protein_count"], 1)
        self.assertEqual(self.manifest.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
