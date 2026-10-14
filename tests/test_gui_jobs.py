"""Local control plane contracts, without external biological tools."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen
from urllib.error import HTTPError

import yaml

from basis import jobs
from basis.config import default_config
from basis.gui_artifacts import ReportServer, safe_path


class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshot_resolves_relative_paths_preserves_scientific_settings(self):
        source = self.root / "old"
        source.mkdir()
        cfg = default_config()
        cfg["deep"]["reference_fasta"] = "references.faa"
        cfg["deep"]["maximum_candidates"] = 17
        cfg["modules"]["amrfinder"]["mode"] = "import"
        file = source / "config.yaml"
        file.write_text(yaml.safe_dump(cfg))
        dest = self.root / "job"
        dest.mkdir()
        command, out = jobs._prepare(dest, {"kind": "run", "config": str(file), "cores": 2}, self.root)
        actual = yaml.safe_load((dest / "config.yaml").read_text())
        self.assertEqual(actual["samples"], str(source / "samples.tsv"))
        self.assertEqual(actual["deep"]["reference_fasta"], str(source / "references.faa"))
        self.assertEqual(actual["deep"]["maximum_candidates"], 17)
        self.assertEqual(actual["modules"], cfg["modules"])
        self.assertEqual(actual["outdir"], str(dest / "results"))
        self.assertEqual(actual["execution"]["cores"], 2)
        self.assertEqual(yaml.safe_load(file.read_text()), cfg)
        self.assertIn("-I", command)
        self.assertEqual(command[command.index("--engine") + 1], "snakemake")

    def test_engine_selection_is_explicit_and_preserved(self):
        for engine in ("local", "snakemake"):
            request = jobs.normalized_request({"kind": "demo", "engine": engine})
            command, _ = jobs._prepare(self.root, request, self.root)
            self.assertEqual(command[command.index("--engine") + 1], engine)
        for request in ({"kind": "demo", "engine": "shell"},
                        {"kind": "panel", "engine": "snakemake", "results": [str(self.root)]},
                        {"kind": "demo", "retry_failed": True}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                jobs.normalized_request(request)

    def test_setup_attempt_has_discoverable_summary_and_explicit_retry(self):
        request = jobs.normalized_request({"kind": "setup", "envs": ["minced"],
                                           "create": True, "retry_failed": True})
        command, outdir = jobs._prepare(self.root, request, self.root)
        self.assertEqual(command[command.index("--engine") + 1], "snakemake")
        self.assertEqual(command[command.index("--report-dir") + 1], outdir)
        self.assertIn("--retry-failed", command)
        self.assertEqual(jobs.outcome("setup", 0, outdir)[0], "failed")
        Path(outdir).mkdir()
        (Path(outdir) / "summary.json").write_text(json.dumps({"status": "completed"}))
        self.assertEqual(jobs.outcome("setup", 0, outdir)[0], "completed")
        self.assertEqual(jobs.outcome("setup", 2, outdir)[0], "needs_attention")
        (Path(outdir) / "summary.json").write_text(json.dumps({"status": "failed"}))
        self.assertEqual(jobs.outcome("setup", 0, outdir)[0], "needs_attention")

    def test_setup_preserves_explicit_environment_parent(self):
        parent = self.root / "earlier-release" / ".basis-envs"
        parent.mkdir(parents=True)
        request = jobs.normalized_request({"kind": "setup", "envs": ["minced"],
                                           "prefix_root": str(parent)})
        command, outdir = jobs._prepare(self.root, request, self.root)
        self.assertEqual(command[command.index("--prefix-root") + 1], str(parent))
        self.assertIsNone(outdir)
        self.assertFalse((self.root / ".basis-envs").exists())
        for invalid in ({"kind": "demo", "prefix_root": str(parent)},
                        {"kind": "setup", "envs": ["minced"], "prefix_root": ""}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                jobs.normalized_request(invalid)

    def test_unknown_operations_and_invalid_options_rejected(self):
        for req in ({"kind": "shell"}, {"kind": "demo", "shell": "hi"},
                    {"kind": "run"}, {"kind": "demo", "cores": True},
                    {"kind": "demo", "gpus": 2}, {"kind": "setup", "envs": ["../bad"]}):
            with self.subTest(req=req), self.assertRaises(ValueError):
                jobs.normalized_request(req)

    def test_duplicate_and_active_job_rejection(self):
        with patch("basis.jobs.subprocess.Popen") as popen:
            popen.return_value.pid = os.getpid()
            first = jobs.submit(self.root, {"kind": "demo"})
            second = jobs.submit(self.root, {"kind": "demo"})
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(popen.call_count, 1)
            with self.assertRaisesRegex(ValueError, "already active"):
                jobs.submit(self.root, {"kind": "setup", "envs": ["minced"]})
            third = jobs.submit(self.root, {"kind": "demo"}, new_attempt=True)
            self.assertEqual(first["id"], third["id"])

    def test_outcome_does_not_treat_all_exit_two_as_failure_or_success(self):
        self.assertEqual(jobs.outcome("run", 0, self.root)[0], "failed")
        (self.root / "evidence.json").write_text(json.dumps({"run": {"completeness": "incomplete"}}))
        self.assertEqual(jobs.outcome("run", 2, self.root)[0], "incomplete")
        self.assertEqual(jobs.outcome("run", 1, self.root)[0], "failed")
        self.assertEqual(jobs.outcome("run", 0, self.root)[0], "failed")
        self.assertEqual(jobs.outcome("doctor", 2, self.root)[0], "needs_attention")

    def test_worker_exit_detected_after_reconnect(self):
        with patch("basis.jobs.subprocess.Popen") as popen:
            popen.return_value.pid = 999999999
            state = jobs.submit(self.root, {"kind": "demo"})
        state["created_at"] = "2020-01-01T00:00:00+00:00"
        jobs.atomic_json(jobs.job_path(self.root, state["id"]) / "status.json", state)
        # A missing identity must not compare equal to another missing identity.
        actual = jobs.read_job(self.root, state["id"])
        self.assertEqual(actual["state"], "interrupted")

    def test_startup_without_proc_identity_gets_grace(self):
        with patch("basis.jobs.subprocess.Popen") as popen, patch("basis.jobs._identity", return_value=None):
            popen.return_value.pid = os.getpid()
            state = jobs.submit(self.root, {"kind": "demo"})
            self.assertEqual(jobs.read_job(self.root, state["id"])["state"], "queued")

    def test_orphaned_queue_without_pid_does_not_block_forever(self):
        path = jobs.job_path(self.root, "a"*24)
        path.mkdir(parents=True)
        (path / "status.json").write_text(json.dumps({"id": "a"*24, "state": "queued", "created_at": "2020-01-01T00:00:00+00:00"}))
        self.assertEqual(jobs.read_job(self.root, "a"*24)["state"], "interrupted")

    def test_relative_tool_prefix_rejected_before_execution(self):
        cfg = default_config()
        cfg["modules"]["amrfinder"]["command_prefix"] = ["conda", "run", "-p", "env"]
        file = self.root / "config.yaml"
        file.write_text(yaml.safe_dump(cfg))
        with self.assertRaisesRegex(ValueError, "absolute"):
            jobs._prepare(self.root, {"kind": "run", "config": str(file)}, self.root)

    def test_related_results_require_explicit_mount(self):
        from urllib.parse import urljoin
        panel, result = self.root / "panel", self.root / "results"
        panel.mkdir()
        result.mkdir()
        (panel / "index.html").write_text('<a href="../results/report.html">Report</a>')
        (result / "report.html").write_text("RESULT")
        for roots, allowed in (((), False), ((result,), True)):
            server = ReportServer(panel, roots)
            try:
                url = urljoin(server.url("index.html"), "../results/report.html")
                if allowed:
                    with urlopen(url, timeout=3) as response:
                        self.assertEqual(response.read(), b"RESULT")
                else:
                    with self.assertRaises(HTTPError):
                        urlopen(url, timeout=3)
            finally:
                server.close()

    def test_bounded_tail(self):
        file = self.root / "log"
        file.write_bytes(b"x" * 10000 + b"END")
        self.assertEqual(jobs.tail(file, 6), "xxxEND")

    def test_artifact_traversal_and_symlink_escape(self):
        root = self.root / "report"
        root.mkdir()
        (root / "index.html").write_text("test")
        (self.root / "outside.txt").write_text("secret")
        (root / "link").symlink_to(self.root / "outside.txt")
        for path in ("../outside.txt", "link", "/etc/passwd", "..\\outside.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_path(root, path)
        with self.assertRaises(ValueError):
            safe_path(root, ".")
        self.assertEqual(safe_path(root, "index.html"), root / "index.html")

    def test_scoped_report_server_preserves_relative_files(self):
        root = self.root / "report"
        root.mkdir()
        (root / "index.html").write_text('<a href="data.json">Data</a>')
        (root / "data.json").write_text('{}')
        server = ReportServer(root)
        try:
            with urlopen(server.url("index.html"), timeout=3) as response:
                self.assertIn(b"data.json", response.read())
            with urlopen(server.url("data.json"), timeout=3) as response:
                self.assertEqual(response.read(), b"{}")
            for path in ("/wrong/index.html", "/"+server.token+"/%2e%2e/outside.txt"):
                with self.assertRaises(HTTPError):
                    urlopen(f"http://127.0.0.1:{server.server.server_port}"+path, timeout=3)
        finally:
            server.close()


if __name__ == "__main__":
    unittest.main()
