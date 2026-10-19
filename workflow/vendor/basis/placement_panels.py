"""Pinned, source-audited comparator panels for exploratory family placement.

Retrieval is automated; experimental function labels are not inferred from live
database annotations. Only registry-reviewed experimental evidence qualifies an
anchor. Sequence duplicates never count as independent reference support.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .candidate_labels import qualification_reason


EXPERIMENTAL_BASES = {"experimental", "sequence_linked_experimental"}
ROLES = {"amr", "alternative", "context"}
PROTEIN = re.compile(r"[ACDEFGHIKLMNPQRSTVWYBXZJUO]+")
HEX_SHA = re.compile(r"[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]+")
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024


class PanelResourceError(RuntimeError):
    """A reference could not be retrieved or failed its pinned integrity check."""


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_sequence(sequence: str) -> tuple[str, dict]:
    """Normalize case and one terminal stop; reject internal/repeated stops."""
    if not isinstance(sequence, str) or not sequence:
        raise ValueError("Reference protein sequence must be a nonempty string")
    normalized = sequence.upper()
    terminal_stop = normalized.endswith("*")
    if terminal_stop:
        normalized = normalized[:-1]
    if not PROTEIN.fullmatch(normalized):
        raise ValueError("Reference is not an unaligned protein with at most one terminal stop")
    return normalized, {"terminal_stop_removed": terminal_stop,
                        "case_normalized": normalized != sequence.rstrip("*")}


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _alias(value: str) -> str:
    return " ".join(value.casefold().split())


def validate_registry(data: dict) -> dict:
    """Validate labels, immutable sequence identities and comparator contrasts."""
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Placement registry needs schema_version: 1")
    _text(data.get("registry_id"), "registry_id")
    if not isinstance(data.get("panels"), list):
        raise ValueError("Placement registry panels must be a list")
    result = deepcopy(data)
    seen_panels = set()
    for panel in result["panels"]:
        if not isinstance(panel, dict):
            raise ValueError("Placement panel must be a mapping")
        key = _text(panel.get("panel_id"), "panel_id")
        if not IDENTIFIER.fullmatch(key) or key in seen_panels:
            raise ValueError(f"Invalid or duplicate panel_id: {key}")
        seen_panels.add(key)
        aliases = panel.get("candidate_aliases")
        if not isinstance(aliases, list) or not aliases or any(
                not isinstance(x, str) or not x.strip() for x in aliases):
            raise ValueError(f"Panel {key} needs candidate_aliases")
        if len({_alias(x) for x in aliases}) != len(aliases):
            raise ValueError(f"Panel {key} has duplicate candidate aliases")
        groups = panel.get("groups")
        if not isinstance(groups, list) or not groups:
            raise ValueError(f"Panel {key} needs group definitions")
        group_map = {}
        for group in groups:
            if not isinstance(group, dict):
                raise ValueError("Placement group must be a mapping")
            name = _text(group.get("group_id"), "group_id")
            if name in group_map or group.get("role") not in ROLES:
                raise ValueError(f"Invalid or duplicate group: {name}")
            if not isinstance(group.get("required", True), bool):
                raise ValueError("Group required must be Boolean")
            group.setdefault("required", True)
            if group["role"] == "context" and group["required"]:
                raise ValueError("Context groups cannot satisfy required functional comparisons")
            group_map[name] = group
        required_roles = {g["role"] for g in groups if g["required"]}
        if not {"amr", "alternative"}.issubset(required_roles):
            raise ValueError(f"Panel {key} needs required AMR and alternative-function groups")
        minimum = panel.setdefault("min_anchors_per_group", 2)
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 2:
            raise ValueError("min_anchors_per_group must be an integer of at least 2")
        references = panel.get("references")
        if not isinstance(references, list):
            raise ValueError(f"Panel {key} references must be a list")
        seen_accessions = set()
        for ref in references:
            if not isinstance(ref, dict):
                raise ValueError("Placement reference must be a mapping")
            accession = _text(ref.get("accession"), "reference accession")
            if accession in seen_accessions:
                raise ValueError(f"Duplicate accession {accession} in panel {key}")
            seen_accessions.add(accession)
            if not isinstance(ref.get("sequence_sha256"), str) or not HEX_SHA.fullmatch(ref["sequence_sha256"]):
                raise ValueError(f"Reference {accession} needs a pinned SHA256")
            if ref.get("role") not in ROLES:
                raise ValueError(f"Reference {accession} has invalid role")
            declared_group = ref.get("group")
            if ref["role"] != "context":
                if declared_group not in group_map or group_map[declared_group]["role"] != ref["role"]:
                    raise ValueError(f"Reference {accession} has incompatible group and role")
            elif declared_group is not None and declared_group not in group_map:
                raise ValueError(f"Reference {accession} has unknown context group")
            if ref.get("review_status") not in {"source_reviewed", "unreviewed"}:
                raise ValueError(f"Reference {accession} needs an explicit review_status")
            _text(ref.get("evidence_basis"), "evidence_basis")
            pubmed = ref.get("pubmed_ids", [])
            if not isinstance(pubmed, list) or any(
                    not isinstance(x, str) or not re.fullmatch(r"[1-9][0-9]*", x) for x in pubmed):
                raise ValueError(f"Reference {accession} pubmed_ids must be a list of numeric strings")
            ref.setdefault("pubmed_ids", [])
            if "sequence" in ref:
                normalized, _ = normalize_sequence(ref["sequence"])
                if _digest(normalized.encode("ascii")) != ref["sequence_sha256"]:
                    raise ValueError(f"Pinned sequence hash mismatch: {accession}")
            elif "source_url" in ref:
                _download_url(ref)
            else:
                # An accession plus checksum can be resolved locally or from
                # UniProt; the endpoint still undergoes strict validation.
                _download_url(ref)
    return result


def load_registry(path: str | Path) -> dict:
    raw = Path(path).read_bytes()
    result = validate_registry(json.loads(raw.decode("utf-8")))
    result["_registry_content_sha256"] = _digest(_canonical(result))
    result["_registry_sha256"] = _digest(raw)
    result["_registry_path"] = str(Path(path).resolve())
    return result


def _download_url(ref: dict) -> str:
    accession = ref["accession"]
    if re.fullmatch(r"[A-Z]{1,5}_[A-Z0-9]+\.[1-9][0-9]*", accession):
        expected = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urlencode({
            "db": "protein", "id": accession, "rettype": "fasta", "retmode": "text"})
    elif re.fullmatch(r"[A-Z0-9]+(?:-[0-9]+)?", accession):
        expected = f"https://rest.uniprot.org/uniprotkb/{accession}.fasta"
    else:
        raise ValueError(f"Reference {accession} requires an embedded sequence or supported accession")
    url = ref.get("source_url", expected)
    if url != expected:
        raise ValueError(f"Reference download URL must be the official exact accession FASTA endpoint: {accession}")
    return url


def _parse_download(raw: bytes, accession: str) -> str:
    text = raw.decode("utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or not lines[0].startswith(">") or sum(x.startswith(">") for x in lines) != 1:
        raise PanelResourceError(f"Expected one FASTA record for {accession}")
    identifier = lines[0][1:].split()[0]
    tokens = identifier.split("|")
    if identifier != accession and not (len(tokens) >= 3 and tokens[1] == accession):
        raise PanelResourceError(f"Downloaded FASTA accession does not match {accession}")
    return "".join(lines[1:])


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".panel-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(_canonical(data) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _verify(sequence: str, ref: dict) -> tuple[str, dict]:
    normalized, audit = normalize_sequence(sequence)
    if _digest(normalized.encode("ascii")) != ref["sequence_sha256"]:
        raise PanelResourceError(f"Pinned reference sequence hash mismatch: {ref['accession']}")
    return normalized, audit


def _sequence(ref: dict, cache: Path, catalogue, allow_download: bool):
    if "sequence" in ref:
        normalized, audit = _verify(ref["sequence"], ref)
        return normalized, {"source": "registry_embedded", "normalization": audit}
    cache_file = cache / "sequences" / (ref["sequence_sha256"] + ".json")
    if cache_file.exists():
        try:
            saved = json.loads(cache_file.read_text(encoding="utf-8"))
            if saved.get("schema_version") != 1 or saved.get("sequence_sha256") != ref["sequence_sha256"]:
                raise ValueError("Invalid cached sequence metadata")
            normalized, audit = _verify(saved["sequence"], ref)
            return normalized, {"source": "verified_sequence_cache", "path": str(cache_file),
                                "normalization": audit}
        except (ValueError, KeyError, TypeError) as exc:
            raise PanelResourceError(f"Corrupt placement reference cache: {cache_file}") from exc
    if catalogue is not None:
        for reference_id, sequence in catalogue.sequences.items():
            try:
                normalized, audit = normalize_sequence(sequence)
            except ValueError:
                continue
            if _digest(normalized.encode("ascii")) == ref["sequence_sha256"]:
                _atomic_json(cache_file, {"schema_version": 1, "sequence": normalized,
                                         "sequence_sha256": ref["sequence_sha256"]})
                return normalized, {"source": "verified_local_catalogue", "reference_id": reference_id,
                                    "normalization": audit}
    if not allow_download:
        return None, {"source": "unavailable", "reason": "download_disabled_and_reference_not_local"}
    url = _download_url(ref)
    try:
        with urlopen(Request(url, headers={"User-Agent": "BaSIS-pinned-reference-retrieval/1"}), timeout=60) as response:
            # No untrusted redirection, query sequence submission or alternate
            # live annotation endpoint participates in label assignment.
            if response.geturl() != url:
                raise PanelResourceError(f"Unexpected reference download redirect: {ref['accession']}")
            raw = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(raw) > MAX_DOWNLOAD_BYTES:
                raise PanelResourceError("Reference response exceeds the allowed FASTA size")
        normalized, audit = _verify(_parse_download(raw, ref["accession"]), ref)
    except PanelResourceError:
        raise
    except Exception as exc:
        raise PanelResourceError(f"Failed to retrieve pinned reference {ref['accession']}: {exc}") from exc
    _atomic_json(cache_file, {"schema_version": 1, "sequence": normalized,
                             "sequence_sha256": ref["sequence_sha256"], "source_url": url})
    return normalized, {"source": "pinned_official_download", "source_url": url,
                        "raw_sha256": _digest(raw), "normalization": audit}


def _route(candidate: dict, registry: dict) -> tuple[list[dict], dict]:
    labels = []
    for finding in candidate.get("source_evidence") or []:
        if qualification_reason(finding):
            labels.extend(finding.get(field) for field in ("gene", "family") if finding.get(field))
    # Persisted source-qualified metadata is usable when the original source
    # rows are absent. An unqualified gene string is never a routing authority.
    if not candidate.get("source_evidence"):
        provenance = candidate.get("label_provenance") or {}
        if provenance.get("policy") == "qualified_source_labels_v1" and provenance.get("qualification"):
            labels.extend(candidate.get(field) for field in ("gene", "family") if candidate.get(field))
    aliases = {_alias(x) for x in labels if isinstance(x, str)}
    panels = [p for p in registry["panels"] if aliases.intersection(_alias(x) for x in p["candidate_aliases"])]
    return panels, {"policy": "exact_qualified_source_aliases_v1", "qualified_labels": sorted(set(labels)),
                    "matched_panels": [p["panel_id"] for p in panels]}


def prepare_panel(candidate: dict, registry: dict, cache_dir: str | Path,
                  catalogue=None, allow_download: bool = True, *, panel_id: str | None = None) -> dict:
    """Build one immutable reference panel, or return explicit scientific holds.

    Missing local resources with downloads disabled produce an adequacy hold.
    Failed downloads and checksum violations raise PanelResourceError; they must
    be reported as operational failures, never as a biological negative result.
    Explicit ``panel_id`` selects an exact registry entry for panel-oriented
    work such as internal seed benchmarks. This bypasses candidate alias routing
    and records that no candidate qualification was assessed by this selection.
    """
    if not isinstance(allow_download, bool):
        raise ValueError("allow_download must be Boolean")
    checked = validate_registry(registry)
    content_sha = _digest(_canonical({k: v for k, v in checked.items() if not k.startswith("_")}))
    if registry.get("_registry_content_sha256") not in {None, content_sha}:
        raise ValueError("Loaded placement registry was modified after validation")
    registry_sha = registry.get("_registry_sha256") or _digest(_canonical({
        k: v for k, v in checked.items() if not k.startswith("_")}))
    if panel_id is None:
        panels, routing = _route(candidate, checked)
    else:
        _text(panel_id, "explicit panel_id")
        panels = [p for p in checked["panels"] if p["panel_id"] == panel_id]
        if len(panels) != 1:
            raise ValueError(f"Explicit panel_id must exactly match one registry panel: {panel_id}")
        routing = {"policy": "explicit_registry_panel_selection_v1",
                   "requested_panel_id": panel_id, "matched_panels": [panel_id],
                   "candidate_qualification": "not_evaluated_by_explicit_panel_selection"}
    base = {"panel_id": None, "panel_sha256": None, "members": [],
            "adequacy": {"eligible": False, "reasons": []},
            "provenance": {"registry_id": checked["registry_id"], "registry_sha256": registry_sha,
                           "routing": routing, "retrievals": [],
                           "label_policy": "source_reviewed_experimental_evidence_only_v1",
                           "function_probability": None}}
    if len(panels) != 1:
        base["adequacy"]["reasons"] = ["no_supported_family_panel" if not panels else "ambiguous_family_routing"]
        return base
    panel = panels[0]
    base["panel_id"] = panel["panel_id"]
    base["provenance"]["panel_definition"] = {
        key: deepcopy(value) for key, value in panel.items() if key != "references"}
    cache = Path(cache_dir)
    grouped = defaultdict(list)
    missing = []
    for ref in panel["references"]:
        seq, retrieval = _sequence(ref, cache, catalogue, allow_download)
        retrieval["accession"] = ref["accession"]
        base["provenance"]["retrievals"].append(retrieval)
        if seq is None:
            missing.append(ref["accession"])
            continue
        qualifies = (ref["role"] != "context" and ref["review_status"] == "source_reviewed"
                     and ref["evidence_basis"] in EXPERIMENTAL_BASES and bool(ref["pubmed_ids"]))
        evidence = {k: deepcopy(v) for k, v in ref.items() if k != "sequence"}
        evidence["qualifies_as_functional_anchor"] = qualifies
        if not qualifies:
            evidence["label_use"] = "context_only_no_negative_function_inference"
        grouped[ref["sequence_sha256"]].append((seq, ref, qualifies, evidence))
    conflicts = []
    counts = {g["group_id"]: 0 for g in panel["groups"]}
    for sha, duplicates in sorted(grouped.items()):
        qualified_labels = {(ref["group"], ref["role"]) for _, ref, valid, _ in duplicates if valid}
        if len(qualified_labels) > 1:
            conflicts.append({"sequence_sha256": sha, "labels": [list(x) for x in sorted(qualified_labels)],
                              "accessions": sorted(row[1]["accession"] for row in duplicates)})
        group, role = next(iter(qualified_labels)) if len(qualified_labels) == 1 else (None, "context")
        if group is not None:
            counts[group] += 1
        base["members"].append({"tip_id": "P" + sha, "sequence": duplicates[0][0],
                                "sequence_sha256": sha, "group": group, "role": role,
                                "accessions": sorted(row[1]["accession"] for row in duplicates),
                                "evidence": [row[3] for row in duplicates],
                                "label_conflict": len(qualified_labels) > 1})
    reasons = []
    if missing:
        reasons.append("pinned_reference_resources_unavailable")
    if conflicts:
        reasons.append("identical_sequences_have_conflicting_function_labels")
    required = [g["group_id"] for g in panel["groups"] if g["required"]]
    for group in required:
        if counts[group] < panel["min_anchors_per_group"]:
            reasons.append(f"insufficient_distinct_experimental_anchors:{group}")
    if len(base["members"]) < 4:
        reasons.append("fewer_than_four_distinct_reference_sequences")
    base["adequacy"] = {"eligible": not reasons, "reasons": reasons,
                         "distinct_qualified_anchors_by_group": counts,
                         "required_groups": required,
                         "min_anchors_per_group": panel["min_anchors_per_group"],
                         "raw_reference_records": len(panel["references"]),
                         "distinct_sequences": len(base["members"]),
                         "missing_accessions": missing, "conflicting_sequences": conflicts,
                         "interpretation": "Reference adequacy is a minimum comparison gate, not classifier validation."}
    identity = {"schema_version": 1, "registry_sha256": registry_sha,
                "panel_id": panel["panel_id"], "members": base["members"],
                "adequacy": base["adequacy"]}
    base["panel_sha256"] = _digest(_canonical(identity))
    manifest = cache / "panels" / base["panel_sha256"] / "panel.json"
    # Content-addressed panel identity excludes transient acquisition locations;
    # the separate provenance records how each pinned sequence was obtained.
    if manifest.exists():
        try:
            existing = json.loads(manifest.read_text(encoding="utf-8"))
            if existing != identity:
                raise ValueError("Content-addressed panel manifest mismatch")
        except (ValueError, TypeError) as exc:
            raise PanelResourceError(f"Corrupt placement panel manifest: {manifest}") from exc
    else:
        _atomic_json(manifest, identity)
    base["provenance"]["panel_manifest"] = str(manifest.resolve())
    return base
