"""Local, provenance-preserving reference catalogues for exploratory DeepSafe.

Similarity thresholds here retrieve comparators; they are not functional cutoffs.
No reference sequence or reviewed label is bundled with BaSIS.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yaml


PROTEIN_ALPHABET = re.compile(r"^[ACDEFGHIKLMNPQRSTVWYBXZJUO*]+$")
CATALOG_FIELDS = (
    "reference_id", "accession", "family", "comparison_group", "role",
    "review_status", "source", "sequence_sha256", "retrieved_at",
)


def read_fasta(path: Path) -> dict[str, str]:
    """Read unaligned FASTA, rejecting duplicate identifiers and empty records."""
    result: dict[str, str] = {}
    identifier = None
    chunks: list[str] = []

    def finish():
        if identifier is None:
            return
        sequence = "".join(chunks).upper()
        if not sequence:
            raise ValueError(f"Empty FASTA record {identifier!r} in {path}")
        result[identifier] = sequence

    with Path(path).open(encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                finish()
                words = line[1:].split()
                if not words:
                    raise ValueError(f"Empty FASTA identifier at {path}:{lineno}")
                identifier = words[0]
                if identifier in result:
                    raise ValueError(f"Duplicate FASTA identifier {identifier!r} in {path}")
                chunks = []
            else:
                if identifier is None:
                    raise ValueError(f"Sequence before FASTA header at {path}:{lineno}")
                chunks.append("".join(line.split()))
    finish()
    if not result:
        raise ValueError(f"No FASTA records in {path}")
    return result


def sequence_hash(sequence: str) -> str:
    return hashlib.sha256(sequence.upper().encode("ascii")).hexdigest()


def write_fasta(records: dict[str, str], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for identifier, sequence in records.items():
            if not identifier or re.search(r"\s", identifier):
                raise ValueError("FASTA identifiers must be nonempty and contain no whitespace")
            handle.write(f">{identifier}\n")
            for pos in range(0, len(sequence), 80):
                handle.write(sequence[pos:pos + 80] + "\n")


@dataclass
class ReferenceCatalog:
    sequences: dict[str, str]
    metadata: dict[str, dict]
    fasta: Path
    metadata_path: Path
    warnings: list[str]

    def manifest(self, reference_id: str) -> dict:
        row = dict(self.metadata[reference_id])
        row["sequence_sha256"] = sequence_hash(self.sequences[reference_id])
        row["length"] = len(self.sequences[reference_id])
        return row


def load_catalog(fasta: Path, metadata_tsv: Path) -> ReferenceCatalog:
    sequences = read_fasta(Path(fasta))
    for key, seq in sequences.items():
        if not PROTEIN_ALPHABET.fullmatch(seq):
            raise ValueError(f"Reference {key!r} is not an unaligned protein sequence")
    with Path(metadata_tsv).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"reference_id", "accession", "family", "comparison_group", "role", "review_status", "source"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Reference metadata missing columns: " + ", ".join(sorted(required - set(reader.fieldnames or []))))
        metadata = {}
        warnings = []
        for row in reader:
            if None in row:
                raise ValueError("Reference metadata has excess TSV fields")
            row = {key: (value or "").strip() for key, value in row.items()}
            ref = row["reference_id"]
            if not ref or ref in metadata:
                raise ValueError(f"Missing or duplicate reference_id {ref!r}")
            if ref not in sequences:
                raise ValueError(f"Metadata reference {ref!r} is absent from FASTA")
            if row["role"] not in {"reference", "anchor", "outgroup"}:
                raise ValueError(f"Invalid reference role for {ref!r}")
            if row["review_status"] not in {"reviewed", "unreviewed", "rejected"}:
                raise ValueError(f"Invalid review_status for {ref!r}")
            if not row["accession"] or not row["source"]:
                raise ValueError(f"Reference {ref!r} needs accession and source provenance")
            checksum = sequence_hash(sequences[ref])
            if row.get("sequence_sha256") and row["sequence_sha256"] != checksum:
                raise ValueError(f"Reference sequence hash mismatch for {ref!r}")
            row["sequence_sha256"] = checksum
            if not re.search(r"\.\d+$", row["accession"]):
                warnings.append(f"Reference {ref}: accession is not versioned; SHA256 identifies this sequence.")
            metadata[ref] = row
    missing = set(sequences) - set(metadata)
    if missing:
        raise ValueError("FASTA references missing metadata: " + ", ".join(sorted(missing)))
    return ReferenceCatalog(sequences, metadata, Path(fasta), Path(metadata_tsv), warnings)


def validate_profiles(directory: Path | None) -> tuple[list[dict], list[str]]:
    """Load a registry; invalid profiles are rejected, never silently activated."""
    if directory is None:
        return [], []
    directory = Path(directory)
    if not directory.is_dir():
        return [], [f"Profile directory does not exist: {directory}"]
    profiles, errors, seen = [], [], set()
    for path in sorted(set(directory.glob("*.yaml")) | set(directory.glob("*.yml"))):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != 1:
                raise ValueError("expected schema_version: 1 mapping")
            key = data.get("profile_id")
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", key) or key in seen:
                raise ValueError("invalid or duplicate profile_id")
            state = data.get("validation_status")
            if state not in {"unvalidated_template", "reviewed", "validated"}:
                raise ValueError("invalid validation_status")
            for field in ("candidate_aliases", "required_comparison_groups", "anchor_reference_ids"):
                if not isinstance(data.get(field), list) or any(not isinstance(x, str) or not x for x in data[field]):
                    raise ValueError(f"{field} must be a list of nonempty strings")
            if not data["candidate_aliases"]:
                raise ValueError("candidate_aliases must not be empty")
            if state in {"reviewed", "validated"}:
                review = data.get("review", {})
                if not all(review.get(k) for k in ("reviewer", "date", "evidence")):
                    raise ValueError("reviewed/validated profile requires reviewer, date, evidence")
                if not data["required_comparison_groups"] or not data["anchor_reference_ids"]:
                    raise ValueError("reviewed/validated profile requires comparison groups and explicit anchors")
            if data.get("interpretation_policy") != "exploratory_only":
                raise ValueError("BaSIS 0.1 accepts only interpretation_policy: exploratory_only")
            if "analyses" in data and (not isinstance(data["analyses"], list) or any(
                    x not in {"integrity", "context", "references", "domains", "phylogeny"} for x in data["analyses"])):
                raise ValueError("analyses must list supported integrity/context/references/domains/phylogeny steps")
            resources = data.get("resources", {})
            if not isinstance(resources, dict):
                raise ValueError("resources must be a mapping")
            domain = resources.get("domains", {})
            if isinstance(domain, str):
                domain = {"database": domain}
            if not isinstance(domain, dict) or domain.get("native_cutoff") not in {None, "ga", "tc", "nc"}:
                raise ValueError("resources.domains must configure a database and optional native cutoff ga/tc/nc")
            if domain.get("database") is not None and not isinstance(domain["database"], str):
                raise ValueError("Domain resource database must be a path string")
            data["registry_path"] = str(path.resolve())
            data["registry_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            profiles.append(data)
            seen.add(key)
        except (ValueError, TypeError, yaml.YAMLError, OSError) as exc:
            errors.append(f"{path.name}: {exc}")
    return profiles, errors


def profile_for_candidate(candidate: dict, profiles: list[dict]) -> dict:
    names = {str(candidate.get(key) or "").casefold() for key in ("gene", "family")}
    names.update(str(value).casefold() for value in candidate.get("family_labels", []))
    matches = [p for p in profiles if names.intersection(x.casefold() for x in p["candidate_aliases"])]
    if len(matches) == 1:
        return matches[0]
    return {
        "schema_version": 1, "profile_id": "generic", "validation_status": "unsupported",
        "candidate_aliases": [], "required_comparison_groups": [], "anchor_reference_ids": [],
        "interpretation_policy": "exploratory_only",
        "reason": "multiple_matching_profiles" if matches else "no_family_profile",
    }


def select_references(catalog: ReferenceCatalog, hits: list[dict], profile: dict,
                      candidate: dict, maximum: int = 30, metadata_only: bool = False) -> tuple[list[dict], list[str]]:
    """Combine ranked sequence hits with explicit anchors; do not deduplicate queries."""
    if maximum < 1:
        raise ValueError("max_references must be >= 1")
    notes = []
    selected: dict[str, dict] = {}
    eligible_ids = {h["reference_id"] for h in hits}
    for ref in profile.get("anchor_reference_ids", []):
        if ref not in catalog.metadata:
            notes.append(f"missing_anchor:{ref}")
            continue
        meta = catalog.metadata[ref]
        if meta["review_status"] != "reviewed":
            notes.append(f"unreviewed_anchor:{ref}")
            continue
        if profile.get("require_anchor_similarity") and ref not in eligible_ids:
            notes.append(f"anchor_without_eligible_whole_protein_hit:{ref}")
            continue
        selected[ref] = {**catalog.manifest(ref), "selection_reason": "profile_anchor",
                         "similarity": next((dict(h) for h in hits if h["reference_id"] == ref), None)}
    if len(selected) > maximum:
        return [], notes + ["anchor_set_exceeds_reference_budget"]
    ranked = sorted(hits, key=lambda h: (-h["bitscore"], h["evalue"], h["reference_id"]))
    if metadata_only:
        labels = {str(candidate.get("family") or "").casefold(), str(candidate.get("gene") or "").casefold()}
        labels.update(x.casefold() for x in profile.get("candidate_aliases", []))
        ranked = [{"reference_id": ref, "metadata_only": True} for ref, row in sorted(catalog.metadata.items())
                  if row["family"].casefold() in labels and row["family"]]
    for hit in ranked:
        ref = hit["reference_id"]
        if ref not in catalog.metadata:
            raise ValueError(f"Search reference {ref!r} absent from catalogue")
        if ref in selected or catalog.metadata[ref]["review_status"] == "rejected":
            continue
        if len(selected) >= maximum:
            notes.append("reference_budget_applied")
            break
        selected[ref] = {**catalog.manifest(ref), "selection_reason": "family_metadata" if metadata_only else "sequence_similarity",
                         "similarity": None if metadata_only else dict(hit)}
    actual_groups = {v["comparison_group"] for v in selected.values() if v["review_status"] == "reviewed"}
    for group in profile.get("required_comparison_groups", []):
        if group not in actual_groups:
            notes.append(f"missing_reviewed_comparison_group:{group}")
    if profile["validation_status"] not in {"reviewed", "validated"}:
        notes.append("unvalidated_family_profile")
    return list(selected.values()), list(dict.fromkeys(notes))


def _fetch_protein(accession: str) -> str:
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urlencode(
        {"db": "protein", "id": accession, "rettype": "fasta", "retmode": "text"})
    request = Request(url, headers={"User-Agent": "BaSIS-reference-preparer/0.1"})
    with urlopen(request, timeout=45) as response:
        return response.read(8_000_001).decode("utf-8")


def prepare_accession_catalog(requests_tsv: Path, out_fasta: Path, out_metadata: Path,
                              fetcher=None, maximum_accessions: int = 100) -> dict:
    """Explicit opt-in public protein retrieval; only accession IDs leave the machine.

    User assertions of family/group/review remain visible. Retrieval does not verify
    biological function. A failure raises before either final catalogue is written.
    An injectable fetcher(accession)->FASTA text makes this independently testable.
    """
    import tempfile
    with Path(requests_tsv).open(encoding="utf-8-sig", newline="") as handle:
        requests = list(csv.DictReader(handle, delimiter="\t"))
    if not requests or len(requests) > maximum_accessions:
        raise ValueError(f"Provide 1–{maximum_accessions} accession rows")
    fetcher = fetcher or _fetch_protein
    sequences, rows = {}, []
    timestamp = datetime.now(timezone.utc).isoformat()
    for row in requests:
        accession = (row.get("accession") or "").strip()
        if not re.fullmatch(r"[A-Za-z]{1,6}_?\d+(?:\.\d+)?", accession):
            raise ValueError(f"Not a public protein accession: {accession!r}")
        ref = (row.get("reference_id") or accession).strip()
        if ref in sequences or re.search(r"\s", ref):
            raise ValueError(f"Duplicate/invalid reference_id {ref!r}")
        content = fetcher(accession)
        if not isinstance(content, str) or len(content) > 8_000_000:
            raise ValueError(f"Invalid response size for {accession}")
        with tempfile.TemporaryDirectory(prefix="basis-ref-") as temporary:
            path = Path(temporary) / "response.faa"
            path.write_text(content, encoding="utf-8")
            fetched = read_fasta(path)
        if len(fetched) != 1:
            raise ValueError(f"Expected exactly one protein for {accession}")
        fetched_id, sequence = next(iter(fetched.items()))
        ids = fetched_id.split("|")
        matches = [x for x in ids if x.split(".")[0] == accession.split(".")[0]]
        if not matches or ("." in accession and accession not in matches):
            raise ValueError(f"Returned identifier {fetched_id!r} does not match {accession}")
        if not PROTEIN_ALPHABET.fullmatch(sequence):
            raise ValueError(f"Invalid protein sequence returned for {accession}")
        resolved = matches[0]
        sequences[ref] = sequence
        rows.append({"reference_id": ref, "accession": resolved, "family": row.get("family", ""),
                     "comparison_group": row.get("comparison_group", ""), "role": row.get("role") or "reference",
                     "review_status": row.get("review_status") or "unreviewed",
                     "source": f"https://www.ncbi.nlm.nih.gov/protein/{resolved}",
                     "sequence_sha256": sequence_hash(sequence), "retrieved_at": timestamp})
    for row in rows:
        if row["role"] not in {"reference", "anchor", "outgroup"} or row["review_status"] not in {"reviewed", "unreviewed", "rejected"}:
            raise ValueError("Invalid role or review_status in accession requests")
    write_fasta(sequences, Path(out_fasta))
    Path(out_metadata).parent.mkdir(parents=True, exist_ok=True)
    with Path(out_metadata).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CATALOG_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return {"reference_count": len(rows), "fasta": str(out_fasta), "metadata": str(out_metadata),
            "note": "Retrieved public protein sequences; biological labels remain user-supplied."}
