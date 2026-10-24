"""JSON checkpoint regression tests using only synthetic, non-biological data."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workflow/lib"))
sys.path.insert(0, str(ROOT / "workflow/vendor"))
from safety import deep
from safety import state_storage as storage


class JsonRoundtripTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "deepsafe/states/example.json"
        self.document = {
            "schema": storage.LEGACY_SCHEMA,
            "context": {"name": "synthetic", "unicode": "é"},
            "state": {"result": {"status": "complete"},
                      "internal": {"audit": []},
                      "family_inventory": {"example": "synthetic"}},
            "budget": {"count": 0},
            "unknown_metadata": [None, False, 1.5],
        }
        self.document["context_sha256"] = storage.logical_digest(self.document["context"])

    def test_numeric_key_digest_matches_persisted_json_form(self):
        self.assertEqual(
            storage.logical_digest({2: "two", 10: "ten"}),
            storage.logical_digest({"2": "two", "10": "ten"}),
        )

    def test_mixed_supported_keys_use_json_spelling(self):
        examples = (
            ({2: "number", "text": "text"}, {"2": "number", "text": "text"}),
            ({True: "yes", None: "empty"}, {"true": "yes", "null": "empty"}),
            ({False: "no", "falsehood": "text"}, {"false": "no", "falsehood": "text"}),
            ({0.5: "half", -0.0: "zero", 1e20: "large"},
             {"0.5": "half", "-0.0": "zero", "1e+20": "large"}),
        )
        for original, persisted in examples:
            with self.subTest(keys=list(original)):
                self.assertEqual(storage.logical_digest(original), storage.logical_digest(persisted))

    def test_inline_nested_numeric_keys_roundtrip_without_mutating_caller(self):
        self.document["state"]["result"] = {
            "counts": [{2: "two", 10: "ten"}],
            "tuple": ({20: 2, 3: 1},),
            "path": Path("synthetic/example.txt"),
        }
        original = copy.deepcopy(self.document)
        expected = copy.deepcopy(self.document)
        expected["state"]["result"] = {
            "counts": [{"2": "two", "10": "ten"}],
            "tuple": [{"20": 2, "3": 1}],
            "path": "synthetic/example.txt",
        }
        storage.write_document(self.path, self.document)
        self.assertEqual(storage.read_document(self.path), expected)
        self.assertEqual(self.document, original)

    def test_numeric_keys_roundtrip_at_every_shared_location(self):
        for location in storage.LOCATIONS:
            with self.subTest(location=location):
                document = copy.deepcopy(self.document)
                expected = copy.deepcopy(self.document)
                actual_parent, expected_parent = document, expected
                for key in location[:-1]:
                    actual_parent = actual_parent[key]
                    expected_parent = expected_parent[key]
                actual_parent[location[-1]] = {"nested": {2: "two", 10: "ten"}}
                expected_parent[location[-1]] = {"nested": {"2": "two", "10": "ten"}}
                document["context_sha256"] = storage.logical_digest(document["context"])
                expected["context_sha256"] = storage.logical_digest(expected["context"])
                target = self.path.with_name("-".join(location) + ".json")
                storage.write_document(target, document)
                self.assertEqual(storage.read_document(target), expected)
                # Exercise existing-object reuse as well as initial publication.
                storage.write_document(target, document)
                self.assertEqual(storage.read_document(target), expected)

    def test_legacy_string_key_digest_and_context_identity_are_unchanged(self):
        old_context_digest = hashlib.sha256(json.dumps(
            self.document["context"], sort_keys=True, default=str, allow_nan=False
        ).encode("utf-8")).hexdigest()
        old_document_digest = hashlib.sha256(json.dumps(
            self.document, sort_keys=True, default=str, allow_nan=False
        ).encode("utf-8")).hexdigest()
        self.assertEqual(storage.logical_digest(self.document), old_document_digest)
        self.assertEqual(self.document["context_sha256"], old_context_digest)
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(self.document), encoding="utf-8")
        loaded = storage.read_document(self.path)
        storage.write_document(self.path, loaded)
        self.assertEqual(storage.logical_digest(storage.read_document(self.path)), old_document_digest)
        self.assertEqual(storage.read_document(self.path)["context_sha256"], old_context_digest)

    def test_json_key_collisions_are_rejected_instead_of_losing_data(self):
        examples = (
            {1: "number", "1": "text"},
            {True: "boolean", "true": "text"},
            {None: "null", "null": "text"},
            {1.5: "number", "1.5": "text"},
        )
        for value in examples:
            with self.subTest(keys=list(value)):
                with self.assertRaises(ValueError):
                    storage.logical_digest({"nested": [value]})

    def test_failed_collision_write_preserves_existing_checkpoint(self):
        storage.write_document(self.path, self.document)
        original_bytes = self.path.read_bytes()
        objects = {path: path.read_bytes() for path in storage.object_paths(self.path)}
        changed = copy.deepcopy(self.document)
        changed["state"]["result"] = {1: "number", "1": "text"}
        with self.assertRaises(ValueError):
            storage.write_document(self.path, changed)
        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(storage.read_document(self.path), self.document)
        self.assertTrue(all(path.read_bytes() == content for path, content in objects.items()))
        self.assertEqual(list(self.path.parent.glob(".state-*.tmp")), [])

    def test_cycles_are_rejected_without_replacing_existing_checkpoint(self):
        storage.write_document(self.path, self.document)
        original_bytes = self.path.read_bytes()
        mapping = {}
        mapping["self"] = mapping
        sequence = []
        sequence.append(sequence)
        indirect = {"items": []}
        indirect["items"].append(indirect)
        for value in (mapping, sequence, indirect):
            with self.subTest(container_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    storage.logical_digest(value)
                changed = copy.deepcopy(self.document)
                changed["state"]["result"] = value
                with self.assertRaises(ValueError):
                    storage.write_document(self.path, changed)
                self.assertEqual(self.path.read_bytes(), original_bytes)
                self.assertEqual(storage.read_document(self.path), self.document)
                self.assertEqual(list(self.path.parent.glob(".state-*.tmp")), [])

    def test_repeated_aliases_are_accepted_and_not_mistaken_for_cycles(self):
        shared = {2: "two", 10: "ten"}
        self.document["state"]["result"] = [shared, shared]
        self.document["state"]["internal"] = shared
        expected = copy.deepcopy(self.document)
        expected["state"]["result"] = [
            {"2": "two", "10": "ten"}, {"2": "two", "10": "ten"},
        ]
        expected["state"]["internal"] = {"2": "two", "10": "ten"}
        self.assertEqual(storage.logical_digest(self.document), storage.logical_digest(expected))
        storage.write_document(self.path, self.document)
        self.assertEqual(storage.read_document(self.path), expected)
        self.assertIs(self.document["state"]["result"][0], shared)
        self.assertIs(self.document["state"]["result"][1], shared)
        self.assertIs(self.document["state"]["internal"], shared)

    def test_plain_json_writer_supports_nested_mixed_keys(self):
        value = {"nested": [{2: "two", 10: "ten", "label": "synthetic"}]}
        original = copy.deepcopy(value)
        deep.write(self.path, value)
        self.assertEqual(deep.read(self.path), {
            "nested": [{"2": "two", "10": "ten", "label": "synthetic"}],
        })
        self.assertEqual(storage.logical_digest(deep.read(self.path)), storage.logical_digest(value))
        self.assertEqual(value, original)
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())

    def test_plain_json_writer_rejects_collisions_and_preserves_previous_file(self):
        original = {"saved": "synthetic"}
        deep.write(self.path, original)
        original_bytes = self.path.read_bytes()
        with self.assertRaises(ValueError):
            deep.write(self.path, {"nested": {2: "number", "2": "text"}})
        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(deep.read(self.path), original)
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())

    def test_nonfinite_numbers_are_rejected_as_values_and_keys(self):
        for number in (float("nan"), float("inf"), -float("inf")):
            for value in ({"value": number}, {number: "value"}):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        storage.logical_digest(value)

    def test_unsupported_dictionary_keys_are_rejected(self):
        with self.assertRaises(TypeError):
            storage.logical_digest({("tuple",): "value"})

    def test_tampered_numeric_key_shared_object_still_fails_verification(self):
        self.document["state"]["internal"] = {2: "two", 10: "ten"}
        storage.write_document(self.path, self.document)
        envelope = storage.read_raw(self.path)
        reference = next(item for item in envelope["shared_objects"]
                         if item["location"] == ["state", "internal"])
        target = self.path.parent / reference["path"]
        target.write_text('{"2": "modified", "10": "ten"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            storage.read_document(self.path)


if __name__ == "__main__":
    unittest.main()
