"""Conservative, uncalibrated functional-group association from jplace output.

The labels supplied here must already have passed the reference-panel evidence
audit.  This module does not promote database annotations to functional labels.
It does not estimate a probability of resistance or of protein function.

The Newick root is a serialization detail.  On the undirected tree we find
maximal edge-defined sides containing at least two labelled reference tips of
one group and no labelled tip of another group.  Only edges *inside* such a
side, with group anchors on both sides of the edge, contribute likelihood
weight to that group.  Boundary edges, context-only branches, and singleton
groups remain unresolved.  Multiple disjoint pure clades are permitted.

Likelihood weights are never renormalized.  Omitted placement weight is
reported separately from reported weight on unresolved edges.
"""
from dataclasses import dataclass, field
import math
import re


POLICY = "homogeneous_clade_interior_v1"
WEIGHT_TOLERANCE = 1e-6
REQUIRED_FIELDS = {
    "edge_num", "likelihood", "like_weight_ratio", "distal_length", "pendant_length"
}


@dataclass
class _Node:
    name: str | None = None
    children: list = field(default_factory=list)
    length: float | None = None
    edge: int | None = None


def _number(value, what):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{what} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{what} must be a finite number")
    return result


class _Newick:
    """Strict jplace Newick reader; supports quoted tips and support labels."""

    def __init__(self, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("jplace tree must be nonempty Newick text")
        self.text, self.pos, self.node_count = text, 0, 0

    def _peek(self):
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def _take(self, char):
        if self._peek() != char:
            raise ValueError(f"Malformed jplace Newick: expected {char!r}")
        self.pos += 1

    def _label(self):
        if self._peek() == "'":
            self.pos += 1
            chars = []
            while self.pos < len(self.text):
                char = self.text[self.pos]
                self.pos += 1
                if char == "'":
                    if self.pos < len(self.text) and self.text[self.pos] == "'":
                        chars.append("'")
                        self.pos += 1
                    else:
                        return "".join(chars)
                else:
                    chars.append(char)
            raise ValueError("Unterminated quoted label in jplace tree")
        start = self.pos
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char.isspace() or char in "(),:;{}[]'":
                break
            self.pos += 1
        return self.text[start:self.pos]

    def _node(self):
        self.node_count += 1
        if self.node_count > 100_000:
            raise ValueError("jplace tree exceeds the supported node count")
        node = _Node()
        if self._peek() == "(":
            self._take("(")
            node.children.append(self._node())
            while self._peek() == ",":
                self._take(",")
                node.children.append(self._node())
            self._take(")")
            if len(node.children) < 2:
                raise ValueError("Unary internal nodes are not supported")
            node.name = self._label() or None
        else:
            node.name = self._label()
            if not node.name:
                raise ValueError("Missing reference tip label in jplace tree")
        if self._peek() == ":":
            self._take(":")
            token = self._label()
            try:
                node.length = float(token)
            except ValueError as exc:
                raise ValueError("Invalid branch length in jplace tree") from exc
            if not math.isfinite(node.length) or node.length < 0:
                raise ValueError("Branch lengths must be finite and nonnegative")
        if self._peek() == "{":
            self._take("{")
            start = self.pos
            while self.pos < len(self.text) and self.text[self.pos].isdigit():
                self.pos += 1
            token = self.text[start:self.pos]
            if not re.fullmatch(r"[0-9]+", token):
                raise ValueError("Invalid jplace edge number")
            node.edge = int(token)
            self._take("}")
            if node.length is None:
                raise ValueError("Numbered jplace edges require branch lengths")
        return node

    def parse(self):
        try:
            root = self._node()
            self._take(";")
            if self._peek():
                raise ValueError("Trailing text after jplace Newick tree")
        except RecursionError as exc:
            raise ValueError("jplace tree is too deeply nested") from exc
        if not root.children:
            raise ValueError("jplace tree must contain multiple reference tips")
        return root


def _graph(tree):
    root = _Newick(tree).parse()
    adjacency, edges, tips = {}, {}, {}

    def add_node():
        index = len(adjacency)
        adjacency[index] = set()
        return index

    def edge(parent, child, node, artificial=False):
        if node.edge is None or node.length is None:
            raise ValueError("Every non-root jplace branch needs a length and edge number")
        if node.edge in edges:
            raise ValueError("Duplicate edge number in jplace tree")
        edges[node.edge] = {
            "nodes": (parent, child), "length": node.length, "artificial": artificial
        }
        adjacency[parent].add(child)
        adjacency[child].add(parent)

    def visit(node, parent=None):
        index = add_node()
        if parent is not None:
            edge(parent, index, node)
        if not node.children:
            if node.name in tips:
                raise ValueError("Duplicate reference tip in jplace tree")
            tips[node.name] = index
        for child in node.children:
            visit(child, index)
        return index

    try:
        root_index = visit(root)
    except RecursionError as exc:
        raise ValueError("jplace tree is too deeply nested") from exc
    if root.edge is not None:
        edge(add_node(), root_index, root, artificial=True)
    elif root.length not in (None, 0.0):
        raise ValueError("An unnumbered root branch cannot have positive length")
    return adjacency, edges, tips


def _labels(reference_labels, tips):
    if not isinstance(reference_labels, dict) or set(reference_labels) != set(tips):
        raise ValueError("jplace reference tips differ from reference labels")
    groups, roles = {}, {}
    for tip, label in reference_labels.items():
        if not isinstance(tip, str) or not tip or not isinstance(label, dict):
            raise ValueError("Reference labels must map nonempty tip IDs to objects")
        role, group = label.get("role"), label.get("group")
        if not isinstance(role, str) or role not in {"amr", "alternative", "context"}:
            raise ValueError(f"Invalid reference role for {tip}")
        if role == "context":
            if group is not None:
                raise ValueError("Context references cannot carry a functional group")
        elif not isinstance(group, str) or not group.strip():
            raise ValueError("Qualified references require a nonempty functional group")
        if group is not None:
            if group in roles and roles[group] != role:
                raise ValueError("Functional group has conflicting reference roles")
            groups[tip], roles[group] = group, role
        accessions = label.get("accessions", [])
        if (not isinstance(accessions, list)
                or any(not isinstance(a, str) or not a for a in accessions)
                or len(accessions) != len(set(accessions))):
            raise ValueError("Reference accessions must be a list of unique nonempty strings")
    return groups, roles


def _component(adjacency, start, blocked):
    seen, pending = set(), [start]
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        pending.extend(other for other in adjacency[node]
                       if other not in seen and {node, other} != blocked)
    return frozenset(seen)


def _edge_groups(adjacency, edges, tips, groups):
    all_nodes = frozenset(adjacency)
    counts = {group: sum(value == group for value in groups.values())
              for group in sorted(set(groups.values()))}
    cuts, pure = {}, []
    for number, row in edges.items():
        a, b = row["nodes"]
        side = _component(adjacency, a, {a, b})
        cuts[number] = (side, all_nodes - side)
        if row["artificial"] or len(counts) < 2:
            continue
        for side in cuts[number]:
            anchors = [tip for tip, node in tips.items() if node in side and tip in groups]
            labels = {groups[tip] for tip in anchors}
            if len(anchors) >= 2 and len(labels) == 1:
                pure.append({"nodes": side, "group": next(iter(labels)),
                             "anchors": sorted(anchors), "boundary_edge": number})
    # Degree-two serialization roots can duplicate the same biological split.
    # Keeping only maximal node components yields the same interior tip edges.
    maximal = [item for item in pure if not any(
        item["group"] == other["group"] and item["nodes"] < other["nodes"]
        for other in pure)]
    assignments, clades = {}, []
    for item in maximal:
        interior = []
        anchor_nodes = {tips[tip] for tip in item["anchors"]}
        for number, row in edges.items():
            if row["artificial"] or not set(row["nodes"]) <= item["nodes"]:
                continue
            left, right = cuts[number]
            if anchor_nodes & left and anchor_nodes & right:
                interior.append(number)
                assignments.setdefault(number, set()).add(item["group"])
        clades.append({"group": item["group"], "anchor_tips": item["anchors"],
                       "anchor_count": len(item["anchors"]),
                       "boundary_edge": item["boundary_edge"],
                       "interior_edges": sorted(interior)})
    clades.sort(key=lambda item: (item["group"], item["anchor_tips"], item["boundary_edge"]))
    boundary = {item["boundary_edge"] for item in clades}
    answer = {}
    for number, row in edges.items():
        assigned = assignments.get(number, set())
        if row["artificial"]:
            answer[number] = (None, "artificial_root_edge")
        elif len(counts) < 2:
            answer[number] = (None, "fewer_than_two_functional_groups")
        elif number in boundary:
            answer[number] = (None, "homogeneous_clade_boundary")
        elif len(assigned) == 1:
            answer[number] = (next(iter(assigned)), "homogeneous_clade_interior")
        elif len(assigned) > 1:
            answer[number] = (None, "ambiguous_group_interior")
        else:
            side_anchors = [set(groups[tip] for tip, node in tips.items()
                                if node in side and tip in groups)
                            for side in cuts[number]]
            reason = "context_only_branch" if any(not labels for labels in side_anchors) else "no_qualified_group_interior"
            answer[number] = (None, reason)
    return answer, counts, clades


def _placement_rows(data, expected_query, edges):
    if (not isinstance(data, dict) or type(data.get("version")) is not int
            or data.get("version") != 3):
        raise ValueError("Expected jplace version 3")
    if not isinstance(expected_query, str) or not expected_query:
        raise ValueError("Expected query ID must be nonempty")
    fields = data.get("fields")
    if (not isinstance(fields, list) or any(not isinstance(f, str) or not f for f in fields)
            or len(fields) != len(set(fields)) or not REQUIRED_FIELDS <= set(fields)):
        raise ValueError("Invalid or missing jplace field names")
    records = data.get("placements")
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise ValueError("Expected exactly one query placement record")
    record = records[0]
    if ("n" in record) == ("nm" in record):
        raise ValueError("Query record requires exactly one of n or nm")
    if "n" in record:
        if record["n"] != [expected_query]:
            raise ValueError("jplace query name differs from expected query")
    else:
        nm = record["nm"]
        if (not isinstance(nm, list) or len(nm) != 1 or not isinstance(nm[0], list)
                or len(nm[0]) != 2 or nm[0][0] != expected_query
                or _number(nm[0][1], "Query multiplicity") != 1):
            raise ValueError("Expected one named query with unit multiplicity")
    placements = record.get("p")
    if not isinstance(placements, list) or not placements:
        raise ValueError("Expected at least one reported placement")
    rows, seen = [], set()
    for values in placements:
        if not isinstance(values, list) or len(values) != len(fields):
            raise ValueError("Placement values do not match declared fields")
        row = {key: _number(value, f"Placement {key}") for key, value in zip(fields, values)}
        edge = row["edge_num"]
        if not edge.is_integer() or edge < 0 or int(edge) not in edges:
            raise ValueError("Placement refers to an invalid or missing edge")
        edge = int(edge)
        if edge in seen:
            raise ValueError("Duplicate placement edge for query")
        seen.add(edge)
        weight = row["like_weight_ratio"]
        if weight < 0 or weight > 1 + WEIGHT_TOLERANCE:
            raise ValueError("Placement likelihood weight must be between zero and one")
        if row["distal_length"] < 0 or row["pendant_length"] < 0:
            raise ValueError("Placement branch lengths must be nonnegative")
        tolerance = max(1e-8, abs(edges[edge]["length"]) * 1e-6)
        if row["distal_length"] > edges[edge]["length"] + tolerance:
            raise ValueError("Placement distal length exceeds its reference edge")
        row["edge_num"] = edge
        rows.append(row)
    if math.fsum(row["like_weight_ratio"] for row in rows) > 1 + WEIGHT_TOLERANCE:
        raise ValueError("Reported placement weights sum to more than one")
    return rows


def analyze_jplace(data: dict, reference_labels: dict, expected_query: str) -> dict:
    """Validate one jplace query and summarize uncalibrated group association.

    ``reference_labels`` maps each reference tip to ``group`` and ``role``;
    roles are ``amr``, ``alternative`` or ``context``.  Context tips must have
    ``group=None``.  Functionally labelled tips must have a nonempty group.
    Accessions, when supplied, are a list.  Tip identities must match exactly.

    All malformed or mismatched inputs raise ``ValueError``.  A valid but
    uninformative tree returns a completed analysis with unresolved mass.
    """
    if not isinstance(data, dict):
        raise ValueError("jplace data must be an object")
    if not isinstance(expected_query, str) or not expected_query:
        raise ValueError("Expected query ID must be nonempty")
    adjacency, edges, tips = _graph(data.get("tree"))
    groups, roles = _labels(reference_labels, tips)
    if expected_query in tips:
        raise ValueError("Query cannot also be a reference tip")
    rows = _placement_rows(data, expected_query, edges)
    assignments, counts, clades = _edge_groups(adjacency, edges, tips, groups)
    masses = {group: [] for group in counts}
    placements, unresolved = [], []
    for row in sorted(rows, key=lambda item: item["edge_num"]):
        edge = row["edge_num"]
        group, reason = assignments[edge]
        weight = row["like_weight_ratio"]
        if group is None:
            unresolved.append(weight)
        else:
            masses[group].append(weight)
        placements.append({"edge_num": edge, "group": group, "reason": reason,
                           "weight": weight, "likelihood": row["likelihood"],
                           "distal_length": row["distal_length"],
                           "pendant_length": row["pendant_length"]})
    support = {group: math.fsum(masses[group]) for group in sorted(masses)}
    maximum = max(support.values(), default=0.0)
    best = [group for group, mass in support.items()
            if maximum > 0 and math.isclose(mass, maximum, rel_tol=1e-12, abs_tol=1e-12)]
    reported = math.fsum(row["like_weight_ratio"] for row in rows)
    warnings = []
    if len(counts) < 2:
        warnings.append("fewer_than_two_functional_groups")
    if "alternative" not in roles.values():
        warnings.append("no_alternative_function_reference_group")
    if any(value < 2 for value in counts.values()):
        warnings.append("singleton_group_references_do_not_define_a_scored_clade")
    if 1 - reported > WEIGHT_TOLERANCE:
        warnings.append("unreported_placement_weight_not_renormalized")
    if reported > 1:
        warnings.append("reported_mass_exceeds_one_within_rounding_tolerance")
    if len(best) > 1:
        warnings.append("tied_best_group_support")
    return {
        "status": "completed", "policy": POLICY, "query_id": expected_query,
        "score_type": "uncalibrated_placement_likelihood_weight_mass",
        "function_probability": None,
        "group_support": support, "group_roles": dict(sorted(roles.items())),
        "best_group": best[0] if len(best) == 1 else None,
        "best_group_support": maximum,
        "assigned_mass": math.fsum(support.values()),
        "unresolved_mass": math.fsum(unresolved),
        "missing_mass": max(0.0, 1.0 - reported), "reported_mass": reported,
        "reference_group_counts": counts, "reference_tip_count": len(tips),
        "context_tip_count": len(tips) - len(groups),
        "homogeneous_clades": clades, "placements": placements,
        "confidence_assessment": ("uncalibrated_phylogenetic_association"
                                  if len(best) == 1 else "insufficient_group_placement_evidence"),
        "warnings": warnings,
        "interpretation": "Group association is conditional on the reference panel, alignment and evolutionary model; likelihood weight is not a probability of AMR function, isolate resistance or safety.",
        "root_policy": "Newick root is a representation only; group sides are evaluated on the undirected tree.",
    }
