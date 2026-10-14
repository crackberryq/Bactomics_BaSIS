"""Pinned, separately attributed MLST/CRISPRCasFinder first-run resources.

These are external-tool resources, not re-licensed BaSIS code. CASFinder3.1.0
models carry CC BY-NC-SA4.0; their license and source metadata stay in snapshots.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import tarfile

from .util import atomic_json, now, sha256_file

SOURCES = {
    "crisprcasfinder": {"repository": "https://github.com/dcouvin/CRISPRCasFinder",
        "revision": "c13475ebf983040b3fb0b685380e7da317752002", "version": "4.3.2",
        "sha256": "ef7cbe1e10a4b388bfdceae6718730dcb813e39a051eb1628dd33d9caaa37165",
        "url": "https://codeload.github.com/dcouvin/CRISPRCasFinder/tar.gz/c13475ebf983040b3fb0b685380e7da317752002",
        "license": "GPL-3.0-or-later"},
    "casfinder_models": {"repository": "https://github.com/macsy-models/CasFinder",
        "revision": "ad7096548a814788ca04538602d71f85115b4c71", "version": "3.1.0",
        "sha256": "c2f7cb124d0500ed97826a77fefd1c6a2ebce7c791e5e990c445f9cbf8da0bc8",
        "url": "https://codeload.github.com/macsy-models/CasFinder/tar.gz/ad7096548a814788ca04538602d71f85115b4c71",
        "license": "CC BY-NC-SA 4.0"},
}


def _extract(archive, destination):
    """Extract one archive root, rejecting links and escaping paths."""
    destination = Path(destination)
    destination.mkdir(exist_ok=False)
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        roots = {PurePosixPath(m.name).parts[0] for m in members if PurePosixPath(m.name).parts}
        if len(roots) != 1:
            raise ValueError("Expected a single pinned source archive root")
        for member in members:
            parts = PurePosixPath(member.name).parts
            if member.name.startswith("/") or ".." in parts or not parts or not (member.isdir() or member.isfile()):
                raise ValueError("Unsupported or escaping source archive member")
            target = destination.joinpath(*parts[1:])
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = handle.extractfile(member)
            if source is None:
                raise ValueError("Unreadable source archive member")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)


def _inventory(root):
    return {str(path.relative_to(root)): sha256_file(path)
            for path in sorted(Path(root).rglob("*")) if path.is_file()}


def _download(source, archive, runner):
    runner.run(["curl", "--fail", "--location", "--retry", "5", "--connect-timeout", "30",
                "--max-time", "1200", "--output", str(archive), source["url"]], cwd=archive.parent)
    if sha256_file(archive) != source["sha256"]:
        raise ValueError("Pinned upstream archive checksum mismatch: " + source["repository"])


def _probe_vmatch(database, destination, runner):
    """Exercise the pinned plugin callback, not just binary/version startup."""
    work = destination / "runtime-probe"
    work.mkdir(exist_ok=False)
    shutil.copy2(database / "sel392v2.so", work / "sel392v2.so")
    repeat = "GTTCACTGCCGTACAGGCAGCTTAGAAA"
    (work / "probe.fna").write_text(">probe\n" + repeat + "ATCGTCAGTAGCTAGATCGATCGTAGCATGCTAGCTA" +
                                  repeat + "ACGATCATCGACTAGCATCGATCGACTAGCTAGCTGA" + repeat + "\n")
    runner.run(["mkvtree", "-db", "probe.fna", "-dna", "-pl", "-lcp", "-suf", "-tis", "-ois",
                "-bwt", "-bck", "-sti1"], cwd=work)
    response = runner.run(["vmatch", "-l", "23", "25", "60", "-e", "1", "-s", "leftseq", "-evalue", "1",
                           "-absolute", "-nodist", "-noevalue", "-noscore", "-noidentity", "-sort", "ia",
                           "-best", "1000000", "-selfun", "./sel392v2.so", "55", "probe.fna"], cwd=work)
    (work / "vmatch.txt").write_text(response.stdout)
    if not re.search(r"(?m)^>\d+\s+\d+\s+D\s+\d+\s+\d+", response.stdout) or repeat not in response.stdout:
        raise ValueError("CRISPRCasFinder Vmatch plugin failed the synthetic-repeat callback probe")
    return {"vmatch_plugin": "synthetic_repeat_detected", "input_sha256": sha256_file(work / "probe.fna"),
            "output_sha256": sha256_file(work / "vmatch.txt"), "scope": "software callback, not biological validation"}


def prepare_resource(spec, destination, runner):
    backend = spec.get("backend") or spec.get("module")
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    manifest = destination / "provenance.json"
    if manifest.exists():
        raise ValueError("Refusing to overwrite prepared typing/defense resources")
    if backend == "mlst":
        version = runner.run(["mlst", "--version"], cwd=destination).stdout.strip()
        if version != "mlst 2.35.0":
            raise ValueError("MLST bootstrap requires native version 2.35.0")
        locate = ('my $p=abs_path(which("mlst")); die "mlst not found" unless $p; '
                  'print dirname(dirname($p))."/db\\n";')
        response = runner.run(["perl", "-MFile::Which=which", "-MCwd=abs_path", "-MFile::Basename=dirname",
                               "-e", locate], cwd=destination)
        source = Path(response.stdout.strip()).resolve(strict=True)
        if not (source / "pubmlst").is_dir() or not (source / "blast/mlst.fa").is_file():
            raise ValueError("Installed MLST bundled database could not be verified")
        if not list((source / "pubmlst").glob("*/*.txt")):
            raise ValueError("Installed MLST database has no scheme definitions")
        database = destination / "db"
        shutil.copytree(source, database, symlinks=False)
        provenance = {"schema": "basis-bundled-typing-resource-v1", "backend": backend,
                      "version": "2.35.0", "source_path": str(source),
                      "source_url": "https://github.com/tseemann/mlst/tree/v2.35.0/db",
                      "software_license": "GPL-2.0", "database_source": "PubMLST snapshot bundled with mlst",
                      "created_at": now(), "sha256": _inventory(database),
                      "limitations": ["A bundled snapshot is not a current live PubMLST update.",
                                      "PubMLST scheme attribution and data terms remain applicable."]}
        updates = {"mlst": {"database": str(database), "metadata": str(manifest), "version": "2.35.0",
                            "database_version": "PubMLST snapshot bundled with mlst2.35.0"}}
    elif backend == "crisprcasfinder":
        if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
            raise ValueError("Pinned CRISPRCasFinder4.3.2 Vmatch plugin requires Linux x86_64")
        archives = destination / "archives"; archives.mkdir(exist_ok=False)
        for name, source in SOURCES.items():
            _download(source, archives / (name + ".tar.gz"), runner)
        database = destination / "source"
        _extract(archives / "crisprcasfinder.tar.gz", database)
        models = database / "models"; models.mkdir(exist_ok=True)
        _extract(archives / "casfinder_models.tar.gz", models / "CASFinder")
        for relative in ("CRISPRCasFinder.pl", "sel392v2.so", "COPYING", "models/CASFinder/LICENSE",
                         "models/CASFinder/metadata.yml"):
            if not (database / relative).is_file():
                raise ValueError("Pinned CRISPRCasFinder resource missing: " + relative)
        # Compile/load Perl imports; the adapter independently verifies the pinned script.
        runner.run(["perl", "-c", str(database / "CRISPRCasFinder.pl")], cwd=destination)
        runtime_checks = _probe_vmatch(database, destination, runner)
        provenance = {"schema": "basis-bundled-defense-resource-v1", "backend": backend,
                      "version": "4.3.2", "created_at": now(), "sources": SOURCES,
                      "sha256": _inventory(database), "supported_platform": "linux-x86_64",
                      "runtime_checks": runtime_checks,
                      "limitations": ["CASFinder3.1.0 models are CC BY-NC-SA4.0, not MIT.",
                                      "Vmatch is a separately licensed native dependency.",
                                      "Source/import checks do not establish end-to-end biological validation."]}
        updates = {"crisprcasfinder": {"database": str(database), "metadata": str(manifest),
                   "executable": "perl", "version": "4.3.2", "version_args": [],
                   "database_version": "CRISPRCasFinder4.3.2/CASFinder3.1.0"}}
    else:
        raise ValueError("Unsupported typing/defense bootstrap backend: " + str(backend))
    atomic_json(manifest, provenance)
    return {"module_updates": updates, "deep_updates": {}, "provenance": provenance}
