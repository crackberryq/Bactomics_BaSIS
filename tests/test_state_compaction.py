"""Offline storage migration tests using generic synthetic JSON envelopes."""
import copy
from contextlib import redirect_stdout
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety import state_storage as storage

SPEC = importlib.util.spec_from_file_location("compact_deep_states", ROOT / "scripts/compact_deep_states.py")
compact = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = compact
SPEC.loader.exec_module(compact)


class StateCompactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.isolate = Path(self.temporary.name) / "isolate"
        self.root = self.isolate / "safety/deepsafe"
        self.path = self.root / "states/prepare.json"
        self.path.parent.mkdir(parents=True)
        self.document = {
            "schema": storage.LEGACY_SCHEMA,
            "context": {"sample": {"sample_id": "synthetic"}, "records": [],
                        "config": {}, "work": str(self.root), "padding": "x" * 4096},
            "state": {"identity": {"synthetic": "identity"},
                      "internal": {"shared": ["fixture"] * 100},
                      "family_inventory": {"fixture": [1, 2]},
                      "candidates": [{"candidate_id": "fixture", "execution": "failed",
                                      "reason": "Synthetic incomplete result"}],
                      "notes": ["Must survive storage migration"], "artifacts": ["fixture.bin"]},
            "budget": {"fixture": {"count": 2}},
            "commands": [{"command": "synthetic-only", "returncode": 1}],
            "selected_candidate_ids": ["fixture"],
            "native_artifacts": ["native/untouched.bin"],
            "unknown_extension": {"value": [None, True, 3.5]}, "stage": "fixture",
        }
        self.document["context_sha256"] = storage.logical_digest(self.document["context"])
        self.write_legacy(self.path, self.document)
        self.validator = patch.object(compact, "validate_document", side_effect=storage.read_document)
        self.validate = self.validator.start()
        self.addCleanup(self.validator.stop)
        self.addCleanup(self.temporary.cleanup)

    @staticmethod
    def write_legacy(path, document):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    def review(self, apply=False, isolates=None):
        paths = compact.isolate_batch(isolates or [self.isolate], apply=apply)
        return [json.loads(Path(path).read_text()) for path in paths]

    def test_dry_run_is_default_and_only_writes_audit(self):
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        output = io.StringIO()
        with redirect_stdout(output), patch.object(compact.storage, "write_document", side_effect=AssertionError("dry run wrote state")):
            self.assertEqual(compact.main(["--isolate", str(self.isolate)]), 0)
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns))
        self.assertFalse((self.root / "state-objects").exists())
        reviews = list((self.root / "storage-reviews").glob("*.json"))
        self.assertEqual(len(reviews), 1)
        self.assertEqual(json.loads(reviews[0].read_text())["status"], "dry-run-complete")
        self.assertIn("[preflight 1/1]", output.getvalue())
        self.assertIn("MiB", output.getvalue())
        self.assertIn("1 reviewed, 1 eligible", output.getvalue())
        self.assertIn("reclaimed: 0.000 GiB", output.getvalue())
        self.assertIn("not a prediction", output.getvalue())

    def test_preflight_decodes_each_state_only_once(self):
        real_read = storage.read_raw
        with patch.object(compact.storage, "read_raw", wraps=real_read) as decoded:
            self.review()
        state_decodes = [call for call in decoded.call_args_list if Path(call.args[0]) == self.path]
        self.assertEqual(len(state_decodes), 1)
        self.assertEqual(self.validate.call_args.kwargs["raw"], self.document)

    def test_apply_cli_reports_progress_and_actual_totals(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(compact.main(["--isolate", str(self.isolate), "--apply"]), 0)
        result = output.getvalue()
        self.assertIn("[preflight 1/1]", result)
        self.assertIn("[compact 1/1]", result)
        self.assertIn("1 compacted, 0 already compact", result)
        self.assertIn("State files:", result)
        self.assertIn("shared objects:", result)
        self.assertIn("Observed total:", result)
        self.assertNotIn("prediction", result)

    def test_apply_preserves_all_content_timestamps_mode_and_other_files(self):
        sentinel = self.root / "native/fixture/result.bin"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"native sentinel")
        evidence = self.isolate / "safety/evidence/deepsafe.json"
        evidence.parent.mkdir(parents=True)
        evidence.write_bytes(b"evidence sentinel")
        resource = self.root / "states/resource-inputs.json"
        self.write_legacy(resource, {"schema": compact.DISCOVERY_SCHEMA, "fixture": True})
        untouched = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                     for path in (sentinel, evidence, resource)}
        os.chmod(self.path, 0o640)
        os.utime(self.path, ns=(1_600_000_000_123_456_789, 1_600_000_001_987_654_321))
        before = self.path.stat()
        review = self.review(apply=True)[0]
        after = self.path.stat()
        self.assertEqual((before.st_atime_ns, before.st_mtime_ns),
                         (after.st_atime_ns, after.st_mtime_ns))
        self.assertEqual(stat.S_IMODE(after.st_mode), 0o640)
        self.assertEqual(storage.read_document(self.path), self.document)
        self.assertEqual(storage.read_raw(self.path)["schema"], storage.COMPACT_SCHEMA)
        self.assertEqual(review["files"][0]["logical_sha256"], storage.logical_digest(self.document))
        self.assertEqual(review["files"][0]["action"], "compacted")
        self.assertEqual(review["skipped"][0]["path"], str(resource))
        self.assertLess(review["files"][0]["after_bytes"], review["files"][0]["before_bytes"])
        for path, original in untouched.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), original)

    def test_repeated_apply_does_not_replace_compact_files(self):
        first_review = self.review(apply=True)[0]
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns, self.path.stat().st_ino
        with patch.object(compact.storage, "write_document", side_effect=AssertionError("rewrote v2")):
            second_review = self.review(apply=True)[0]
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns, self.path.stat().st_ino))
        self.assertEqual(second_review["files"][0]["action"], "already-compact")
        self.assertNotEqual(first_review["review_id"], second_review["review_id"])

    def test_shared_objects_are_deduplicated_across_state_locations(self):
        candidate = self.root / "candidates/fixture/step.json"
        child = copy.deepcopy(self.document)
        child["candidate_id"] = "fixture"
        self.write_legacy(candidate, child)
        self.review(apply=True)
        self.assertEqual(storage.read_document(candidate), child)
        self.assertEqual(set(storage.object_paths(self.path)), set(storage.object_paths(candidate)))
        self.assertEqual(len(list((self.root / "state-objects").glob("*.json"))), 4)

    def test_preflight_all_isolates_before_any_replacement(self):
        other_isolate = Path(self.temporary.name) / "other"
        other_path = other_isolate / "safety/deepsafe/states/prepare.json"
        self.write_legacy(other_path, self.document)
        other_path.write_text('{"schema": "basis-snakemake-deep-state-v1",', encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaises(compact.CompactionError):
            self.review(apply=True, isolates=[self.isolate, other_isolate])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / "state-objects").exists())
        self.assertEqual(json.loads(next((self.root / "storage-reviews").glob("*.json")).read_text())["status"], "failed")

    def test_provenance_validator_failure_prevents_replacement(self):
        before = self.path.read_bytes()
        with patch.object(compact, "validate_document", side_effect=ValueError("Resource checksum mismatch")):
            with self.assertRaisesRegex(compact.CompactionError, "checksum mismatch"):
                self.review(apply=True)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / "state-objects").exists())

    def test_source_change_immediately_before_replace_is_not_overwritten(self):
        real_writer = storage.write_document
        changed = b'{"concurrent_writer": true}\n'

        def interfere(path, document, **kwargs):
            path.write_bytes(changed)
            return real_writer(path, document, **kwargs)

        with patch.object(compact.storage, "write_document", side_effect=interfere):
            with self.assertRaisesRegex(compact.CompactionError, "changed during storage review"):
                self.review(apply=True)
        self.assertEqual(self.path.read_bytes(), changed)

    def test_full_disk_error_explains_space_needed_before_replacement(self):
        before = self.path.read_bytes()
        with patch.object(compact.storage, "write_document", side_effect=OSError(errno.ENOSPC, "No space left")):
            with self.assertRaisesRegex(compact.CompactionError, "Preflight does not reclaim disk space") as error:
                self.review(apply=True)
        self.assertIn(str(self.path), str(error.exception))
        self.assertIn("before the original is replaced", str(error.exception))
        self.assertEqual(self.path.read_bytes(), before)

    def test_interrupted_batch_remains_readable_and_resumable(self):
        other = self.root / "states/second.json"
        self.write_legacy(other, self.document)
        real_writer = storage.write_document
        calls = 0

        def fail_second(path, document, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("Synthetic interruption")
            return real_writer(path, document, **kwargs)

        with patch.object(compact.storage, "write_document", side_effect=fail_second):
            with self.assertRaisesRegex(compact.CompactionError, "Synthetic interruption"):
                self.review(apply=True)
        self.assertEqual(storage.read_raw(self.path)["schema"], storage.COMPACT_SCHEMA)
        self.assertEqual(storage.read_raw(other)["schema"], storage.LEGACY_SCHEMA)
        self.assertEqual(storage.read_document(self.path), self.document)
        self.assertEqual(storage.read_document(other), self.document)
        self.review(apply=True)
        self.assertEqual(storage.read_raw(other)["schema"], storage.COMPACT_SCHEMA)

    def test_corrupt_compact_object_fails_without_rewriting_state(self):
        self.review(apply=True)
        before = self.path.read_bytes()
        blob = storage.object_paths(self.path)[0]
        blob.write_text('{"changed": true}\n', encoding="utf-8")
        with self.assertRaises(compact.CompactionError):
            self.review(apply=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_json_keys_are_rejected(self):
        self.path.write_text('{"schema":"basis-snakemake-deep-state-v1","schema":"other"}', encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(compact.CompactionError, "Duplicate JSON key"):
            self.review(apply=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_incomplete_json_object_is_not_silently_skipped(self):
        self.path.write_text('{}\n', encoding="utf-8")
        with self.assertRaisesRegex(compact.CompactionError, "incomplete state schema"):
            self.review(apply=True)
        self.assertEqual(self.path.read_text(), '{}\n')

    def test_symlink_state_is_not_followed(self):
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_bytes(self.path.read_bytes())
        self.path.unlink()
        self.path.symlink_to(outside)
        before = outside.read_bytes()
        with self.assertRaisesRegex(compact.CompactionError, "non-regular or escaped"):
            self.review(apply=True)
        self.assertEqual(outside.read_bytes(), before)
        self.assertTrue(self.path.is_symlink())

    def test_symlink_audit_directory_is_rejected_even_for_dry_run(self):
        outside = Path(self.temporary.name) / "outside-audits"
        outside.mkdir()
        (self.root / "storage-reviews").symlink_to(outside, target_is_directory=True)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(compact.CompactionError, "Audit directory must not be a symlink"):
            self.review()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_error_measurement_failure_does_not_hide_original_failure(self):
        before = self.path.read_bytes()
        real_sizes = compact.storage_bytes
        calls = 0

        def fail_second_measurement(root, paths):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError("Synthetic sizing failure")
            return real_sizes(root, paths)

        with patch.object(compact, "storage_bytes", side_effect=fail_second_measurement):
            with patch.object(compact, "validate_document", side_effect=ValueError("Original validation failure")):
                with self.assertRaisesRegex(compact.CompactionError, "Original validation failure") as error:
                    self.review(apply=True)
        review = json.loads(Path(error.exception.audit_paths[0]).read_text())
        self.assertIn("Original validation failure", review["error"])
        self.assertIn("Synthetic sizing failure", review["storage_measurement_error"])
        self.assertIsNone(review["after_storage"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_audit_publication_syncs_its_directory(self):
        with patch.object(compact.storage, "sync_directory", wraps=storage.sync_directory) as synced:
            self.review()
        self.assertIn(self.root / "storage-reviews", [call.args[0] for call in synced.call_args_list])


if __name__ == "__main__":
    unittest.main()
