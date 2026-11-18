"""Short-path, byte-preserving sharing copies of saved results.

The live run is never renamed. Original files are stored under short portable
names in a ZIP; a manifest preserves their paths, links and full SHA-256 hashes.
Separate derived BaSIS HTML views rewrite only presentation links.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PureWindowsPath
import posixpath
import re
import stat
import tempfile
from urllib.parse import unquote, urlsplit, urlunsplit
import zipfile


SCHEMA = "basis-share-bundle-v1"
MAX_RELATIVE_PATH = 64
DEFAULT_WINDOWS_ROOT_UNITS = 180
_RESERVED = re.compile(r"^(?:con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(?:\.|$)", re.I)
_URL_ATTRIBUTES = {"href", "src", "poster", "xlink:href"}


def units(value):
    """Conservatively count Windows UTF-16 code units, including non-BMP text."""
    return len(str(value).encode("utf-16-le")) // 2


def _signature(path):
    return _stat_signature(path.stat())


def _stat_signature(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _name_issues(name):
    issues = []
    if (re.search(r'["*:<>?/\\|\x00-\x1f]', name) or name != name.strip()
            or name.endswith(".") or _RESERVED.match(name)
            or name.lower() in {".lock", "desktop.ini", ".ds_store"}
            or "_vti_" in name.lower() or name.startswith("~$")
            or name.startswith(("\u309b", "\u1027"))):
        issues.append("unsupported_name")
    if units(name) > 255:
        issues.append("component_over_255")
    if name.lower().endswith(".tmp"):
        issues.append("temporary_file_type")
    return issues


def _short_name(relative):
    suffix = "".join(Path(relative).suffixes[-2:]).lower()
    if not re.fullmatch(r"(?:\.[a-z0-9]{1,8}){1,2}", suffix) or suffix.endswith(".tmp"):
        suffix = ".bin"
    return "f/" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24] + suffix


def _windows_root(value):
    path = PureWindowsPath(value)
    if not path.is_absolute():
        raise ValueError("--windows-root must be the absolute Windows extraction folder")
    if any(_name_issues(part) for part in path.parts[1:]):
        raise ValueError("The Windows extraction folder contains an unsupported name")
    return str(path).rstrip("\\/")


def check_paths(paths, *, windows_root=None, cloud_root=None, max_windows_path=240):
    """Check the archive members at their intended extracted destination."""
    if not 80 <= max_windows_path <= 259:
        raise ValueError("Windows path budget must be between 80 and 259 UTF-16 units")
    prefix = _windows_root(windows_root) if windows_root is not None else None
    if cloud_root is not None:
        if not cloud_root.startswith("/") or "://" in cloud_root or "\\" in cloud_root:
            raise ValueError("--cloud-root must be a decoded server-relative folder path, not a URL")
        if any(_name_issues(part) for part in cloud_root.strip("/").split("/")):
            raise ValueError("The SharePoint extraction folder contains an unsupported name")
    longest = 0
    for relative in paths:
        if relative.startswith("/") or any(p in {"", ".", ".."} or _name_issues(p)
                                            for p in relative.split("/")):
            raise ValueError("Unsafe sharing member name: " + relative)
        size = units(relative)
        longest = max(longest, size)
        if size > MAX_RELATIVE_PATH:
            raise ValueError("Sharing member exceeds the short-path budget: " + relative)
        windows_size = (units(prefix) if prefix is not None else DEFAULT_WINDOWS_ROOT_UNITS) + 1 + size
        if windows_size > max_windows_path:
            raise ValueError("Extraction path exceeds the Windows path budget; choose a shorter --windows-root")
        if cloud_root is not None and units(cloud_root.rstrip("/") + "/" + relative) > 400:
            raise ValueError("Extraction path exceeds SharePoint's decoded 400-character budget")
    return {"max_relative_units": longest, "max_windows_path_units": max_windows_path,
            "windows_root": prefix, "assumed_windows_root_units": DEFAULT_WINDOWS_ROOT_UNITS if prefix is None else None,
            "cloud_root": cloud_root, "cloud_limit_checked": cloud_root is not None,
            "note": "Budgets apply to the extraction folder supplied here. A deeper destination must be checked again."}


@dataclass
class Plan:
    source: Path
    output: Path
    entries: list[dict]
    files: dict[str, dict]
    directories: dict[Path, tuple]
    links: dict[Path, tuple]
    views: dict[str, str]
    path_check: dict
    source_issues: list[dict]
    allowed_roots: tuple[Path, ...] = ()
    unavailable_links: list[dict] = field(default_factory=list)

    def summary(self):
        return {"schema": SCHEMA, "status": "preview", "source": str(self.source),
                "output": str(self.output), "files": sum(e["kind"] == "file" for e in self.entries),
                "unique_files": len(self.files), "uncompressed_bytes": sum(f["signature"][2] for f in self.files.values()),
                "links_materialized": len(self.links), "source_path_issues": len(self.source_issues),
                "source_path_examples": self.source_issues[:20], "path_check": self.path_check,
                "native_commands_run": False, "source_files_changed": False}


def _output_path(source, output, allowed):
    selected = Path(output).expanduser().absolute()
    if any(p.is_symlink() for p in (selected, *selected.parents)):
        raise ValueError("Sharing output must not pass through a symlink")
    output = selected.resolve()
    if output.exists():
        raise FileExistsError("Sharing output already exists; choose a new ZIP name: " + str(output))
    if output.suffix.lower() != ".zip" or _name_issues(output.name):
        raise ValueError("Choose a portable output filename ending in .zip")
    if any(output.is_relative_to(root) for root in (source, *allowed)):
        raise ValueError("Sharing output must be outside the source and any allowed linked-result roots")
    if not output.parent.is_dir():
        raise ValueError("Create the output parent directory before exporting")
    # Also check the ZIP itself at a WSL/Windows destination, when identifiable.
    match = re.match(r"^/mnt/([a-zA-Z])/(.*)$", str(output))
    windows = match[1].upper() + ":\\" + match[2].replace("/", "\\") if match else str(output) if os.name == "nt" else None
    if windows and units(windows) > 240:
        raise ValueError("ZIP output path is too long; use a shorter destination")
    return output


def build_plan(source, output, *, allow_external_roots=(), windows_root=None,
               cloud_root=None, max_windows_path=240):
    lexical = Path(source).expanduser().absolute()
    if lexical.is_symlink():
        raise ValueError("Choose a real source directory, not a root symlink")
    source = lexical.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("Sharing source must be a directory")
    allowed = tuple(Path(p).expanduser().resolve(strict=True) for p in allow_external_roots)
    if any(not p.is_dir() for p in allowed):
        raise ValueError("Allowed external roots must be directories")
    output = _output_path(source, output, allowed)
    entries, files, directories, links, issues = [], {}, {}, {}, []
    canonical = {}

    def visit(path, relative, ancestors=()):
        mode = path.lstat().st_mode
        real = path.resolve(strict=True)
        if not any(real.is_relative_to(root) for root in (source, *allowed)):
            raise ValueError("External symlink requires an explicit --allow-external-root: " + str(path) + " -> " + str(real))
        if relative:
            problems = sorted({issue for part in Path(relative).parts for issue in _name_issues(part)})
            if DEFAULT_WINDOWS_ROOT_UNITS + 1 + units(relative) > 240:
                problems.append("long_relative_path_with_180_unit_sync_root")
            if problems:
                issues.append({"path": relative, "reasons": problems})
        if stat.S_ISLNK(mode):
            links[path] = (os.readlink(path), str(real))
            entries.append({"path": relative, "kind": "symlink", "target": os.readlink(path),
                            "materialized": True})
        if real.is_dir():
            if real in ancestors:
                raise ValueError("Directory symlink cycle: " + str(path))
            directories[real] = _signature(real)
            if relative:
                entries.append({"path": relative, "kind": "directory"})
            for child in sorted(path.iterdir(), key=lambda p: p.name):
                visit(child, (relative + "/" if relative else "") + child.name, (*ancestors, real))
        elif real.is_file() and (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
            if real not in canonical:
                short = _short_name(relative)
                if short in files:
                    raise ValueError("Short-name collision; no export was published")
                canonical[real] = short
                files[short] = {"source": real, "original_path": relative, "signature": _signature(real)}
            entries.append({"path": relative, "kind": "file", "member": canonical[real]})
        else:
            raise ValueError("Unsupported special file in sharing source: " + str(path))

    visit(source, "")
    if not files:
        raise ValueError("No regular result files to share")
    views = {}
    for entry in entries:
        if entry["kind"] != "file":
            continue
        name = entry["path"]
        if name == "report.html":
            views[name] = "report.html"
        elif name == "deepsafe-report/report.html":
            views[name] = "followup.html"
        elif re.fullmatch(r"(?:deepsafe-report/)?report-data/[^/]+\.html", name):
            views[name] = "v/" + hashlib.sha256(name.encode()).hexdigest()[:24] + ".html"
    members = ["index.html", "manifest.json", "README.txt", *files, *views.values()]
    if len(set(m.casefold() for m in members)) != len(members):
        raise ValueError("Sharing output names collide")
    path_check = check_paths(members, windows_root=windows_root, cloud_root=cloud_root,
                             max_windows_path=max_windows_path)
    return Plan(source, output, entries, files, directories, links, views, path_check, issues, allowed)


def _unchanged(plan):
    for path, expected in plan.directories.items():
        if _signature(path) != expected:
            raise ValueError("Source directory changed during export: " + str(path))
    for path, expected in plan.links.items():
        if (os.readlink(path), str(path.resolve(strict=True))) != expected:
            raise ValueError("Source link changed during export: " + str(path))
    for row in plan.files.values():
        if _signature(row["source"]) != row["signature"]:
            raise ValueError("Source file changed during export: " + str(row["source"]))


class _ReportCopy(HTMLParser):
    def __init__(self, plan, original, member, targets):
        super().__init__(convert_charrefs=False)
        self.plan, self.original, self.member, self.targets = plan, original, member, targets
        self.parts = []

    def _url(self, value):
        parsed = urlsplit(value)
        if parsed.scheme == "file" and parsed.netloc:
            self.plan.unavailable_links.append({"page": self.original, "href": value})
            return None
        if not parsed.path or parsed.scheme not in {"", "file"} or parsed.netloc:
            return value
        raw = Path(unquote(parsed.path))
        candidate = raw if raw.is_absolute() else self.plan.source / Path(self.original).parent / raw
        candidate = candidate.resolve()
        target = self.targets.get(candidate)
        if target is None:
            self.plan.unavailable_links.append({"page": self.original, "href": value})
            return None
        target_url = urlsplit(target)
        relative = posixpath.relpath(target_url.path, posixpath.dirname(self.member) or ".")
        return urlunsplit(("", "", relative, parsed.query, target_url.fragment or parsed.fragment))

    def _tag(self, tag, attrs, closed=False):
        if tag == "base":
            return  # An original base URL must not redirect the sharing copy.
        result = []
        for name, value in attrs:
            if name in _URL_ATTRIBUTES and value is not None:
                mapped = self._url(value)
                if mapped is None:
                    result.extend([("aria-disabled", "true"), ("title", "File unavailable in this export: " + value)])
                    continue
                value = mapped
            result.append((name, value))
        self.parts.append("<" + tag + "".join(" " + k + ("" if v is None else '="' + html.escape(v, quote=True) + '"')
                                              for k, v in result) + (" />" if closed else ">"))
        if tag == "body":
            index = posixpath.relpath("index.html", posixpath.dirname(self.member) or ".")
            self.parts.append('<div style="position:relative;z-index:9999;padding:10px 18px;background:#fff2cd;color:#263847;font:14px system-ui">'
                              'Sharing copy · <a href="' + index + '">Files and original-path map</a>. '
                              'Original data is preserved; this copy is for review, not workflow restart.</div>')

    def handle_starttag(self, tag, attrs): self._tag(tag, attrs)
    def handle_startendtag(self, tag, attrs): self._tag(tag, attrs, True)
    def handle_endtag(self, tag):
        if tag != "base": self.parts.append("</" + tag + ">")
    def handle_data(self, value): self.parts.append(value)
    def handle_entityref(self, value): self.parts.append("&" + value + ";")
    def handle_charref(self, value): self.parts.append("&#" + value + ";")
    def handle_comment(self, value): self.parts.append("<!--" + value + "-->")
    def handle_decl(self, value): self.parts.append("<!" + value + ">")
    def handle_pi(self, value): self.parts.append("<?" + value + ">")


def _index(plan):
    links = ''.join('<a href="' + member + '">' + ("Screening report" if name == "report.html" else "Follow-up report") + '</a> · '
                    for name, member in plan.views.items() if name.endswith("report.html"))
    rows, anchors = [], set()
    for entry in plan.entries:
        if entry["kind"] != "file":
            continue
        original, member = entry["path"], entry["member"]
        anchor = member.split("/")[1].split(".")[0]
        row_id = ' id="' + anchor + '"' if anchor not in anchors else ""
        anchors.add(anchor)
        rows.append('<tr' + row_id + '><td>' + html.escape(original)
                    + '</td><td><a download href="' + member + '">Download original</a></td></tr>')
    missing = ('<p>' + str(len(plan.unavailable_links)) + ' unavailable local link(s) are listed in manifest.json.</p>') if plan.unavailable_links else ""
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>BaSIS shared results</title><style>body{font:15px/1.6 system-ui;color:#173a4a;background:#f3f7f9;max-width:1200px;margin:auto;padding:24px}'
            'a{color:#087d79}table{border-collapse:collapse;width:100%;background:white}th,td{text-align:left;padding:10px;border-bottom:1px solid #dce7ec}'
            'td:first-child{overflow-wrap:anywhere}input{padding:10px;width:95%;margin:14px 0}tr[hidden]{display:none}</style><body>'
            '<h1>BaSIS shared results</h1><p>' + links + '<a href="manifest.json">Path map and checksums</a></p>'
            '<p>This sharing copy uses short filenames. Original file contents and original paths are preserved separately. '
            'The displayed BaSIS report has adjusted local links. Downloaded native HTML, scripts and checkpoints retain their '
            'original content and may require their original folder layout; this bundle is not a restart directory.</p>' + missing +
            '<label>Find a file by its original path<input id="search" type="search"></label>'
            '<table><thead><tr><th>Original path</th><th>Original bytes</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
            '<script>document.getElementById("search").addEventListener("input",function(){var q=this.value.toLowerCase();'
            'document.querySelectorAll("tbody tr").forEach(function(r){r.hidden=!r.textContent.toLowerCase().includes(q);});});</script></body></html>')


def export_bundle(plan):
    """Write a new ZIP exclusively. Any failure removes only this call's temp file."""
    _output_path(plan.source, plan.output, plan.allowed_roots)
    _unchanged(plan)
    handle, temporary = tempfile.mkstemp(prefix=".bs-", suffix=".zip", dir=plan.output.parent)
    os.close(handle)
    temporary = Path(temporary)
    try:
        manifest = {"schema": SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(),
                    "source_root": str(plan.source), "scope": "Sharing copy; original file bytes retained, no analysis or restart migration.",
                    "path_check": plan.path_check, "source_path_issues": plan.source_issues,
                    "entries": plan.entries, "files": {}, "derived_views": [], "unavailable_links": plan.unavailable_links}
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for member, row in plan.files.items():
                digest, size = hashlib.sha256(), 0
                if _signature(row["source"]) != row["signature"]:
                    raise ValueError("Source changed before copying: " + str(row["source"]))
                with row["source"].open("rb") as source, archive.open(member, "w", force_zip64=True) as target:
                    info = os.fstat(source.fileno())
                    if not stat.S_ISREG(info.st_mode) or _stat_signature(info) != row["signature"]:
                        raise ValueError("Source changed before its contents were opened")
                    while block := source.read(1024 * 1024):
                        digest.update(block)
                        size += len(block)
                        target.write(block)
                    if _stat_signature(os.fstat(source.fileno())) != row["signature"]:
                        raise ValueError("Source changed while its contents were open")
                if _signature(row["source"]) != row["signature"] or size != row["signature"][2]:
                    raise ValueError("Source changed while copying: " + str(row["source"]))
                manifest["files"][member] = {"sha256": digest.hexdigest(), "bytes": size,
                                              "source_mtime_ns": row["signature"][3]}
        # Read HTML from the copied snapshot, never from a changing live file.
        targets = {row["source"]: member for member, row in plan.files.items()}
        by_path = {entry["path"]: entry["member"] for entry in plan.entries if entry["kind"] == "file"}
        for name, member in by_path.items():
            real = (plan.source / name).resolve()
            if name in plan.views:
                targets[real] = plan.views[name]
            elif name in {"metadata/files.html", "deepsafe-report/metadata/files.html"}:
                targets[real] = "index.html"
            elif name.lower().endswith((".html", ".htm")):
                targets[real] = "index.html#" + member.split("/")[1].split(".")[0]
        with zipfile.ZipFile(temporary, "a", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for original, member in plan.views.items():
                parser = _ReportCopy(plan, original, member, targets)
                parser.feed(archive.read(by_path[original]).decode("utf-8"))
                parser.close()
                data = ''.join(parser.parts).encode("utf-8")
                archive.writestr(member, data)
                manifest["derived_views"].append({"path": member, "original_path": original,
                                                  "sha256": hashlib.sha256(data).hexdigest()})
            archive.writestr("index.html", _index(plan))
            archive.writestr("README.txt", "Extract this ZIP and open index.html. All member paths are short.\n"
                              "Use manifest.json to map each original path to its unchanged file bytes.\n"
                              "BaSIS report views have rewritten local links; originals remain under f/.\n"
                              "Native HTML/scripts and checkpoints may require their original layout.\n"
                              "This is a review copy, not a runnable workflow.\n"
                              "Check path budgets again if moving to a deeper Windows/SharePoint folder.\n")
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        _unchanged(plan)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        # Linking is an atomic no-overwrite publication; an existing ZIP is never replaced.
        os.link(temporary, plan.output)
        result = plan.summary()
        result.update(status="exported", output_bytes=plan.output.stat().st_size,
                      unavailable_links=len(plan.unavailable_links))
        return result
    finally:
        temporary.unlink(missing_ok=True)
