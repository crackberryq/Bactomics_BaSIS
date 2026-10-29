"""Presentation scope for saved reports; never schedules analysis work."""
from __future__ import annotations

from pathlib import Path


# Flat exports belong to the standalone renderer. The workflow renderer moves
# some into metadata/tables and adds its own navigation and completion exports.
_FLAT_OUTPUTS = ("report.html", "evidence.json", "findings.tsv", "screening-findings.tsv", "recommendations.tsv",
                 "module-scope.tsv", "deferred-candidates.tsv", "methods.txt",
                 "run-summary.json", "run-summary.tsv", "report-navigation.json",
                 "CITATION.cff", "CITATION.txt", "BaSIS.bib", "BaSIS.ris")
_NESTED_OUTPUTS = ("metadata", "tables", "report-data", "metadata/citations",
                   "metadata/run-summary.json", "metadata/methods.txt",
                   "metadata/report-navigation.json", "metadata/files.html",
                   "metadata/files.json", "metadata/report-artifacts.json",
                   "metadata/deep-workflow.json", "metadata/deep-workflow.tsv")


def validate_report_output_paths(outdir: Path) -> None:
    """Reject aliases for renderer-owned destinations before writing anything.

    Saved analysis and input metadata may contain symlinks: this checks only
    destinations the report owns, without reading or modifying their targets.
    """
    root = Path(outdir)
    destinations = list(_FLAT_OUTPUTS) + list(_NESTED_OUTPUTS)
    destinations.extend("tables/" + name for name in _FLAT_OUTPUTS if name.endswith(".tsv"))
    destinations.extend("metadata/citations/" + name for name in
                        ("CITATION.cff", "CITATION.txt", "BaSIS.bib", "BaSIS.ris"))
    for relative in destinations:
        path = root
        for part in Path(relative).parts:
            path = path / part
            if path.is_symlink():
                raise ValueError(f"Report output path must not be a symlink: {path}")
    # report-data contains dynamically named renderer exports only. Inspect it
    # without following aliases, so a pre-existing export cannot redirect a write.
    pending = [root / "report-data"]
    while pending:
        directory = pending.pop()
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_symlink():
                raise ValueError(f"Report output path must not be a symlink: {path}")
            if path.is_dir():
                pending.append(path)


def deep_requested(run, deep_records=()):
    """Honor explicit report scope, retaining record-based legacy behavior.

    Contradictory inputs are rejected instead of silently hiding supplied
    evidence or presenting historical follow-up as part of a disabled run.
    """
    view = run.get("report_view", "combined")
    if view not in {"combined", "screening", "deep"}:
        raise ValueError("Report view must be combined, screening or deep")
    options = run.get("options")
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError("Report options must be a mapping")
    if "deep" not in options:
        requested = bool(deep_records)
    else:
        requested = options["deep"]
    if not isinstance(requested, bool):
        raise ValueError("Report options.deep must be boolean")
    if view == "screening" and (requested or deep_records):
        raise ValueError("Screening report cannot contain DeepSafe evidence")
    if view == "deep" and "deep" in options and not requested:
        raise ValueError("DeepSafe report conflicts with explicitly disabled report scope")
    if not requested and deep_records:
        raise ValueError("DeepSafe evidence conflicts with explicitly disabled report scope")
    return requested
