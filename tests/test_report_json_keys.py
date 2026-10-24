"""Generic report serialization tests; no native tools or biological data."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workflow/vendor"))
from basis.report import _jsonable


class ReportJsonKeyTests(unittest.TestCase):
    def test_numeric_and_text_key_collision_is_rejected_in_either_order(self):
        for value in ({1: "integer", "1": "text"}, {"1": "text", 1: "integer"}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "Report JSON keys collide"):
                    _jsonable(value)

    def test_nested_key_collisions_are_rejected(self):
        for value in (
            {"outer": {1: "integer", "1": "text"}},
            {"outer": [{1: "integer", "1": "text"}]},
            {"outer": ({1: "integer", "1": "text"},)},
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "Report JSON keys collide"):
                    _jsonable(value)

    def test_rejected_conversion_leaves_original_unchanged(self):
        value = {"before": {2: ("a", "b")}, "bad": [{1: "integer", "1": "text"}]}
        original = copy.deepcopy(value)
        with self.assertRaises(ValueError):
            _jsonable(value)
        self.assertEqual(value, original)

    def test_existing_key_spellings_are_preserved(self):
        value = {None: "null", True: "boolean", 2: "integer", 1.5: "float",
                 (3, 4): "tuple", Path("sample"): "path"}
        self.assertEqual(_jsonable(value), {
            "None": "null", "True": "boolean", "2": "integer", "1.5": "float",
            "(3, 4)": "tuple", "sample": "path",
        })

    def test_existing_boolean_and_null_key_collisions_are_rejected(self):
        for value in ({True: "boolean", "True": "text"},
                      {None: "null", "None": "text"}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "Report JSON keys collide"):
                    _jsonable(value)

    def test_ordinary_values_and_container_conversions_are_unchanged(self):
        value = {"numbers": [None, False, 2, 1.5], "nested": ("é", {2: "two"}),
                 "path": Path("results/item.json"), "set": {"one", "two"}}
        original = copy.deepcopy(value)
        converted = _jsonable(value)
        self.assertEqual(converted, {
            "numbers": [None, False, 2, 1.5], "nested": ["é", {"2": "two"}],
            "path": "results/item.json", "set": list(value["set"]),
        })
        self.assertEqual(value, original)
        self.assertIsNot(converted, value)
        self.assertIsNot(converted["numbers"], value["numbers"])


if __name__ == "__main__":
    unittest.main()
