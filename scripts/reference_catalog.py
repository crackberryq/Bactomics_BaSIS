#!/usr/bin/env python3
"""Check, build and verify a portable catalog of locally reviewed BaSIS resources."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import date, datetime, timezone
import hashlib
from html import unescape
import http.client
import json
from pathlib import Path
import re
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workflow/vendor"))
from basis.placement_panels import load_registry


SPEC = "basis-reference-catalog-spec-v1"
PACKAGE = "basis-reference-catalog-v1"
METADATA = ("source", "source_version", "citation", "license", "reviewer", "reviewed_on")
STATUSES = ("approved", "pending", "excluded")
DIGEST = re.compile(r"[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}")


def load_json(data):
    def unique_keys(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key {key!r}")
            result[key] = value
        return result
    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique_keys)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def validate(catalog, schema):
    if not isinstance(catalog, dict) or catalog.get("schema") != schema:
        raise ValueError(f"Expected schema {schema}")
    fields = {"schema", "name", "version", "records"}
    if schema == PACKAGE:
        fields.add("spec_sha256")
        if not isinstance(catalog.get("spec_sha256"), str) or not DIGEST.fullmatch(catalog["spec_sha256"]):
            raise ValueError("Missing original specification SHA256")
    if set(catalog) != fields:
        raise ValueError(f"Expected catalog fields: {sorted(fields)}")
    if not all(nonempty(catalog[k]) for k in ("name", "version")):
        raise ValueError("Supply a catalog name and version")
    if not isinstance(catalog["records"], list) or not catalog["records"]:
        raise ValueError("Supply a nonempty records list")
    seen = set()
    registry_count = 0
    for row in catalog["records"]:
        if not isinstance(row, dict):
            raise ValueError("Each catalog record must be an object")
        extra = set(row) - {"id", "status", "kind", "path", "sha256", "notes", *METADATA}
        if extra:
            raise ValueError(f"Unrecognized record fields: {sorted(extra)}")
        key = row.get("id")
        if not isinstance(key, str) or not IDENTIFIER.fullmatch(key) or key.casefold() in seen:
            raise ValueError(f"Invalid or duplicate record ID: {key!r}")
        seen.add(key.casefold())
        if row.get("status") not in STATUSES or row.get("kind") not in ("reference", "placement_registry"):
            raise ValueError(f"{key}: unknown status or kind")
        if any(not isinstance(value, str) for value in row.values()):
            raise ValueError(f"{key}: record fields must be strings")
        if row["status"] != "approved":
            if schema == PACKAGE and "path" in row:
                raise ValueError(f"{key}: only approved records may reference packaged files")
            continue
        for field in ("path", "sha256", *METADATA):
            if not nonempty(row.get(field)):
                raise ValueError(f"{key}: approved record requires {field}")
        if not DIGEST.fullmatch(row["sha256"]):
            raise ValueError(f"{key}: supply the lowercase SHA256 of the reviewed file")
        if date.fromisoformat(row["reviewed_on"]).isoformat() != row["reviewed_on"]:
            raise ValueError(f"{key}: reviewed_on must be YYYY-MM-DD")
        registry_count += row["kind"] == "placement_registry"
    if registry_count > 1:
        raise ValueError("A catalog may contain at most one approved placement registry")


def check_payload(source, row, target=None):
    if not source.is_file():
        raise ValueError(f"{row['id']}: expected a local file at {source}")
    digest = hashlib.sha256()
    with source.open("rb") as reader, (target.open("xb") if target else nullcontext()) as writer:
        while block := reader.read(1024 * 1024):
            digest.update(block)
            if writer is not None:
                writer.write(block)
    if digest.hexdigest() != row["sha256"]:
        raise ValueError(f"{row['id']}: reviewed file SHA256 mismatch")
    if row["kind"] == "placement_registry":
        registry = load_registry(target or source)
        if registry["_registry_sha256"] != row["sha256"]:
            raise ValueError(f"{row['id']}: registry changed during validation")


def summary(catalog, root=None, checksum=None):
    counts = {s: sum(row["status"] == s for row in catalog["records"]) for s in STATUSES}
    registry = next((row["path"] for row in catalog["records"]
                     if row["status"] == "approved" and row["kind"] == "placement_registry"), None)
    return {"schema": "basis-reference-catalog-status-v1", "name": catalog["name"],
            "version": catalog["version"], **counts, "catalog_sha256": checksum,
            "output": str(root) if root else None,
            "placement_registry": str(root / registry) if root and registry else None}


def prepare(spec_path, expected=None, stage=None):
    spec_path = Path(spec_path).resolve(strict=True)
    spec_bytes = spec_path.read_bytes()
    spec_digest = hashlib.sha256(spec_bytes).hexdigest()
    if expected is not None and expected != spec_digest:
        raise ValueError("The selected specification SHA256 has changed")
    catalog = load_json(spec_bytes)
    validate(catalog, SPEC)
    packaged = dict(catalog, schema=PACKAGE, spec_sha256=spec_digest, records=[])
    if stage:
        (stage / "files").mkdir()
    members = set()
    for row in catalog["records"]:
        entry = dict(row)
        entry.pop("path", None)
        if row["status"] == "approved":
            source = spec_path.parent / Path(row["path"]).expanduser()
            suffix = ".json" if row["kind"] == "placement_registry" else source.suffix
            if not re.fullmatch(r"\.[A-Za-z0-9]{1,12}", suffix):
                suffix = ".data"
            member = "files/ref-" + row["id"] + suffix
            if member.casefold() in members:
                raise ValueError(f"Conflicting package filenames for {row['id']}")
            members.add(member.casefold())
            check_payload(source, row, stage / member if stage else None)
            entry["path"] = member
        packaged["records"].append(entry)
    return packaged


def verify(directory):
    root = Path(directory).resolve(strict=True)
    catalog_path = root / "catalog.json"
    digest_file = root / "catalog.sha256"
    if catalog_path.is_symlink() or digest_file.is_symlink() or (root / "files").is_symlink():
        raise ValueError("Catalog metadata and file directory must be regular local entries")
    data = catalog_path.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    if digest_file.read_text(encoding="ascii").strip() != checksum:
        raise ValueError("Catalog manifest SHA256 mismatch")
    catalog = load_json(data)
    validate(catalog, PACKAGE)
    expected_members = {catalog_path, digest_file, root / "files"}
    for row in catalog["records"]:
        if row["status"] != "approved":
            continue
        path = root / row["path"]
        prefix = "files/ref-" + row["id"]
        suffix = r"\.json" if row["kind"] == "placement_registry" else r"\.[A-Za-z0-9]{1,12}"
        if not re.fullmatch(re.escape(prefix) + suffix, row["path"]) or path.is_symlink() or path in expected_members:
            raise ValueError(f"{row['id']}: invalid or repeated package file")
        expected_members.add(path)
        check_payload(path, row)
    details = summary(catalog, root, checksum)
    if not details["approved"] or set(root.rglob("*")) != expected_members:
        raise ValueError("Approved file inventory differs from the package")
    return details


def build(spec_path, destination, expected=None):
    root = Path(destination).expanduser().absolute()
    if root.exists() or root.is_symlink():
        raise ValueError(f"Choose an unused output directory: {root}")
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root.parent, prefix=".basis-catalog-") as temp:
        stage = Path(temp) / "package"
        stage.mkdir()
        catalog = prepare(spec_path, expected, stage)
        if not any(row["status"] == "approved" for row in catalog["records"]):
            raise ValueError("At least one reviewed, approved file is required to build a catalog")
        data = (json.dumps(catalog, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
        (stage / "catalog.json").write_bytes(data)
        checksum = hashlib.sha256(data).hexdigest()
        (stage / "catalog.sha256").write_text(checksum + "\n", encoding="ascii")
        verify(stage)
        stage.rename(root)
    return summary(catalog, root, checksum)


def draft_doi(value):
    """Fetch one explicit Crossref work as a pending bibliography entry."""
    doi = value.strip()
    if re.match(r"^https?://(?:dx\.)?doi\.org/", doi, flags=re.I):
        doi = unquote(re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, count=1, flags=re.I))
    else:
        doi = re.sub(r"^doi:\s*", "", doi, count=1, flags=re.I)
    if (len(doi) > 2048 or not doi.isprintable() or
            not re.fullmatch(r"10\.[0-9]{4,9}/[^\s]+", doi)):
        raise ValueError("Enter a DOI or a doi.org link")
    endpoint = "https://api.crossref.org/works/" + quote(doi, safe="")
    request = Request(endpoint, headers={
        "Accept": "application/json",
        "User-Agent": "BaSIS-reference-catalog/1 (https://github.com/crackberryq/Bactomics_BaSIS)",
    })
    limit = 2 * 1024 * 1024
    try:
        with urlopen(request, timeout=10) as response:
            data = response.read(limit + 1)
    except HTTPError as exc:
        if exc.code == 404:
            raise ValueError("No Crossref work was found for this DOI") from exc
        if exc.code == 429:
            raise ValueError("Crossref rate limit reached; retry later") from exc
        raise ValueError(f"Crossref returned HTTP {exc.code}") from exc
    except (URLError, OSError, http.client.HTTPException) as exc:
        raise ValueError("Crossref lookup failed; check the connection and retry") from exc
    if len(data) > limit:
        raise ValueError("Crossref returned an oversized citation record")
    try:
        document = json.loads(data)
        record = document["message"]
        if document["status"] != "ok" or record["DOI"].casefold() != doi.casefold():
            raise ValueError("Crossref response did not identify the requested DOI")
        title = record["title"][0]
        if not nonempty(title):
            raise ValueError("Crossref returned no usable title")
        # Crossref titles can carry inline JATS markup. Store plain citation text.
        title = " ".join(unescape(re.sub(r"<[^>]+>", "", title)).split())
        if not title:
            raise ValueError("Crossref returned no usable title")
        authors = record.get("author") or []
        author = ""
        if authors:
            author = authors[0].get("family") or authors[0].get("name") or ""
            if author and len(authors) > 1:
                author += " et al."
        dates = (record.get("issued") or {}).get("date-parts") or []
        year = str(dates[0][0]) if dates and dates[0] else ""
        venue = (record.get("container-title") or [""])[0]
        stable_url = "https://doi.org/" + quote(doi, safe="/")
        citation = " ".join(str(part) for part in (author, f"({year})" if year else "",
                                                     title, venue, stable_url) if part)
    except (AttributeError, KeyError, IndexError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("Crossref returned an unreadable bibliographic record") from exc
    retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ref_id = "doi-" + hashlib.sha256(doi.casefold().encode()).hexdigest()[:20]
    row = {"id": ref_id, "kind": "reference", "status": "pending", "source": stable_url,
           "citation": citation, "source_version": "", "license": "", "reviewer": "",
           "reviewed_on": "", "path": "", "sha256": "",
           "notes": f"Crossref bibliographic metadata retrieved {retrieved}. Review the cited work and supply the local resource and its license before approval."}
    return {"schema": SPEC, "name": "Literature reference catalog", "version": "0.1.0", "records": [row]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("draft-doi", help="Look up a Crossref citation for a pending record").add_argument("doi")
    checker = commands.add_parser("check")
    checker.add_argument("spec")
    checker.add_argument("--expect-spec-sha256")
    builder = commands.add_parser("build")
    builder.add_argument("spec")
    builder.add_argument("output")
    builder.add_argument("--expect-spec-sha256")
    commands.add_parser("verify").add_argument("output")
    args = parser.parse_args(argv)
    try:
        if args.command == "draft-doi":
            result = draft_doi(args.doi)
        elif args.command == "check":
            result = summary(prepare(args.spec, args.expect_spec_sha256))
        elif args.command == "build":
            result = build(args.spec, args.output, args.expect_spec_sha256)
        else:
            result = verify(args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Reference catalog: {error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
