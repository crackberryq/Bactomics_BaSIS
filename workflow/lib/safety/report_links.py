"""Optional report navigation from matching, already-rendered source snapshots."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


def _snapshot(run):
    """Compare saved source bytes, excluding the extra follow-up record."""
    inputs = run["report_inputs"]
    values = [(key, inputs[key]["sha256"]) for key in ("sample", "config")]
    sources = run["evidence_sources"]
    modules = [item["module"] for item in sources if item["module"] != "deepsafe"]
    if len(modules) != len(set(modules)):
        raise ValueError("Duplicate report source")
    values.extend(("evidence:" + item["module"], item["sha256"])
                  for item in sources if item["module"] != "deepsafe")
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
           for _, value in values):
        raise ValueError("Invalid report snapshot hash")
    return sorted(values)


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


def matching_report_link(run: dict, source_root: Path) -> tuple[str, str] | None:
    """Return a link only if the existing counterpart matches at render time.

    Missing, stale, malformed, edited or out-of-root bundles simply have no
    navigation link. No report or analysis output is changed here.
    """
    view = run.get("report_view")
    if view not in {"screening", "deep"}:
        return None
    root = Path(source_root).resolve()
    target = root / "deepsafe-report" if view == "screening" else root
    expected = "deep" if view == "screening" else "screening"
    try:
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            return None
        paths = [target / name for name in
                 ("report.html", "evidence.json", "metadata/report-navigation.json")]
        if any(path.is_symlink() or not path.resolve().is_relative_to(target.resolve())
               for path in paths):
            return None
        page, evidence_bytes, navigation_bytes = [path.read_bytes() for path in paths]
        navigation = json.loads(navigation_bytes)
        if (navigation["report_sha256"] != hashlib.sha256(page).hexdigest()
                or navigation["evidence_sha256"] != hashlib.sha256(evidence_bytes).hexdigest()):
            return None
        counterpart = json.loads(evidence_bytes)["run"]
        if counterpart.get("report_view") != expected or _snapshot(counterpart) != _snapshot(run):
            return None
        if not _sources_current(counterpart, root):
            return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return None
    return (("deepsafe-report/report.html", "DeepSafe follow-up report")
            if view == "screening" else ("../report.html", "Screening report"))
