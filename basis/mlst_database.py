"""Validate the compiled database prefix used by the pinned MLST adapter."""
from pathlib import Path


def verify_mlst_database(database, runner, *, cwd):
    """Require scheme profiles and ask native BLAST to open its nucleotide DB.

    MLST 2.35.0 ships blast/mlst.fa.n* indices, without blast/mlst.fa itself.
    The three core index files are shared by its BLAST v4/v5 layouts; the
    native probe also checks that the installed BLAST can open the database.
    This is a software readiness check, not biological validation.
    """
    database = Path(database).resolve()
    schemes = database / "pubmlst"
    if not schemes.is_dir() or not any(
        path.is_file() and path.name == path.parent.name + ".txt" and path.stat().st_size
        for path in schemes.glob("*/*.txt")
    ):
        raise ValueError("MLST database has no nonempty PubMLST scheme definitions")
    prefix = database / "blast/mlst.fa"
    for suffix in ("nhr", "nin", "nsq"):
        index = Path(str(prefix) + "." + suffix)
        if not index.is_file() or not index.stat().st_size:
            raise ValueError("MLST database is missing a nonempty BLAST index: " + str(index))
    response = runner.run(["blastdbcmd", "-db", str(prefix), "-dbtype", "nucl", "-info"], cwd=cwd)
    if response.returncode:
        raise ValueError("MLST BLAST database could not be opened: " + str(prefix))
    return prefix
