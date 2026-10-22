"""Synthetic JSON/provenance contracts; no sequence data or native tools are used."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workflow/vendor"))

from basis.hybas import _json_summary, enrich_sample


class HybasMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis-hybas-metadata-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "synthetic-sample"
        self.metadata = self.root / "metadata"
        self.metadata.mkdir(parents=True)
        self.path = self.metadata / "run_summary.json"
        self.sample = {"sample_id": self.root.name, "hybas_dir": str(self.root),
                       "input_mode": "hybas"}
        for key, suffix in (("assembly", ".fna"), ("proteins", ".faa"), ("gff", ".gff")):
            self.sample[key] = str(self.root / "annotation" / (self.root.name + suffix))

    def summary(self):
        # Shapes emitted by HybAs 9.0.0 Hybas.smk at commit
        # 9477fd3b6578e23a84d6524e1c89e65e01590f03 (polish_execution/run_summary).
        return {"isolate": self.root.name, "workflow_version": "HybAs-9.0.0-AWARE",
                "run_kraken": False, "kraken_decision_mode": "off",
                "kraken_decision": "skipped", "kraken_decision_reason": "synthetic_disabled",
                "executed_stages": ["racon1", "racon2", "medaka"],
                "skipped_stages": {"polypolish": "no_mapped_alignments"}}

    def write_summary(self, data):
        self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def test_known_execution_metadata_is_preserved_including_empty_containers(self):
        for executed, skipped in ((["racon1", "medaka"], {"polypolish": "a recorded reason"}),
                                  ([], {})):
            with self.subTest(executed=executed, skipped=skipped):
                data = self.summary()
                data.update(executed_stages=executed, skipped_stages=skipped)
                self.write_summary(data)
                before = self.path.read_bytes()
                self.assertEqual(_json_summary(self.path), data)
                self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_flat_summary_and_unknown_scalar_fields_remain_supported(self):
        data = {"isolate": self.root.name, "text": "value", "empty": None,
                "count": 2, "ratio": 1.25, "enabled": False}
        self.write_summary(data)
        self.assertEqual(_json_summary(self.path), data)

    def test_summary_requires_an_object_with_valid_keys(self):
        for data in ([], "summary", None, 1, {"bad-key": "value"}):
            with self.subTest(data=data):
                self.write_summary(data)
                with self.assertRaisesRegex(ValueError, "object with valid keys"):
                    _json_summary(self.path)

    def test_other_nested_fields_are_rejected(self):
        for field in ("unexpected", "isolate", "kraken_decision", "kraken_selected_target_taxid"):
            for value in ([], {}, {"nested": "value"}):
                with self.subTest(field=field, value=value):
                    data = self.summary()
                    data[field] = value
                    self.write_summary(data)
                    with self.assertRaisesRegex(ValueError, "finite scalar"):
                        _json_summary(self.path)

    def test_executed_stages_rejects_wrong_types_and_nested_values(self):
        for value in (None, True, 1, "racon1", {}, [None], [True], [1], [[]], [{}],
                      [""], ["bad-stage"], ["stage name"]):
            with self.subTest(value=value):
                data = self.summary()
                data["executed_stages"] = value
                self.write_summary(data)
                with self.assertRaisesRegex(ValueError, "executed_stages must be a list of stage names"):
                    _json_summary(self.path)

    def test_skipped_stages_rejects_wrong_types_keys_and_nested_values(self):
        values = (None, True, 1, "racon", [], {"": "reason"}, {"bad-stage": "reason"},
                  {"stage name": "reason"}, {"racon": None}, {"racon": True},
                  {"racon": 1}, {"racon": []}, {"racon": {}}, {"racon": ""}, {"racon": "  "})
        for value in values:
            with self.subTest(value=value):
                data = self.summary()
                data["skipped_stages"] = value
                self.write_summary(data)
                with self.assertRaisesRegex(ValueError, "skipped_stages must map stage names"):
                    _json_summary(self.path)

    def test_duplicate_keys_are_rejected_at_both_levels(self):
        for raw in ('{"isolate":"one","isolate":"two"}',
                    '{"executed_stages":[],"executed_stages":[]}',
                    '{"skipped_stages":{"racon":"first","racon":"second"}}'):
            with self.subTest(raw=raw):
                self.path.write_text(raw, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "duplicate key"):
                    _json_summary(self.path)

    def test_nonfinite_numbers_are_rejected_at_all_supported_levels(self):
        for token in ("NaN", "Infinity", "-Infinity", "1e999"):
            for raw in ('{"metric":' + token + '}',
                        '{"executed_stages":[' + token + ']}',
                        '{"skipped_stages":{"racon":' + token + '}}'):
                with self.subTest(raw=raw):
                    self.path.write_text(raw, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        _json_summary(self.path)

    def test_import_preserves_source_hash_and_recorded_decision(self):
        data = self.summary()
        self.write_summary(data)
        before = self.path.read_bytes()
        digest = hashlib.sha256(before).hexdigest()
        checksums = self.metadata / "checksums.tsv"
        checksums.write_text("label\tpath\tsha256\n"
                             f"run_summary_json\t{self.path}\t{digest}\n", encoding="utf-8")
        context = enrich_sample(self.sample)["hybas_context"]
        self.assertEqual(context["status"], "imported")
        self.assertEqual(context["taxonomy_status"], "disabled")
        self.assertEqual(context["upstream"]["workflow_version"], data["workflow_version"])
        self.assertEqual(context["decision"]["kraken_decision_reason"], "synthetic_disabled")
        source = next(item for item in context["sources"] if item["role"] == "summary_json")
        self.assertEqual(source, {"role": "summary_json", "path": str(self.path), "sha256": digest})
        self.assertEqual(self.path.read_bytes(), before)

    def test_execution_metadata_does_not_bypass_isolate_identity(self):
        data = self.summary()
        data["isolate"] = "another-sample"
        self.write_summary(data)
        with self.assertRaisesRegex(ValueError, "isolate must match sample_id"):
            enrich_sample(self.sample)

    def test_execution_metadata_does_not_bypass_decision_conflicts(self):
        self.write_summary(self.summary())
        for filename, header, field in (("kraken_decision.tsv", "field", "kraken_decision_reason"),
                                         ("run_summary.tsv", "metric", "kraken_decision_reason")):
            with self.subTest(filename=filename):
                other = self.metadata / filename
                other.write_text(f"{header}\tvalue\nisolate\t{self.root.name}\n"
                                 f"{field}\tconflicting_reason\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "decision/summary conflict"):
                    enrich_sample(self.sample)
                other.unlink()

    def test_execution_metadata_does_not_bypass_checksum_validation(self):
        self.write_summary(self.summary())
        for digest, error in (("0" * 64, "checksum mismatch"), ("invalid", "malformed checksum")):
            with self.subTest(digest=digest):
                (self.metadata / "checksums.tsv").write_text(
                    "label\tpath\tsha256\n" f"run_summary_json\t{self.path}\t{digest}\n",
                    encoding="utf-8")
                with self.assertRaisesRegex(ValueError, error):
                    enrich_sample(self.sample)


if __name__ == "__main__":
    unittest.main()
