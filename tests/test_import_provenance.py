"""Import identity and provenance tests using synthetic saved records only."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basis.normalize import normalize_result
from basis.pipeline import _import_result, execute_module, record_path
from basis.util import atomic_json, sha256_file


class ImportProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basis_import_record_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input.txt"
        self.input.write_text("synthetic input identity\n")
        self.source_artifact = self.root / "historical.txt"
        self.source_artifact.write_text("synthetic saved artifact\n")
        self.source = self.root / "source.json"
        self.sample = {"sample_id": "sampleA", "assembly": str(self.input)}
        self.validation = {"input_hashes": {"assembly": sha256_file(self.input)},
                           "protein_coordinates": {}, "contig_lengths": {}}
        self.record = {
            "schema_version": 1, "sample_id": "sampleA", "module": "amrfinder",
            "tool": "amrfinder", "record_id": "historical:sampleA:amrfinder",
            "version": "historical-version", "validation_mode": "native",
            "execution_status": "completed", "interpretation": "not_detected",
            "input_hashes": copy.deepcopy(self.validation["input_hashes"]),
            "findings": [], "metrics": {"native_rows": 0}, "notes": ["Historical note"],
            "effective_config": {"mode": "native", "synthetic_option": "recorded"},
            "provenance": {"tool_version": {"status": "completed", "output": "historical-version"},
                           "resources": {"database": {"sha256": "historical-resource"}},
                           "source_hash": "historical-source", "commands": [{"argv": ["historical-command"]}]},
            "artifacts": [str(self.source_artifact)],
            "artifact_checksums": {str(self.source_artifact): sha256_file(self.source_artifact)},
            "started_at": "historical-start", "finished_at": "historical-finish", "elapsed_seconds": 7.0,
            "cache_key": "never-promote-this-key", "cache_hit": True,
            "deep_cache": {"policy": "never-copy-this-claim"},
            "recovery": {"method": "reparse_saved_native_outputs", "inference_rerun": False},
        }
        atomic_json(self.source, self.record)
        self.plan = {
            "outdir": str(self.root / "output"), "source_hash": "current-source",
            "config": {"validation_mode": "synthetic", "modules": {
                "amrfinder": {"enabled": True, "mode": "import", "result": str(self.source)}},
                "execution": {"cores": 1}},
            "options": {"deep": False, "extend": False},
            "validations": {"sampleA": self.validation}, "enabled_modules": ["amrfinder"],
        }

    def test_import_keeps_historical_execution_separate_from_current_provenance(self):
        source_hash = sha256_file(self.source)
        with patch("basis.pipeline.probe_version", side_effect=AssertionError("native probe attempted")):
            result = execute_module(self.plan, self.sample, "amrfinder")
        self.assertEqual(result["execution_status"], "completed")
        imported = result["imported_evidence"]
        self.assertFalse(imported["native_execution_performed"])
        self.assertEqual(imported["input_hash_verification"], "matches_validated_current_inputs")
        self.assertEqual(imported["source_artifact_checksum_scope"],
                         "historical_recorded_checksums_not_reverified_by_import")
        original = imported["source_record"]
        self.assertEqual((original["path"], original["sha256"], original["bytes"]),
                         (str(self.source.resolve()), source_hash, self.source.stat().st_size))
        for field in ("provenance", "effective_config", "execution_status", "input_hashes",
                      "artifact_checksums", "recovery", "started_at", "finished_at"):
            self.assertEqual(original[field], self.record[field])
        self.assertFalse(set(original) & {"cache_key", "cache_hit", "deep_cache"})
        self.assertEqual(result["provenance"]["source_hash"], "current-source")
        self.assertEqual(result["provenance"]["commands"], [])
        self.assertEqual(result["provenance"]["tool_version"]["reason"], "imported_evidence")
        self.assertEqual(result["effective_config"]["mode"], "import")
        self.assertFalse(result["cache_hit"])
        self.assertNotEqual(result["cache_key"], self.record["cache_key"])
        self.assertNotIn("deep_cache", result)
        self.assertEqual(result["artifact_checksums"][str(self.source)], source_hash)
        self.assertEqual(json.loads(record_path(self.plan, "sampleA", "amrfinder").read_text())["imported_evidence"], imported)
        self.assertEqual(sha256_file(self.source), source_hash)

    def test_repeat_import_cache_retains_provenance_without_native_claim(self):
        first = execute_module(self.plan, self.sample, "amrfinder")
        with patch("basis.pipeline._import_result", side_effect=AssertionError("cache reparsed unexpectedly")):
            second = execute_module(self.plan, self.sample, "amrfinder")
        self.assertTrue(second["cache_hit"])
        self.assertEqual(second["imported_evidence"], first["imported_evidence"])
        self.assertFalse(second["imported_evidence"]["native_execution_performed"])

    def test_reimport_preserves_previous_import_chain(self):
        first = execute_module(self.plan, self.sample, "amrfinder")
        source = record_path(self.plan, "sampleA", "amrfinder")
        second = _import_result(str(source), self.sample, "amrfinder", self.validation)
        historical = second["imported_evidence"]["source_record"]
        self.assertEqual(historical["imported_evidence"], first["imported_evidence"])
        self.assertEqual(historical["sha256"], sha256_file(source))
        self.assertNotIn("cache_hit", historical)

    def test_source_mutation_during_normalization_is_rejected(self):
        def changing_normalizer(*args):
            payload = normalize_result(*args)
            changed = copy.deepcopy(self.record)
            changed["notes"].append("mutated during normalization")
            atomic_json(self.source, changed)
            return payload
        with patch("basis.pipeline.normalize_result", side_effect=changing_normalizer):
            with self.assertRaisesRegex(ValueError, "changed while it was being read"):
                _import_result(str(self.source), self.sample, "amrfinder", self.validation)

    def test_changed_source_after_fingerprint_fails_closed(self):
        real_import = _import_result
        def changing_import(*args):
            changed = copy.deepcopy(self.record)
            changed["notes"].append("mutated after fingerprinting")
            atomic_json(self.source, changed)
            return real_import(*args)
        with patch("basis.pipeline._import_result", side_effect=changing_import):
            result = execute_module(self.plan, self.sample, "amrfinder")
        self.assertEqual(result["execution_status"], "failed")
        self.assertIn("changed after resource fingerprinting", result["error"])
        self.assertEqual(result["findings"], [])
        self.assertNotIn("imported_evidence", result)

    def test_source_identity_and_completion_validation_remain_required(self):
        for field, value in (("schema_version", 999), ("sample_id", "different"),
                             ("module", "different"), ("input_hashes", {}),
                             ("execution_status", "failed")):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.record)
                changed[field] = value
                atomic_json(self.source, changed)
                with self.assertRaises(ValueError):
                    _import_result(str(self.source), self.sample, "amrfinder", self.validation)


if __name__ == "__main__":
    unittest.main()
