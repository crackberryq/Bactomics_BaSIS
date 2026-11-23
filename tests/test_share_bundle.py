"""Sharing storage tests over synthetic bytes; no native analyses."""
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import posixpath
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]
from safety.share_bundle import build_plan, check_paths, export_bundle, units
from basis.report import write_report


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links, self.ids = [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.links.extend(v for k, v in attrs if k in {"href", "src"} and v)
        if "id" in values:
            self.ids.append(values["id"])


class ShareBundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="basis-share-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source = self.base / "safety"
        self.source.mkdir()
        self.output = self.base / "sample.zip"

    def put(self, relative, content=b"synthetic output\n"):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        return path

    def publish(self, **options):
        plan = build_plan(self.source, self.output, **options)
        with patch("subprocess.Popen", side_effect=AssertionError("Export attempted analysis")):
            result = export_bundle(plan)
        self.assertEqual(result["status"], "exported")
        return plan, result

    def manifest(self):
        with zipfile.ZipFile(self.output) as archive:
            return json.loads(archive.read("manifest.json"))

    def check_links(self):
        with zipfile.ZipFile(self.output) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
            for member in ["index.html", *[r["path"] for r in manifest["derived_views"]]]:
                parser = Links(archive.read(member).decode())
                self.assertEqual(len(parser.ids), len(set(parser.ids)), member)
                for link in parser.links:
                    parsed = urlsplit(link)
                    if not parsed.path or parsed.scheme or parsed.netloc:
                        continue
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(member), unquote(parsed.path)))
                    self.assertIn(target, names, (member, link))
                    if parsed.fragment and target == "index.html":
                        self.assertIn(parsed.fragment, Links(archive.read(target).decode()).ids)

    def test_long_names_invalid_names_and_case_collisions_are_short_and_lossless(self):
        paths = ["results-cache/tool/" + "a" * 64 + "/attempt-" + "b" * 32 + "/data/" + "deep/" * 25 + "description" * 20 + ".tsv",
                 "CON", "x:y?.txt", "trailing. ", "UPPER.tsv", "upper.tsv", "cache/.lock", "build.tmp",
                 "unicode/\U0001f600.txt", "symbol/#percent%.tsv", "unsafe_<script>.txt"]
        original = {}
        for index, name in enumerate(paths):
            path = self.put(name, bytes([index, 0, 255]))
            original[name] = (path.read_bytes(), path.stat().st_mtime_ns)
        plan, result = self.publish(windows_root="C:\\Users\\Researcher\\OneDrive - Lab\\Results\\sample")
        self.assertGreater(result["source_path_issues"], 0)
        with zipfile.ZipFile(self.output) as archive:
            self.assertTrue(all(units(name) <= 64 for name in archive.namelist()))
            self.assertEqual(len(archive.namelist()), len(set(n.casefold() for n in archive.namelist())))
            manifest = json.loads(archive.read("manifest.json"))
            for entry in manifest["entries"]:
                if entry["kind"] != "file": continue
                data, stamp = original[entry["path"]]
                self.assertEqual(archive.read(entry["member"]), data)
                self.assertEqual(manifest["files"][entry["member"]]["sha256"], hashlib.sha256(data).hexdigest())
                path = self.source / entry["path"]
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), (data, stamp))
            self.assertNotIn(b"unsafe_<script>", archive.read("index.html"))
            self.assertFalse(any(n.endswith(".tmp") for n in archive.namelist()))
        self.check_links()

    def test_internal_links_materialize_once_and_preserve_link_records(self):
        target = self.put("results-cache/module/data/file.tsv")
        (self.source / "native").mkdir()
        (self.source / "native/module").symlink_to(target.parent, target_is_directory=True)
        self.put("report.html", '<html><body><a href="native/module/file.tsv">Saved output</a></body></html>')
        plan, result = self.publish()
        self.assertEqual(result["links_materialized"], 1)
        self.assertEqual(result["unique_files"], 2)
        self.assertTrue((self.source / "native/module").is_symlink())
        self.check_links()
        manifest = self.manifest()
        self.assertTrue(any(e["kind"] == "symlink" for e in manifest["entries"]))
        with zipfile.ZipFile(self.output) as archive:
            for info in archive.infolist():
                self.assertFalse((info.external_attr >> 16) & 0o170000 == 0o120000)

    def test_external_links_need_explicit_scope_and_dont_copy_neighbor_files(self):
        external = self.base / "cache"
        external.mkdir()
        (external / "result.tsv").write_text("selected")
        (external / "unrelated.txt").write_text("not selected")
        (self.source / "linked.tsv").symlink_to(external / "result.tsv")
        with self.assertRaisesRegex(ValueError, "explicit --allow-external-root"):
            build_plan(self.source, self.output)
        self.publish(allow_external_roots=[external])
        with zipfile.ZipFile(self.output) as archive:
            raw = [archive.read(n) for n in archive.namelist() if n.startswith("f/")]
        self.assertEqual(raw, [b"selected"])

    def test_cycles_broken_links_and_special_files_fail_without_output(self):
        for kind in ("cycle", "broken", "fifo"):
            with self.subTest(kind=kind):
                path = self.source / kind
                if kind == "cycle": path.symlink_to(self.source, target_is_directory=True)
                elif kind == "broken": path.symlink_to(self.base / "missing")
                else: os.mkfifo(path)
                try:
                    with self.assertRaises((OSError, ValueError, RuntimeError)):
                        build_plan(self.source, self.output)
                finally:
                    path.unlink()
                self.assertFalse(self.output.exists())

    def test_preview_writes_nothing_and_cli_returns_machine_readable_result(self):
        self.put("file.tsv")
        before = sorted(self.base.rglob("*"))
        result = subprocess.run([sys.executable, str(ROOT / "scripts/export_share_bundle.py"),
                                 "--source", str(self.source), "--output", str(self.output)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "preview")
        self.assertEqual(sorted(self.base.rglob("*")), before)

    def test_no_overwrite_nested_output_or_alias_destination(self):
        self.put("file.tsv")
        self.output.write_text("keep")
        with self.assertRaises(FileExistsError): build_plan(self.source, self.output)
        self.assertEqual(self.output.read_text(), "keep")
        with self.assertRaises(ValueError): build_plan(self.source, self.source / "share.zip")
        link = self.base / "alias"
        link.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(ValueError): build_plan(self.source, link / "new.zip")

    def test_destination_created_after_preview_is_not_overwritten(self):
        self.put("file.tsv")
        plan = build_plan(self.source, self.output)
        self.output.write_text("keep")
        with self.assertRaises(FileExistsError): export_bundle(plan)
        self.assertEqual(self.output.read_text(), "keep")

    def test_mutation_after_preview_and_during_copy_fail_without_publication(self):
        path = self.put("file.tsv")
        plan = build_plan(self.source, self.output)
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed"): export_bundle(plan)
        self.assertFalse(self.output.exists())
        plan = build_plan(self.source, self.output)
        original_open = zipfile.ZipFile.open
        def mutate(archive, name, mode="r", *args, **kwargs):
            if mode == "w": path.write_bytes(b"changed again")
            return original_open(archive, name, mode, *args, **kwargs)
        with patch.object(zipfile.ZipFile, "open", mutate):
            with self.assertRaisesRegex(ValueError, "changed"): export_bundle(plan)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.base.glob(".bs-*")), [])

    def test_new_source_file_after_preview_is_detected(self):
        self.put("file.tsv")
        plan = build_plan(self.source, self.output)
        self.put("new.tsv")
        with self.assertRaisesRegex(ValueError, "directory changed"): export_bundle(plan)
        self.assertFalse(self.output.exists())

    def test_collision_is_detected_before_writing(self):
        self.put("one.tsv")
        self.put("two.tsv")
        with patch("safety.share_bundle._short_name", return_value="f/collision.tsv"):
            with self.assertRaisesRegex(ValueError, "collision"): build_plan(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_windows_unicode_and_cloud_prefix_budgets(self):
        self.assertEqual(units("\U0001f600"), 2)
        with self.assertRaisesRegex(ValueError, "Windows path budget"):
            check_paths(["f/" + "a" * 24 + ".json"], windows_root="C:\\" + "x" * 220)
        with self.assertRaisesRegex(ValueError, "400-character"):
            check_paths(["f/" + "a" * 24 + ".json"], cloud_root="/" + "/".join(["x" * 100] * 4))
        with self.assertRaises(ValueError): check_paths(["f/a.json"], windows_root="C:\\CON\\x")
        with self.assertRaises(ValueError): check_paths(["../outside"])
        with self.assertRaises(ValueError): check_paths(["f/a.json"], max_windows_path=1000)
        result = check_paths(["f/a.json"])
        self.assertEqual(result["assumed_windows_root_units"], 180)
        self.assertFalse(result["cloud_limit_checked"])

    def test_static_links_fragments_missing_links_and_native_html_downloads(self):
        self.put("tables/file #1.tsv")
        self.put("native/tool/index.html", '<html><body>Original native HTML</body></html>')
        self.put("metadata/files.html", '<html><body>Original index</body></html>')
        self.put("deepsafe-report/report.html", '<html><body><a href="../report.html#section">Back</a></body></html>')
        original = ('<!doctype html><html><body><h1 id="section">Saved &amp; unchanged</h1>'
                    '<a href="tables/file%20%231.tsv">Table</a><a href="deepsafe-report/report.html">Follow-up</a>'
                    '<a href="native/tool/index.html#region">Native HTML</a><a href="metadata/files.html">Files</a>'
                    '<a href="missing.tsv">Missing</a><a href="https://example.org">External</a>'
                    '<script>const text="<not-a-tag> &amp;";</script></body></html>')
        self.put("report.html", original)
        _, result = self.publish()
        self.assertEqual(result["unavailable_links"], 1)
        self.check_links()
        with zipfile.ZipFile(self.output) as archive:
            derived = archive.read("report.html").decode()
            self.assertIn('const text="<not-a-tag> &amp;";', derived)
            self.assertIn('href="https://example.org"', derived)
            self.assertNotIn('href="missing.tsv"', derived)
            manifest = self.manifest()
            source = next(e for e in manifest["entries"] if e["path"] == "report.html")
            self.assertEqual(archive.read(source["member"]).decode(), original)

    def test_real_renderer_bundle_retains_data_and_all_exported_links(self):
        write_report({"run_id": "synthetic", "version": "test", "report_view": "screening",
                      "options": {"deep": False}, "completeness": "completed_requested_scope"},
                     [{"sample_id": "synthetic", "input_mode": "external"}], [], [], self.source)
        originals = {str(p.relative_to(self.source)): (p.read_bytes(), p.stat().st_mtime_ns)
                     for p in self.source.rglob("*") if p.is_file()}
        self.publish()
        self.check_links()
        self.assertEqual(originals, {str(p.relative_to(self.source)): (p.read_bytes(), p.stat().st_mtime_ns)
                                    for p in self.source.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()
