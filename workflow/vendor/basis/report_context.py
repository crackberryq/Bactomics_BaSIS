"""Display-only context qualifiers copied from recorded native annotations.

These helpers never change evidence, classify protein function, or infer that
an input protein is truncated from a reference sequence's fragment annotation.
"""
from __future__ import annotations

import math
import re


def _percent(value):
    if isinstance(value, bool):
        return "not recorded"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "not recorded"
    if not math.isfinite(number) or not 0 <= number <= 100:
        return "not recorded"
    return f"{number:.1f}%"


def finding_context(finding):
    """Return plain text qualifiers for a screening row or genome tooltip.

    ``coverage_text`` is supplied only for BacMet, where the recorded adapter
    uses percent identity, reference coverage and separate query coverage.
    Every returned string still needs escaping by an HTML renderer.
    """
    module = finding.get("module") or finding.get("tool")
    attrs = finding.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}
    display = {
        "source_call": str(finding.get("gene") or finding.get("family") or finding.get("category") or "Recorded finding"),
        "caveats": [], "coverage_text": None,
        "is_prediction_type": None, "reference_fragment": False,
    }
    if module == "isescan":
        native = attrs.get("native") or {}
        code = str(native.get("type") or "").strip().lower() if isinstance(native, dict) else ""
        kind = {"p": "Partial IS prediction", "c": "Complete IS prediction"}.get(code)
        family = finding.get("family") or finding.get("gene")
        display["is_prediction_type"] = {"p": "partial", "c": "complete"}.get(code)
        display["source_call"] = " · ".join(str(x) for x in (
            family, kind or "IS prediction · completeness not recorded") if x)
        display["caveats"] = [
            "ISEScan completeness is a prediction; it does not establish an active or transferable element."
        ]
    elif module == "bacmet":
        reference = str(finding.get("reference") or "")
        annotation = attrs.get("source_reference_annotation") or {}
        headers = []
        if isinstance(annotation, dict) and annotation.get("reference_id") == reference:
            headers.append(annotation.get("source_header"))
        headers.append(attrs.get("subject_title"))
        # Bind the qualifier to this exact subject ID. A fragment annotation in
        # an unrelated title or another hit must not leak onto the displayed hit.
        fragment = bool(reference) and any(
            isinstance(header, str) and header.split(maxsplit=1)[0:1] == [reference]
            and re.search(r"\(fragment\)", header, re.IGNORECASE)
            for header in headers
        )
        display["reference_fragment"] = fragment
        display["coverage_text"] = (
            f"Identity {_percent(finding.get('identity'))} · "
            f"Query coverage {_percent(attrs.get('query_coverage'))} · "
            f"Reference coverage {_percent(finding.get('coverage'))}"
        )
        if fragment:
            display["caveats"].append(
                "Reference annotated as a fragment; reference coverage refers to that fragment, "
                "not a complete protein. This does not establish that the query is truncated."
            )
    return display
