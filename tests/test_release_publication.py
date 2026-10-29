"""Release publication uses mock GitHub responses; no external writes in tests."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("basis_release", ROOT / "scripts/publish_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
COMMIT = "a" * 40
OTHER = "b" * 40


class GitHub:
    def __init__(self, record=None, tag=None, release_tag="v0.4.0a2"):
        self.record, self.tag, self.calls = record, tag, []
        self.release_tag = release_tag

    def __call__(self, path, method="GET", payload=None):
        self.calls.append((path, method, copy.deepcopy(payload)))
        if path.endswith("/git/ref/tags/" + self.release_tag):
            return {"object": {"type": "commit", "sha": self.tag}} if self.tag else None
        if path.endswith("/releases/tags/" + self.release_tag):
            return copy.deepcopy(self.record)
        if method == "POST" and path.endswith("/releases"):
            self.record = dict(payload, id=23)
            return copy.deepcopy(self.record)
        if method == "POST" and path.endswith("/git/refs"):
            self.tag = payload["sha"]
            return {"object": {"type": "commit", "sha": self.tag}}
        if method == "PATCH" and path.endswith("/releases/23"):
            self.record.update(payload)
            return copy.deepcopy(self.record)
        raise AssertionError((path, method, payload))

    @property
    def writes(self):
        return [row for row in self.calls if row[1] != "GET"]


class ReleaseTests(unittest.TestCase):
    def version_fixture(self, root, version):
        for name in ("workflow/lib/safety/__init__.py", "workflow/lib/safety/context.py",
                     "workflow/lib/safety/reporting.py", "CITATION.cff", "docs/VENDORED_SOURCE.json"):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((ROOT / name).read_text().replace("0.4.0a2", version))
        (root / f"docs/RELEASE_{version}.md").write_text(
            f"# BaSIS AWARE {version}\n\nSynthetic release metadata fixture.\n")

    def test_version_metadata_and_workflow_gate(self):
        version, notes = release.metadata(ROOT)
        self.assertEqual(version, "0.4.0a2")
        self.assertIn("automatically", notes)
        workflow = yaml.load((ROOT / ".github/workflows/snakemake.yml").read_text(), Loader=yaml.BaseLoader)
        publish = workflow["jobs"]["publish"]
        self.assertEqual(set(publish["needs"]), {"workflow", "report-browser", "real-conda-deployment"})
        self.assertIn("refs/heads/basis-aware", publish["if"])
        self.assertEqual(publish["permissions"], {"contents": "write"})
        self.assertEqual(workflow["permissions"], {"contents": "read"})

    def test_create_tag_publish_and_verify_exact_checked_commit(self):
        api = GitHub()
        with patch.object(release, "github", api):
            result = release.publish(ROOT, COMMIT)
        self.assertFalse(result["draft"])
        self.assertTrue(result["prerelease"])
        self.assertEqual(api.tag, COMMIT)
        self.assertEqual(result["target_commitish"], COMMIT)
        self.assertEqual([call[1] for call in api.writes], ["POST", "POST", "PATCH"])
        self.assertIn(COMMIT, result["body"])
        self.assertEqual(result["name"], "BaSIS AWARE 0.4.0a2 — Alpha")

    def test_beta_publication_remains_a_prerelease_at_the_tested_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.version_fixture(root, "0.4.0b1")
            api = GitHub(release_tag="v0.4.0b1")
            with patch.object(release, "github", api):
                result = release.publish(root, COMMIT)
            self.assertFalse(result["draft"])
            self.assertTrue(result["prerelease"])
            self.assertEqual(result["name"], "BaSIS AWARE 0.4.0b1 — Research beta")
            self.assertEqual(result["tag_name"], "v0.4.0b1")
            self.assertEqual(result["target_commitish"], COMMIT)
            self.assertEqual(api.tag, COMMIT)
            self.assertEqual([call[1] for call in api.writes], ["POST", "POST", "PATCH"])

    def test_beta_still_rejects_conflicting_tag_and_published_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.version_fixture(root, "0.4.0b1")
            cases = [(None, OTHER),
                     ({"id": 23, "tag_name": "v0.4.0b1", "target_commitish": OTHER,
                       "draft": False, "prerelease": True}, COMMIT)]
            for record, tag in cases:
                with self.subTest(record=record):
                    api = GitHub(record, tag, release_tag="v0.4.0b1")
                    with patch.object(release, "github", api), self.assertRaises(ValueError):
                        release.publish(root, COMMIT)
                    self.assertEqual(api.writes, [])

    def test_unsupported_release_channels_are_rejected_before_network(self):
        for version in ("0.4.0", "0.4.0rc1", "0.5.0b1", "0.4.0b0", "0.4.0a0"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.version_fixture(root, version)
                with patch.object(release, "github") as network, self.assertRaises(ValueError):
                    release.publish(root, COMMIT)
                network.assert_not_called()

    def test_beta_requires_matching_runtime_metadata_and_notes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative, replacement in (
                ("workflow/lib/safety/context.py", 'version = "0.4.0a2"\n'),
                ("docs/RELEASE_0.4.0b1.md", "# BaSIS AWARE 0.4.0a2\n"),
            ):
                with self.subTest(relative=relative):
                    self.version_fixture(root, "0.4.0b1")
                    (root / relative).write_text(replacement)
                    with patch.object(release, "github") as network, self.assertRaises(ValueError):
                        release.publish(root, COMMIT)
                    network.assert_not_called()

    def test_draft_retry_reuses_release_and_matching_tag(self):
        original = {"id": 23, "tag_name": "v0.4.0a2", "target_commitish": COMMIT,
                    "draft": True, "prerelease": True, "body": "retained notes"}
        api = GitHub(original, COMMIT)
        with patch.object(release, "github", api):
            result = release.publish(ROOT, COMMIT)
        self.assertEqual([call[1] for call in api.writes], ["PATCH"])
        self.assertEqual(result["body"], "retained notes")

    def test_published_release_is_never_replaced_by_later_commit(self):
        original = {"id": 23, "tag_name": "v0.4.0a2", "target_commitish": OTHER,
                    "draft": False, "prerelease": True}
        api = GitHub(original, OTHER)
        with patch.object(release, "github", api):
            self.assertEqual(release.publish(ROOT, COMMIT), original)
        self.assertEqual(api.writes, [])

    def test_conflicting_tag_or_draft_is_not_changed(self):
        cases = [(None, OTHER), ({"id": 23, "tag_name": "v0.4.0a2", "target_commitish": OTHER,
                                 "draft": True, "prerelease": True}, None)]
        for original, tag in cases:
            with self.subTest(tag=tag):
                api = GitHub(original, tag)
                with patch.object(release, "github", api), self.assertRaises(ValueError):
                    release.publish(ROOT, COMMIT)
                self.assertEqual(api.writes, [])

    def test_inconsistent_published_release_is_not_modified(self):
        api = GitHub({"id": 23, "tag_name": "v0.4.0a2", "target_commitish": OTHER,
                      "draft": False, "prerelease": True}, COMMIT)
        with patch.object(release, "github", api), self.assertRaises(ValueError):
            release.publish(ROOT, COMMIT)
        self.assertEqual(api.writes, [])

    def test_invalid_commit_and_nonrelease_workflows_are_rejected_before_network(self):
        with patch.object(release, "github") as network:
            with self.assertRaises(ValueError):
                release.publish(ROOT, "basis-aware")
            with patch.dict("os.environ", {"GITHUB_REPOSITORY": release.REPO,
                           "GITHUB_REF": "refs/heads/basis-aware", "GITHUB_EVENT_NAME": "pull_request"}):
                with self.assertRaises(SystemExit):
                    release.main()
            network.assert_not_called()

    def test_mismatched_runtime_version_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("workflow/lib/safety/__init__.py", "workflow/lib/safety/context.py",
                         "workflow/lib/safety/reporting.py", "CITATION.cff", "docs/VENDORED_SOURCE.json",
                         "docs/RELEASE_0.4.0a2.md"):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / name).read_bytes())
            (root / "workflow/lib/safety/context.py").write_text('version = "0.4.0a1"\n')
            with patch.object(release, "github") as network, self.assertRaises(ValueError):
                release.publish(root, COMMIT)
            network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
