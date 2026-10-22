"""Generic checkpoint serialization tests; no native tools or biological data."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workflow/lib"))
from safety import state_storage as storage


class StateStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "deepsafe"
        self.path = self.root / "states/prepare.json"
        self.path.parent.mkdir(parents=True)
        self.document = {
            "schema": storage.LEGACY_SCHEMA,
            "context": {"sample": {"id": "fixture"}, "records": ["x" * 65536]},
            "state": {"candidates": [{"status": "pending"}], "internal": {"audit": [1, 2]},
                      "family_inventory": {"example": "fixture"}},
            "budget": {"unchanged": True}, "selected_candidate_ids": ["fixture"],
            "commands": [], "unknown": {"unicode": "é", "values": [None, False, 1.2]},
        }
        self.document["context_sha256"] = storage.logical_digest(self.document["context"])

    def test_digest_matches_legacy_exactly(self):
        expected = hashlib.sha256(json.dumps(self.document, sort_keys=True,
            default=str, allow_nan=False).encode()).hexdigest()
        self.assertEqual(storage.logical_digest(self.document), expected)

    def test_legacy_and_compact_are_logically_identical(self):
        self.path.write_text(json.dumps(self.document))
        self.assertEqual(storage.read_document(self.path), self.document)
        storage.write_document(self.path, self.document)
        raw = storage.read_raw(self.path)
        self.assertEqual(raw["schema"], storage.COMPACT_SCHEMA)
        self.assertNotIn("context", raw)
        self.assertEqual(raw["selected_candidate_ids"], ["fixture"])
        self.assertEqual(storage.read_document(self.path), self.document)

    def test_twenty_states_share_one_copy_and_reduce_storage(self):
        original_bytes = len(json.dumps(self.document, indent=2).encode()) * 20
        paths = []
        for index in range(20):
            path = self.root / "candidates" / str(index) / "state.json"
            storage.write_document(path, self.document)
            paths.append(path)
        objects = list((self.root / "state-objects").glob("*.json"))
        self.assertEqual(len(objects), 4)
        stored_bytes = sum(p.stat().st_size for p in paths + objects)
        self.assertLess(stored_bytes, original_bytes / 10)

    def test_generations_coexist(self):
        storage.write_document(self.path, self.document)
        first = set(storage.object_paths(self.path))
        changed = copy.deepcopy(self.document)
        changed["context"]["sample"]["id"] = "another"
        changed["context_sha256"] = storage.logical_digest(changed["context"])
        other = self.path.with_name("other.json")
        storage.write_document(other, changed)
        self.assertEqual(storage.read_document(self.path), self.document)
        self.assertEqual(storage.read_document(other), changed)
        self.assertTrue(all(p.is_file() for p in first))

    def test_missing_or_corrupt_object_is_rejected(self):
        storage.write_document(self.path, self.document)
        blob = storage.object_paths(self.path)[0]
        content = blob.read_bytes()
        blob.unlink()
        with self.assertRaises(FileNotFoundError):
            storage.read_document(self.path)
        blob.write_bytes(b'{}\n')
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            storage.read_document(self.path)
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            storage.write_document(self.path, self.document)
        blob.write_bytes(content)
        self.assertEqual(storage.read_document(self.path), self.document)

    def test_reference_traversal_and_duplicate_locations_rejected(self):
        storage.write_document(self.path, self.document)
        original = storage.read_raw(self.path)
        changed = copy.deepcopy(original)
        changed["shared_objects"][0]["path"] = "../../outside.json"
        self.path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "outside its object store"):
            storage.read_document(self.path)
        changed = copy.deepcopy(original)
        changed["shared_objects"].append(changed["shared_objects"][0])
        self.path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "Duplicate shared"):
            storage.read_document(self.path)

    def test_failure_before_replacement_preserves_original(self):
        self.path.write_text(json.dumps(self.document))
        original = self.path.read_bytes()
        def fail():
            raise OSError("Synthetic disk failure")
        with self.assertRaisesRegex(OSError, "Synthetic disk failure"):
            storage.write_document(self.path, self.document, before_replace=fail)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.assertEqual(storage.read_document(self.path), self.document)

    def test_cached_object_change_is_detected(self):
        storage.write_document(self.path, self.document)
        cache = {}
        storage.read_document(self.path, cache)
        blob = storage.object_paths(self.path)[0]
        blob.write_text('{}\n')
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            storage.read_document(self.path, cache)

    def test_migration_retains_group_read_permissions(self):
        self.path.write_text(json.dumps(self.document))
        os.chmod(self.path, 0o640)
        storage.write_document(self.path, self.document, preserve_stat=self.path.stat())
        for path in storage.object_paths(self.path):
            self.assertEqual(path.stat().st_mode & 0o040, 0o040)
            self.assertEqual(path.stat().st_gid, self.path.stat().st_gid)

    def test_shared_store_outside_job_cleanup_directories(self):
        storage.write_document(self.path, self.document)
        self.assertTrue(all(path.parent == self.root / "state-objects"
                            for path in storage.object_paths(self.path)))

    def test_directory_sync_precedes_state_replacement(self):
        events = []
        real_replace = os.replace
        def replace(source, target):
            events.append(("replace", Path(target)))
            return real_replace(source, target)
        with patch.object(storage, "sync_directory", side_effect=lambda p: events.append(("sync", Path(p)))), \
                patch.object(storage.os, "replace", side_effect=replace):
            storage.write_document(self.path, self.document)
        replace_index = events.index(("replace", self.path))
        self.assertIn(("sync", self.root / "state-objects"), events[:replace_index])
        self.assertEqual(events[-1], ("sync", self.path.parent))


if __name__ == "__main__":
    unittest.main()
