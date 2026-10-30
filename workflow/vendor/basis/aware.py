"""BaSIS AWARE: auditable admission to computational follow-up.

Presets are workload policies, not calibrated biological classifiers. Native
findings and confidence labels remain intact, including deferred evidence.
This module does not run tools, download data, or infer a phenotype.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import hashlib
import json
import math

from .amr_review import VERSION as REVIEW_VERSION, REVIEW_METHODS, review_finding

VERSION = "basis-aware-v1"
EXACT_METHODS = ["ALLELE", "ALLELEP", "ALLELEX", "EXACT", "EXACTP", "EXACTX"]
NATIVE_METHODS = EXACT_METHODS + [
    "BLAST", "BLASTP", "BLASTX", "HMM", "POINT", "POINTP", "POINTX",
    "PARTIAL", "PARTIALP", "PARTIALX", "PARTIAL_CONTIG_END", "PARTIAL_CONTIG_ENDP",
    "PARTIAL_CONTIG_ENDX", "INTERNAL_STOP", "INTERNAL_STOPP", "INTERNAL_STOPX",
    "FRAME_SHIFT", "FRAME_SHIFTP", "FRAME_SHIFTX",
]


def _preset(name):
    if not isinstance(name, str) or name not in {"auto-strict", "auto-medium", "auto-loose"}:
        raise ValueError("deep.aware.preset must be auto-strict, auto-medium or auto-loose")
    strict, loose = name == "auto-strict", name == "auto-loose"
    identity, coverage, evalue = ((90.0, 90.0, 1e-10) if strict else
                                  (60.0, 50.0, 1e-3) if loose else (80.0, 70.0, 1e-5))
    similarity = dict(enabled=True, min_identity=identity,
                      min_reference_coverage=coverage, min_query_coverage=coverage,
                      max_evalue=evalue)
    return {"version": VERSION, "enabled": True, "preset": name,
            "queues": {"exploratory": loose, "attention": loose},
            "tools": {
                "amrfinder": {"enabled": True,
                    "methods": list(EXACT_METHODS if strict else NATIVE_METHODS),
                    "min_identity": None, "min_reference_coverage": None},
                "rgi": {"enabled": True,
                    "cutoffs": ["perfect"] if strict else ["perfect", "strict", "loose"] if loose else ["perfect", "strict"],
                    "allow_nudged": loose, "min_identity": None, "min_reference_coverage": None},
                "vfdb": dict(similarity), "bacmet": dict(similarity),
                "pathogenfinder2": {"enabled": True, "maximum_rank": 20}},
            "interpretation": "Computational admission only; deferral does not establish absence or safety."}


def resolve_policy(config):
    """Expand a preset, then explicit overrides; reject typos and invalid units.

    Direct legacy API calls without an aware block retain their old selection.
    The standard configuration supplies enabled auto-medium for new executions.
    """
    supplied = config.get("aware", {"enabled": False})
    if not isinstance(supplied, dict):
        raise ValueError("deep.aware must be a mapping")
    unknown = set(supplied) - {"enabled", "preset", "scope", "queues", "tools"}
    if unknown:
        raise ValueError("Unknown deep.aware settings: " + str(sorted(unknown)))
    result = _preset(supplied.get("preset", "auto-medium"))
    scope = supplied.get("scope", "all-supported")
    if not isinstance(scope, str) or scope not in {"all-supported", "amrfinder-review"}:
        raise ValueError("deep.aware.scope must be all-supported or amrfinder-review")
    result["scope"] = scope
    result["review_policy"] = REVIEW_VERSION
    if scope == "amrfinder-review":
        # A dedicated review scope must not inherit strict's exact-only list.
        # Explicit tool overrides below still apply and remain auditable.
        result["tools"]["amrfinder"]["methods"] = sorted(REVIEW_METHODS)
    enabled = supplied.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("deep.aware.enabled must be boolean")
    result["enabled"] = enabled
    for section in ("queues", "tools"):
        value = supplied.get(section, {})
        if not isinstance(value, dict) or set(value) - set(result[section]):
            raise ValueError("Unknown or invalid deep.aware." + section)
        for name, override in value.items():
            if section == "queues":
                if not isinstance(override, bool):
                    raise ValueError("deep.aware.queues." + name + " must be boolean")
                result[section][name] = override
                continue
            rule = result[section][name]
            if not isinstance(override, dict) or set(override) - set(rule):
                raise ValueError("Unknown or invalid deep.aware.tools." + name)
            rule.update(copy.deepcopy(override))
    for name, rule in result["tools"].items():
        for key, value in rule.items():
            label = "deep.aware.tools." + name + "." + key
            if key in {"enabled", "allow_nudged"}:
                if not isinstance(value, bool):
                    raise ValueError(label + " must be boolean")
            elif key in {"methods", "cutoffs"}:
                allowed = NATIVE_METHODS if key == "methods" else ["perfect", "strict", "loose"]
                if not isinstance(value, list) or not value or any(not isinstance(v, str) or v not in allowed for v in value):
                    raise ValueError(label + " must be a nonempty list of supported native labels")
                rule[key] = sorted(set(value))
            elif key == "maximum_rank":
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError(label + " must be a positive integer")
            elif value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError(label + " must be finite and nonnegative, or null")
                if key != "max_evalue" and value > 100:
                    raise ValueError(label + " must be a percentage in [0,100]")
    result["overrides"] = copy.deepcopy(supplied)
    result["effective_sha256"] = hashlib.sha256(json.dumps(
        {k: v for k, v in result.items() if k != "overrides"}, sort_keys=True).encode()).hexdigest()
    return result


def evaluate(finding, policy, config):
    """Evaluate each source independently; missing requested metrics fail closed."""
    from .deep import _is_exploratory
    tool = finding.get("tool", "unknown")
    attrs = finding.get("attributes") or {}
    category = finding.get("category")
    attention = category == "ml_attention"
    exploratory = _is_exploratory(finding) or attrs.get("passes_threshold") is False
    queue = "ml_attention" if attention else "exploratory" if exploratory else "native_screen"
    reasons, observed = [], {}
    review = review_finding(finding)
    if policy.get("scope") == "amrfinder-review":
        if review is None:
            reasons.append("outside_amrfinder_review_scope")
        elif not review["automatic_followup"]:
            reasons.append("manual_review_without_automatic_followup" if review["investigation_required"]
                           else "native_call_retained_without_deep_followup")
    if category not in {"amr", "virulence", "stress", "ml_attention"}:
        reasons.append("category_outside_followup_scope")
    if category == "stress" and not config.get("include_stress", False):
        reasons.append("stress_followup_disabled")
    if attention and not policy["queues"]["attention"]:
        reasons.append("attention_followup_disabled")
    if exploratory and not attention and (not policy["queues"]["exploratory"] or not config.get("include_exploratory", True)):
        reasons.append("exploratory_followup_disabled")
    rule = policy["tools"].get(tool)
    if rule is None:
        reasons.append("unsupported_admission_tool")
    else:
        categories = {"amrfinder": {"amr", "virulence", "stress"}, "rgi": {"amr"},
                      "vfdb": {"virulence"}, "bacmet": {"stress"}, "pathogenfinder2": {"ml_attention"}}
        if category not in categories[tool]:
            reasons.append("tool_category_mismatch")
        if not rule["enabled"]:
            reasons.append("tool_followup_disabled")
        if tool == "amrfinder":
            method = (review["native_method"] if review else str(attrs.get("native_method") or "").upper()) or ""
            observed["native_method"] = method or None
            if method not in rule["methods"]:
                reasons.append("native_method_not_admitted")
        if tool == "rgi":
            cutoff = str(attrs.get("cutoff") or "").lower()
            nudged = attrs.get("nudged")
            observed.update(cutoff=cutoff or None, nudged=nudged)
            if cutoff not in rule["cutoffs"]:
                reasons.append("native_cutoff_not_admitted")
            if type(nudged) is not bool:
                reasons.append("missing_or_invalid_nudged_flag")
            elif nudged and not rule["allow_nudged"]:
                reasons.append("nudged_hit_not_admitted")
        checks = (("min_identity", finding.get("identity"), "min"),
                  ("min_reference_coverage", finding.get("coverage"), "min"),
                  ("min_query_coverage", attrs.get("query_coverage"), "min"),
                  ("max_evalue", attrs.get("evalue"), "max"),
                  ("maximum_rank", attrs.get("rank"), "rank"))
        for field, value, direction in checks:
            threshold = rule.get(field)
            if threshold is None:
                continue
            observed[field] = value
            valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                     and math.isfinite(value) and value >= 0)
            if field.startswith("min_"):
                valid = valid and value <= 100
            if direction == "rank":
                valid = valid and value >= 1 and int(value) == value
            if not valid:
                reasons.append("missing_or_invalid:" + field)
            elif (direction == "min" and value < threshold) or (direction != "min" and value > threshold):
                reasons.append("threshold_not_met:" + field)
    return {"finding_id": finding["finding_id"], "source_record_id": finding.get("source_record_id"),
            "tool": tool, "queue": queue, "eligible": not reasons,
            "reasons": reasons or ["tool_criteria_met"], "observed": observed, "review": review}


def identities(findings, sample):
    """Reconcile verified annotation IDs; never merge by names or similarity.

    A partial span with an explicit annotated protein ID can map to that CDS.
    Conflicting locations and ambiguous protein-only links remain distinct.
    """
    from .deep import _locus_key
    validation = (sample or {}).get("input_validation") or {}
    coordinates = validation.get("protein_coordinates") or {}
    aliases = validation.get("contig_aliases") or {}
    annotated = defaultdict(list)
    for pid, feature in coordinates.items():
        annotated[_locus_key(feature)].append(pid)
    protein_loci = defaultdict(set)
    for finding in findings:
        contig = finding.get("contig")
        key = _locus_key(dict(finding, contig=aliases.get(contig, contig)))
        if finding.get("protein_id") and key[0] == "locus":
            protein_loci[str(finding["protein_id"])].add(key)
    result = []
    for ordinal, finding in enumerate(findings):
        fields = {k: finding.get(k) for k in ("protein_id", "contig", "start", "end", "strand")}
        fields["contig"] = aliases.get(fields["contig"], fields["contig"])
        key = _locus_key(dict(finding, **fields))
        basis, conflict = key[0], False
        if key[0] == "unlocalized":
            key = ("unlocalized", finding.get("source_record_id"), finding.get("tool"), finding["finding_id"])
        pid = fields["protein_id"]
        if pid in coordinates:
            feature = coordinates[pid]
            canonical = _locus_key(feature)
            compatible = (key[0] == "protein" and fields["contig"] in (None, feature["contig"])) or (
                key[0] == "locus" and fields["contig"] == feature["contig"]
                and fields["strand"] in (None, ".", feature["strand"])
                and feature["start"] <= fields["start"] <= fields["end"] <= feature["end"])
            if compatible:
                key, basis = canonical, "validated_annotation_protein"
                fields.update({k: feature[k] for k in ("contig", "start", "end", "strand")})
                if len(annotated[canonical]) > 1:
                    # Multiple annotated proteins at identical coordinates need
                    # independent sequence identity checks; do not assume aliases.
                    key = ("annotated_protein", str(pid), canonical)
            else:
                key = ("identity_conflict", str(pid), key, ordinal)
                basis, conflict = "annotation_location_conflict", True
        elif key[0] == "locus" and len(annotated.get(key, [])) == 1 and not pid:
            fields["protein_id"] = annotated[key][0]
            basis = "exact_validated_annotation_locus"
        elif key[0] == "protein":
            locations = protein_loci.get(str(pid), set())
            if len(locations) == 1:
                key = next(iter(locations))
                fields.update(dict(zip(("contig", "start", "end", "strand"), key[1:])))
                basis = "unique_recorded_protein_locus"
            elif len(locations) > 1:
                basis, conflict = "ambiguous_protein_locus", True
        result.append((key, fields, {"finding_id": finding["finding_id"], "basis": basis,
                                    "conflict": conflict}))
    return result


def select(records, config, sample, policy):
    from .deep import _flatten, evidence_priority, taxonomy_review
    from .candidate_labels import refresh_candidate_labels
    limits = {}
    for key, default, minimum in (("maximum_candidates", 5, 1),
                                  ("maximum_exploratory_candidates", 20, 0),
                                  ("maximum_attention_candidates", 5, 0)):
        value = config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(key + f" must be an integer >= {minimum}")
        limits[key] = value
    for key, default in (("include_stress", False), ("include_exploratory", True),
                         ("attention_queue_for_mixed_evidence", False)):
        if not isinstance(config.get(key, default), bool):
            raise ValueError(key + " must be boolean")
    findings = list(_flatten(records))
    sid = (sample or {}).get("sample_id")
    if any(sid and f.get("sample_id") not in (None, sid) for f in findings):
        raise ValueError("AWARE findings belong to another sample")
    reconciled = identities(findings, sample)
    groups = {}
    for finding, (key, fields, identity) in zip(findings, reconciled):
        if finding.get("category") not in {"amr", "virulence", "stress", "ml_attention"}:
            continue
        decision = evaluate(finding, policy, config)
        if identity["conflict"]:
            decision["eligible"] = False
            decision["reasons"] = [r for r in decision["reasons"] if r != "tool_criteria_met"] + ["identity_review_required"]
        if key not in groups:
            digest = hashlib.sha256(json.dumps(key, separators=(",", ":")).encode()).hexdigest()[:16]
            groups[key] = {"candidate_id": "cand_" + digest, **fields,
                "gene": finding.get("gene"), "family": finding.get("family"),
                "finding_ids": [], "source_evidence": [], "selection_reasons": [], "family_labels": [],
                "priority": 99, "attention_rank": None, "attention_max": None,
                "execution": "pending", "interpretation": "inconclusive",
                "aware": {"version": VERSION, "scope": policy.get("scope", "all-supported"), "evidence": [], "identity": []}}
        candidate = groups[key]
        for field, value in fields.items():
            if candidate.get(field) is None and value is not None:
                candidate[field] = value
        coordinates = ((sample or {}).get("input_validation") or {}).get("protein_coordinates") or {}
        if fields["protein_id"] in coordinates and candidate.get("protein_id") not in coordinates:
            candidate["protein_id"] = fields["protein_id"]
        candidate["source_evidence"].append(copy.deepcopy(finding))
        candidate["finding_ids"].append(finding["finding_id"])
        candidate["family_labels"].extend(str(finding[k]) for k in ("gene", "family") if finding.get(k))
        candidate["aware"]["evidence"].append(decision)
        candidate["aware"]["identity"].append(identity)
    for key, candidate in list(groups.items()):
        admitted = [(f, d) for f, d in zip(candidate["source_evidence"], candidate["aware"]["evidence"]) if d["eligible"]]
        candidate["aware"]["eligible"] = bool(admitted)
        reviews = [d["review"] for d in candidate["aware"]["evidence"] if d.get("review")]
        candidate["review"] = {"policy": REVIEW_VERSION,
            "status": "requires_review" if reviews else "outside_amrfinder_review_scope",
            "investigation_required": any(r["investigation_required"] for r in reviews),
            "reasons": sorted({reason for r in reviews for reason in r["reasons"]})}
        candidate["aware"]["qualifying_finding_ids"] = sorted({f["finding_id"] for f, _ in admitted})
        queues = {d["queue"] for _, d in admitted}
        queue = ("native_screen" if "native_screen" in queues else
                 "ml_attention" if "ml_attention" in queues and config.get("attention_queue_for_mixed_evidence", False) else
                 "exploratory" if "exploratory" in queues else
                 "ml_attention" if "ml_attention" in queues else "not_admitted")
        candidate["selection_queue"] = queue
        candidate["priority"] = {"native_screen": 0, "exploratory": 1, "ml_attention": 2, "not_admitted": 99}[queue]
        candidate["selection_reasons"] = sorted({d["queue"] for _, d in admitted})
        ranking_findings = [f for f, d in admitted if d["queue"] == queue]
        candidate["evidence_priority"] = evidence_priority({"source_evidence": ranking_findings})
        if queue == "ml_attention":
            candidate["attention_rank"] = min((f.get("attributes") or {})["rank"] for f in ranking_findings)
        if admitted:
            candidate["gene"], candidate["family"] = admitted[0][0].get("gene"), admitted[0][0].get("family")
        candidate, _ = refresh_candidate_labels(candidate)
        candidate["taxonomy_review"] = taxonomy_review(candidate, sample)
        for field in ("finding_ids", "family_labels"):
            candidate[field] = list(dict.fromkeys(candidate[field]))
        groups[key] = candidate
    def order(candidate):
        rank = candidate["evidence_priority"]
        quality = rank["best_identity_coverage_product"]
        return (candidate["priority"], candidate["attention_rank"] or float("inf"),
                -len(rank["tools"]), -(quality if quality is not None else -1), candidate["candidate_id"])
    ordered, used = sorted(groups.values(), key=order), Counter()
    for candidate in ordered:
        queue = candidate["selection_queue"]
        if not candidate["aware"]["eligible"]:
            reason = "aware_admission_deferred"
        elif used["total"] >= limits["maximum_candidates"] or (
                queue == "exploratory" and used[queue] >= limits["maximum_exploratory_candidates"]) or (
                queue == "ml_attention" and used[queue] >= limits["maximum_attention_candidates"]):
            reason = "candidate_resource_budget"
        else:
            reason = "aware_criteria_met"
            used["total"] += 1
            used[queue] += 1
        selected = reason == "aware_criteria_met"
        candidate.update(selected=selected, reason=reason, execution="pending" if selected else "deferred")
    return ordered


def audit(candidates, records, config):
    from .deep import _flatten
    policy = resolve_policy(config)
    if not policy["enabled"]:
        return None
    by_tool, by_queue, reasons = {}, {}, Counter()
    for finding in _flatten(records):
        tool = finding.get("tool", "unknown")
        by_tool.setdefault(tool, {"received": 0, "evaluated": 0, "qualifying": 0,
                                  "selected_loci": set()})["received"] += 1
    for candidate in candidates:
        queue = candidate["selection_queue"]
        group = by_queue.setdefault(queue, {"candidates": 0, "eligible": 0, "selected": 0, "deferred": 0})
        group["candidates"] += 1
        group["eligible"] += bool(candidate["aware"]["eligible"])
        group["selected"] += bool(candidate["selected"])
        group["deferred"] += not candidate["selected"]
        for row in candidate["aware"]["evidence"]:
            item = by_tool[row["tool"]]
            item["evaluated"] += 1
            item["qualifying"] += bool(row["eligible"])
            if row["eligible"] and candidate["selected"]:
                item["selected_loci"].add(candidate["candidate_id"])
            if not row["eligible"]:
                reasons.update(row["reasons"])
    for row in by_tool.values():
        row["selected_loci"] = len(row["selected_loci"])
    counts = {"findings_received": sum(r["received"] for r in by_tool.values()),
              "findings_evaluated": sum(r["evaluated"] for r in by_tool.values()),
              "findings_qualifying": sum(r["qualifying"] for r in by_tool.values()),
              "unique_candidates": len(candidates),
              "eligible_candidates": sum(c["aware"]["eligible"] for c in candidates),
              "selected_candidates": sum(c["selected"] for c in candidates),
              "admission_deferred": sum(c["reason"] == "aware_admission_deferred" for c in candidates),
              "budget_deferred": sum(c["reason"] == "candidate_resource_budget" for c in candidates)}
    counts["investigation_candidates"] = sum(c.get("review", {}).get("investigation_required", False) for c in candidates)
    counts["investigation_deferred"] = sum(c.get("review", {}).get("investigation_required", False)
                                          and not c["selected"] for c in candidates)
    counts["findings_merged"] = counts["findings_evaluated"] - counts["unique_candidates"]
    hashes = [c.get("query", {}).get("sequence_sha256") for c in candidates if c["selected"]]
    counts["selected_queries_available"] = sum(h is not None for h in hashes)
    counts["unique_selected_sequences"] = len({h for h in hashes if h is not None})
    return {"schema": VERSION, "effective_policy": policy, "counts": counts,
            "by_tool": by_tool, "by_queue": by_queue, "deferral_reasons": dict(reasons),
            "budget": {k: config.get(k, default) for k, default in (
                ("maximum_candidates", 5), ("maximum_exploratory_candidates", 20), ("maximum_attention_candidates", 5))},
            "sequence_count_note": "Unique sequences are an inventory, not a claim of cross-sample computation reuse."}
