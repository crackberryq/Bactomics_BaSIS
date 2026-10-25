"""Bounded, family-aware follow-up of normalized safety-screen evidence.

DeepSafe produces inspectable sequence/context/phylogeny evidence. It never
converts similarity, a nearest tree tip, or an ML score into a safety verdict.
"""
from __future__ import annotations

from .progress import emit

import hashlib
import json
import math
import re
from pathlib import Path

from .deep_decisions import assess_candidate
from .deep_reporting import completion_summary
from .motifs import load_rules, assess_motifs
from .deep_trees import run_bounded, reuse_review
from .deep_alignment_review import normalize_alignment_sequence
from .candidate_labels import refresh_candidate_labels
from .references import (PROTEIN_ALPHABET, load_catalog, profile_for_candidate,
                         read_fasta, select_references, sequence_hash,
                         validate_profiles, write_fasta)


def _flatten(records: list[dict]):
    for index, record in enumerate(records):
        if "findings" in record:
            if record.get("execution_status", "completed") != "completed":
                continue
            for number, finding in enumerate(record.get("findings") or []):
                item = dict(finding)
                item.setdefault("tool", record.get("module") or record.get("tool", "unknown"))
                item.setdefault("finding_id", item.get("id") or f"record-{index}-finding-{number}")
                item["source_record_id"] = record.get("record_id") or record.get("id") or f"record-{index}"
                yield item
        else:
            item = dict(record)
            item.setdefault("finding_id", item.get("id") or f"finding-{index}")
            yield item


def _locus_key(finding: dict) -> tuple:
    # Coordinates join protein and nucleotide calls while retaining separate copies.
    if finding.get("contig") and finding.get("start") is not None and finding.get("end") is not None:
        return ("locus", str(finding["contig"]), int(finding["start"]), int(finding["end"]), finding.get("strand") or ".")
    if finding.get("protein_id"):
        return ("protein", str(finding["protein_id"]))
    return ("unlocalized", str(finding["finding_id"]))


def _is_exploratory(finding: dict) -> bool:
    attributes = finding.get("attributes") or {}
    # Native RGI labels remain authoritative even for imported normalized records.
    return finding.get("evidence_level") == "exploratory" or (
        finding.get("tool") == "rgi" and (
            str(attributes.get("cutoff") or "").casefold() == "loose" or
            str(attributes.get("nudged") or "").casefold() == "true"))


def taxonomy_review(candidate: dict, sample: dict | None = None) -> dict:
    """Conservative review flags, never a resistance or safety classifier.

    The named M. tuberculosis model has a declared organism context. Other
    variant models remain unresolved until explicit model scope is available.
    No taxonomy restriction is inferred for transferable homology models.
    """
    sample = sample or {}
    taxon = str(sample.get("taxon") or "").strip()
    checks = []
    for finding in candidate["source_evidence"]:
        attrs = finding.get("attributes") or {}
        if finding.get("tool") != "rgi" or str(attrs.get("model_type", "")).casefold() != "protein variant model":
            continue
        label = str(finding.get("gene") or "")
        declared = "Mycobacterium tuberculosis" if label == "Mycobacterium tuberculosis rpoC mutations confer resistance to rifampicin" else None
        if not declared or not taxon:
            status = "unresolved"
        elif taxon.casefold() == declared.casefold() or taxon.casefold().startswith(declared.casefold() + " "):
            status = "declared_name_matches_review_required"
        elif taxon.split()[0].casefold() != "mycobacterium":
            status = "outside_declared_genus_review_required"
        else:
            status = "unresolved"
        checks.append({"finding_id": finding["finding_id"], "declared_model_taxon": declared,
                       "status": status, "model_id": (attrs.get("raw") or {}).get("Model_ID")})
    return {"sample_taxon": taxon or None, "checks": checks,
            "status": "review_required" if checks else "not_evaluated",
            "note": "Name-based scope review only; unknown scope is not applicability, and no resistance phenotype is inferred."}


def evidence_priority(candidate: dict) -> dict:
    """Transparent triage heuristic; scores are not calibrated across databases."""
    pairs = []
    for finding in candidate["source_evidence"]:
        try:
            identity, coverage = float(finding["identity"]), float(finding["coverage"])
        except (KeyError, TypeError, ValueError):
            continue
        if all(math.isfinite(x) and 0 <= x <= 100 for x in (identity, coverage)):
            pairs.append(identity * coverage / 100)
    return {"tools": sorted({str(f.get("tool", "unknown")) for f in candidate["source_evidence"]}),
            "best_identity_coverage_product": max(pairs) if pairs else None,
            "policy": "tool_count_then_identity_times_reference_coverage; missing metrics last; stable ID breaks ties",
            "qualification": "Heuristic review order, not independent validation or a biological risk score."}


def select_candidates(records: list[dict], config: dict, sample: dict | None = None) -> list[dict]:
    from .aware import resolve_policy, select
    policy = resolve_policy(config)
    if policy["enabled"]:
        return select(records, config, sample, policy)
    return _select_candidates_legacy(records, config, sample)


def _select_candidates_legacy(records: list[dict], config: dict, sample: dict | None = None) -> list[dict]:
    budgets = {}
    for key, default, minimum in (("maximum_candidates", 5, 1),
                                  ("maximum_attention_candidates", 5, 0),
                                  ("maximum_exploratory_candidates", 20, 0)):
        value = config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{key} must be an integer >= {minimum}")
        budgets[key] = value
    maximum = budgets["maximum_candidates"]
    attention_maximum = budgets["maximum_attention_candidates"]
    exploratory_maximum = budgets["maximum_exploratory_candidates"]
    include_exploratory = config.get("include_exploratory", True)
    if not isinstance(include_exploratory, bool):
        raise ValueError("include_exploratory must be a boolean")
    include_stress = config.get("include_stress", False)
    if not isinstance(include_stress, bool):
        raise ValueError("include_stress must be a boolean")
    targeted_categories = {"amr", "virulence"} | ({"stress"} if include_stress else set())
    mixed_attention = config.get("attention_queue_for_mixed_evidence", False)
    if not isinstance(mixed_attention, bool):
        raise ValueError("attention_queue_for_mixed_evidence must be boolean")
    candidates = {}
    all_findings = list(_flatten(records))
    protein_loci = {}
    for finding in all_findings:
        key = _locus_key(finding)
        if finding.get("protein_id") and key[0] == "locus":
            protein_loci.setdefault(str(finding["protein_id"]), set()).add(key)
    for finding in all_findings:
        category = finding.get("category")
        attention = category == "ml_attention"
        targeted = category in targeted_categories and not _is_exploratory(finding)
        exploratory = not attention and _is_exploratory(finding) and (
            category in {"amr", "virulence"} or
            finding.get("protein_id") or (finding.get("gene") and finding.get("contig")))
        if not (targeted or attention or exploratory):
            continue
        key = _locus_key(finding)
        if key[0] == "protein" and len(protein_loci.get(key[1], set())) == 1:
            key = next(iter(protein_loci[key[1]]))
        if key not in candidates:
            digest = hashlib.sha256(json.dumps(key, separators=(",", ":")).encode()).hexdigest()[:16]
            candidates[key] = {"candidate_id": "cand_" + digest,
                **{k: finding.get(k) for k in ("gene", "family", "protein_id", "contig", "start", "end", "strand")},
                "finding_ids": [], "source_evidence": [], "selection_reasons": [], "family_labels": [],
                "priority": 99, "attention_rank": None, "attention_max": None,
                "execution": "pending", "interpretation": "inconclusive"}
        candidate = candidates[key]
        # Prefer usable annotation supplied by another call at the identical locus.
        for field in ("gene", "family", "protein_id", "strand", "contig", "start", "end"):
            if candidate.get(field) is None and finding.get(field) is not None:
                candidate[field] = finding[field]
        candidate["finding_ids"].append(finding["finding_id"])
        candidate["source_evidence"].append(finding)
        candidate["family_labels"].extend(str(finding[k]) for k in ("gene", "family") if finding.get(k))
        reason = ("exploratory_screen" if exploratory else "amr_screen" if category == "amr"
                  else "virulence_screen" if category == "virulence" else "stress_screen"
                  if category == "stress" else "ml_attention_followup")
        candidate["selection_reasons"].append(reason)
        candidate["priority"] = min(candidate["priority"], 0 if targeted else 2 if attention else 1)
        rank = (finding.get("attributes") or {}).get("rank") if attention else None
        if rank is not None:
            if isinstance(rank, bool) or int(rank) != float(rank) or int(rank) < 1:
                raise ValueError("ML attention rank must be a positive integer")
            candidate["attention_rank"] = min(candidate["attention_rank"] or int(rank), int(rank))
        score = (finding.get("attributes") or {}).get("attention_max") if attention else None
        if score is not None:
            score = float(score)
            if not math.isfinite(score) or score < 0:
                raise ValueError("ML attention maximum must be finite and nonnegative")
            candidate["attention_max"] = max(candidate["attention_max"] or 0, score)
    for key, candidate in candidates.items():
        candidate, _ = refresh_candidate_labels(candidate)
        candidates[key] = candidate
        if mixed_attention and candidate["priority"] != 0 and "ml_attention_followup" in candidate["selection_reasons"]:
            candidate["priority"] = 2
            candidate["attention_routing_policy"] = "explicit_attention_queue_for_mixed_exploratory_evidence"
        candidate["evidence_priority"] = evidence_priority(candidate)
        candidate["taxonomy_review"] = taxonomy_review(candidate, sample)
    def order(c):
        rank = c["evidence_priority"]
        quality = rank["best_identity_coverage_product"]
        return (c["priority"], c["attention_rank"] or float("inf"),
                -(c["attention_max"] or 0), -len(rank["tools"]),
                -(quality if quality is not None else -1), c["candidate_id"])
    ordered = sorted(candidates.values(), key=order)
    used = used_attention = used_exploratory = 0
    for candidate in ordered:
        for field in ("finding_ids", "selection_reasons", "family_labels"):
            candidate[field] = list(dict.fromkeys(candidate[field]))
        pure_attention = candidate["priority"] == 2
        pure_exploratory = candidate["priority"] == 1
        candidate["selection_queue"] = ("native_screen" if candidate["priority"] == 0 else
                                        "exploratory" if pure_exploratory else "ml_attention")
        if pure_exploratory and not include_exploratory:
            candidate.update(execution="deferred", reason="exploratory_followup_disabled", selected=False)
        elif (used >= maximum or (pure_attention and used_attention >= attention_maximum) or
              (pure_exploratory and used_exploratory >= exploratory_maximum)):
            candidate.update(execution="deferred", reason="candidate_resource_budget", selected=False)
        else:
            candidate["selected"] = True
            used += 1
            used_attention += int(pure_attention)
            used_exploratory += int(pure_exploratory)
    return ordered


def parse_similarity(path: Path, min_identity: float = 0, min_coverage: float = 0,
                     min_reference_coverage: float = 0, audit: list | None = None) -> dict[str, list[dict]]:
    """Parse 12-column BLAST/DIAMOND native evidence; empty existing file is valid."""
    thresholds = (min_identity, min_coverage, min_reference_coverage)
    if any(not math.isfinite(float(x)) or not 0 <= float(x) <= 100 for x in thresholds):
        raise ValueError("Retrieval identity/coverage thresholds must be percentages in [0,100]")
    if not Path(path).is_file():
        raise ValueError(f"Similarity search output missing: {path}")
    best = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) != 12:
                raise ValueError(f"Similarity output {path}:{line_number}: expected 12 columns")
            try:
                identity, length, qlen, slen = float(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])
                qs, qe, ss, se = map(int, parts[6:10])
                evalue, bitscore = map(float, parts[10:12])
                if not all(math.isfinite(x) for x in (identity, evalue, bitscore)) or not 0 <= identity <= 100:
                    raise ValueError("invalid identity/score")
                if min(qlen, slen, length) <= 0 or not (1 <= qs <= qe <= qlen and 1 <= ss <= se <= slen):
                    raise ValueError("invalid alignment coordinates/length")
                if evalue < 0 or bitscore < 0:
                    raise ValueError("negative score")
            except ValueError as exc:
                raise ValueError(f"Invalid similarity row {path}:{line_number}: {exc}") from exc
            qcov, scov = 100 * (qe - qs + 1) / qlen, 100 * (se - ss + 1) / slen
            row = {"query_id": parts[0], "reference_id": parts[1], "identity": identity,
                   "query_coverage": qcov, "reference_coverage": scov, "alignment_length": length,
                   "evalue": evalue, "bitscore": bitscore, "query_length": qlen, "reference_length": slen,
                   "query_start": qs, "query_end": qe, "reference_start": ss, "reference_end": se}
            reasons = []
            if identity < min_identity: reasons.append("identity_below_threshold")
            if qcov < min_coverage: reasons.append("query_coverage_below_threshold")
            if scov < min_reference_coverage: reasons.append("reference_coverage_below_threshold")
            if audit is not None:
                audit.append({**row, "line_number": line_number, "filter_reasons": reasons,
                              "passes_retrieval_filters": not reasons})
            if reasons:
                continue
            key = (parts[0], parts[1])
            if key not in best or (bitscore, -evalue) > (best[key]["bitscore"], -best[key]["evalue"]):
                best[key] = row
    grouped = {}
    for row in best.values():
        grouped.setdefault(row["query_id"], []).append(row)
    return grouped


def _extract_query(candidate: dict, proteins: dict[str, str]) -> tuple[str | None, str]:
    protein_id = candidate.get("protein_id")
    if protein_id and protein_id in proteins:
        sequence = proteins[protein_id]
        origin = "sample_protein_fasta"
    else:
        sequences = {str((f.get("attributes") or {}).get("protein_sequence") or "").upper()
                     for f in candidate["source_evidence"]}
        sequences.discard("")
        if len(sequences) > 1:
            raise ValueError("Conflicting supplied protein sequences at this candidate locus")
        if not sequences:
            return None, "no_linked_protein_sequence"
        sequence = sequences.pop()
        origin = "source_finding_protein_sequence"
    if not PROTEIN_ALPHABET.fullmatch(sequence):
        raise ValueError("Candidate query is not a valid unaligned protein sequence")
    return sequence, origin


def inspect_integrity(candidate: dict, sequence: str | None) -> dict:
    """Record native partial/disruption flags and literal sequence observations."""
    flags, source_flags = [], []
    for finding in candidate["source_evidence"]:
        attributes = finding.get("attributes") or {}
        native = attributes.get("flags") or []
        if isinstance(native, str):
            native = [native]
        source_flags.extend({"finding_id": finding["finding_id"], "flag": str(flag)} for flag in native)
        method = str(attributes.get("native_method") or "").upper()
        if "PARTIAL" in method:
            flags.append("native_partial_sequence")
        if "INTERNAL_STOP" in method:
            flags.append("native_internal_stop")
        if any(word in str(native).lower() for word in ("partial", "truncat", "frameshift", "internal_stop", "disrupt")):
            flags.append("native_integrity_concern")
    internal_stops = [i + 1 for i, value in enumerate((sequence or "")[:-1]) if value == "*"]
    if internal_stops:
        flags.append("query_internal_stop")
    return {"status": "completed" if sequence else "not_assessed", "flags": list(dict.fromkeys(flags)),
            "native_flags": source_flags, "internal_stop_positions": internal_stops,
            "terminal_stop_present": bool(sequence and sequence.endswith("*")),
            "ambiguous_residue_count": sum(x in "XBZJ" for x in sequence) if sequence else None,
            "note": "Sequence observations do not establish expression, activity or phenotypic resistance."}


def make_analysis_plan(candidate: dict, profile: dict, integrity: dict, config: dict) -> dict:
    sources = candidate["source_evidence"]
    categories = {f.get("category") for f in sources}
    native_categories = {f.get("category") for f in sources if not _is_exploratory(f)}
    direct = [f for f in sources if f.get("category") == "amr" and not _is_exploratory(f)]
    gene_labels = {str(f.get("gene")).casefold() for f in direct if f.get("gene")}
    exact_evidence = []
    for finding in direct:
        attributes = finding.get("attributes") or {}
        if finding.get("evidence_level") != "curated_model" or finding.get("identity") != 100 or finding.get("coverage") != 100:
            continue
        if finding.get("tool") == "amrfinder" and attributes.get("scope") == "core" and attributes.get("native_method") in {"EXACTP", "EXACTX", "ALLELEP", "ALLELEX"}:
            exact_evidence.append(finding["finding_id"])
        elif finding.get("tool") == "rgi" and str(attributes.get("cutoff")).casefold() == "perfect" and not attributes.get("nudged") and str(attributes.get("model_type")).casefold() == "protein homolog model":
            exact_evidence.append(finding["finding_id"])
    exact = bool(exact_evidence) and not integrity["flags"] and "virulence" not in categories and len(gene_labels) <= 1
    if exact and not config.get("investigate_exact_calls", False):
        route = "exact_curated_amr_context_review"
    elif "amr" in native_categories:
        route = "ambiguous_amr_family_investigation"
    elif "virulence" in native_categories:
        route = "virulence_homology_investigation"
    elif categories == {"ml_attention"} or candidate.get("selection_queue") == "ml_attention":
        route = "bounded_ml_attention_exploration"
    else:
        route = "exploratory_family_investigation"
    investigate = route != "exact_curated_amr_context_review"
    profile_analyses = profile.get("analyses")
    allowed = set(profile_analyses) if profile_analyses is not None else {"integrity", "context", "references", "domains", "phylogeny"}
    trace = [{"decision": "taxonomy_review", "value": candidate.get("taxonomy_review", {"status": "not_evaluated"})},
             {"decision": "route", "value": route, "exact_source_finding_ids": exact_evidence,
              "source_gene_labels": sorted(gene_labels), "integrity_flags": integrity["flags"],
              "reason": "Native exact/Perfect full-reference match permits context review without redundant phylogeny; phenotype remains unresolved."
                        if not investigate else "No unambiguous complete exact-call shortcut; investigate available family evidence."},
             {"decision": "profile", "value": profile["profile_id"], "validation_status": profile["validation_status"],
              "reason": "All profiles remain exploratory-only in BaSIS 0.1."}]
    return {"route": route, "analyses": {"integrity": True, "context": True,
            "references": investigate and "references" in allowed,
            "domains": investigate and "domains" in allowed and config.get("domain_scan", True) is not False,
            "phylogeny": investigate and "phylogeny" in allowed and bool(config.get("phylogeny", False))},
            "decision_trace": trace}


def parse_domtbl(path: Path, expected_query: str | None = None) -> list[dict]:
    """Strict HMMER3 hmmscan domain-table parser, including completion evidence."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"HMMER domain output missing: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not any(re.match(r"#\s*Program:\s*hmmscan\s*$", line) for line in lines) or not any(line.strip() == "# [ok]" for line in lines):
        raise ValueError("HMMER domain table lacks hmmscan program/completion markers")
    hits = []
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split(maxsplit=22)
        if len(fields) < 22:
            raise ValueError(f"Malformed domain table row {number}: expected >=22 fields")
        try:
            tlen, qlen, domain_number, domain_total = (int(fields[i]) for i in (2, 5, 9, 10))
            hstart, hend, qstart, qend, estart, eend = (int(fields[i]) for i in range(15, 21))
            numeric = {i: float(fields[i]) for i in (6, 7, 8, 11, 12, 13, 14, 21)}
            if not all(math.isfinite(x) for x in numeric.values()) or any(numeric[i] < 0 for i in (6, 8, 11, 12, 14)):
                raise ValueError("invalid domain numeric values")
            if not (1 <= domain_number <= domain_total and 1 <= hstart <= hend <= tlen and 1 <= estart <= qstart <= qend <= eend <= qlen and 0 <= numeric[21] <= 1):
                raise ValueError("invalid domain bounds")
        except ValueError as exc:
            raise ValueError(f"Malformed domain table row {number}: {exc}") from exc
        if expected_query and fields[3] != expected_query:
            raise ValueError(f"Unexpected domain query ID {fields[3]!r}")
        hits.append({"domain_name": fields[0], "domain_accession": None if fields[1] == "-" else fields[1],
                     "query_id": fields[3], "model_length": tlen, "query_length": qlen,
                     "full_evalue": numeric[6], "full_score": numeric[7], "conditional_evalue": numeric[11],
                     "independent_evalue": numeric[12], "domain_score": numeric[13], "domain_number": domain_number,
                     "domain_total": domain_total, "hmm_start": hstart, "hmm_end": hend,
                     "query_start": qstart, "query_end": qend, "envelope_start": estart, "envelope_end": eend,
                     "alignment_accuracy": numeric[21], "description": fields[22] if len(fields) > 22 else ""})
    return hits


def pfam_ga_thresholds(database: Path) -> dict:
    """Read both curated GA scores; reject incomplete/ambiguous models."""
    thresholds, model = {}, {}
    with database.open(encoding="utf-8") as handle:
        for line in handle:
            parts = line.split()
            if not parts: continue
            if parts[0] in {"NAME", "ACC"}: model[parts[0]] = parts[1]
            elif parts[0] == "GA":
                values = [float(x.rstrip(";")) for x in parts[1:3]]
                if len(values) != 2 or not all(math.isfinite(x) for x in values):
                    raise ValueError("Invalid GA thresholds")
                model["GA"] = values
            elif parts[0] == "//":
                if "NAME" not in model or "GA" not in model:
                    raise ValueError("Every model must have NAME and both GA thresholds")
                for key in {model["NAME"], model.get("ACC", model["NAME"])}:
                    if key in thresholds: raise ValueError("Duplicate HMM model identifier")
                    thresholds[key] = model["GA"]
                model = {}
    if model or not thresholds: raise ValueError("Incomplete/empty HMM database")
    return thresholds


def filter_domains_ga(hits: list[dict], thresholds: dict) -> tuple[list, list]:
    accepted, rejected = [], []
    for hit in hits:
        cutoff = thresholds.get(hit.get("domain_accession")) or thresholds.get(hit["domain_name"])
        if cutoff is None: raise ValueError("No GA threshold for reported domain")
        reasons = []
        if hit["full_score"] < cutoff[0]: reasons.append("sequence_score_below_GA")
        if hit["domain_score"] < cutoff[1]: reasons.append("domain_score_below_GA")
        row = {**hit, "ga_sequence_threshold": cutoff[0], "ga_domain_threshold": cutoff[1],
               "filter_reasons": reasons, "passes_ga": not reasons}
        (rejected if reasons else accepted).append(row)
    return accepted, rejected


def summarize_domain_architecture(hits: list[dict]) -> dict:
    """Group overlapping accepted annotations without inventing physical domains.

    Any shared query residue joins a connected component. Alternative, nested,
    and chained models remain visible; a representative is for display only.
    Nonoverlapping repeat copies remain distinct even for the same model.
    """
    ordered = sorted((dict(h) for h in hits), key=lambda h: (
        h["query_start"], h["query_end"], h["domain_name"], h.get("domain_accession") or "",
        h.get("independent_evalue", float("inf")), -h.get("domain_score", 0)))
    groups = []
    for hit in ordered:
        a,b=hit["query_start"],hit["query_end"]
        if not isinstance(a,int) or not isinstance(b,int) or not 1 <= a <= b:
            raise ValueError("Invalid domain annotation coordinates")
        if not groups or a > groups[-1]["end"]:
            groups.append({"start":a,"end":b,"annotations":[]})
        group=groups[-1];group["end"]=max(group["end"],b);group["annotations"].append(hit)
    for index,group in enumerate(groups,1):
        annotations=group["annotations"]
        best=min(annotations,key=lambda h:(h.get("independent_evalue",float("inf")),
                 -h.get("domain_score",0),h["domain_name"],h["query_start"],h["query_end"]))
        group.update(region_index=index, representative_label=best["domain_name"],
                     model_labels=sorted({h["domain_name"] for h in annotations}),
                     annotation_count=len(annotations),
                     status="overlapping_models_unresolved" if len(annotations)>1 else "single_annotation")
    return {"status":"completed", "accepted_hit_count":len(hits), "overlap_region_count":len(groups),
            "ambiguous_region_count":sum(g["annotation_count"]>1 for g in groups),"regions":groups,
            "policy":"Connected query-interval overlap; representative ordered by independent E-value, score, then label.",
            "limitations":"Regions are reporting groups, not proven physical-domain counts. Nested/cross-clan overlaps are retained; no clan competition or functional assignment is inferred."}


def partial_reference_review(rows: list, domains: list, config: dict) -> list:
    """Retain coverage-rejected HSPs overlapping accepted domains; never tree input."""
    fraction = float(config.get("partial_domain_min_overlap", 0.7))
    if not 0 < fraction <= 1: raise ValueError("partial_domain_min_overlap must be in (0,1]")
    reviewed = []
    for row in rows:
        reasons = set(row["filter_reasons"])
        if not reasons or reasons - {"query_coverage_below_threshold", "reference_coverage_below_threshold"}:
            continue
        overlaps = []
        for domain in domains:
            # Only threshold-qualified domains may enable this exploratory route.
            if not domain.get("passes_ga"): continue
            a,b = domain["query_start"], domain["query_end"]
            overlap = max(0, min(b,row["query_end"])-max(a,row["query_start"])+1)/(b-a+1)
            if overlap >= fraction:
                overlaps.append({"domain_name":domain["domain_name"], "domain_accession":domain.get("domain_accession"),
                                 "query_domain_overlap_fraction":overlap})
        if overlaps:
            reviewed.append({**row, "overlapping_domains":overlaps, "route":"partial_domain_review",
                             "eligible_for_whole_protein_tree":False,
                             "note":"Query-domain overlap only; reference domain architecture and function remain unverified."})
    return reviewed


def _scan_domains(candidate: dict, config: dict, directory: Path, runner) -> dict:
    profile = candidate["profile"]
    resource = (profile.get("resources") or {}).get("domains") or {}
    if isinstance(resource, str):
        resource = {"database": resource}
    database = resource.get("database") or config.get("domain_database")
    if not candidate["analysis_plan"]["analyses"]["domains"]:
        return {"status": "not_assessed", "reason": "disabled_or_not_requested_by_route"}
    if not database:
        return {"status": "not_assessed", "reason": "domain_database_not_configured"}
    database = Path(database).expanduser()
    if not database.is_absolute() and resource.get("database") and profile.get("registry_path"):
        database = Path(profile["registry_path"]).parent / database
    database = database.resolve()
    if not database.is_file():
        raise ValueError(f"Configured domain database missing: {database}")
    # HMMER requires a pressed database for hmmscan. Never mutate a shared source DB.
    missing = [str(database) + suffix for suffix in (".h3f", ".h3i", ".h3m", ".h3p") if not Path(str(database) + suffix).is_file()]
    if missing:
        raise ValueError("Domain database is not fully hmmpress-indexed: " + ", ".join(missing))
    cutoff = resource.get("native_cutoff")
    if cutoff not in {None, "ga", "tc", "nc"}:
        raise ValueError("Profile domain native_cutoff must be ga, tc, nc, or absent")
    output = directory / "domains.domtblout"
    output.unlink(missing_ok=True)
    stdout = directory / "domains.hmmscan.txt"
    argv = [config.get("executables", {}).get("hmmscan", "hmmscan"), "--noali", "--cpu", str(int(config.get("threads", 1))),
            "--domtblout", str(output)]
    if cutoff:
        argv.append("--cut_" + cutoff)
    argv += [str(database), candidate["query"]["path"]]
    runner.run(argv, stdout_path=stdout)
    hits = parse_domtbl(output, candidate["query"]["id"])
    raw_hits = hits
    rejected = []
    domain_policy = config.get("domain_native_cutoff")
    if domain_policy not in (None, "ga"):
        raise ValueError("domain_native_cutoff must be ga or absent")
    if domain_policy == "ga":
        if cutoff not in (None,"ga"):
            raise ValueError("Conflicting profile and global domain cutoff")
        cache = config.setdefault("_ga_threshold_cache", {})
        if str(database) not in cache: cache[str(database)] = pfam_ga_thresholds(database)
        hits, rejected = filter_domains_ga(raw_hits, cache[str(database)])
    stat = database.stat()
    hash_key = (str(database), stat.st_size, stat.st_mtime_ns)
    hash_cache = config.setdefault("_domain_hash_cache", {})
    if hash_key not in hash_cache:
        hash_cache[hash_key] = _file_hash(database)
    return {"status": "completed", "interpretation": "detected" if hits else "not_detected",
            "database": str(database), "database_sha256": hash_cache[hash_key],
            "native_cutoff": cutoff, "cutoff_basis": "profile_requested_native_model_cutoff" if cutoff else "native_hmmer_reporting_defaults",
            "hits": hits, "raw_hits": raw_hits, "rejected_hits": rejected,
            "architecture": summarize_domain_architecture(hits),
            "postfilter": "model_GA_sequence_and_domain" if domain_policy == "ga" else "none", "domain_table": str(output), "native_output": str(stdout),
            "note": "Domain calls are exploratory structural evidence, not proof of a resistance/virulence function."}


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _context(candidate: dict, findings: list[dict], assembly: dict[str, str], flank: int,
             circular: set[str], directory: Path) -> dict:
    contig, start, end = candidate.get("contig"), candidate.get("start"), candidate.get("end")
    result = {"status": "not_assessed", "evidence": [], "notes": []}
    if not contig or start is None or end is None:
        result["notes"].append("Candidate coordinates unavailable")
        return result
    if contig not in assembly:
        raise ValueError(f"Candidate contig {contig!r} absent from assembly")
    start, end, size = int(start), int(end), len(assembly[contig])
    if not 1 <= start <= end <= size:
        raise ValueError("Candidate coordinates are outside assembly or span an unsupported annotation origin")
    lower, upper = start - flank, end + flank
    if contig in circular:
        if upper - lower + 1 >= size:
            segments = [(1, size)]
        elif lower < 1:
            segments = [(size + lower, size), (1, upper)]
        elif upper > size:
            segments = [(lower, size), (1, upper - size)]
        else:
            segments = [(lower, upper)]
    else:
        segments = [(max(1, lower), min(size, upper))]
        if lower < 1 or upper > size:
            result["notes"].append("Linear/unknown topology: context truncated at contig boundary")
    sequence = "".join(assembly[contig][a - 1:b] for a, b in segments)
    path = directory / "locus_context.fna"
    write_fasta({candidate["candidate_id"] + "_context": sequence}, path)
    result.update(status="completed", sequence_path=str(path), contig=contig,
                  coordinate_segments=segments, flank_requested=flank, circular=contig in circular)
    for finding in findings:
        if finding.get("finding_id") in candidate["finding_ids"] or finding.get("contig") != contig:
            continue
        a, b = finding.get("start"), finding.get("end")
        if a is None or b is None:
            continue
        if any(int(a) <= right and int(b) >= left for left, right in segments):
            result["evidence"].append(finding)
    result["notes"].append("Co-location is context evidence; it does not demonstrate transferability or functional linkage.")
    return result


def _search(queries: dict[str, str], catalog, config: dict, directory: Path, runner) -> tuple[dict, list[str]]:
    mode = config.get("reference_search_tool", "blastp")
    if mode == "metadata":
        return {}, []
    if mode not in {"blastp", "diamond"}:
        raise ValueError("reference_search_tool must be blastp, diamond, or metadata")
    query_path = directory / "candidate_queries.faa"
    # Opaque sequence IDs avoid tool-specific rewriting of original reference labels.
    search_fasta = directory / "catalogue_search.faa"
    idmap = {f"REF{i:06d}": ref for i, ref in enumerate(catalog.sequences, 1)}
    write_fasta(queries, query_path)
    write_fasta({key: catalog.sequences[ref] for key, ref in idmap.items()}, search_fasta)
    (directory / "search_reference_ids.json").write_text(json.dumps(idmap, indent=2) + "\n")
    output = directory / "reference_search.tsv"
    output.unlink(missing_ok=True)
    columns = "qseqid sseqid pident length qlen slen qstart qend sstart send evalue bitscore"
    threads = int(config.get("threads", 1))
    if threads < 1:
        raise ValueError("threads must be >=1")
    evalue = str(config.get("retrieval_evalue", 1e-5))
    # Budget is explicit: metadata anchors may supplement this sequence shortlist.
    target_count = int(config.get("search_max_targets", max(100, int(config.get("max_references", 30)))))
    executables = config.get("executables", {})
    if mode == "blastp":
        argv = [executables.get("blastp", "blastp"), "-query", str(query_path), "-subject", str(search_fasta),
                "-out", str(output), "-outfmt", "6 " + columns, "-evalue", evalue,
                "-num_threads", str(threads), "-max_target_seqs", str(target_count)]
        runner.run(argv)
    else:
        db = directory / "catalogue"
        executable = executables.get("diamond", "diamond")
        runner.run([executable, "makedb", "--in", str(search_fasta), "--db", str(db)])
        runner.run([executable, "blastp", "--query", str(query_path), "--db", str(db), "--out", str(output),
                    "--outfmt", "6", *columns.split(), "--evalue", evalue,
                    "--threads", str(threads), "--max-target-seqs", str(target_count)])
    audit = []
    hits = parse_similarity(output, float(config.get("min_identity", 0)), float(config.get("min_coverage", 0)),
                            float(config.get("min_reference_coverage", 0)), audit=audit)
    for query, rows in hits.items():
        if query not in queries:
            raise ValueError(f"Unexpected query {query!r} in reference search")
        for row in rows:
            if row["reference_id"] not in idmap:
                raise ValueError("Unrecognized reference ID in sequence search")
            row["reference_id"] = idmap[row["reference_id"]]
    for row in audit:
        if row["query_id"] not in queries or row["reference_id"] not in idmap:
            raise ValueError("Unknown identifier in reference audit")
        row["reference_id"] = idmap[row["reference_id"]]
    audit_path = directory / "reference_retrieval_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2) + "\n")
    config["_retrieval_audit"] = audit
    return hits, [str(audit_path), str(query_path), str(search_fasta), str(output), str(directory / "search_reference_ids.json")]


def search_anchor_panel(queries: dict, candidates: list, catalog, config: dict,
                        directory: Path, runner, hits: dict) -> tuple[dict, list]:
    """Search the complete eligible anchor panel independently of catalogue top-N.

    Same retrieval filters; no anchor bypasses coverage. Panel-specific E-values
    reflect its smaller search space and are not independent confirmation.
    """
    requested = {c["query"]["id"]: list(dict.fromkeys(c["profile"].get("anchor_reference_ids", [])))
                 for c in candidates if c.get("query", {}).get("id") in queries}
    audits = {qid: [] for qid in requested}
    eligible = set()
    for qid, ids in requested.items():
        for ref in ids:
            if ref not in catalog.metadata:
                status = "missing_from_catalogue"
            elif catalog.metadata[ref]["review_status"] != "reviewed":
                status = "not_reviewed"
            else:
                status = "pending"
                eligible.add(ref)
            audits[qid].append({"reference_id": ref, "status": status})
    artifacts = []
    if eligible and config.get("reference_search_tool", "blastp") != "metadata":
        work = directory / "anchor_panel"
        work.mkdir(exist_ok=True)
        panel = type(catalog)({r: catalog.sequences[r] for r in sorted(eligible)},
                              {r: catalog.metadata[r] for r in sorted(eligible)},
                              catalog.fasta, catalog.metadata_path, [])
        anchor_config = dict(config)
        # Every subject is allowed; max(5, N) also avoids BLAST small-top-N warnings.
        anchor_config["search_max_targets"] = max(5, len(eligible))
        anchor_hits, artifacts = _search(queries, panel, anchor_config, work, runner)
        raw = anchor_config.get("_retrieval_audit", [])
        combined = {qid: {h["reference_id"]: dict(h) for h in rows} for qid, rows in hits.items()}
        for qid, checks in audits.items():
            passing = {h["reference_id"]: h for h in anchor_hits.get(qid, [])}
            for check in checks:
                if check["status"] != "pending": continue
                ref = check["reference_id"]
                evidence = [{**r, "search_scope": "anchor_panel"} for r in raw
                            if r["query_id"] == qid and r["reference_id"] == ref]
                check["alignment_rows"] = evidence
                check["status"] = ("eligible" if ref in passing else
                                   "rejected_by_retrieval_filters" if evidence else
                                   "no_reported_alignment_at_search_settings")
                if ref in passing:
                    row = {**passing[ref], "search_scope": "anchor_panel"}
                    previous = combined.setdefault(qid, {}).get(ref)
                    if previous is None or (row["bitscore"], -row["evalue"]) > (previous["bitscore"], -previous["evalue"]):
                        combined[qid][ref] = row
                config.setdefault("_retrieval_audit", []).extend(evidence)
        hits = {qid: list(rows.values()) for qid, rows in combined.items()}
    else:
        for checks in audits.values():
            for check in checks:
                if check["status"] == "pending": check["status"] = "not_assessed_metadata_mode"
    config["_anchor_review"] = audits
    manifest = directory / "anchor_review.json"
    manifest.write_text(json.dumps({"queries":audits,
        "scope":"All reviewed configured anchors searched in a separate panel; native reporting sensitivity still applies.",
        "note":"Panel E-values use the panel search space; not independent confirmation."}, indent=2) + "\n")
    return hits, artifacts + [str(manifest)]


def _build_phylogeny(query_id: str, sequence: str, references: list[dict], catalog,
                     config: dict, directory: Path, runner) -> dict:
    source_query_hash = sequence_hash(sequence)
    sequence, query_normalization = normalize_alignment_sequence(sequence)
    normalization = {"query": query_normalization, "references": {}}
    alignment_input = {query_id: sequence}
    labels = {query_id: {"role": "query", "sequence_sha256": source_query_hash,
                         "alignment_sequence_sha256": sequence_hash(sequence)}}
    # Only reference duplicates are collapsed. An identical query ALWAYS survives.
    seen = {}
    for reference in references:
        seq = catalog.sequences[reference["reference_id"]]
        seq, normalization["references"][reference["reference_id"]] = normalize_alignment_sequence(seq)
        if seq in seen:
            labels[seen[seq]]["reference_aliases"].append(reference)
            continue
        label = f"R{len(seen) + 1:05d}"
        seen[seq] = label
        alignment_input[label] = seq
        labels[label] = {"role": "reference", "reference_aliases": [reference]}
    path = directory / "query_plus_references.faa"
    write_fasta(alignment_input, path)
    label_path = directory / "phylogeny_labels.json"
    label_path.write_text(json.dumps(labels, indent=2) + "\n")
    result = {"status": "not_assessed", "input": str(path), "labels": str(label_path),
              "query_id": query_id, "reference_sequences": len(seen), "normalization": normalization,
              "notes": ["Tree is exploratory; BaSIS does not infer function from nearest-tip distance."]}
    if not config.get("phylogeny", False):
        result["reason"] = "phylogeny_disabled"
        return result
    if len(set(alignment_input.values())) < 4:
        result.update(status="inconclusive", reason="fewer_than_four_distinct_sequences")
        return result
    alignment = directory / "alignment.faa"
    executables = config.get("executables", {})
    threads = str(int(config.get("threads", 1)))
    runner.run([executables.get("mafft", "mafft"), "--auto", "--thread", threads, str(path)], stdout_path=alignment)
    # Read aligned FASTA separately because '-' is valid here.
    aligned = read_fasta(alignment)
    if set(aligned) != set(alignment_input) or len({len(x) for x in aligned.values()}) != 1:
        raise ValueError("Alignment must retain exactly all submitted IDs and have equal row lengths")
    for key in alignment_input:
        if aligned[key].replace("-", "").replace(".", "") != alignment_input[key]:
            raise ValueError(f"Alignment altered protein sequence for {key}")
    prefix = directory / "phylogeny"
    tree = Path(str(prefix) + ".treefile")
    tree.unlink(missing_ok=True)
    runner.run([executables.get("iqtree", "iqtree"), "-s", str(alignment), "-m", "MFP", "-bb", "1000",
                "-nt", threads, "-seed", str(int(config.get("phylogeny_seed", 42))),
                "-pre", str(prefix), "-redo"])
    if not tree.is_file() or not tree.read_text().strip():
        raise ValueError("IQ-TREE did not produce a nonempty treefile")
    # IQ-TREE may remove identical sequences during inference, but must restore tips.
    text = tree.read_text()
    tips = set(re.findall(r"(?:\(|,)\s*['\"]?([^\s:(),;'\"]+)['\"]?(?=\s*[:),])", text))
    if not text.strip().endswith(";") or set(alignment_input) != tips:
        raise ValueError("Tree does not retain exactly the submitted query/reference tip IDs")
    result.update(status="completed", alignment=str(alignment), tree=str(tree))
    return result


def run_deep(sample: dict, records: list[dict], config: dict, outdir: Path, runner) -> dict:
    """Compatibility entry point; the same stages are separate Snakemake jobs."""
    from .deep_stages import STAGES, run_stage
    state = None
    for stage in STAGES:
        state = run_stage(stage, sample, records, config, outdir, runner, state)
    return state["payload"]
