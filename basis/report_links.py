"""Stable, sample-scoped anchors shared by the offline report views."""
import hashlib
import json


def candidate_anchor(sample_id, protein_id):
    value = json.dumps([str(sample_id), str(protein_id)], ensure_ascii=False,
                       separators=(",", ":")).encode("utf-8")
    return "candidate-" + hashlib.sha256(value).hexdigest()[:20]


def candidate_section_anchor(sample_id, protein_id, section):
    if section not in {'overview', 'domains', 'motifs', 'trees', 'placement', 'references', 'files'}:
        raise ValueError('Unknown candidate report section')
    return candidate_anchor(sample_id, protein_id) + '-' + section
