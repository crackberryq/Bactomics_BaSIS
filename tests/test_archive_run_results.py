"""Result archival touches only owned folders, after complete read-only checks."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("archive_run_results", ROOT / "scripts/archive_run_results.py")
archive = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(archive)

from basis.config import ALL_MODULES


class ArchiveResultsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="basis-archive-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.samples = [self.root / name for name in ("sampleA", "sampleB")]
        for folder in self.samples:
            annotation = folder / "annotation"
            annotation.mkdir(parents=True)
            (annotation / (folder.name + ".fna")).write_text(">synthetic\nATGAAATTTTAA\n")
            (annotation / (folder.name + ".faa")).write_text(">synthetic\nMKF\n")
            (annotation / (folder.name + ".gff")).write_text("##gff-version 3\n")
            result = folder / "safety/native"
            result.mkdir(parents=True)
            (result / "output.txt").write_text("completed synthetic result\n")
        self.config = {"isolates": list(map(str, self.samples)), "extend": False,
                       "deep": {"enabled": False},
                       "modules": {name: {"enabled": False} for name in ALL_MODULES},
                       "resources": {"directory": str(self.root / "resources")}}
        self.configuration = self.root / "config.yaml"
        self.configuration.write_text(yaml.safe_dump(self.config))
        (self.root / "resources").mkdir()
        (self.root / "resources/database.bin").write_text("database bytes\n")
        self.stopped = patch.object(archive, "active_workflows", return_value=[])
        self.stopped.start()
        self.addCleanup(self.stopped.stop)

    def snapshot(self):
        return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.root.rglob("*") if path.is_file()}

    def test_default_preview_is_read_only_and_overlays_select_samples(self):
        overlay = self.root / "one.yaml"
        overlay.write_text(yaml.safe_dump({"isolates": [str(self.samples[1])]}))
        before = self.snapshot()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(archive.main(["--workflow-root", str(self.root), "--configfile", str(overlay), "--json"]), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan["status"], "preview")
        self.assertEqual([row["sample_id"] for row in plan["results"]], ["sampleB"])
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.root / "archive-reviews").exists())

    def test_apply_renames_without_copying_or_changing_inputs_and_resources(self):
        before = self.snapshot()
        inodes = [(folder / "safety/native/output.txt").stat().st_ino for folder in self.samples]
        plan = archive.build_plan(self.root, stamp="test")
        archive.apply_plan(plan)
        self.assertEqual(plan["status"], "archived")
        for folder, inode in zip(self.samples, inodes):
            self.assertFalse((folder / "safety").exists())
            result = folder / "safety.before-test/native/output.txt"
            self.assertEqual(result.stat().st_ino, inode, "Archive must rename rather than copy output files")
            self.assertEqual(result.read_text(), "completed synthetic result\n")
        after = self.snapshot()
        for path in before:
            if "/safety/" not in path:
                self.assertEqual(after[path], before[path])
        self.assertEqual(json.loads(Path(plan["manifest"]).read_text())["status"], "archived")
        fresh = archive.build_plan(self.root, stamp="again")
        archive.apply_plan(fresh)
        self.assertEqual(fresh["status"], "nothing_to_archive")
        self.assertFalse(Path(fresh["manifest"]).exists())

    def test_all_destinations_are_checked_before_any_folder_moves(self):
        (self.samples[1] / "safety.before-test").mkdir()
        with self.assertRaises(FileExistsError):
            archive.build_plan(self.root, stamp="test")
        self.assertTrue(all((sample / "safety").is_dir() for sample in self.samples))

    def test_unreadable_folder_prevents_all_moves(self):
        plan = archive.build_plan(self.root, stamp="unreadable")
        scandir = os.scandir
        def deny_second(path):
            if Path(path) == self.samples[1] / "safety":
                raise PermissionError("synthetic unreadable result")
            return scandir(path)
        with patch.object(os, "scandir", deny_second):
            with self.assertRaisesRegex(PermissionError, "synthetic unreadable"):
                archive.apply_plan(plan)
        self.assertTrue(all((sample / "safety").is_dir() for sample in self.samples))
        self.assertFalse(Path(plan["manifest"]).exists())

    def test_cli_apply_scans_each_tree_only_once(self):
        with patch.object(os, "walk", wraps=os.walk) as walk, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(archive.main(["--workflow-root", str(self.root), "--apply", "--json"]), 0)
        self.assertEqual(walk.call_count, len(self.samples))

    def test_active_process_and_process_read_failure_prevent_mutation(self):
        plan = archive.build_plan(self.root, stamp="test")
        before = self.snapshot()
        with patch.object(archive, "active_workflows", return_value=[{"pid": 123, "program": "snakemake"}]):
            with self.assertRaisesRegex(ValueError, "123"):
                archive.apply_plan(plan)
        with patch.object(archive, "active_workflows", side_effect=RuntimeError("Cannot inspect process")):
            with self.assertRaisesRegex(RuntimeError, "Cannot inspect"):
                archive.apply_plan(plan)
        self.assertEqual(before, self.snapshot())

    def test_writer_started_during_contents_scan_prevents_every_move(self):
        plan = archive.build_plan(self.root, stamp="new-writer", scan_contents=False)
        original = archive._preflight
        scanned = False
        def inspect(*args, **kwargs):
            nonlocal scanned
            original(*args, **kwargs)
            if kwargs.get("scan_contents", True):
                scanned = True
        with patch.object(archive, "_preflight", side_effect=inspect), \
             patch.object(archive, "active_workflows", side_effect=lambda: [{"pid": 456}] if scanned else []):
            with self.assertRaisesRegex(ValueError, "456"):
                archive.apply_plan(plan)
        self.assertTrue(all((sample / "safety").is_dir() for sample in self.samples))
        self.assertFalse(Path(plan["manifest"]).exists())

    def test_symlink_root_special_file_and_broken_internal_links_are_rejected(self):
        source = self.samples[0] / "safety"
        target = self.samples[0] / "saved"
        source.rename(target)
        source.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink result root"):
            archive.build_plan(self.root)
        source.unlink()
        target.rename(source)
        fifo = source / "named-pipe"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "special file"):
            archive.build_plan(self.root)
        fifo.unlink()
        link = source / "alias"
        link.symlink_to(source / "native", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "internal absolute symlink"):
            archive.build_plan(self.root)

    def test_external_and_relative_internal_links_keep_their_targets(self):
        source = self.samples[0] / "safety"
        (source / "external-models").symlink_to(self.root / "resources", target_is_directory=True)
        (source / "relative-output").symlink_to("native/output.txt")
        plan = archive.build_plan(self.root, stamp="links")
        archive.apply_plan(plan)
        moved = self.samples[0] / "safety.before-links"
        self.assertEqual((moved / "external-models").resolve(), self.root / "resources")
        self.assertEqual((moved / "relative-output").read_text(), "completed synthetic result\n")

    def test_shared_resource_or_input_inside_results_blocks_every_move(self):
        original = self.config["resources"]
        self.config["resources"] = {"directory": str(self.samples[1] / "safety/resources")}
        self.configuration.write_text(yaml.safe_dump(self.config))
        with self.assertRaisesRegex(ValueError, "required input, resource or environment"):
            archive.build_plan(self.root)
        self.config["resources"] = original
        self.config["samples"] = str(self.root / "samples.tsv")
        (self.root / "samples.tsv").write_text("sample_id\tisolate_dir\tassembly\tinput_mode\n" +
            "sampleA\t" + str(self.samples[0]) + "\t" + str(self.samples[0] / "safety/input.fna") + "\texternal\n")
        (self.samples[0] / "safety/input.fna").write_text(">synthetic\nATG\n")
        self.configuration.write_text(yaml.safe_dump(self.config))
        with self.assertRaisesRegex(ValueError, "required input, resource or environment"):
            archive.build_plan(self.root)
        self.assertTrue(all((sample / "safety").is_dir() for sample in self.samples))

    def test_partial_filesystem_failure_is_journalled_without_deleting_results(self):
        plan = archive.build_plan(self.root, stamp="partial")
        rename = Path.rename
        def fail_second(source, destination):
            if source == self.samples[1] / "safety":
                raise OSError("synthetic rename failure")
            return rename(source, destination)
        with patch.object(Path, "rename", fail_second), contextlib.redirect_stderr(io.StringIO()) as errors:
            with self.assertRaisesRegex(OSError, "synthetic rename"):
                archive.apply_plan(plan)
        saved = json.loads(Path(plan["manifest"]).read_text())
        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual([row["status"] for row in saved["results"]], ["archived", "ready"])
        self.assertTrue((self.samples[0] / "safety.before-partial/native/output.txt").is_file())
        self.assertTrue((self.samples[1] / "safety/native/output.txt").is_file())
        self.assertIn("no files were deleted", errors.getvalue())

    def test_process_matching_uses_command_tokens_not_incidental_text(self):
        self.assertTrue(archive._workflow_command(["/env/bin/python", "/env/bin/snakemake", "--cores", "2"]))
        self.assertTrue(archive._workflow_command(["python", "-m", "safety.extend", "ccf-contig"]))
        self.assertTrue(archive._workflow_command(["python", "-m", "safety.pf2_classify"]))
        self.assertTrue(archive._workflow_command(["/env/bin/prodigal", "-i", "input.fna"]))
        self.assertTrue(archive._workflow_command(["/env/bin/diamond", "blastp", "--query", "input.faa"]))
        self.assertTrue(archive._workflow_command(["bash", "-c", "source /env/bin/activate; python -m safety.deep stage"]))
        self.assertFalse(archive._workflow_command(["python", "scripts/archive_run_results.py", "--configfile", "study.yaml"]))
        self.assertFalse(archive._workflow_command(["bash", "-c", "python scripts/archive_run_results.py --apply"]))
        self.assertFalse(archive._workflow_command(["python", "test_snakemake_helpers.py"]))


if __name__ == "__main__":
    unittest.main()
