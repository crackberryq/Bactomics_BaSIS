"""Custom family-resource provenance and resume contracts; no native tools."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from safety.deep_resources import (config_input_paths, discover_inputs,
                                   load_discovered_inputs, resolve_profile_resources,
                                   snapshot_resources, verify_resources)


class DeepResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profiles = self.root / "profiles"
        self.profiles.mkdir()
        (self.profiles / "first.yaml").write_text("profile_id: synthetic\n")
        self.registry = self.root / "registry.json"
        self.registry.write_text(json.dumps({"schema_version": 1, "panels": []}))
        self.config = {"profiles_dir": str(self.profiles),
                       "placement_registry": str(self.registry), "maximum_candidates": 5}

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self, name="snapshot"):
        return snapshot_resources(self.config, self.root / name)

    def test_explicit_inputs_include_directory_members_and_missing_resource(self):
        missing = self.root / "missing.json"
        config = {**self.config, "motif_rules": str(missing)}
        self.assertEqual(config_input_paths(config), sorted(map(str, [
            self.profiles, self.profiles / "first.yaml", self.registry, missing])))
        (self.profiles / "second.yaml").write_text("profile_id: other\n")
        self.assertIn(str(self.profiles / "second.yaml"), config_input_paths(config))

    def test_snapshots_preserve_exact_bytes_originals_and_configuration(self):
        original = copy.deepcopy(self.config)
        result = self.snapshot()
        effective = result["effective_config"]
        self.assertEqual(self.config, original)
        self.assertEqual(effective["maximum_candidates"], original["maximum_candidates"])
        self.assertEqual(Path(effective["placement_registry"]).read_bytes(), self.registry.read_bytes())
        self.assertEqual((Path(effective["profiles_dir"]) / "first.yaml").read_bytes(),
                         (self.profiles / "first.yaml").read_bytes())
        self.assertEqual(verify_resources(result["manifest"]), result["manifest"])

    def test_same_path_changes_create_new_content_identity_but_old_snapshot_stays_frozen(self):
        first = self.snapshot("first")
        self.registry.write_text(json.dumps({"schema_version": 1, "panels": [], "note": "changed"}))
        second = self.snapshot("second")
        self.assertNotEqual(first["manifest"]["content_sha256"], second["manifest"]["content_sha256"])
        self.assertNotEqual(first["effective_config"]["family_resource_snapshot"],
                            second["effective_config"]["family_resource_snapshot"])
        verify_resources(first["manifest"])

    def test_snapshot_tampering_is_rejected_even_with_same_size(self):
        result = self.snapshot()
        path = Path(result["effective_config"]["placement_registry"])
        original = path.read_text()
        path.write_text(original.replace("panels", "PANELS"))
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            verify_resources(result["manifest"])

    def test_snapshot_new_file_and_manifest_mutation_are_rejected(self):
        result = self.snapshot()
        (Path(result["effective_config"]["profiles_dir"]) / "unrecorded.yaml").write_text("x\n")
        with self.assertRaisesRegex(ValueError, "members changed"):
            verify_resources(result["manifest"])
        tampered = copy.deepcopy(result["manifest"])
        tampered["content_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            verify_resources(tampered)

    def test_existing_output_and_source_nested_destination_are_not_modified(self):
        destination = self.root / "existing"
        destination.mkdir()
        keep = destination / "keep"
        keep.write_text("preserve")
        with self.assertRaises(FileExistsError):
            snapshot_resources(self.config, destination)
        self.assertEqual(keep.read_text(), "preserve")
        with self.assertRaisesRegex(ValueError, "inside a source"):
            snapshot_resources(self.config, self.profiles / "snapshot")
        self.assertFalse((self.profiles / "snapshot").exists())

    def test_only_consumed_profiles_are_copied_and_file_link_targets_are_explicit(self):
        linked = self.profiles / "linked.yaml"
        linked.symlink_to(self.registry)
        self.assertIn(str(self.registry), config_input_paths(self.config))
        result = self.snapshot()
        self.assertFalse((Path(result["effective_config"]["profiles_dir"]) / "linked.yaml").is_symlink())
        (self.profiles / "large-database.hmm").write_text("not a family profile")
        nested = self.profiles / "other"
        nested.mkdir()
        (nested / "ignored.yaml").write_text("not consumed by validate_profiles")
        self.assertNotIn(str(nested / "ignored.yaml"), config_input_paths(self.config))
        copied = self.snapshot("small")
        self.assertFalse((Path(copied["effective_config"]["profiles_dir"]) / "large-database.hmm").exists())

    def test_manifest_file_removal_or_edit_and_root_symlink_are_rejected(self):
        result = self.snapshot()
        root = Path(result["manifest"]["snapshot_root"])
        manifest_path = root / "manifest.json"
        original = manifest_path.read_bytes()
        manifest_path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "stored manifest"):
            verify_resources(result["manifest"])
        manifest_path.unlink()
        with self.assertRaisesRegex(ValueError, "stored manifest"):
            verify_resources(result["manifest"])
        manifest_path.write_bytes(original)
        relocated = self.root / "relocated"
        root.rename(relocated)
        root.symlink_to(relocated, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "root is a symlink"):
            verify_resources(result["manifest"])

    def test_discovery_binds_generated_configuration_and_enumerates_new_members(self):
        source = self.root / "config.json"
        source.write_text(json.dumps({"deep": self.config}))
        output = self.root / "discovery.json"
        discover_inputs(source, output)
        before = load_discovered_inputs(output)
        added = self.profiles / "added.yaml"
        added.write_text("profile_id: added\n")
        self.assertIn(str(added), config_input_paths(load_discovered_inputs(output)["deep"]))
        source.write_text(json.dumps({"deep": {**self.config, "maximum_candidates": 6}}))
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            load_discovered_inputs(output)
        self.assertEqual(before["deep"]["maximum_candidates"], 5)

    def test_large_resources_remain_under_existing_receipt_contract(self):
        config = {"domain_database": str(self.root / "large.hmm"),
                  "reference_fasta": str(self.root / "large.faa")}
        self.assertEqual(config_input_paths(config), [])
        result = snapshot_resources(config, self.root / "small-snapshot")
        self.assertEqual(result["manifest"]["resources"], [])
        self.assertEqual(result["effective_config"]["domain_database"], config["domain_database"])
        verify_resources(result["manifest"])

    def test_profile_relative_domain_paths_remain_original_and_large_files_are_not_copied(self):
        models = self.root / "models"
        models.mkdir()
        files = [models / ("synthetic.hmm" + suffix)
                 for suffix in ("", ".h3f", ".h3i", ".h3m", ".h3p")]
        for path in files:
            path.write_text("fixture bytes only")
        profile = self.profiles / "first.yaml"
        original = "profile_id: synthetic\nresources:\n  domains:\n    database: ../models/synthetic.hmm\n    native_cutoff: ga\n"
        profile.write_text(original)
        self.assertTrue(set(map(str, files)).issubset(config_input_paths(self.config)))
        result = self.snapshot()
        frozen = Path(result["effective_config"]["profiles_dir"]) / "first.yaml"
        candidate_profile = {"registry_path": str(frozen),
                             "resources": {"domains": {"database": "../models/synthetic.hmm", "native_cutoff": "ga"}}}
        resolved = resolve_profile_resources(candidate_profile, result["manifest"])
        self.assertEqual(resolved["resources"]["domains"], {"database": str(files[0]), "native_cutoff": "ga"})
        self.assertEqual(resolved["original_registry_path"], str(profile))
        self.assertEqual(frozen.read_text(), original)
        self.assertFalse((self.root / "snapshot/models").exists())
        self.assertEqual(len(result["manifest"]["external_files"]), 5)
        verify_resources(result["manifest"])
        old = files[0].stat()
        files[0].write_text("Fixture bytes only")
        os.utime(files[0], ns=(old.st_atime_ns, old.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "database changed"):
            verify_resources(result["manifest"])

    def test_symlink_profile_keeps_resolved_source_directory_for_relative_resources(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        target = elsewhere / "source.yaml"
        target.write_text("profile_id: linked\nresources:\n  domains: sibling.hmm\n")
        database = elsewhere / "sibling.hmm"
        database.write_text("synthetic database placeholder")
        profile = self.profiles / "linked.yaml"
        profile.symlink_to(target)
        self.assertIn(str(database), config_input_paths(self.config))
        result = self.snapshot()
        frozen = Path(result["effective_config"]["profiles_dir"]) / "linked.yaml"
        resolved = resolve_profile_resources({"registry_path": str(frozen),
            "resources": {"domains": "sibling.hmm"}}, result["manifest"])
        self.assertEqual(resolved["resources"]["domains"]["database"], str(database))
        self.assertEqual(resolved["original_registry_path"], str(target))


if __name__ == "__main__":
    unittest.main()
