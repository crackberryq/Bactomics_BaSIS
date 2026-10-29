"""Publish checked standalone prereleases; never move tags or replace releases.

Called only by the publish job after every acceptance job has succeeded.
This workflow distribution uses GitHub source archives, not a Python wheel.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request

REPO = "crackberryq/Bactomics_BaSIS"
BRANCH = "basis-aware"
BASE = f"repos/{REPO}"


def github(path, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request("https://api.github.com/" + path, data=data, method=method,
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                 "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if method == "GET" and error.code == 404:
            return None
        raise


def metadata(root):
    source = (root / "workflow/lib/safety/__init__.py").read_text()
    matched = re.search(r'^__version__ = "(0\.4\.0[ab][1-9][0-9]*)"$', source, re.M)
    if matched is None:
        raise ValueError("Publication supports explicitly versioned 0.4.0 alpha or beta releases only")
    version = matched[1]
    citation = (root / "CITATION.cff").read_text()
    if not re.search(r"^version: " + re.escape(version) + r"$", citation, re.M):
        raise ValueError("Citation and standalone release versions differ")
    for relative in ("workflow/lib/safety/context.py", "workflow/lib/safety/reporting.py"):
        if '"' + version + '"' not in (root / relative).read_text():
            raise ValueError("Runtime version differs: " + relative)
    provenance = json.loads((root / "docs/VENDORED_SOURCE.json").read_text())
    if provenance["standalone_version"] != version:
        raise ValueError("Standalone source provenance version differs")
    notes = (root / f"docs/RELEASE_{version}.md").read_text().strip()
    if not notes.startswith("# BaSIS AWARE " + version + "\n"):
        raise ValueError("Missing release-specific notes")
    return version, notes


def tag_commit(tag):
    ref = github(f"{BASE}/git/ref/tags/{tag}")
    if ref is None:
        return None
    obj, seen = ref["object"], set()
    while obj["type"] == "tag":
        if obj["sha"] in seen or len(seen) >= 10:
            raise ValueError("Invalid annotated-tag chain")
        seen.add(obj["sha"])
        obj = github(f"{BASE}/git/tags/{obj['sha']}")["object"]
    if obj["type"] != "commit":
        raise ValueError("Release tag does not identify a commit")
    return obj["sha"]


def publish(root, commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("An exact tested commit is required")
    version, notes = metadata(root)
    tag = "v" + version
    release = github(f"{BASE}/releases/tags/{tag}")
    existing_tag = tag_commit(tag)
    if release and not release["draft"]:
        if (release.get("tag_name") != tag or not release.get("prerelease")
                or not existing_tag or release.get("target_commitish") != existing_tag):
            raise ValueError("Published release identity is inconsistent; review it manually")
        print(f"{tag} is already published; bump the version for another release.")
        return release
    if existing_tag is not None and existing_tag != commit:
        raise ValueError("Existing tag points to another commit; it will not be moved")
    if release and (release.get("target_commitish") != commit or release.get("tag_name") != tag):
        raise ValueError("Existing draft belongs to another commit; it will not be replaced")
    if not release:
        channel = "Research beta" if "b" in version else "Alpha"
        notes += (f"\n\nTested source commit: `{commit}`. "
                  f"[Source](https://github.com/{REPO}/tree/{commit}).\n")
        release = github(f"{BASE}/releases", "POST", {
            "tag_name": tag, "target_commitish": commit, "name": f"BaSIS AWARE {version} — {channel}",
            "body": notes, "draft": True, "prerelease": True})
    if existing_tag is None:
        github(f"{BASE}/git/refs", "POST", {"ref": "refs/tags/" + tag, "sha": commit})
    if tag_commit(tag) != commit:
        raise ValueError("Release tag verification failed")
    release = github(f"{BASE}/releases/{release['id']}", "PATCH", {"draft": False, "prerelease": True})
    if (release.get("draft") is not False or release.get("prerelease") is not True
            or release.get("tag_name") != tag or release.get("target_commitish") != commit
            or tag_commit(tag) != commit):
        raise ValueError("Published release identity could not be verified")
    print(f"Published {tag} at {commit}: https://github.com/{REPO}/releases/tag/{tag}")
    return release


def main():
    if (os.environ.get("GITHUB_REPOSITORY") != REPO
            or os.environ.get("GITHUB_REF") != "refs/heads/" + BRANCH
            or os.environ.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}):
        raise SystemExit("Publication is restricted to checked basis-aware branch workflows")
    commit = os.environ["GITHUB_SHA"]
    observed = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if observed != commit:
        raise SystemExit("Checkout differs from the tested workflow commit")
    publish(Path.cwd(), commit)


if __name__ == "__main__":
    main()
