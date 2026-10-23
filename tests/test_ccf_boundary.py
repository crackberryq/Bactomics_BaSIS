"""Saved synthetic interval validation; no biological executables or databases."""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from basis.adapters import crisprcasfinder as ccf
from basis.report_genome import build_genome_data
from safety import ccf_assembly as assembly, extend, reporting, runtime
from tests.test_ccf_assembly import fixture


CONTIG = "synthetic-boundary-contig"
ARRAY = "synthetic-boundary-array"
SEQUENCE = "A" * 21200
REVIEW = {
    "status": "native_end_unresolved",
    "reported_start": 20967,
    "reported_end": 21178,
    "component_start": 20967,
    "component_end": 21177,
    "unassigned_terminal_bases": 1,
}


def synthetic_array(*, terminal_overhang=1):
    """Reproduce interval arithmetic using invented, constant sequence content."""
    end = 21177 + terminal_overhang
    parts = [("DR", 20967, 20994), ("Spacer", 20995, 21027),
             ("DR", 21028, 21055), ("Spacer", 21056, 21088),
             ("DR", 21089, 21116), ("Spacer", 21117, 21149),
             ("DR", 21150, 21177)]
    regions = [("LeftFLANK", 20966, 20966), *parts,
               ("RightFLANK", end + 1, end + 1)]
    return {
        "Name": ARRAY, "Start": 20967, "End": end,
        "DR_Consensus": "A" * 28, "DR_Length": 28, "Spacers": 3,
        "Evidence_Level": 3, "Potential_Orientation": "+", "CRISPRDirection": "+",
        "Conservation_DRs": 100, "Conservation_Spacers": 0,
        "Regions": [{"Type": kind, "Start": start, "End": stop,
                     "Sequence": SEQUENCE[start - 1:stop], "Leader": 0, "AT": 100}
                    for kind, start, stop in regions],
    }


def refresh_sequence(region):
    region["Sequence"] = SEQUENCE[region["Start"] - 1:region["End"]]


def set_array_end(array, end):
    array["End"] = end
    right = array["Regions"][-1]
    right.update(Start=end + 1, End=end + 1)
    refresh_sequence(right)


class ArrayBoundaryTests(unittest.TestCase):
    def parse(self, array, *, level=3):
        return ccf._parse_arrays([array], CONTIG, SEQUENCE, level)[0]

    def test_single_terminal_base_is_retained_as_unresolved_native_evidence(self):
        array = synthetic_array()
        before = copy.deepcopy(array)
        finding = self.parse(array)
        self.assertEqual((finding["start"], finding["end"]), (20967, 21178))
        self.assertEqual(finding["attributes"]["native"], before)
        self.assertEqual(array, before)
        self.assertEqual(finding["attributes"]["boundary_review"], REVIEW)
        self.assertEqual(finding["attributes"]["coordinate_basis"], "1-based inclusive")
        self.assertEqual(finding["attributes"]["repeat_count"], 4)
        self.assertEqual(finding["attributes"]["spacer_count"], 3)
        self.assertEqual(finding["attributes"]["repeat_intervals"], [
            {"start": 20967, "end": 20994, "length": 28},
            {"start": 21028, "end": 21055, "length": 28},
            {"start": 21089, "end": 21116, "length": 28},
            {"start": 21150, "end": 21177, "length": 28},
        ])

    def test_exact_boundaries_keep_existing_evidence_without_review_flag(self):
        array = synthetic_array(terminal_overhang=0)
        before = copy.deepcopy(array)
        finding = self.parse(array)
        self.assertEqual((finding["start"], finding["end"]), (20967, 21177))
        self.assertNotIn("boundary_review", finding["attributes"])
        self.assertEqual(finding["attributes"]["native"], before)
        self.assertEqual(array, before)

    def test_boundary_review_does_not_promote_native_evidence_level(self):
        array = synthetic_array()
        array["Evidence_Level"] = 1
        finding = self.parse(array)
        self.assertEqual(finding["evidence_level"], "exploratory")
        self.assertFalse(finding["attributes"]["passes_threshold"])
        self.assertEqual(finding["attributes"]["native_evidence_level"], 1)
        self.assertEqual(finding["attributes"]["boundary_review"], REVIEW)

    def test_other_endpoint_disagreements_rejected_with_array_identity(self):
        cases = {}
        for end in (21176, 21179, 21180):
            array = synthetic_array()
            set_array_end(array, end)
            cases[f"reported end {end}"] = array
        array = synthetic_array()
        array["Start"] -= 1
        array["Regions"][0].update(Start=20965, End=20965)
        cases["first component starts after array"] = array
        for label, array in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError) as raised:
                    self.parse(array)
                self.assertIn(ARRAY, str(raised.exception))
                self.assertIn(CONTIG, str(raised.exception))

    def test_interior_gap_or_overlap_rejected_even_with_matching_sequence(self):
        for shift in (-1, 1):
            array = synthetic_array()
            spacer = array["Regions"][2]
            spacer["Start"] += shift
            refresh_sequence(spacer)
            with self.subTest(shift=shift):
                with self.assertRaises(ValueError) as raised:
                    self.parse(array)
                self.assertIn(ARRAY, str(raised.exception))
                self.assertIn(CONTIG, str(raised.exception))

    def test_terminal_exception_requires_each_repeat_to_match_declared_length(self):
        # Adjust the adjacent spacer too, so coverage and sequence agreement
        # remain intact and only the exception's repeat-length guard can fail.
        for repeat_index in (1, 3, 5, 7):
            array = synthetic_array()
            regions = array["Regions"]
            if repeat_index < 7:
                regions[repeat_index]["End"] -= 1
                regions[repeat_index + 1]["Start"] -= 1
                changed = (repeat_index, repeat_index + 1)
            else:
                regions[repeat_index]["Start"] += 1
                regions[repeat_index - 1]["End"] += 1
                changed = (repeat_index - 1, repeat_index)
            for index in changed:
                refresh_sequence(regions[index])
            with self.subTest(repeat=repeat_index), self.assertRaises(ValueError):
                self.parse(array)

    def test_malformed_or_unverified_regions_are_not_accepted_by_exception(self):
        cases = {}
        array = synthetic_array()
        array["Regions"][2]["Sequence"] = "C" * 33
        cases["sequence mismatch"] = array
        array = synthetic_array()
        array["Regions"][3]["Type"] = "Spacer"
        cases["nonalternating parts"] = array
        array = synthetic_array()
        del array["Regions"][3]
        cases["missing repeat"] = array
        array = synthetic_array()
        del array["Regions"][0]
        cases["missing flank"] = array
        array = synthetic_array()
        array["Regions"][-1].update(Start=21178, End=21178)
        cases["flank anchored to component instead of native end"] = array
        array = synthetic_array()
        array["Regions"][3]["Start"] = True
        cases["boolean component coordinate"] = array
        array = synthetic_array()
        array["Regions"][3]["End"] = 21027
        cases["inverted component"] = array
        array = synthetic_array()
        array["End"] = len(SEQUENCE) + 1
        cases["array outside assembly"] = array
        array = synthetic_array()
        array["Start"] = 20967.5
        cases["fractional array coordinate"] = array
        array = synthetic_array()
        array["Spacers"] = 2
        cases["wrong spacer count"] = array
        array = synthetic_array()
        array["DR_Length"] = 27
        cases["consensus length mismatch"] = array
        for label, array in cases.items():
            with self.subTest(case=label), self.assertRaises(ValueError):
                self.parse(array)


class BoundaryEvidencePreservationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="synthetic boundary review ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.safety = self.root / "sample/safety"
        self.work = self.safety / "native/crisprcasfinder"
        self.sequences = {CONTIG: SEQUENCE}
        fixture(self.work, self.sequences, cas_records=("CCF000001",), with_array=True)
        self.native_path = self.work / "native/result.json"
        native = runtime.read_json(self.native_path)
        native["Sequences"][0]["Crisprs"] = [synthetic_array()]
        runtime.write_json(self.native_path, native)
        self.validation = {"protein_coordinates": {}, "contig_lengths": {CONTIG: len(SEQUENCE)}}
        self.sample = {"sample_id": "synthetic", "input_validation": self.validation}
        self.config = {"modules": {"crisprcasfinder": {"enabled": True}},
                       "module_decisions": {"crisprcasfinder": {
                           "requested": True, "action": "run", "scope_incomplete": False,
                           "reason": "requested"}}}

    def parse(self):
        return assembly.parse_assembly(self.work, self.sequences)

    def test_saved_assembly_marks_unresolved_boundary_without_altering_counts(self):
        before = self.native_path.read_bytes()
        with patch("subprocess.Popen", side_effect=AssertionError("native process forbidden")):
            parsed = self.parse()
        self.assertEqual(self.native_path.read_bytes(), before)
        self.assertTrue(parsed["scope_incomplete"])
        self.assertEqual(parsed["reason"], "crisprcasfinder_boundary_review_required")
        self.assertEqual(parsed["interpretation"], "detected")
        self.assertEqual(parsed["metrics"]["array_count"], 1)
        self.assertEqual(parsed["metrics"]["array_count_at_reporting_level"], 1)
        self.assertEqual(parsed["metrics"]["repeat_count"], 4)
        self.assertEqual(parsed["metrics"]["spacer_count"], 3)
        reviews = parsed["metrics"]["array_boundary_reviews"]
        self.assertEqual(len(reviews), 1)
        self.assertEqual({key: reviews[0][key] for key in REVIEW}, REVIEW)
        self.assertTrue(any("unresolved" in note.lower() and "boundar" in note.lower()
                            for note in parsed["notes"]))

    def test_exact_saved_assembly_has_no_boundary_scope_hold(self):
        native = runtime.read_json(self.native_path)
        native["Sequences"][0]["Crisprs"] = [synthetic_array(terminal_overhang=0)]
        runtime.write_json(self.native_path, native)
        parsed = self.parse()
        self.assertFalse(parsed.get("scope_incomplete", False))
        self.assertNotEqual(parsed.get("reason"), "crisprcasfinder_boundary_review_required")
        self.assertFalse(parsed["metrics"].get("array_boundary_reviews"))
        self.assertNotIn("boundary_review", parsed["findings"][0]["attributes"])

    def test_verified_contig_collection_preserves_boundary_scope_and_inputs(self):
        parsed = self.parse()
        verified = {"CCF000001": {
            "findings": parsed["findings"],
            "status": parsed["metrics"]["cas_contig_assessments"][CONTIG],
        }}
        verified_before = copy.deepcopy(verified)
        assembly_path = self.root / "assembly.fna"
        assembly_path.write_text(f">{CONTIG}\n{SEQUENCE}\n", encoding="ascii")
        sample = {**self.sample, "assembly": str(assembly_path)}
        config = self.config["modules"]["crisprcasfinder"]
        sample_before, config_before = copy.deepcopy(sample), copy.deepcopy(config)
        contigs = self.root / "contigs"
        mapping_path = contigs / "contigs.json"
        runtime.write_json(mapping_path, assembly.contig_map(self.sequences))
        files_before = {path: path.read_bytes() for path in (assembly_path, mapping_path)}
        with patch("subprocess.Popen", side_effect=AssertionError("native process forbidden")), \
                patch.object(ccf, "parse_contig", side_effect=AssertionError("verified data reparsed")), \
                patch.object(ccf, "_verify_runtime_isolation", side_effect=AssertionError("verified data rechecked")):
            collected = extend.collect_ccf(sample, config, contigs, self.root / "compatibility-native",
                                           verified_parsed=verified)
        self.assertTrue(collected["scope_incomplete"])
        self.assertEqual(collected["reason"], "crisprcasfinder_boundary_review_required")
        self.assertEqual(collected["metrics"]["array_boundary_reviews"],
                         parsed["metrics"]["array_boundary_reviews"])
        self.assertEqual(collected["findings"], verified_before["CCF000001"]["findings"])
        self.assertEqual(collected["metrics"]["array_count"], 1)
        self.assertEqual(collected["metrics"]["repeat_count"], 4)
        self.assertEqual(collected["metrics"]["spacer_count"], 3)
        self.assertTrue(any("unresolved" in note.lower() and "boundar" in note.lower()
                            for note in collected["notes"]))
        self.assertEqual(verified, verified_before)
        self.assertEqual(sample, sample_before)
        self.assertEqual(config, config_before)
        for path, content in files_before.items():
            self.assertEqual(path.read_bytes(), content, str(path))

    def test_normalization_and_report_keep_coordinates_review_and_incomplete_scope(self):
        parsed = self.parse()
        saved = self.work / "parsed.json"
        runtime.write_json(saved, parsed)
        source_before = {path: path.read_bytes() for path in self.work.rglob("*") if path.is_file()}
        with patch("subprocess.Popen", side_effect=AssertionError("native process forbidden")):
            record = extend.normalize_saved("crisprcasfinder", self.sample, self.config,
                                            self.validation, saved)
            self.assertEqual(record["findings"][0]["attributes"]["native"], synthetic_array())
            self.assertEqual(record["findings"][0]["attributes"]["boundary_review"], REVIEW)
            self.assertTrue(record["scope_incomplete"])
            self.assertEqual(record["execution_status"], "completed")
            self.assertEqual(record["reason"], "crisprcasfinder_boundary_review_required")
            self.assertEqual((record["findings"][0]["start"], record["findings"][0]["end"]),
                             (20967, 21178))
            evidence_path = self.safety / "evidence/crisprcasfinder.json"
            sample_path = self.safety / "metadata/sample.json"
            config_path = self.safety / "metadata/config.json"
            runtime.write_json(evidence_path, record)
            runtime.write_json(sample_path, self.sample)
            runtime.write_json(config_path, self.config)
            evidence_before = evidence_path.read_bytes()
            run, records, deep = reporting.aggregate(self.sample, self.config, [evidence_path])
            self.assertEqual(run["completeness"], "incomplete")
            self.assertEqual(run["incomplete_modules"], ["synthetic:crisprcasfinder"])
            decision = run["module_decisions"]["synthetic"]["crisprcasfinder"]
            self.assertTrue(decision["scope_incomplete"])
            self.assertEqual(decision["reason"], "crisprcasfinder_boundary_review_required")
            genome = build_genome_data(run, [self.sample], records, deep)
            regions = genome["samples"][0]["contigs"][0]["regions"]
            self.assertEqual([(row["track"], row["start"], row["end"]) for row in regions],
                             [("crispr", 20967, 21178)])
            reporting.render(sample_path, config_path, [evidence_path], self.safety)
        self.assertEqual(evidence_path.read_bytes(), evidence_before)
        for path, content in source_before.items():
            self.assertEqual(path.read_bytes(), content, str(path))
        report_evidence = runtime.read_json(self.safety / "evidence.json")
        self.assertEqual(report_evidence["run"]["completeness"], "incomplete")
        reported = report_evidence["records"][0]
        self.assertTrue(reported["scope_incomplete"])
        self.assertEqual(reported["findings"][0]["attributes"]["native"], synthetic_array())
        self.assertEqual(reported["findings"][0]["attributes"]["boundary_review"], REVIEW)
        summary = runtime.read_json(self.safety / "metadata/run-summary.json")
        self.assertEqual(summary["completeness"], "incomplete")
        with (self.safety / "tables/findings.tsv").open(newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["start"], rows[0]["end"]), ("20967", "21178"))
        self.assertEqual(json.loads(rows[0]["attributes"])["boundary_review"], REVIEW)


if __name__ == "__main__":
    unittest.main()
