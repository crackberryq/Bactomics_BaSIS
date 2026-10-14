"""Cache seals must survive the same JSON persistence used by the pipeline."""

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from basis.deep_cache import payload_hash, reusable, seal
from basis.util import atomic_json


class DeepCacheRoundtripTests(unittest.TestCase):
    def _record(self, artifact):
        return {
            "execution_status": "completed",
            "cache_key": "roundtrip-key",
            "upstream_incomplete": [],
            "metrics": {"failed_candidates": 0, "selected_candidates": 1},
            "artifacts": [str(artifact)],
            "candidates": [{
                "execution": "completed",
                "analyses": {
                    "prosite_profiles": {
                        "status": "completed",
                        "hits": [{"position_map": {2: 24, 10: 32, 100: 122}}],
                    }
                },
            }],
        }

    def test_persisted_integer_position_map_reuses_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td) / "work"
            work.mkdir()
            artifact = work / "commands.log"
            artifact.write_text("completed\n")
            record = self._record(artifact)
            original = copy.deepcopy(record)
            before = payload_hash(record)
            self.assertEqual(record, original)
            record["deep_cache"] = seal(record, work)
            self.assertEqual(record["deep_cache"]["policy"], "deep_cache_v2")

            destination = Path(td) / "result.json"
            atomic_json(destination, record)
            saved = json.loads(destination.read_text())
            mapping = saved["candidates"][0]["analyses"]["prosite_profiles"]["hits"][0]["position_map"]
            self.assertEqual(set(mapping), {"2", "10", "100"})
            self.assertEqual(before, payload_hash(saved))
            self.assertTrue(reusable(saved, "roundtrip-key"))

    def test_order_and_integer_key_spelling_match_persisted_representation(self):
        one = {"nested": {2: "second", 10: "tenth", 100: "hundredth"}}
        two = {"nested": {100: "hundredth", 10: "tenth", 2: "second"}}
        three = {"nested": {"10": "tenth", "100": "hundredth", "2": "second"}}
        self.assertEqual(payload_hash(one), payload_hash(two))
        self.assertEqual(payload_hash(one), payload_hash(three))

    def test_writer_supported_values_have_same_hash_after_serialization(self):
        record = {"artifact": Path("some/output"), "values": (1, "two", None)}
        persisted = json.loads(json.dumps(record, default=str, allow_nan=False))
        self.assertEqual(payload_hash(record), payload_hash(persisted))

    def test_persisted_payload_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "evidence.txt"
            artifact.write_text("saved evidence")
            record = self._record(artifact)
            record["deep_cache"] = seal(record, td)
            saved = json.loads(json.dumps(record))
            saved["candidates"][0]["analyses"]["prosite_profiles"]["hits"][0]["position_map"]["10"] = 999
            self.assertFalse(reusable(saved, "roundtrip-key"))

    def test_persisted_artifact_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "evidence.txt"
            artifact.write_text("saved evidence")
            record = self._record(artifact)
            record["deep_cache"] = seal(record, td)
            saved = json.loads(json.dumps(record))
            artifact.write_text("changed evidence")
            self.assertFalse(reusable(saved, "roundtrip-key"))

    def test_old_policy_is_not_promoted_even_with_matching_payload_digest(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "evidence.txt"
            artifact.write_text("saved evidence")
            record = self._record(artifact)
            record["deep_cache"] = seal(record, td)
            record["deep_cache"]["policy"] = "deep_cache_v1"
            self.assertFalse(reusable(record, "roundtrip-key"))

    def test_missing_declared_artifact_checksum_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "evidence.txt"
            artifact.write_text("saved evidence")
            (Path(td) / "intermediate.log").write_text("extra sealed file")
            record = self._record(artifact)
            record["deep_cache"] = seal(record, td)
            del record["deep_cache"]["checksums"][str(artifact)]
            self.assertFalse(reusable(record, "roundtrip-key"))
            artifact.write_text("altered evidence")
            self.assertFalse(reusable(record, "roundtrip-key"))

    def test_failed_upstream_or_wrong_key_cannot_reuse_even_when_sealed(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "evidence.txt"
            artifact.write_text("saved evidence")
            base = self._record(artifact)
            cases = [
                {"execution_status": "failed"},
                {"upstream_incomplete": ["earlier-stage"]},
                {"metrics": {"failed_candidates": 1}},
            ]
            for changes in cases:
                with self.subTest(changes=changes):
                    record = {**copy.deepcopy(base), **changes}
                    record["deep_cache"] = seal(record, td)
                    self.assertFalse(reusable(record, "roundtrip-key"))
            base["deep_cache"] = seal(base, td)
            self.assertFalse(reusable(base, "different-key"))

    def test_nonfinite_values_cannot_be_sealed_or_reused(self):
        for invalid in (math.nan, math.inf, -math.inf):
            with self.subTest(value=invalid), tempfile.TemporaryDirectory() as td:
                artifact = Path(td) / "evidence.txt"
                artifact.write_text("saved evidence")
                record = self._record(artifact)
                record["deep_cache"] = seal(record, td)
                record["bad_value"] = {"nested": [invalid]}
                with self.assertRaises((ValueError, TypeError)):
                    payload_hash(record)
                self.assertFalse(reusable(record, "roundtrip-key"))

    def test_json_key_collisions_cannot_be_sealed_or_reused(self):
        for mapping in ({1: "integer", "1": "text"}, {None: "null", "null": "text"}):
            with self.subTest(mapping=mapping), tempfile.TemporaryDirectory() as td:
                artifact = Path(td) / "evidence.txt"
                artifact.write_text("saved evidence")
                record = self._record(artifact)
                record["deep_cache"] = seal(record, td)
                record["ambiguous"] = mapping
                with self.assertRaises((ValueError, TypeError)):
                    payload_hash(record)
                self.assertFalse(reusable(record, "roundtrip-key"))

    def test_pipeline_reuses_persisted_integer_map_without_second_native_call(self):
        from unittest.mock import patch
        from basis.config import ALL_MODULES
        from basis.pipeline import execute_deep, record_path

        with tempfile.TemporaryDirectory() as td:
            profiles = Path(td) / "profiles"
            profiles.mkdir()
            plan = {
                "outdir": td,
                "config": {
                    "outdir": td,
                    "validation_mode": "native",
                    "deep": {"profiles_dir": str(profiles)},
                    "execution": {"cores": 1},
                },
                "options": {"deep": True},
                "validations": {"s": {"input_hashes": {}}},
                "source_hash": "unchanged-source",
                "enabled_modules": [],
            }
            for module in ALL_MODULES:
                atomic_json(record_path(plan, "s", module), {
                    "module": module, "execution_status": "completed",
                })

            def native(sample, records, cfg, work, runner):
                artifact = work / "evidence.txt"
                artifact.write_text("saved evidence")
                record = self._record(artifact)
                return {key: record[key] for key in ("artifacts", "metrics", "candidates")}

            with patch("basis.deep_cache.engine_identity", return_value={"status": "available", "hash": "engine"}), \
                    patch("basis.deep.run_deep", side_effect=native) as run:
                first = execute_deep(plan, {"sample_id": "s"})
                second = execute_deep(plan, {"sample_id": "s"})
            self.assertEqual(first["execution_status"], "completed")
            self.assertTrue(second["cache_hit"])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(first["deep_cache"]["payload_sha256"], second["deep_cache"]["payload_sha256"])


if __name__ == "__main__":
    unittest.main()
