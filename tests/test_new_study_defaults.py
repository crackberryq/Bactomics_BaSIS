import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from basis.bootstrap import plan_auto_resources
from basis.cli import import_study, initialize
from basis.config import ALL_MODULES, CORE, OPTIONAL, default_config, load_config, new_study_config


class NewStudyDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.env = patch.dict("os.environ", {"XDG_CACHE_HOME": str(self.cache)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def annotation(self, sample="isolate2"):
        folder = self.root / "inputs" / sample / "annotation"
        folder.mkdir(parents=True)
        for suffix, content in {
            "fna": ">contig\nATGAAATAA\n", "faa": ">protein\nMK\n",
            "gff": "##gff-version 3\ncontig\tfixture\tCDS\t1\t9\t.\t+\t0\tID=protein\n"}.items():
            (folder / f"{sample}.{suffix}").write_text(content)
        return folder

    def sheet(self, sample="isolate2"):
        path = self.root / "samples.tsv"
        path.write_text("sample_id\n" + sample + "\n")
        return path

    def assert_clean_full_config(self, config):
        self.assertEqual(config["resources"]["policy"], "auto")
        self.assertEqual(config["resources"]["preset"], "full")
        self.assertEqual(config["resources"]["directory"], str(self.cache / "bactromics-basis"))
        self.assertEqual(config["execution"]["device"], "auto")
        self.assertEqual(config["execution"]["conda_prefix"], str(self.cache / "bactromics-basis/environments"))
        self.assertEqual(set(config["modules"]), set(ALL_MODULES))
        for module in config["modules"].values():
            self.assertTrue(module["enabled"])
            self.assertEqual(module["mode"], "native")
            self.assertEqual(module["command_prefix"], [])
            self.assertFalse(module.get("database"))
            self.assertFalse(module.get("result"))
            self.assertFalse(module.get("embeddings"))
        for key in ("prosite_profiles", "motifs", "auto_phylogeny", "family_placement"):
            self.assertTrue(config["deep"][key])
        self.assertFalse(config["deep"].get("reference_fasta"))
        self.assertFalse(config["deep"].get("tree_review_registry"))
        text = json.dumps(config)
        for forbidden in ("/path/to/databases", "BaSIS_v0.1.0", "study-isolate1", "pilot456", "/home/jimi"):
            self.assertNotIn(forbidden, text)

    def test_new_full_config_uses_shared_cache_without_creating_it(self):
        with patch("basis.config.os.cpu_count", return_value=2):
            config = new_study_config()
        self.assert_clean_full_config(config)
        self.assertEqual(config["execution"]["cores"], 2)
        self.assertFalse(self.cache.exists())

    def test_initialize_isolate_discovers_inputs_and_writes_runnable_resource_policy(self):
        annotation = self.annotation()
        source_before = {path: path.read_bytes() for path in annotation.iterdir()}
        with patch("basis.bootstrap.run_resource") as provider:
            result = initialize(self.root / "study", annotation)
        provider.assert_not_called()
        self.assert_clean_full_config(load_config(result["config"]))
        with Path(result["samples"]).open(newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sample_id"], "isolate2")
        self.assertEqual(Path(rows[0]["assembly"]), annotation / "isolate2.fna")
        self.assertFalse(rows[0]["taxon"])
        self.assertEqual(source_before, {path: path.read_bytes() for path in annotation.iterdir()})
        self.assertFalse(self.cache.exists())

    def test_initialize_preserves_existing_study(self):
        annotation = self.annotation()
        result = initialize(self.root / "study", annotation)
        original = Path(result["config"]).read_bytes()
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            initialize(self.root / "study", annotation)
        self.assertEqual(Path(result["config"]).read_bytes(), original)

    def test_import_without_base_config_has_same_clean_defaults(self):
        self.annotation()
        with patch("basis.bootstrap.run_resource") as provider:
            result = import_study(self.sheet(), self.root / "batch", root=self.root / "inputs", input_mode="external")
        provider.assert_not_called()
        self.assert_clean_full_config(load_config(result["config"]))
        self.assertEqual(result["sample_count"], 1)
        self.assertFalse(self.cache.exists())

    def test_core_preset_keeps_optional_modules_and_advanced_deep_features_off(self):
        config = new_study_config("core", self.root / "explicit-cache")
        self.assertEqual(config["resources"]["directory"], str(self.root / "explicit-cache"))
        self.assertTrue(all(config["modules"][name]["enabled"] for name in CORE))
        self.assertTrue(all(not config["modules"][name]["enabled"] for name in OPTIONAL))
        self.assertFalse(config["deep"]["prosite_profiles"])
        self.assertFalse(config["deep"]["auto_phylogeny"])
        self.assertFalse(config["deep"]["family_placement"])
        _, plan = plan_auto_resources(config, deep=False, extend=False)
        self.assertEqual({entry["id"] for entry in plan}, {
            "amrfinder", "card", "vfdb", "antismash", "genomad", "mobsuite", "integronfinder"})

    def test_full_plan_respects_run_depth_and_explicit_feature_disabling(self):
        config = new_study_config()
        for key in ("motifs", "prosite_profiles", "auto_phylogeny", "family_placement"):
            config["deep"][key] = False
        planned, resources = plan_auto_resources(config, deep=True, extend=True)
        for key in ("motifs", "prosite_profiles", "auto_phylogeny", "family_placement"):
            self.assertFalse(planned["deep"][key])
        _, without_followup = plan_auto_resources(config, deep=False, extend=False)
        self.assertFalse({"deep_catalogue", "prosite", "prott5"} & {entry["id"] for entry in without_followup})
        self.assertIn("deep_catalogue", {entry["id"] for entry in resources})

    def test_loading_legacy_configuration_does_not_activate_bootstrap_or_change_scope(self):
        legacy = {"schema_version": 1, "samples": "old-samples.tsv", "outdir": "old-results",
            "execution": {"cores": 2}, "modules": {
                "amrfinder": {"enabled": True, "database": "/existing/amr", "command_prefix": ["conda", "run", "-p", "/existing/env"]},
                "vfdb": {"enabled": False}},
            "deep": {"motifs": False, "auto_phylogeny": False, "family_placement": False}}
        path = self.root / "manual.yaml"
        path.write_text(yaml.safe_dump(legacy))
        before = path.read_bytes()
        config = load_config(path)
        planned, resources = plan_auto_resources(config, deep=True, extend=True)
        self.assertEqual(resources, [])
        self.assertEqual(config, planned)
        self.assertNotIn("device", config["execution"])
        self.assertFalse(config["modules"]["vfdb"]["enabled"])
        self.assertEqual(config["modules"]["amrfinder"]["command_prefix"], legacy["modules"]["amrfinder"]["command_prefix"])
        self.assertEqual(path.read_bytes(), before)

    def test_batch_base_configuration_remains_manual_with_external_paths(self):
        self.annotation()
        base = default_config()
        base["resources"] = {"policy": "manual"}
        base["modules"]["amrfinder"].update(database="/external/amrfinder", command_prefix=["conda", "run", "-p", "/external/core-amr"])
        base["deep"].update(prosite_profiles=False, auto_phylogeny=False, family_placement=False)
        path = self.root / "base.yaml"
        path.write_text(yaml.safe_dump(base))
        result = import_study(self.sheet(), self.root / "batch", root=self.root / "inputs", input_mode="external", base_config=path)
        config = load_config(result["config"])
        self.assertEqual(config["resources"]["policy"], "manual")
        self.assertEqual(config["modules"]["amrfinder"]["database"], "/external/amrfinder")
        self.assertFalse(config["deep"]["prosite_profiles"])
        self.assertEqual(plan_auto_resources(config, deep=True, extend=True)[1], [])


if __name__ == "__main__":
    unittest.main()
