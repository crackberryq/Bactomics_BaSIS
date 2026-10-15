"""Recovery verifies historical receipts and snapshots without running providers."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from basis.bootstrap import MANIFEST, POLICY, plan_auto_resources
from basis.config import new_study_config, load_config
from basis.resource_recovery import AUDITED_PROVIDER_SOURCE, prepare_recovery
from basis.util import atomic_json, hash_object, read_json, sha256_file
from basis.workflow_runtime import _write


class ResourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config_path = self.root / "config.yaml"
        cfg = new_study_config(resource_dir=self.root / "cache")
        cfg["outdir"] = str(self.root / "results")
        cfg["samples"] = str(self.root / "samples.tsv")
        self.config_path.write_text(yaml.safe_dump(cfg))
        cfg = load_config(self.config_path)
        with patch("basis.bootstrap._implementation", return_value=AUDITED_PROVIDER_SOURCE):
            cfg, specs = plan_auto_resources(cfg, deep=False, extend=False)
        self.plan = {"schema_version": 1, "version": "0.3.0rc2", "source_hash": "a" * 64,
                     "config": cfg, "outdir": cfg["outdir"], "options": {"deep": False, "extend": False},
                     "bootstrap_resources": specs}
        self.outdir = Path(cfg["outdir"])
        self.run = self.outdir / "provenance/scheduler/test-run"
        self.context = {"run_id": self.run.name, "version": "0.3.0rc2", "source_hash": self.plan["source_hash"],
                        "scheduler_receipt_dir": str(self.run), "options": self.plan["options"],
                        "config_hash": hash_object(cfg)}
        self.save_plan()
        _write(self.run / "run-context.json", self.context)
        atomic_json(self.outdir / "run.json", {"run_id": self.run.name})
        self.ready("amrfinder")
        self.ready("card")
        self.failed("genomad")
        self.output = self.root / "retry.yaml"

    def save_plan(self):
        self.plan.pop("plan_hash", None)
        self.plan["plan_hash"] = hash_object(self.plan)
        self.plan_path = self.outdir / "provenance/plans" / (self.plan["plan_hash"] + ".json")
        atomic_json(self.plan_path, self.plan)

    def spec(self, name):
        return next(s for s in self.plan["bootstrap_resources"] if s["id"] == name)

    def receipt(self, name, **fields):
        spec = self.spec(name)
        value = {"schema": "basis-bootstrap-receipt-v1", "run_id": self.run.name,
                 "resource_id": name, "provider": name, "cache_key": spec["cache_key"],
                 "plan_hash": self.plan["plan_hash"], "source_hash": self.plan["source_hash"], **fields}
        path = self.run / "bootstrap" / (name + ".json")
        _write(path, value)
        return path

    def ready(self, name, external=None):
        spec = self.spec(name)
        root = Path(spec["snapshot"])
        root.mkdir(parents=True)
        data = root / "resource.bin"
        data.write_bytes(b"synthetic frozen database")
        module = "rgi" if name == "card" else name
        values = {"database": str(data), "database_version": "fixture"}
        ext = {}
        if external:
            values["metadata"] = str(external)
            ext[str(external)] = {"sha256": sha256_file(external), "size": external.stat().st_size}
        self.manifest = {"schema": POLICY, "resource_id": name, "provider": name, "cache_key": spec["cache_key"],
                         "spec": spec, "symlinks": {}, "files": {data.name: {"sha256": sha256_file(data), "size": data.stat().st_size}},
                         "external_files": ext, "result": {"module_updates": {module: values}, "deep_updates": {}, "provenance": {}}}
        manifest = root / MANIFEST
        atomic_json(manifest, self.manifest)
        return self.receipt(name, status="ready", manifest=str(manifest), manifest_sha256=sha256_file(manifest))

    def failed(self, name):
        return self.receipt(name, status="failed", staging_directory=str(self.root / "unpublished"))

    def test_recover_two_resources_preserve_configuration_and_originals(self):
        before = self.config_path.read_bytes()
        old_plan = self.plan_path.read_bytes()
        audit = prepare_recovery(self.config_path, self.output)
        cfg = load_config(self.output)
        self.assertEqual([r["resource_id"] for r in audit["recovered"]], ["amrfinder", "card"])
        self.assertTrue(Path(cfg["modules"]["rgi"]["database"]).is_absolute())
        self.assertIsNone(cfg["modules"]["genomad"]["database"])
        self.assertEqual(cfg["resources"]["policy"], "auto")
        self.assertEqual(cfg["modules"]["vfdb"]["min_identity"], 80.0)
        self.assertNotEqual(cfg["outdir"], str(self.outdir))
        self.assertFalse(Path(cfg["outdir"]).exists())
        self.assertFalse(audit["native_analyses_reused"])
        self.assertEqual(before, self.config_path.read_bytes())
        self.assertEqual(old_plan, self.plan_path.read_bytes())
        saved = read_json(self.output.with_suffix(".resource-recovery.json"))
        digest = saved.pop("content_sha256")
        self.assertEqual(digest, hash_object(saved))
        self.assertEqual(saved["output_config_sha256"], sha256_file(self.output))
        with self.assertRaisesRegex(ValueError, "new files"):
            prepare_recovery(self.config_path, self.output)

    def test_explicit_vfdb_snapshot_is_verified_and_does_not_change_thresholds(self):
        root = self.root / "legacy-vfdb"
        root.mkdir()
        (root / "vfdb_setA.dmnd").write_bytes(b"fixture DIAMOND index")
        (root / "VFDB_setA_pro.utf8.fas").write_text(">VFG1(gb|WP_1) fixture\nMAMAM\n")
        manifest = {"database": "VFDB", "dataset": "Set A core proteins",
                    "source_url": "https://www.mgc.ac.cn/VFs/Down/VFDB_setA_pro.fas.gz",
                    "database_version": "fixture snapshot", "protein_count": 1,
                    "files": {p.name: {"sha256": sha256_file(p)} for p in root.iterdir()}}
        atomic_json(root / "provenance.json", manifest)
        audit = prepare_recovery(self.config_path, self.output, vfdb_snapshot=root)
        self.assertIsNotNone(audit["legacy_vfdb"])
        self.assertNotIn("vfdb", [r["resource_id"] for r in audit["retained_automatic"]])
        cfg = load_config(self.output)
        self.assertEqual(cfg["modules"]["vfdb"]["database"], str(root / "vfdb_setA.dmnd"))
        self.assertEqual(cfg["modules"]["vfdb"]["database_manifest"], str(root / "provenance.json"))
        for name in ("min_identity", "min_coverage", "min_query_coverage", "command_prefix", "executable"):
            self.assertEqual(cfg["modules"]["vfdb"][name], self.plan["config"]["modules"]["vfdb"][name])

    def test_failed_and_non_audited_ready_snapshots_remain_automatic(self):
        self.ready("vfdb")
        audit = prepare_recovery(self.config_path, self.output)
        retained = {r["resource_id"]: r for r in audit["retained_automatic"]}
        self.assertEqual(retained["genomad"]["reason"], "preparation_not_ready")
        self.assertEqual(retained["vfdb"]["reason"], "not_in_audited_recovery_scope")
        self.assertIsNone(load_config(self.output)["modules"]["vfdb"]["database"])

    def test_changed_snapshot_refused_without_creating_configuration(self):
        (Path(self.spec("amrfinder")["snapshot"]) / "resource.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            prepare_recovery(self.config_path, self.output)
        self.assertFalse(self.output.exists())

    def test_changed_external_bundled_resource_refused(self):
        external = self.root / "external.txt"
        external.write_text("original")
        self.ready("isescan", external=external)
        external.write_text("different")
        with self.assertRaisesRegex(ValueError, "External bundled resources changed"):
            prepare_recovery(self.config_path, self.output)

    def test_receipt_wrong_run_is_refused_even_with_valid_document_checksum(self):
        path = self.run / "bootstrap/card.json"
        value = read_json(path)
        value["run_id"] = "unrelated-run"
        _write(path, value)
        with self.assertRaisesRegex(ValueError, "receipt identity mismatch"):
            prepare_recovery(self.config_path, self.output)

    def test_changed_config_and_plan_are_refused(self):
        supplied = yaml.safe_load(self.config_path.read_text())
        supplied["modules"]["vfdb"]["min_identity"] = 50
        self.config_path.write_text(yaml.safe_dump(supplied))
        with self.assertRaisesRegex(ValueError, "configuration differs"):
            prepare_recovery(self.config_path, self.output)
        supplied["modules"]["vfdb"]["min_identity"] = 80.0
        self.config_path.write_text(yaml.safe_dump(supplied))
        plan = read_json(self.plan_path)
        plan["enabled_modules"] = ["unexpected"]
        atomic_json(self.plan_path, plan)
        with self.assertRaisesRegex(ValueError, "plan checksum mismatch"):
            prepare_recovery(self.config_path, self.output)

    def test_changed_environment_recipe_is_not_recovered(self):
        with patch("basis.resource_recovery.sha256_file", wraps=sha256_file) as hashing:
            def digest(path):
                return "f" * 64 if str(path).endswith("core-amr.yml") else sha256_file(path)
            hashing.side_effect = digest
            with self.assertRaisesRegex(ValueError, "Environment recipe changed"):
                prepare_recovery(self.config_path, self.output)

    def test_original_output_directory_is_never_reused(self):
        with self.assertRaisesRegex(ValueError, "output directory must be new"):
            prepare_recovery(self.config_path, self.output, output_directory=self.outdir)


if __name__ == "__main__":
    unittest.main()
