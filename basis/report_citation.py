"""Deterministic software citations for the author and version actually known.

Release dates, repository links, identifiers and publication metadata are not
inferred from a run's timestamp or supplied by the reporting layer.
"""
from __future__ import annotations

from pathlib import Path
import re

import yaml

from . import __version__


TITLE = "BaSIS: Bacterial Adaptive Safety Investigation System"
AUTHOR = "Jimi Goldstein"
ORCID = "https://orcid.org/0000-0002-7829-0090"
CITATION_NOTE = (
    "Also cite the underlying tools and databases used in the analysis; "
    "this citation credits the BaSIS software."
)
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}\Z")


def _version(value: str | None) -> str:
    value = __version__ if value is None else value
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError("Software citation version must be a nonempty, safe version identifier")
    return value


def citation_text(version: str | None = None) -> str:
    """Return the human-readable software citation without inventing a date."""
    version = _version(version)
    return f"Goldstein, J. (n.d.). {TITLE} (Version {version}) [Computer software]."


def write_citations(outdir: str | Path, version: str | None = None) -> dict[str, str]:
    """Write CFF, BibTeX, RIS and plain-text citations and return their paths.

    ``version`` should be the recorded software version for the cited analysis,
    if available. Omitting it explicitly cites the currently installed version.
    Invalid values fail before creating files instead of entering citation syntax.
    """
    version = _version(version)
    cff = {
        "cff-version": "1.2.0",
        "message": "If you use BaSIS, cite this software and the underlying tools and databases used in your run.",
        "type": "software",
        "title": TITLE,
        "authors": [{"family-names": "Goldstein", "given-names": "Jimi", "orcid": ORCID}],
        "version": version,
        "license": "MIT",
    }
    # @misc is portable to classic BibTeX as well as contemporary managers.
    bib = (
        "@misc{goldstein_basis,\n"
        "  author = {Goldstein, Jimi},\n"
        f"  title = {{{{BaSIS}}: Bacterial Adaptive Safety Investigation System}},\n"
        "  howpublished = {Computer software},\n"
        f"  version = {{{version}}},\n"
        f"  note = {{Version {version}. Author ORCID: {ORCID}. {CITATION_NOTE}}}\n"
        "}\n"
    )
    ris = (
        "TY  - COMP\n"
        "AU  - Goldstein, Jimi\n"
        f"TI  - {TITLE}\n"
        f"ET  - {version}\n"
        f"N1  - Version {version}. Author ORCID: {ORCID}. {CITATION_NOTE}\n"
        "ER  - \n"
    )
    txt = citation_text(version) + "\n\nAuthor ORCID: " + ORCID + "\n\n" + CITATION_NOTE + "\n"
    rendered = {
        "cite_cff": ("CITATION.cff", yaml.safe_dump(cff, sort_keys=False, allow_unicode=True)),
        "cite_bib": ("BaSIS.bib", bib),
        "cite_ris": ("BaSIS.ris", ris),
        "cite_txt": ("CITATION.txt", txt),
    }
    directory = Path(outdir)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for key, (filename, content) in rendered.items():
        path = directory / filename
        path.write_text(content, encoding="utf-8", newline="\n")
        paths[key] = str(path)
    return paths
