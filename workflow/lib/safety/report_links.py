"""Optional report navigation from matching, already-rendered source snapshots."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from basis.report_review import matching_screening_snapshot


def _sources_current(run, root):
    sources = list(run["report_inputs"].values()) + run["evidence_sources"]
    for source in sources:
        path = Path(source["path"])
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            return False
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != source["sha256"]:
                return False
    return True


def matching_report_bundle(run: dict, source_root: Path):
    """Return the companion link and evidence from one verified snapshot.

    Missing, stale, malformed, edited or out-of-root bundles simply have no
    navigation link. No report or analysis output is changed here.
    """
    view = run.get("report_view")
    if view not in {"screening", "deep"}:
        return None
    root = Path(source_root).resolve()
    target = root / "deepsafe-report" if view == "screening" else root
    try:
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            return None
        paths = [target / name for name in
                 ("report.html", "evidence.json", "metadata/report-navigation.json")]
        if any(path.is_symlink() or not path.resolve().is_relative_to(target.resolve())
               for path in paths):
            return None
        page, evidence, manifest = paths
        navigation = json.loads(manifest.read_bytes())
        evidence_bytes = evidence.read_bytes()
        with page.open("rb") as stream:
            page_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if (navigation["report_sha256"] != page_digest
                or navigation["evidence_sha256"] != hashlib.sha256(evidence_bytes).hexdigest()):
            return None
        bundle = json.loads(evidence_bytes)
        counterpart = bundle["run"]
        pair = (run, counterpart) if view == "screening" else (counterpart, run)
        if not matching_screening_snapshot(*pair):
            return None
        if not _sources_current(counterpart, root):
            return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return None
    link = (("deepsafe-report/report.html", "DeepSafe follow-up report")
            if view == "screening" else ("../report.html", "Screening report"))
    return link, bundle


def matching_report_link(run: dict, source_root: Path) -> tuple[str, str] | None:
    match = matching_report_bundle(run, source_root)
    return match[0] if match else None
