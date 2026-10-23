"""Retained, validated geNomad downloads, run by the selected tool environment.

This helper uses geNomad's installed version and its official compatibility
table. It does not run an analysis or update an existing database. Interrupted
transfers are retained for diagnosis; retries start fresh (HTTP Range resume is
not asserted). A completed, verified archive is reused for later extraction.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import fcntl
import gzip
import hashlib
import http.client
from importlib.metadata import version as installed_version
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import ssl
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib


BASE_URL = "https://portal.nersc.gov/genomad/__data__/"
SCHEMA = "basis-genomad-download-v1"
MAX_ARCHIVE_BYTES = 25_000_000_000
MAX_EXTRACTED_BYTES = 100_000_000_000
ATTEMPTS = 3
BLOCK = 1024 * 1024


class DownloadIntegrityError(ValueError):
    pass


class UnsafeArchiveError(ValueError):
    pass


def _official_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "portal.nersc.gov"
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or not parsed.path.startswith("/genomad/__data__/")):
        raise ValueError("geNomad download left the official HTTPS source")
    return url


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url):
    request = urllib.request.Request(_official_url(url), headers={
        "User-Agent": "BaSIS-resource/1", "Accept-Encoding": "identity"})
    response = urllib.request.build_opener(_Redirect()).open(request, timeout=120)
    _official_url(response.geturl())
    return response


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".writing-" + uuid.uuid4().hex)
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _retryable(error):
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {408, 429} or 500 <= error.code <= 599
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(cause, ssl.SSLCertVerificationError):
        return False
    if isinstance(cause, OSError) and cause.errno in {
        errno.ENOSPC, errno.EACCES, errno.EPERM, errno.EROFS,
    }:
        return False
    return isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError,
                              http.client.IncompleteRead, DownloadIntegrityError))


def resolve_database_version(releases, tool_version):
    """Use the same last-compatible-row policy as geNomad's native installer."""
    package_version = str(tool_version).rsplit(".", 1)[0]
    selected = None
    for line in releases.strip().splitlines()[1:]:
        if not line.strip():
            continue
        parts = line.strip().split("\t")
        if len(parts) != 2:
            raise ValueError("Malformed official geNomad compatibility table")
        database_version, compatible_package = parts
        if compatible_package == package_version:
            selected = database_version
    if not selected or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", selected):
        raise ValueError("No compatible geNomad database version in the official table")
    return selected


def _read_releases(open_url, sleep):
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with open_url(BASE_URL + "releases.txt") as response:
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise ValueError("Official compatibility table exceeds size limit")
                declared = response.headers.get("Content-Length")
                if declared is not None and (not str(declared).isdigit() or int(declared) != len(raw)):
                    raise DownloadIntegrityError("Incomplete official geNomad compatibility table")
                return raw.decode("utf-8")
        except Exception as error:
            if attempt == ATTEMPTS or not _retryable(error):
                raise
            print(f"geNomad compatibility request interrupted; retry {attempt + 1}/{ATTEMPTS}", flush=True)
            sleep(attempt * 2)
    raise AssertionError("Compatibility request did not finish")


def _member_path(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts
            or not path.parts or path.parts[0] != "genomad_db"
            or any("\x00" in part or "\\" in part for part in path.parts)):
        raise UnsafeArchiveError("Unsafe geNomad archive path: " + name)
    return path


def _link_target(member):
    name = _member_path(member.name)
    raw = member.linkname
    if not raw or PurePosixPath(raw).is_absolute() or "\\" in raw or "\x00" in raw:
        raise UnsafeArchiveError("Unsafe geNomad archive link: " + member.name)
    target = posixpath.normpath(
        str(name.parent / raw) if member.issym() else raw)
    return _member_path(target)


def validate_archive(path, expected_version=None):
    """Read all gzip bytes (including CRC) and validate every tar entry."""
    members = []
    seen = set()
    declared = 0
    archive_version = None
    try:
        with gzip.open(path, "rb") as compressed:
            with tarfile.open(fileobj=compressed, mode="r|") as archive:
                for member in archive:
                    name = _member_path(member.name).as_posix()
                    if name in seen:
                        raise UnsafeArchiveError("Duplicate geNomad archive path: " + name)
                    seen.add(name)
                    if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                        raise UnsafeArchiveError("Unsupported geNomad archive entry: " + name)
                    if member.issym() or member.islnk():
                        _link_target(member)
                    declared += member.size
                    if member.size < 0 or declared > MAX_EXTRACTED_BYTES:
                        raise UnsafeArchiveError("geNomad archive exceeds extraction size limit")
                    if name == "genomad_db/version.txt":
                        if not member.isfile() or not 0 < member.size <= 200:
                            raise DownloadIntegrityError("geNomad archive has an invalid version file")
                        with archive.extractfile(member) as version_file:
                            try:
                                archive_version = version_file.read().decode("utf-8").strip()
                            except UnicodeDecodeError as error:
                                raise DownloadIntegrityError("geNomad archive has an invalid version file") from error
                    members.append(member)
            # tar ends at its zero blocks, before gzip necessarily checks CRC.
            while compressed.read(BLOCK):
                if compressed.tell() > MAX_EXTRACTED_BYTES:
                    raise UnsafeArchiveError("geNomad archive exceeds extraction size limit")
    except (EOFError, gzip.BadGzipFile, tarfile.ReadError, zlib.error) as error:
        raise DownloadIntegrityError("geNomad archive is truncated or corrupt") from error
    if "genomad_db/version.txt" not in seen or not any(member.isfile() for member in members):
        raise DownloadIntegrityError("geNomad archive lacks its database/version files")
    if expected_version is not None and archive_version != expected_version:
        raise DownloadIntegrityError("geNomad archive version differs from the selected release")
    links = {_member_path(member.name) for member in members if member.issym() or member.islnk()}
    for member in members:
        if any(parent in links for parent in _member_path(member.name).parents):
            raise UnsafeArchiveError("geNomad archive entry descends through an archive link")
    return members


def _archive_identity(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _acquired(target, record, members, validation):
    if validation is not None:
        validation.update(members=members, identity=_archive_identity(target.stat()))
    return target, record


def _transfer(url, partial, open_url):
    print(f"Requesting {url}", flush=True)
    with open_url(url) as response, partial.open("xb") as output:
        _official_url(response.geturl())
        declared = response.headers.get("Content-Length")
        if declared is not None and not str(declared).isdigit():
            raise DownloadIntegrityError("Invalid geNomad Content-Length")
        expected = int(declared) if declared is not None else None
        if expected is not None and expected > MAX_ARCHIVE_BYTES:
            raise ValueError("geNomad archive exceeds download size limit")
        size = 0
        last_progress = time.monotonic()
        for block in iter(lambda: response.read(BLOCK), b""):
            size += len(block)
            if size > MAX_ARCHIVE_BYTES:
                raise ValueError("geNomad archive exceeds download size limit")
            output.write(block)
            if time.monotonic() - last_progress >= 30:
                print(f"geNomad archive: {size / 1048576:.1f} MiB received", flush=True)
                last_progress = time.monotonic()
        output.flush()
        os.fsync(output.fileno())
        headers = {key: response.headers.get(key) for key in
                   ("ETag", "Last-Modified", "Content-Length") if response.headers.get(key)}
    if not size or expected is not None and size != expected:
        raise DownloadIntegrityError(f"Incomplete geNomad transfer: received {size}, expected {expected} bytes")
    return size, headers


@contextmanager
def _cache_lock(directory):
    with (directory / ".download.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another geNomad download is using this cache; retry after it finishes") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def acquire_archive(database_version, cache, *, open_url=None, sleep=None, validation=None):
    open_url = open_url or _open
    sleep = sleep or time.sleep
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", database_version):
        raise ValueError("Invalid geNomad database version")
    directory = Path(cache).resolve() / database_version
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"genomad_db_v{database_version}.tar.gz"
    complete = directory / "complete"
    target = complete / filename
    receipt = complete / "download.json"
    url = BASE_URL + filename
    with _cache_lock(directory):
        if target.is_file() and receipt.is_file():
            try:
                record = json.loads(receipt.read_text())
                before = _archive_identity(target.stat())
                if (record.get("schema") != SCHEMA or record.get("source_url") != url
                        or record.get("bytes") != target.stat().st_size
                        or record.get("sha256") != _sha256(target)):
                    raise DownloadIntegrityError("Recorded geNomad archive does not match cached bytes")
                members = validate_archive(target, expected_version=database_version)
                if before != _archive_identity(target.stat()):
                    raise DownloadIntegrityError("geNomad archive changed during verification")
                print(f"Reusing verified geNomad archive: {target}", flush=True)
                return _acquired(target, {**record, "archive_reused": True}, members, validation)
            except (ValueError, EOFError, OSError) as error:
                print(f"Cached geNomad archive failed validation ({type(error).__name__}); retaining it for diagnosis", flush=True)
        if complete.exists() or complete.is_symlink():
            complete.rename(complete.with_name(complete.name + ".invalid-" + uuid.uuid4().hex))
        for attempt in range(1, ATTEMPTS + 1):
            partial = directory / (filename + ".part-" + uuid.uuid4().hex)
            print(f"geNomad download attempt {attempt}/{ATTEMPTS}; failed transfers are retained", flush=True)
            try:
                size, headers = _transfer(url, partial, open_url)
                members = validate_archive(partial, expected_version=database_version)
                record = {"schema": SCHEMA, "source_url": url,
                          "database_version": database_version, "bytes": size,
                          "sha256": _sha256(partial), "response_headers": headers,
                          "attempts": attempt, "range_resume": False,
                          "integrity_basis": "HTTPS, response length when supplied, gzip CRC, tar structure, and recorded SHA256"}
                # Publish the archive and its provenance as one directory.
                # A crash cannot leave an apparently complete cache without
                # the matching manifest, or vice versa.
                ready = directory / (".ready-" + uuid.uuid4().hex)
                ready.mkdir()
                partial.replace(ready / filename)
                _atomic_json(ready / "download.json", record)
                ready.replace(complete)
                return _acquired(target, {**record, "archive_reused": False}, members, validation)
            except Exception as error:
                _atomic_json(partial.with_name(partial.name + ".failed.json"), {
                    "schema": SCHEMA, "status": "failed", "attempt": attempt,
                    "source_url": url, "partial": str(partial),
                    "error_type": type(error).__name__, "error": str(error),
                    "range_resume": False})
                if attempt == ATTEMPTS or not _retryable(error):
                    raise RuntimeError(f"geNomad download failed; diagnostic files retained in {directory}: {error}") from error
                print("geNomad transfer failed validation or was interrupted; retrying from the beginning", flush=True)
                sleep(attempt * 2)
    raise AssertionError("geNomad download did not finish")


def extract_archive(archive_path, destination, *, validation=None):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    database = destination / "genomad_db"
    if database.exists() or database.is_symlink():
        raise ValueError("Refusing to overwrite an existing geNomad database: " + str(database))
    members = validate_archive(archive_path) if validation is None else validation["members"]
    print(f"Extracting verified geNomad archive to {database}", flush=True)
    # Files and directories precede all links, so extraction cannot follow an
    # archive-created symlink. No existing database is modified.
    with Path(archive_path).open("rb") as source_archive, tarfile.open(fileobj=source_archive, mode="r:gz") as archive:
        if validation is not None and _archive_identity(os.fstat(source_archive.fileno())) != validation["identity"]:
            raise DownloadIntegrityError("geNomad archive changed after verification")
        for member in members:
            output = destination / _member_path(member.name)
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, output.open("xb") as target:
                    shutil.copyfileobj(source, target, BLOCK)
        for member in members:
            if member.islnk():
                output = destination / _member_path(member.name)
                source = destination / _link_target(member)
                if source.is_symlink() or not source.is_file():
                    raise UnsafeArchiveError("geNomad hard link does not target a regular file")
                output.parent.mkdir(parents=True, exist_ok=True)
                os.link(source, output)
        for member in members:
            if member.issym():
                output = destination / _member_path(member.name)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.symlink_to(member.linkname)
        for member in members:
            output = destination / _member_path(member.name)
            if not output.resolve(strict=True).is_relative_to(database):
                raise UnsafeArchiveError("geNomad archive link leaves its database directory")
        if validation is not None and _archive_identity(os.fstat(source_archive.fileno())) != validation["identity"]:
            raise DownloadIntegrityError("geNomad archive changed during extraction")
    return database


def prepare_database(destination, cache, tool_version, *, open_url=None, sleep=None):
    open_url = open_url or _open
    sleep = sleep or time.sleep
    selected = resolve_database_version(_read_releases(open_url, sleep), tool_version)
    validation = {}
    archive, record = acquire_archive(selected, cache, open_url=open_url, sleep=sleep, validation=validation)
    database = extract_archive(archive, destination, validation=validation)
    if (database / "version.txt").read_text().strip() != selected:
        raise DownloadIntegrityError("Extracted geNomad version differs from the selected release")
    record.update(tool_version=tool_version, archive=str(archive), database=str(database))
    _atomic_json(Path(destination) / "genomad-download.json", record)
    print(f"geNomad database {selected} extracted; verified archive retained at {archive}", flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    args = parser.parse_args()
    prepare_database(args.destination, args.cache, installed_version("genomad"))


if __name__ == "__main__":
    main()
