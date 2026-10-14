"""Synthetic normalization acceptance tests; no native execution."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import statistics
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

WORK = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("normalization_acceptance", WORK / "BaSIS_check_normalization.py")
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)
sys.path.insert(0, str(WORK / "source"))
from basis.normalize import normalize_result


def fixture(sid="isolate4"):
    sample = {"sample_id": sid}
    validation = {"sample_id": sid, "protein_count": 1, "contigs": 1,
        "protein_coordinates": {"P": {"contig": "chr", "start": 10, "end": 99, "strand": "+"}},
        "contig_lengths": {"chr": 1000}, "contig_aliases": {"chr": "chr", "alias": "chr"}}
    raw = {"sample_id": sid, "module": "rgi", "execution_status": "completed", "interpretation": "detected",
        "findings": [{"category": "amr", "protein_id": "P", "gene": "synthetic", "identity": 75.0,
                      "coverage": 88.0, "attributes": {"label": "preserve"}}]}
    normal = normalize_result(raw, "rgi", sample, validation)
    # A previously recorded self alias is preserved even though a fresh pass
    # no longer invents it. Rehash once to create this historical fixture.
    normal["findings"][0]["attributes"]["native_contig_alias"] = "chr"
    normal = normalize_result(normal, "rgi", sample, validation)
    members = [.1, .2, .3, .4]
    pf2 = {"sample_id": sid, "module": "pathogenfinder2", "execution_status": "completed",
           "interpretation": "inconclusive", "findings": [], "metrics": {"ensemble_member_scores": members,
           "prediction_mean": statistics.mean(members), "prediction_std": statistics.pstdev(members)}}
    return {"schema_version": "basis-report-1", "samples": [sample],
            "run": {"input_validations": {sid: validation}}, "records": [normal, pf2,
                {"sample_id": sid, "module": "mlst", "execution_status": "not_assessed"}],
            "deep_records": [{"sample_id": sid, "module": "deepsafe", "candidates": [{"unchanged": True}]}]}


class CheckNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_synthetic_first_provenance_and_pf2_wrapper(self):
        rows = helper.synthetic_checks(normalize_result)
        self.assertEqual([r["case"] for r in rows], ["protein_only", "self_alias", "native_alias", "pf2_wrapper"])
        self.assertTrue(all(r["status"] == "passed" and len(r["normalized_sha256"]) == 64 for r in rows))

    def test_wrong_synthetic_provenance_is_detected(self):
        def broken(*args):
            out = normalize_result(*args)
            if out["findings"]:
                out["findings"][0]["attributes"]["reported_coordinates"]["start"] = 10
            return out
        with self.assertRaisesRegex(ValueError, "provenance changed|second normalization changed"):
            helper.synthetic_checks(broken)

    def test_saved_replay_preserves_legacy_alias_metrics_and_skipped_scope(self):
        evidence = fixture()
        before = helper.canonical(evidence)
        result = helper.replay_evidence(evidence, "isolate4", normalize_result)
        self.assertEqual(result["finding_count"], 1)
        self.assertEqual(result["completed_modules_replayed"], 2)
        self.assertEqual(result["skipped_records"], [{"module": "mlst", "execution_status": "not_assessed"}])
        self.assertFalse(result["deep_records_replayed"])
        self.assertEqual(helper.canonical(evidence), before)
        for module in result["modules"]:
            self.assertEqual(module["saved_findings_sha256"], module["replayed_findings_sha256"])
            self.assertEqual(module["first_pass_sha256"], module["second_pass_sha256"])

    def test_saved_changes_in_ids_scores_and_second_pass_metadata_fail(self):
        for field, value in (("identity", 70.0), ("finding_id", "changed-id")):
            with self.subTest(field=field):
                def broken(record, *args):
                    out = normalize_result(record, *args)
                    out["findings"][0][field] = value
                    return out
                with self.assertRaisesRegex(ValueError, "changed saved findings"):
                    helper.replay_evidence(fixture(), "isolate4", broken)
        counter = [0]
        def wrapper_drift(record, *args):
            out = normalize_result(record, *args)
            counter[0] += 1
            out["unrequested_counter"] = counter[0]
            return out
        with self.assertRaisesRegex(ValueError, "second normalization changed full result"):
            helper.replay_evidence(fixture(), "isolate4", wrapper_drift)

    def test_validation_shapes_and_sample_binding_fail_closed(self):
        mutations = [
            lambda e: e["samples"][0].update(sample_id="other"),
            lambda e: e["run"]["input_validations"]["isolate4"].pop("protein_coordinates"),
            lambda e: e["run"]["input_validations"]["isolate4"]["contig_lengths"].update(chr=True),
            lambda e: e["run"]["input_validations"]["isolate4"]["protein_coordinates"]["P"].update(end=1001),
            lambda e: e["run"]["input_validations"]["isolate4"]["contig_aliases"].update(alias="absent"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                evidence = fixture()
                mutate(evidence)
                with self.assertRaises(ValueError):
                    helper.replay_evidence(evidence, "isolate4", normalize_result)

    def test_source_pin_and_installed_origin_checks(self):
        p = self.root / "evidence.json"
        p.write_text(json.dumps(fixture()))
        pin = helper.file_identity(p)
        self.assertEqual(helper.load_verified(pin)[0], fixture())
        p.write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "Pinned evidence bytes changed"):
            helper.load_verified(pin)
        fake = types.ModuleType("basis")
        fake.__version__ = "wrong"
        fake.__file__ = str(self.root / "basis/__init__.py")
        with patch.dict(sys.modules, {"basis": fake}):
            with self.assertRaisesRegex(ValueError, "Expected installed"):
                helper.installed_runtime()
            fake.__version__ = helper.RELEASE
            with self.assertRaisesRegex(ValueError, "active installed prefix"):
                helper.installed_runtime()
            fake.__file__ = str(Path(sys.prefix) / "fixture-package/basis/__init__.py")
            verifier = types.SimpleNamespace(__file__=str(Path(fake.__file__).parent / "release.py"),
                                             verify_install=lambda: {"status": "failed"})
            with patch.object(helper.importlib, "import_module", return_value=verifier):
                with self.assertRaisesRegex(ValueError, "integrity verification did not pass"):
                    helper.installed_runtime()

    def install_fixture_pins(self):
        (self.root / "validation").mkdir()
        samples = {}
        for sid in helper.SAMPLE_IDS:
            path = self.root / (sid + ".json")
            path.write_text(json.dumps(fixture(sid)))
            samples[sid] = helper.file_identity(path)
        (self.root / "validation/normalization-pilot-pins.json").write_text(json.dumps({
            "schema": "basis-normalization-pilot-pins-v1", "samples": samples}))
        self.expected_pin_digest = helper.file_identity(self.root / "validation/normalization-pilot-pins.json")["sha256"]
        return samples

    def run_main(self):
        with patch.object(helper, "ROOT", self.root), patch.object(helper, "installed_runtime",
                return_value=(normalize_result, {"test_only_package_fingerprint": "fixture"})), \
                patch.object(helper, "EXPECTED_PINS_SHA256", self.expected_pin_digest):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return helper.main(["--outdir-parent", str(self.root)])

    def test_main_pinned_replay_creates_fresh_compact_verified_packages(self):
        pins = self.install_fixture_pins()
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.run_main(), 0)
        folders = sorted(self.root.glob("normalization-acceptance-*"))
        self.assertEqual(len(folders), 2)
        for out in folders:
            summary = json.loads((out / "summary.json").read_text())
            self.assertEqual(summary["status"], "passed")
            self.assertEqual(summary["sources"], pins)
            self.assertTrue(summary["source_files_unchanged"])
            self.assertTrue(summary["installed_package_unchanged"])
            self.assertFalse(summary["native_analyses_run"])
            self.assertEqual(set(summary["samples"]), set(helper.SAMPLE_IDS))
            with zipfile.ZipFile(out / "normalization-acceptance-summary.zip") as z:
                self.assertEqual(set(z.namelist()), {"summary.json", "UPLOAD_MANIFEST.json"})
                manifest = json.loads(z.read("UPLOAD_MANIFEST.json"))
                b = z.read("summary.json")
                self.assertEqual(manifest["files"]["summary.json"]["bytes"], len(b))
                self.assertEqual(manifest["files"]["summary.json"]["sha256"],
                                 helper.hashlib.sha256(b).hexdigest())
        (self.root / "validation/normalization-pilot-pins.json").write_text("{}")
        self.assertEqual(self.run_main(), 2)
        failures = [json.loads((p / "summary.json").read_text()) for p in self.root.glob("normalization-acceptance-*")]
        failed = [s for s in failures if s["status"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("Frozen pilot pins file changed", failed[0]["error"])

    def test_main_rechecks_sources_after_replay_failure(self):
        pins = self.install_fixture_pins()
        def fail_after_mutating(_evidence, _sid, _normalizer):
            Path(pins["isolate4"]["path"]).write_bytes(b"changed during replay")
            raise ValueError("Synthetic replay failure")
        with patch.object(helper, "replay_evidence", side_effect=fail_after_mutating):
            self.assertEqual(self.run_main(), 2)
        out = next(self.root.glob("normalization-acceptance-*"))
        summary = json.loads((out / "summary.json").read_text())
        self.assertEqual(summary["status"], "failed")
        self.assertIn("Synthetic replay failure", summary["error"])
        self.assertIn("Pinned source changed during replay", summary["final_verification_error"])
        self.assertFalse(summary.get("source_files_unchanged", False))
        self.assertTrue(summary["installed_package_unchanged"])


if __name__ == "__main__":
    unittest.main()
