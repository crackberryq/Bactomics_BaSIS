import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from basis.bootstrap_deep import (PROSITE_BASE, PROTT5_REPOSITORY, PROTT5_REVISION,
                                 prepare_deep_resource)
from basis.references import load_catalog
from basis.prosite_profiles import run_profiles
from basis.references import sequence_hash
from basis.util import sha256_file


def prosite_fixture():
    return {
        "ps_reldt.txt": "Release 2026_03\n",
        "prosite.dat": "CC   Release 2026_03\n//\nID   TEST; PATTERN.\nAC   PS99999;\nDE   Test pattern.\nPA   A-C-D.\nDO   PDOC99999;\n//\nID   PROFILE; MATRIX.\nAC   PS99998;\nDE   Test profile.\nMA   /GENERAL_SPEC: LENGTH=3;\nDO   PDOC99998;\nPR   PRU99999;\n//\n",
        "prosite.aux": "CC   Release 2026_03\n//\nAC   PS99999;\nCC   /TAXO-RANGE=AB???;\n//\n",
        "prosite.doc": "Release 2026_03\n{PDOC99999}\nPattern\n{PDOC99998}\nProfile\n",
        "ps_scan.pl": "# official scanner fixture\n",
        "prorule.dat": "CC   Release 2026_03\n//\nAC   PRU99999;\nTR   PROSITE; PS99998;\n//\n",
    }


class DownloadRunner:
    def __init__(self, fixtures=None, changing_release=False):
        self.fixtures = fixtures or prosite_fixture()
        self.changing_release = changing_release
        self.calls = []

    def run(self, argv, cwd=None):
        self.calls.append(argv)
        url, target = argv[3], Path(argv[4])
        assert url.startswith(PROSITE_BASE)
        data = self.fixtures[url.rsplit("/", 1)[-1]]
        if self.changing_release and target.name == "release-after.txt":
            data = "Release 2026_04\n"
        target.write_text(data)
        return subprocess.CompletedProcess(argv, 0, "", "")


class PF2Runner:
    """Execute actual provider Python with mocked remote/package boundaries."""
    def __init__(self, root):
        self.root = Path(root)
        self.package = self.root / "installed-pf2"
        weights = self.package / "data/models_weights"
        weights.mkdir(parents=True)
        for name in ["model.pt"] + [f"weights_model{n}.pt" for n in range(1, 5)]:
            (weights / name).write_bytes(b"classifier-fixture-" + name.encode())
        self.downloads = []
        self.commit = "3837caf6710da5da3038f5d1246dd82ab26e003a"
        self.failures = 0
        self.failure = OSError("network disconnected")
        self.partial_resumed = False

    def snapshot(self, **kwargs):
        assert os.environ['HF_HUB_DISABLE_XET'] == '1'
        self.downloads.append(kwargs)
        model = Path(kwargs["local_dir"])
        model.mkdir(parents=True, exist_ok=True)
        partial = model / '.partial-test'
        if self.failures:
            self.failures -= 1
            partial.write_bytes(b'resume-me')
            raise self.failure
        self.partial_resumed = partial.is_file()
        for name in kwargs["allow_patterns"]:
            (model / name).write_bytes(b"model-fixture")
        (model / "config.json").write_text(json.dumps({"model_type": "t5", "d_model": 1024}))

    def run(self, argv, cwd=None):
        module = types.ModuleType("huggingface_hub")
        module.snapshot_download = self.snapshot
        distribution = types.SimpleNamespace(version="0.7.1", read_text=lambda _: json.dumps({
            "url": "https://github.com/genomicepidemiology/PathogenFinder2.git",
            "vcs_info": {"commit_id": self.commit}}))
        package = types.SimpleNamespace(submodule_search_locations=[str(self.package)])
        with patch.dict(sys.modules, {"huggingface_hub": module}), \
                patch.dict(os.environ, {}), \
                patch("time.sleep"), \
                patch("importlib.metadata.distribution", return_value=distribution), \
                patch("importlib.util.find_spec", return_value=package), \
                patch.object(sys, "argv", ["-c", *argv[3:]]):
            exec(compile(argv[2], "provider-prott5", "exec"), {"__name__": "__main__"})
        return subprocess.CompletedProcess(argv, 0, "", "")


class BootstrapDeepTests(unittest.TestCase):
    def test_prosite_complete_release_matched_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "snapshot"
            runner = DownloadRunner()
            result = prepare_deep_resource({"provider": "prosite"}, destination, runner)
            self.assertEqual(result["provenance"]["release"], "2026_03")
            self.assertEqual(result["provenance"]["profile_count"], 1)
            self.assertEqual(result["provenance"]["prorule_count"], 1)
            self.assertTrue(result["deep_updates"]["prosite_profiles"])
            self.assertEqual(len(runner.calls), 7)
            manifest = json.loads((destination / "provenance.json").read_text())
            self.assertEqual(manifest["sha256"]["prorule.dat"], sha256_file(destination / "prorule.dat"))
            self.assertFalse(result["provenance"]["analyses_run"])

    def test_prosite_mixed_release_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            data = prosite_fixture()
            data["prorule.dat"] = data["prorule.dat"].replace("2026_03", "2026_02")
            with self.assertRaisesRegex(ValueError, "ProRule release mismatch"):
                prepare_deep_resource({"provider": "prosite"}, Path(temp) / "bad", DownloadRunner(data))

    def test_prosite_release_change_during_transfer_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "changed release"):
                prepare_deep_resource({"provider": "prosite"}, Path(temp) / "bad",
                                      DownloadRunner(changing_release=True))

    def test_prosite_missing_referenced_rule_is_audited_without_losing_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            data = prosite_fixture()
            data["prorule.dat"] = data["prorule.dat"].replace("PRU99999", "PRU99998")
            destination = Path(temp) / "partial"
            result = prepare_deep_resource({"provider": "prosite"}, destination, DownloadRunner(data))
            coverage = result['provenance']['rule_coverage']
            self.assertEqual(coverage['status'], 'partial')
            self.assertEqual(coverage['unavailable_rule_ids'], ['PRU99999'])
            self.assertEqual(coverage['affected_profiles'], {'PS99998': ['PRU99999']})
            self.assertEqual(result['provenance']['profile_count'], 1)
            self.assertEqual(json.loads((destination / 'rule-coverage.json').read_text()), coverage)
            self.assertEqual((destination / 'prorule.dat').read_text(), data['prorule.dat'])

    def test_prosite_empty_prorule_still_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            data = prosite_fixture()
            data['prorule.dat'] = 'CC   Release 2026_03\n//\n'
            with self.assertRaisesRegex(ValueError, 'snapshot is empty'):
                prepare_deep_resource({'provider': 'prosite'}, Path(temp) / 'bad', DownloadRunner(data))

    def test_profile_scan_reports_applicable_absent_rule_as_unassessed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = prosite_fixture()
            data['prorule.dat'] = data['prorule.dat'].replace('PRU99999', 'PRU99998')
            prepare_deep_resource({'provider': 'prosite'}, root / 'snapshot', DownloadRunner(data))
            query = root / 'query.faa'
            query.write_text('>Q\nACD\n')
            candidates = [{'selected': True, 'execution': 'completed', 'query': {
                'id': 'Q', 'path': str(query), 'sequence_sha256': sequence_hash('ACD')}}]
            class ProfileRunner:
                def run(self, argv, stdout_path, cwd):
                    stdout_path.write_text('>Q/1-3 motif=PS99998|PROFILE norm_score=10 raw_score=100 level=0 motif_start=1 motif_end=-1 motif_rev=F strand=+\nACD\n')
            run_profiles(candidates, {'prosite_profiles': True, 'prosite_snapshot': str(root / 'snapshot')},
                         root / 'run', ProfileRunner())
            result = candidates[0]['analyses']['prosite_profiles']
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['reason'], 'profiles_detected_with_unavailable_rules')
            self.assertEqual(result['hits'][0]['coordinate_mapping_status'], 'completed')
            self.assertEqual(result['hits'][0]['mapping_status'], 'not_assessed')
            self.assertEqual(result['unavailable_rule_ids'], ['PRU99999'])

    def test_prott5_exact_revision_and_classifiers_no_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner = PF2Runner(root)
            destination = root / "snapshot"
            result = prepare_deep_resource({"provider": "prott5"}, destination, runner)
            self.assertEqual(runner.downloads[0]["repo_id"], PROTT5_REPOSITORY)
            self.assertEqual(runner.downloads[0]["revision"], PROTT5_REVISION)
            self.assertEqual(runner.downloads[0]["max_workers"], 1)
            fields = result["module_updates"]["pathogenfinder2"]
            self.assertEqual(fields["prot_t5_identity"], PROTT5_REPOSITORY + "@" + PROTT5_REVISION)
            self.assertEqual(len(list(Path(fields["weights_model"]).glob("*.pt"))), 5)
            self.assertFalse(result["provenance"]["gpu_computation_performed"])
            self.assertFalse(result["provenance"]["model_loaded"])
            self.assertEqual(result['provenance']['download_attempts'], 1)
            self.assertEqual(result['provenance']['download_transport'], 'huggingface_hub_https_xet_disabled')

    def test_prott5_interrupted_download_reuses_persistent_partial_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner = PF2Runner(root)
            runner.failures = 2
            spec = {'provider': 'prott5', 'cache_directory': str(root / 'resources')}
            result = prepare_deep_resource(spec, root / 'staging', runner)
            self.assertEqual(len(runner.downloads), 3)
            self.assertTrue(runner.partial_resumed)
            self.assertEqual(len({r['local_dir'] for r in runner.downloads}), 1)
            self.assertEqual(Path(runner.downloads[0]['local_dir']), root / 'resources/downloads/prott5' / PROTT5_REVISION)
            self.assertTrue((root / 'staging/model/pytorch_model.bin').is_file())
            self.assertFalse((root / 'staging/model/.partial-test').exists())
            self.assertEqual(result['provenance']['download_attempts'], 3)

    def test_prott5_failure_bounded_and_cache_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner = PF2Runner(root)
            runner.failures = 9
            spec = {'provider': 'prott5', 'cache_directory': str(root / 'resources')}
            with self.assertRaisesRegex(RuntimeError, 'failed after 4 attempt'):
                prepare_deep_resource(spec, root / 'failed', runner)
            self.assertEqual(len(runner.downloads), 4)
            self.assertTrue((Path(runner.downloads[-1]['local_dir']) / '.partial-test').is_file())
            self.assertFalse((root / 'failed/model-identity.json').exists())
            runner.failures = 0
            prepare_deep_resource(spec, root / 'new-staging', runner)
            self.assertTrue(runner.partial_resumed)
            self.assertEqual(runner.downloads[-1]['local_dir'], runner.downloads[0]['local_dir'])

    def test_prott5_permanent_failure_is_not_retried_or_exposed_with_signed_url(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner = PF2Runner(root)
            runner.failures = 9
            runner.failure = ValueError('https://example.invalid/file?secret=private')
            with self.assertRaises(RuntimeError) as error:
                prepare_deep_resource({'provider': 'prott5'}, root / 'failed', runner)
            self.assertEqual(len(runner.downloads), 1)
            self.assertNotIn('secret', str(error.exception))

    def test_prott5_external_model_not_redownloaded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner = PF2Runner(root)
            model = root / "existing"
            model.mkdir()
            (model / "config.json").write_text(json.dumps({"model_type": "t5", "d_model": 1024}))
            (model / "spiece.model").write_bytes(b"tokenizer")
            (model / "model.safetensors").write_bytes(b"external-weights")
            spec = {"provider": "prott5", "options": {"external_modules": {
                "pathogenfinder2": {"prot_t5_path": str(model), "prot_t5_identity": "external-pinned-identity"}}}}
            result = prepare_deep_resource(spec, root / "snapshot", runner)
            self.assertEqual(runner.downloads, [])
            self.assertNotIn("prot_t5_path", result["module_updates"]["pathogenfinder2"])
            self.assertNotIn("prot_t5_identity", result["module_updates"]["pathogenfinder2"])
            self.assertIn("weights_model", result["module_updates"]["pathogenfinder2"])
            self.assertEqual(result["provenance"]["external_resources"]["model"]["identity"], "external-pinned-identity")

    def test_prott5_wrong_source_commit_rejected_before_download(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = PF2Runner(Path(temp))
            runner.commit = "a" * 40
            with self.assertRaisesRegex(ValueError, "source commit differs"):
                prepare_deep_resource({"provider": "prott5"}, Path(temp) / "snapshot", runner)
            self.assertFalse(runner.downloads)

    def source_fixture(self, root):
        amr = root / "amrfinder"
        amr.mkdir()
        (amr / "AMRProt.fa").write_text(">WP_1.1 source label\nMACDEFGHIK\n")
        card = root / "card.json"
        card.write_text(json.dumps({"_version": "4.0.2", "1": {
            "ARO_name": "example family", "model_type": "protein variant model",
            "model_sequences": {"sequence": {"1": {"protein_sequence": {
                "sequence": "MAGHIKLMN", "accession": "ABC.1"}},
                "2": {"dna_sequence": {"sequence": "ATG"}}}}}}))
        vfdb = root / "VFDB_setA_pro.utf8.fas"
        vfdb.write_text(">VFG00001 description \u00a0 preserved\nMACDEFG\n")
        pfam = root / "Pfam-A.hmm"
        pfam.write_text("HMMER3/f\nNAME test\n")
        for suffix in (".h3f", ".h3i", ".h3m", ".h3p"):
            Path(str(pfam) + suffix).write_bytes(b"index")
        dependencies = [
            {"provider": "amrfinder", "module_updates": {"amrfinder": {"database": str(amr)}}},
            {"provider": "card", "module_updates": {"rgi": {"database": str(card)}}},
            {"provider": "vfdb", "provenance": {"reference_sources": [
                {"source": "VFDB", "path": str(vfdb), "format": "fasta", "review_status": "reviewed"}]}},
            {"provider": "antismash", "deep_updates": {"domain_database": str(pfam)}}]
        return {"provider": "deep_catalogue", "dependency_resources": dependencies}, pfam

    def test_catalogue_source_labels_unreviewed_and_seed_audit_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec, pfam = self.source_fixture(root)
            result = prepare_deep_resource(spec, root / "snapshot", None)
            cfg = result["deep_updates"]
            catalogue = load_catalog(Path(cfg["reference_fasta"]), Path(cfg["reference_metadata"]))
            self.assertEqual(len(catalogue.sequences), 36)
            imported = [row for key, row in catalogue.metadata.items() if not key.startswith("UP_")]
            self.assertEqual(len(imported), 3)
            for row in imported:
                self.assertEqual(row["review_status"], "unreviewed")
                self.assertEqual(row["comparison_group"], "unassigned")
                self.assertEqual(row["role"], "reference")
            self.assertEqual(catalogue.metadata["UP_Q9FBG4"]["evidence_basis"], "experimental")
            self.assertEqual(catalogue.metadata["UP_O34617"]["evidence_basis"], "homology")
            self.assertIn("not independent expert validation", catalogue.metadata["UP_Q9FBG4"]["reviewer"])
            self.assertEqual(cfg["domain_database"], str(pfam))
            self.assertEqual(result["provenance"]["screening_counts"]["CARD_entries_without_protein"], 1)
            self.assertEqual(result["provenance"]["reviewed_seed_count"], 33)
            self.assertNotIn("family_placement", cfg)
            self.assertNotIn("auto_phylogeny", cfg)
            self.assertTrue(Path(cfg["placement_registry"]).is_file())

    def test_catalogue_missing_pfam_index_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec, pfam = self.source_fixture(root)
            Path(str(pfam) + ".h3p").unlink()
            with self.assertRaises(FileNotFoundError):
                prepare_deep_resource(spec, root / "snapshot", None)

    def test_external_catalogue_preserved_when_only_pfam_and_bundles_needed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec, pfam = self.source_fixture(root)
            initial = prepare_deep_resource(spec, root / "first", None)["deep_updates"]
            before = {key: sha256_file(initial[key]) for key in ("reference_fasta", "reference_metadata")}
            second = {"provider": "deep_catalogue", "options": {
                "preserve_references": True, "reference_fasta": initial["reference_fasta"],
                "reference_metadata": initial["reference_metadata"], "domain_database": str(pfam)}}
            result = prepare_deep_resource(second, root / "second", None)
            for key in before:
                self.assertEqual(result["deep_updates"][key], initial[key])
                self.assertEqual(sha256_file(initial[key]), before[key])
            self.assertTrue(result["provenance"]["external_catalogue_preserved"])
            self.assertEqual(result["provenance"]["reviewed_seed_count"], 0)
            self.assertEqual(result["provenance"]["reference_count"], 36)
            self.assertFalse((root / "second/references.faa").exists())

    def test_catalogue_rejects_invalid_source_sequences(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec, _ = self.source_fixture(root)
            (root / "amrfinder/AMRProt.fa").write_text(">bad\nACD?\n")
            with self.assertRaisesRegex(ValueError, "Invalid screening reference"):
                prepare_deep_resource(spec, root / "snapshot", None)

    def test_existing_staging_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "existing").write_text("preserve")
            with self.assertRaisesRegex(ValueError, "must be empty"):
                prepare_deep_resource({"provider": "prosite"}, root, DownloadRunner())
            self.assertEqual((root / "existing").read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
