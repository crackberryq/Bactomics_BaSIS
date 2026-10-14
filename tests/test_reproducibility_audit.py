import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from basis.reproducibility_audit import audit_environment, audit_reproducibility
from basis.util import sha256_file


class ReproducibilityAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def package(self, prefix, name="demo", **fields):
        meta = prefix / "conda-meta"
        meta.mkdir(parents=True, exist_ok=True)
        record = {"name": name, "version": "1.2.3", "build": "py311_0", "build_number": 0,
                  "subdir": "linux-64", "fn": f"{name}-1.2.3-py311_0.conda", "sha256": "a" * 64,
                  "url": f"https://conda.anaconda.org/conda-forge/linux-64/{name}-1.2.3-py311_0.conda"}
        record.update(fields)
        path = meta / f"{name}-1.2.3-py311_0.json"
        path.write_text(json.dumps(record))
        return path

    def config(self, **module_fields):
        for name, content in [("assembly.fna", ">x\nAAAA\n"), ("proteins.faa", ">p\nMAAA\n"), ("annotation.gff", "##gff-version 3\n")]:
            (self.root / name).write_text(content)
        sheet = self.root / "samples.tsv"
        sheet.write_text("sample_id\tassembly\tproteins\tgff\nfirst\tassembly.fna\tproteins.faa\tannotation.gff\n")
        config = self.root / "config.yaml"
        config.write_text(yaml.safe_dump({"samples": "samples.tsv", "modules": {"vfdb": module_fields}}))
        return config

    def python_distribution(self, site, name="demo", version="1.0"):
        dist = site / f"{name}-{version}.dist-info"
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        (dist / "INSTALLER").write_text("pip\n")
        return dist

    def venv(self, prefix, config=None):
        prefix.mkdir(parents=True, exist_ok=True)
        path = prefix / "pyvenv.cfg"
        path.write_text(config if config is not None else
                        f"home = {self.root}\ninclude-system-site-packages = false\nversion = 3.11.15\n")
        interpreter = prefix / "bin/python"
        interpreter.parent.mkdir(exist_ok=True)
        interpreter.write_text("inert fixture: must not execute\n")
        return path

    def test_exact_freeze_input_hashes_and_source_preservation_without_execution(self):
        db = self.root / "db.dmnd"
        db.write_bytes(b"database fixture")
        config = self.config(database="db.dmnd", database_version="frozen-v1")
        before = {p: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        with patch("subprocess.run", side_effect=AssertionError("must not execute")), \
                patch("subprocess.Popen", side_effect=AssertionError("must not execute")), \
                patch("socket.create_connection", side_effect=AssertionError("must not connect")):
            result = audit_reproducibility(config_paths=[config], outdir=self.root / "audit")
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(len(result["inputs"]), 3)
        for item in result["sources"]:
            self.assertEqual((self.root / "audit" / item["frozen_path"]).read_bytes(), before[Path(item["path"])] )
        for item in result["inputs"]:
            self.assertEqual(item["sha256"], sha256_file(item["path"]))
        self.assertNotIn("sha256", result["resources"][0])
        self.assertFalse(result["portable_restore_verified"])
        self.assertFalse(result["native_analyses_run"])
        self.assertEqual(result["status"], "completed_with_open_gaps")
        for line in (self.root / "audit/SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, sha256_file(self.root / "audit" / name))

    def test_resource_hashing_is_opt_in_and_missing_resources_remain_gaps(self):
        db = self.root / "db"
        db.mkdir()
        (db / "model.bin").write_bytes(b"model")
        config = self.config(database="db", metadata="missing.json")
        result = audit_reproducibility(config_paths=[config], outdir=self.root / "audit", hash_resources=True)
        rows = {Path(x["path"]).name: x for x in result["resources"]}
        self.assertEqual(rows["db"]["files"][0]["sha256"], hashlib.sha256(b"model").hexdigest())
        self.assertEqual(rows["db"]["status"], "hashed")
        self.assertEqual(rows["missing.json"]["status"], "missing")
        self.assertTrue(any("Resource missing:" in g for g in result["open_gaps"]))
        self.assertFalse(any(p.name == "model.bin" for p in (self.root / "audit").rglob("*")))

    def test_environment_reads_metadata_larger_than_previous_16_mib_limit(self):
        prefix = self.root / "context"
        path = self.package(prefix, name="tensorflow-base", files=["x" * (17 * 1024 * 1024)])
        result = audit_environment(prefix)
        self.assertGreater(path.stat().st_size, 16 * 1024 * 1024)
        self.assertEqual(result["status"], "inventoried")
        self.assertEqual(result["package_count"], 1)
        self.assertEqual(result["packages"][0]["name"], "tensorflow-base")
        self.assertEqual(result["metadata_files"][0]["sha256"], sha256_file(path))
        self.assertNotIn("files", result["packages"][0])

    def test_venv_inventory_is_read_only_and_does_not_require_conda(self):
        prefix = self.root / "venv"
        config = self.venv(prefix)
        site = prefix / "lib/python3.11/site-packages"
        self.python_distribution(site)
        sentinel = self.root / "executed"
        (site / "startup.pth").write_text(f"import pathlib; pathlib.Path({str(sentinel)!r}).touch()\n")
        with patch("subprocess.run", side_effect=AssertionError("must not execute")), \
                patch("subprocess.Popen", side_effect=AssertionError("must not execute")):
            result = audit_environment(prefix)
        self.assertEqual(result["status"], "inventoried")
        self.assertEqual(result["environment_type"], "python_venv")
        self.assertEqual(result["conda_metadata"]["status"], "not_applicable")
        self.assertEqual(result["package_count"], 0)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["pyvenv_cfg"]["sha256"], sha256_file(config))
        self.assertEqual(result["pyvenv_cfg"]["status"], "valid_metadata")
        self.assertFalse(result["pyvenv_cfg"]["fields"]["include_system_site_packages"])
        self.assertFalse(result["pyvenv_cfg"]["interpreter_executed"])
        self.assertEqual(len(result["python"]["distributions"]), 1)
        self.assertFalse(sentinel.exists())

    def test_invalid_venv_configuration_cannot_hide_missing_conda_metadata(self):
        prefix = self.root / "venv"
        cases = [b"", b"home = /tmp\n", b"include-system-site-packages = false\n",
                 b"home = /tmp\ninclude-system-site-packages = maybe\n",
                 b"home = /tmp\ninclude-system-site-packages = false\nhome = /other\n",
                 b"home = /tmp\ninclude-system-site-packages = false\nmalformed\n",
                 b"home = /tmp\x00\ninclude-system-site-packages = false\n",
                 b"home = \xff\ninclude-system-site-packages = false\n"]
        config = self.venv(prefix)
        for raw in cases:
            with self.subTest(raw=raw):
                config.write_bytes(raw)
                result = audit_environment(prefix)
                self.assertEqual(result["environment_type"], "unknown")
                self.assertEqual(result["status"], "incomplete_metadata_inventory")
                self.assertEqual(result["pyvenv_cfg"]["status"], "malformed_metadata")
                self.assertEqual(result["pyvenv_cfg"]["sha256"], hashlib.sha256(raw).hexdigest())

    def test_venv_config_reads_are_bounded_and_read_failures_remain_gaps(self):
        prefix = self.root / "venv"
        config = self.venv(prefix)
        with patch("basis.reproducibility_audit.PYVENV_CONFIG_LIMIT", 16):
            result = audit_environment(prefix)
        self.assertEqual(result["pyvenv_cfg"]["status"], "size_limit_exceeded")
        self.assertNotIn("sha256", result["pyvenv_cfg"])
        self.assertEqual(result["status"], "incomplete_metadata_inventory")
        original_open = Path.open
        def guarded_open(path, *args, **kwargs):
            if path == config:
                raise PermissionError("fixture")
            return original_open(path, *args, **kwargs)
        with patch.object(Path, "open", guarded_open):
            result = audit_environment(prefix)
        self.assertEqual(result["pyvenv_cfg"]["status"], "read_error")
        self.assertEqual(result["environment_type"], "unknown")

    def test_missing_environment_markers_or_interpreter_do_not_pass_as_venv(self):
        prefix = self.root / "venv"
        config = self.venv(prefix)
        interpreter = prefix / "bin/python"
        interpreter.unlink()
        result = audit_environment(prefix)
        self.assertEqual(result["pyvenv_cfg"]["status"], "interpreter_missing")
        self.assertEqual(result["environment_type"], "unknown")
        self.assertEqual(result["status"], "incomplete_metadata_inventory")
        config.unlink()
        result = audit_environment(prefix)
        self.assertEqual(result["pyvenv_cfg"]["status"], "not_present")
        self.assertEqual(result["environment_type"], "unknown")
        self.assertIn({"kind": "conda_meta_missing"}, result["issues"])
        self.assertEqual(audit_environment(self.root / "absent")["status"], "missing_environment")

    def test_venv_config_symlink_preserves_target_identity_and_dangling_marker_fails(self):
        prefix = self.root / "venv"
        config = self.venv(prefix)
        target = self.root / "actual-pyvenv.cfg"
        config.rename(target)
        config.symlink_to(target)
        result = audit_environment(prefix)
        self.assertEqual(result["status"], "inventoried")
        self.assertTrue(result["pyvenv_cfg"]["is_symlink"])
        self.assertEqual(result["pyvenv_cfg"]["resolved_path"], str(target))
        self.assertEqual(result["pyvenv_cfg"]["sha256"], sha256_file(target))
        target.unlink()
        result = audit_environment(prefix)
        self.assertEqual(result["pyvenv_cfg"]["status"], "read_error")
        self.assertEqual(result["status"], "incomplete_metadata_inventory")

    def test_venv_with_conda_markers_is_reported_as_conflicting(self):
        prefix = self.root / "venv"
        self.venv(prefix)
        record = self.package(prefix)
        result = audit_environment(prefix)
        self.assertEqual(result["environment_type"], "unknown")
        self.assertEqual(result["package_count"], 1)
        self.assertIn({"kind": "conflicting_environment_markers"}, result["issues"])
        record.unlink()
        record.parent.rmdir()
        record.parent.write_text("not a metadata directory")
        result = audit_environment(prefix)
        self.assertEqual(result["environment_type"], "unknown")
        self.assertEqual(result["conda_metadata"]["status"], "invalid_marker")
        self.assertEqual(result["status"], "incomplete_metadata_inventory")

    def test_explicit_conda_export_for_venv_never_passes_even_when_empty(self):
        prefix = self.root / "venv"
        self.venv(prefix)
        export = self.root / "explicit.txt"
        for contents, expected in [("@EXPLICIT\n", "mismatch"),
                                   ("@EXPLICIT\nhttps://example.org/demo-1-py311.conda\n", "mismatch"),
                                   ("malformed export\n", "unreadable")]:
            with self.subTest(contents=contents):
                export.write_text(contents)
                result = audit_environment(prefix, export)
                self.assertEqual(result["explicit_export"]["status"], expected)
                self.assertEqual(result["status"], "incomplete_metadata_inventory")
                self.assertIn({"kind": "conda_export_for_non_conda_environment"}, result["issues"])

    def test_venv_audit_report_explains_conda_metadata_not_applicable(self):
        prefix = self.root / "venv"
        self.venv(prefix)
        result = audit_reproducibility(environment_prefixes=[prefix], outdir=self.root / "audit")
        self.assertFalse(any(g.startswith("Environment ") for g in result["open_gaps"]))
        html = (self.root / "audit/report.html").read_text()
        self.assertIn("Environment type", html)
        self.assertIn("python_venv", html)
        self.assertIn("Not applicable (Python venv)", html)

    def test_conda_size_read_and_json_errors_remain_distinct(self):
        prefix = self.root / "env"
        oversized = self.package(prefix, name="oversized", files=["x" * 500])
        malformed = self.package(prefix, name="malformed")
        malformed.write_text("{broken")
        unreadable = self.package(prefix, name="unreadable")
        original_open = Path.open
        def guarded_open(path, *args, **kwargs):
            if path == unreadable:
                raise PermissionError("fixture")
            return original_open(path, *args, **kwargs)
        with patch("basis.reproducibility_audit.CONDA_METADATA_LIMIT", 400), patch.object(Path, "open", guarded_open):
            result = audit_environment(prefix)
        status = {x["file"]: x["status"] for x in result["metadata_files"]}
        self.assertEqual(status[oversized.name], "size_limit_exceeded")
        self.assertEqual(status[malformed.name], "malformed_metadata")
        # Size bounds precede file reads; use a generous limit for the read-error fixture.
        with patch.object(Path, "open", guarded_open):
            result = audit_environment(prefix)
        self.assertIn({"file": unreadable.name, "kind": "read_error"}, result["issues"])

    def test_conda_invalid_optional_identity_fields_are_malformed(self):
        for field, value in [("fn", []), ("url", {}), ("sha256", {}), ("channel", [])]:
            prefix = self.root / field
            self.package(prefix, **{field: value})
            result = audit_environment(prefix)
            self.assertEqual(result["package_count"], 0)
            self.assertEqual(result["metadata_files"][0]["status"], "malformed_metadata")

    def test_explicit_export_comparison_records_missing_and_mismatched_metadata(self):
        prefix = self.root / "env"
        self.package(prefix)
        export = self.root / "explicit.txt"
        export.write_text("@EXPLICIT\nhttps://example.org/demo-1.2.3-py311_0.conda#" + "b" * 64 +
                          "\nhttps://example.org/missing-1-py311.conda#" + "c" * 64 + "\n")
        result = audit_environment(prefix, export)
        compare = result["explicit_export"]
        self.assertEqual(compare["status"], "mismatch")
        self.assertEqual({x["kind"] for x in compare["mismatches"]},
                         {"digest_mismatch_or_unavailable", "missing_metadata_record"})
        self.assertFalse(result["archives_verified"])
        export.write_text("@EXPLICIT\nhttps://example.org/demo-1.2.3-py311_0.conda#" + "a" * 64 + "\n")
        self.assertEqual(audit_environment(prefix, export)["explicit_export"]["status"], "matches_metadata")

    def test_python_metadata_does_not_execute_startup_and_sanitizes_direct_urls(self):
        prefix = self.root / "env"
        self.package(prefix)
        site = prefix / "lib/python3.11/site-packages"
        dist = site / "demo-1.0.dist-info"
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n")
        (dist / "INSTALLER").write_text("pip\n")
        (dist / "direct_url.json").write_text(json.dumps({
            "url": "https://user:secret@example.org/t/private-token/repo.git?token=secret#secret",
            "vcs_info": {"vcs": "git", "commit_id": "1" * 40, "requested_revision": "private-branch"}}))
        sentinel = self.root / "executed"
        (site / "danger.pth").write_text(f"import pathlib; pathlib.Path({str(sentinel)!r}).write_text('bad')\n")
        result = audit_environment(prefix)["python"]
        self.assertFalse(sentinel.exists())
        self.assertEqual(result["pip_origin_count"], 1)
        self.assertTrue(result["pth_files"][0]["contains_startup_code"])
        text = json.dumps(result)
        self.assertNotIn("secret", text)
        self.assertNotIn("private-token", text)
        self.assertNotIn("private-branch", text)
        self.assertIn("1" * 40, text)
        (dist / "direct_url.json").write_text('{"url":"file:///home/private-user/source","dir_info":{"editable":true}}')
        text = json.dumps(audit_environment(prefix)["python"])
        self.assertNotIn("private-user", text)
        self.assertIn("local-or-unrecognized-source", text)

    def test_python_site_aliases_are_inventoried_once_without_executing_pth(self):
        prefix = self.root / "env"
        self.package(prefix)
        site = prefix / "lib/python3.11/site-packages"
        self.python_distribution(site)
        sentinel = self.root / "executed"
        (site / "startup.pth").write_text(f"import pathlib; pathlib.Path({str(sentinel)!r}).touch()\n")
        alias = prefix / "lib/python3.12"
        alias.symlink_to(site.parent, target_is_directory=True)
        result = audit_environment(prefix)["python"]
        self.assertEqual(result["status"], "inventoried")
        self.assertEqual(len(result["distributions"]), 1)
        self.assertEqual(result["pip_origin_count"], 1)
        self.assertEqual(len(result["pth_files"]), 1)
        self.assertFalse(sentinel.exists())
        self.assertEqual([r["status"] for r in result["site_packages"]],
                         ["scanned", "alias_of_scanned_root"])
        self.assertEqual(result["scan_aliases"], [{"kind": "site_packages",
            "path": str(alias / "site-packages"), "alias_of": str(site)}])

    def test_python_dist_and_pth_aliases_deduplicate_by_physical_location(self):
        prefix = self.root / "env"
        self.package(prefix)
        first = prefix / "lib/python3.11/site-packages"
        second = prefix / "lib/python3.12/site-packages"
        dist = self.python_distribution(first)
        second.mkdir(parents=True)
        (second / dist.name).symlink_to(dist, target_is_directory=True)
        pth = first / "startup.pth"
        pth.write_text("# inert fixture\n")
        os.link(pth, second / pth.name)
        result = audit_environment(prefix)["python"]
        self.assertEqual(result["status"], "inventoried")
        self.assertEqual(len(result["site_packages"]), 2)
        self.assertEqual(len(result["distributions"]), 1)
        self.assertEqual(len(result["pth_files"]), 1)
        self.assertEqual({r["kind"] for r in result["scan_aliases"]},
                         {"distribution_metadata", "pth_file"})
        self.assertEqual(result["distributions"][0]["metadata_path"], str(dist))

    def test_python_distinct_installations_and_version_conflicts_are_preserved(self):
        prefix = self.root / "env"
        self.package(prefix)
        first = prefix / "lib/python3.11/site-packages"
        second = prefix / "lib/python3.12/site-packages"
        paths = [self.python_distribution(first), self.python_distribution(second),
                 self.python_distribution(second, version="2.0")]
        for site in (first, second):
            (site / "startup.pth").write_text("# identical bytes, distinct files\n")
        result = audit_environment(prefix)["python"]
        self.assertEqual(result["status"], "inventoried")
        self.assertEqual([(r["name"], r["version"]) for r in result["distributions"]],
                         [("demo", "1.0"), ("demo", "1.0"), ("demo", "2.0")])
        self.assertEqual({r["metadata_path"] for r in result["distributions"]}, {str(p) for p in paths})
        self.assertEqual(result["pip_origin_count"], 3)
        self.assertEqual(len(result["pth_files"]), 2)
        self.assertEqual(result["scan_aliases"], [])

    def test_evidence_freeze_retains_scope_and_declared_environments(self):
        prefix = self.root / "env"
        self.package(prefix)
        evidence = self.root / "evidence.json"
        data = {"schema_version": "basis-report-1", "run": {"completeness": "incomplete"},
                "samples": [{"sample_id": "s", "assembly": "unavailable.fna"}],
                "records": [{"module": "demo", "effective_config": {"command_prefix": ["conda", "run", "-p", str(prefix)]}}],
                "deep_records": [{"candidates": [{"selected": False, "execution": "deferred"}]}]}
        evidence.write_text(json.dumps(data, separators=(",", ":")))
        raw = evidence.read_bytes()
        result = audit_reproducibility(evidence_paths=[evidence], outdir=self.root / "audit")
        self.assertEqual(len(result["environments"]), 1)
        self.assertEqual(evidence.read_bytes(), raw)
        self.assertEqual((self.root / "audit" / result["sources"][0]["frozen_path"]).read_bytes(), raw)
        self.assertEqual(result["inputs"][0]["status"], "missing")

    def test_preexisting_or_dangling_output_rejected_without_mutation(self):
        prefix = self.root / "env"
        self.package(prefix)
        out = self.root / "existing"
        out.mkdir()
        sentinel = out / "keep"
        sentinel.write_text("original")
        with self.assertRaises(FileExistsError):
            audit_reproducibility(environment_prefixes=[prefix], outdir=out)
        self.assertEqual(sentinel.read_text(), "original")
        link = self.root / "dangling"
        target = self.root / "target"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            audit_reproducibility(environment_prefixes=[prefix], outdir=link)
        self.assertTrue(link.is_symlink())
        self.assertFalse(target.exists())

    def test_sources_must_be_valid_before_output_is_created(self):
        out = self.root / "audit"
        with self.assertRaises(FileNotFoundError):
            audit_reproducibility(config_paths=[self.root / "absent"], outdir=out)
        self.assertFalse(out.exists())
        config = self.config()
        config.write_text("- not a mapping\n")
        with self.assertRaisesRegex(ValueError, "mapping"):
            audit_reproducibility(config_paths=[config], outdir=out)
        self.assertFalse(out.exists())

    def test_resolved_export_prefix_collision_and_source_containment_rejected(self):
        prefix = self.root / "env"
        self.package(prefix)
        alias = self.root / "alias"
        alias.symlink_to(prefix, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "same environment"):
            audit_reproducibility(outdir=self.root / "audit", conda_explicit_paths={prefix: "a", alias: "b"})
        with self.assertRaisesRegex(ValueError, "inside an audited environment"):
            audit_reproducibility(environment_prefixes=[prefix], outdir=prefix / "audit")
        config = self.config(database=str(self.root))
        with self.assertRaisesRegex(ValueError, "resource directory"):
            audit_reproducibility(config_paths=[config], outdir=self.root / "audit")

    def test_resource_external_symlinks_are_not_followed_and_remain_open_gaps(self):
        db = self.root / "db"
        db.mkdir()
        external = self.root / "private.bin"
        external.write_text("secret")
        (db / "outside").symlink_to(external)
        config = self.config(database="db")
        result = audit_reproducibility(config_paths=[config], outdir=self.root / "audit", hash_resources=True)
        resource = result["resources"][0]
        self.assertEqual(resource["status"], "incomplete_hash_inventory")
        self.assertEqual(resource["files"], [])
        self.assertEqual(resource["issues"][0]["kind"], "external_symlink_not_hashed")

    def test_resource_content_identity_is_stable_across_directory_locations(self):
        values = []
        for number in (1, 2):
            db = self.root / f"database-{number}"
            db.mkdir()
            (db / "model.bin").write_bytes(b"identical model bytes")
            config = self.config(database=str(db))
            result = audit_reproducibility(config_paths=[config], outdir=self.root / f"audit-{number}", hash_resources=True)
            values.append(result["resources"][0])
        self.assertNotEqual(values[0]["resolved_path"], values[1]["resolved_path"])
        self.assertEqual(values[0]["inventory_sha256"], values[1]["inventory_sha256"])

    def test_source_change_during_freeze_never_finalizes_manifest(self):
        config = self.config()
        from basis import reproducibility_audit as module
        original_copy = module.shutil.copyfile
        def change_after_copy(src, dst):
            result = original_copy(src, dst)
            if src == config:
                config.write_text(config.read_text() + "# external change\n")
            return result
        with patch.object(module.shutil, "copyfile", change_after_copy):
            with self.assertRaisesRegex(RuntimeError, "Source changed"):
                audit_reproducibility(config_paths=[config], outdir=self.root / "audit")
        self.assertFalse((self.root / "audit/manifest.json").exists())

    def test_enabled_import_templates_expand_only_validated_sample_ids(self):
        config = self.config(enabled=True, mode="import", result="saved/{sample_id}.amrfinder.json")
        sheet = self.root / "samples.tsv"
        sheet.write_text(sheet.read_text() + "second\tassembly.fna\tproteins.faa\tannotation.gff\n")
        (self.root / "saved").mkdir()
        for sid in ("first", "second"):
            (self.root / "saved" / f"{sid}.amrfinder.json").write_text('{"saved": true}')
        original = config.read_bytes()
        result = audit_reproducibility(config_paths=[config], outdir=self.root / "audit", hash_resources=True)
        resources = result["resources"]
        self.assertEqual({Path(r["path"]).name for r in resources}, {"first.amrfinder.json", "second.amrfinder.json"})
        self.assertEqual({r["status"] for r in resources}, {"hashed"})
        self.assertFalse(any("Resource missing:" in gap for gap in result["open_gaps"]))
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual((self.root / "audit" / result["sources"][0]["frozen_path"]).read_bytes(), original)
        sheet.write_text("sample_id\tassembly\n../escape\tassembly.fna\n")
        with self.assertRaisesRegex(ValueError, "Invalid or duplicate sample ID"):
            audit_reproducibility(config_paths=[config], outdir=self.root / "invalid")
        self.assertFalse((self.root / "invalid").exists())

    def test_disabled_resources_and_environments_are_inactive_not_missing_gaps(self):
        absent = self.root / "uninstalled-environment"
        config = self.config(enabled=False, database="absent-database", result="saved/{sample_id}.json",
                             command_prefix=["conda", "run", "-p", str(absent)])
        result = audit_reproducibility(config_paths=[config], outdir=self.root / "audit", hash_resources=True)
        self.assertEqual({r["status"] for r in result["resources"]}, {"not_assessed"})
        self.assertEqual(result["environments"], [])
        self.assertTrue(all(not r["declarations"][0]["assessment_requested"] for r in result["resources"]))
        self.assertFalse(any(gap.startswith(("Resource missing:", "Environment missing_")) for gap in result["open_gaps"]))

    def test_unknown_template_is_unresolved_not_a_missing_literal_file(self):
        config = self.config(enabled=True, result="saved/{sample_id}.{unknown}.json")
        result = audit_reproducibility(config_paths=[config], outdir=self.root / "audit")
        self.assertEqual(result["resources"][0]["status"], "unresolved_template")
        self.assertIn("first.{unknown}.json", result["resources"][0]["path"])
        self.assertTrue(any("Resource unresolved_template:" in gap for gap in result["open_gaps"]))
        self.assertFalse(any("Resource missing:" in gap for gap in result["open_gaps"]))

    def test_sample_exclusions_preserve_other_sample_resource_requirements(self):
        config = self.config(enabled=True, mode="import", result="saved/{sample_id}.json")
        data = yaml.safe_load(config.read_text())
        data["sample_module_overrides"] = {"first": {"vfdb": {"enabled": False, "reason": "scope fixture"}}}
        config.write_text(yaml.safe_dump(data))
        sheet = self.root / "samples.tsv"
        sheet.write_text(sheet.read_text() + "second\tassembly.fna\tproteins.faa\tannotation.gff\n")
        result = audit_reproducibility(config_paths=[config], outdir=self.root / "audit")
        status = {Path(r["path"]).name: r["status"] for r in result["resources"]}
        self.assertEqual(status, {"first.json": "not_assessed", "second.json": "missing"})
        missing = [gap for gap in result["open_gaps"] if gap.startswith("Resource missing:")]
        self.assertEqual(len(missing), 1)
        self.assertTrue(missing[0].endswith("second.json"))

    def test_saved_not_requested_decisions_do_not_require_inactive_dependencies(self):
        evidence = self.root / "evidence.json"
        evidence.write_text(json.dumps({"schema_version": "basis-report-1", "run": {}, "samples": [],
            "records": [{"sample_id": "first", "module": "vfdb", "execution_status": "not_assessed",
                         "module_decision": {"requested": False, "action": "disabled"},
                         "effective_config": {"database": "absent"}}], "deep_records": []}))
        result = audit_reproducibility(evidence_paths=[evidence], outdir=self.root / "audit")
        self.assertEqual(result["resources"][0]["status"], "not_assessed")
        self.assertEqual(result["module_scopes"][0]["scope"], "not_requested")
        self.assertFalse(any("Resource missing:" in gap for gap in result["open_gaps"]))

    def test_active_declaration_retains_shared_resource_requirement(self):
        config = self.config(enabled=False, database="shared-absent")
        data = yaml.safe_load(config.read_text())
        data["modules"]["bacmet"] = {"enabled": True, "database": "shared-absent"}
        config.write_text(yaml.safe_dump(data))
        result = audit_reproducibility(config_paths=[config], outdir=self.root / "audit")
        self.assertEqual(len(result["resources"]), 1)
        row = result["resources"][0]
        self.assertEqual(row["status"], "missing")
        self.assertEqual({d["assessment_requested"] for d in row["declarations"]}, {True, False})
        self.assertTrue(any(gap.startswith("Resource missing:") for gap in result["open_gaps"]))


if __name__ == "__main__":
    unittest.main()
