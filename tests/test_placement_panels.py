import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from basis.placement_panels import (
    PanelResourceError, load_registry, normalize_sequence, prepare_panel, validate_registry,
)


def checksum(sequence):
    return hashlib.sha256(sequence.upper().rstrip("*").encode("ascii")).hexdigest()


def reference(accession, sequence, group="Cfr", role="amr", **kwargs):
    result = {"accession": accession, "sequence": sequence,
              "sequence_sha256": checksum(sequence), "group": group, "role": role,
              "review_status": "source_reviewed", "evidence_basis": "experimental",
              "pubmed_ids": ["12345678"]}
    result.update(kwargs)
    return result


def registry():
    return {"schema_version": 1, "registry_id": "test-v1", "panels": [{
        "panel_id": "cfr-v1", "candidate_aliases": ["cfr", "Cfr family"],
        "groups": [{"group_id": "Cfr", "role": "amr", "required": True},
                   {"group_id": "RlmN", "role": "alternative", "required": True}],
        "references": [reference("Q9FBG4", "ACDEFGHIK"),
                       reference("A5HBL2", "ACDEYGHIK"),
                       reference("P36979", "CDEFGHIKL", "RlmN", "alternative"),
                       reference("O34617", "CDEYGHIKL", "RlmN", "alternative")]}]}


def candidate(gene="cfr", **kwargs):
    result = {"gene": gene, "protein_id": "query", "source_evidence": [
        {"gene": gene, "tool": "amrfinder", "evidence_level": "curated_model", "attributes": {}}]}
    result.update(kwargs)
    return result


class Response(io.BytesIO):
    def __init__(self, raw, url):
        super().__init__(raw)
        self.url = url

    def geturl(self):
        return self.url


class PlacementPanelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def prepare(self, data=None, query=None, **kwargs):
        return prepare_panel(query or candidate(), data or registry(), self.root, **kwargs)

    def test_pinned_source_reviewed_contrasting_panel(self):
        r = self.prepare()
        self.assertTrue(r["adequacy"]["eligible"])
        self.assertEqual(r["adequacy"]["distinct_qualified_anchors_by_group"], {"Cfr": 2, "RlmN": 2})
        self.assertEqual(len(r["members"]), 4)
        self.assertIsNone(r["provenance"]["function_probability"])
        self.assertTrue(Path(r["provenance"]["panel_manifest"]).is_file())

    def test_explicit_panel_selection_needs_no_candidate_label_or_alias_routing(self):
        with patch("basis.placement_panels._route", side_effect=AssertionError("must not route")):
            result = prepare_panel({}, registry(), self.root, allow_download=False, panel_id="cfr-v1")
        self.assertTrue(result["adequacy"]["eligible"])
        self.assertEqual(result["provenance"]["routing"], {
            "policy": "explicit_registry_panel_selection_v1", "requested_panel_id": "cfr-v1",
            "matched_panels": ["cfr-v1"],
            "candidate_qualification": "not_evaluated_by_explicit_panel_selection"})
        # Reference identity and source-qualification gates are unchanged.
        self.assertEqual(result["panel_sha256"], self.prepare()["panel_sha256"])

    def test_explicit_panel_selection_is_exact_and_does_not_fall_back_to_candidate(self):
        for key in ("cfr", "CFR-V1", "cfr-v1 ", "", 12):
            with self.subTest(key=key), self.assertRaises(ValueError):
                prepare_panel(candidate(), registry(), self.root, allow_download=False, panel_id=key)

    def test_explicit_panel_selection_rejects_registry_mutation_and_retains_source_holds(self):
        path = self.root / "registry.json"
        path.write_text(json.dumps(registry()))
        loaded = load_registry(path)
        loaded["panels"][0]["references"][0]["pubmed_ids"] = []
        with self.assertRaisesRegex(ValueError, "modified"):
            prepare_panel({}, loaded, self.root, panel_id="cfr-v1")
        data = registry()
        data["panels"][0]["references"][0]["review_status"] = "unreviewed"
        result = prepare_panel({}, data, self.root, panel_id="cfr-v1")
        self.assertFalse(result["adequacy"]["eligible"])
        self.assertIn("insufficient_distinct_experimental_anchors:Cfr", result["adequacy"]["reasons"])

    def test_identical_cfr_accessions_count_once(self):
        data = registry()
        data["panels"][0]["references"][1] = reference("A5HBL2", "ACDEFGHIK")
        r = self.prepare(data)
        self.assertFalse(r["adequacy"]["eligible"])
        self.assertEqual(r["adequacy"]["distinct_qualified_anchors_by_group"]["Cfr"], 1)
        member = next(x for x in r["members"] if x["group"] == "Cfr")
        self.assertEqual(member["accessions"], ["A5HBL2", "Q9FBG4"])
        self.assertIn("insufficient_distinct_experimental_anchors:Cfr", r["adequacy"]["reasons"])

    def test_conflicting_identical_sequences_hold_instead_of_voting(self):
        data = registry()
        data["panels"][0]["references"].append(reference("TEST", "ACDEFGHIK", "RlmN", "alternative"))
        r = self.prepare(data)
        self.assertFalse(r["adequacy"]["eligible"])
        conflict = next(x for x in r["members"] if x["label_conflict"])
        self.assertIsNone(conflict["group"])
        self.assertEqual(conflict["role"], "context")
        self.assertEqual(r["adequacy"]["distinct_qualified_anchors_by_group"]["Cfr"], 1)
        self.assertEqual(self.prepare(data)["panel_sha256"], r["panel_sha256"])

    def test_unreviewed_annotation_and_missing_pmid_do_not_anchor(self):
        for change in ({"review_status": "unreviewed"}, {"evidence_basis": "homology"},
                       {"pubmed_ids": []}):
            with self.subTest(change=change):
                data = registry()
                data["panels"][0]["references"][0].update(change)
                r = self.prepare(data)
                self.assertFalse(r["adequacy"]["eligible"])
                member = next(x for x in r["members"] if "Q9FBG4" in x["accessions"])
                self.assertIsNone(member["group"])
                self.assertEqual(member["role"], "context")

    def test_context_duplicate_does_not_erase_real_anchor(self):
        data = registry()
        data["panels"][0]["references"].append(reference("OTHER", "ACDEFGHIK", None, "context",
                                                          evidence_basis="annotation"))
        r = self.prepare(data)
        self.assertTrue(r["adequacy"]["eligible"])
        self.assertEqual(r["adequacy"]["distinct_qualified_anchors_by_group"]["Cfr"], 2)

    def test_weak_label_does_not_route_and_qualified_alternate_does(self):
        query = candidate("other", family_labels=["cfr"])
        query["source_evidence"].append({"gene": "cfr", "tool": "rgi", "evidence_level": "exploratory",
                                         "attributes": {"cutoff": "loose"}})
        self.assertEqual(self.prepare(query=query)["adequacy"]["reasons"], ["no_supported_family_panel"])
        query["source_evidence"][-1].update(evidence_level="curated_model", attributes={"cutoff": "strict"})
        self.assertTrue(self.prepare(query=query)["adequacy"]["eligible"])

    def test_missing_source_gene_and_substring_matching_cannot_route(self):
        self.assertFalse(self.prepare(query={"gene": "cfr"})["adequacy"]["eligible"])
        self.assertFalse(self.prepare(query=candidate("not-cfr"))["adequacy"]["eligible"])
        query = {"gene": "cfr", "label_provenance": {"policy": "qualified_source_labels_v1",
                                                     "qualification": "curated_model_screen_evidence"}}
        self.assertTrue(self.prepare(query=query)["adequacy"]["eligible"])

    def test_ambiguous_routing_abstains(self):
        data = registry()
        second = copy.deepcopy(data["panels"][0])
        second["panel_id"] = "second"
        data["panels"].append(second)
        self.assertEqual(self.prepare(data)["adequacy"]["reasons"], ["ambiguous_family_routing"])

    def test_hash_mismatch_and_multiple_stops_rejected(self):
        data = registry()
        data["panels"][0]["references"][0]["sequence"] = "ACDEFGHIL"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.prepare(data)
        for seq in ("AC*D", "ACD**", "AC-D", "", "ACD E"):
            with self.assertRaises(ValueError):
                normalize_sequence(seq)
        self.assertEqual(normalize_sequence("acde*")[0], "ACDE")

    def test_normalization_audited_and_single_stop_duplicates(self):
        data = registry()
        data["panels"][0]["references"][0]["sequence"] = "acdefghik*"
        r = self.prepare(data)
        audit = r["provenance"]["retrievals"][0]["normalization"]
        self.assertTrue(audit["terminal_stop_removed"])
        self.assertTrue(audit["case_normalized"])

    def test_registry_load_pins_content_and_refuses_mutation(self):
        path = self.root / "registry.json"
        path.write_text(json.dumps(registry()))
        loaded = load_registry(path)
        self.assertTrue(self.prepare(loaded)["adequacy"]["eligible"])
        loaded["panels"][0]["references"][0]["pubmed_ids"] = ["987654"]
        with self.assertRaisesRegex(ValueError, "modified"):
            self.prepare(loaded)

    def test_remote_download_pinned_and_cached_without_repeat(self):
        data = registry()
        ref = data["panels"][0]["references"][0]
        sequence = ref.pop("sequence")
        url = "https://rest.uniprot.org/uniprotkb/Q9FBG4.fasta"
        with patch("basis.placement_panels.urlopen", return_value=Response(
                f">sp|Q9FBG4|CFR_BACTERIA\n{sequence}\n".encode(), url)) as fetch:
            first = self.prepare(data)
            self.assertTrue(first["adequacy"]["eligible"])
            self.assertEqual(fetch.call_count, 1)
        manifest = Path(first["provenance"]["panel_manifest"])
        original = manifest.read_bytes()
        with patch("basis.placement_panels.urlopen", side_effect=AssertionError("should use cache")):
            second = self.prepare(data)
        self.assertEqual(second["panel_sha256"], first["panel_sha256"])
        self.assertEqual(manifest.read_bytes(), original)

    def test_download_hash_accession_and_redirect_checks(self):
        data = registry()
        data["panels"][0]["references"][0].pop("sequence")
        url = "https://rest.uniprot.org/uniprotkb/Q9FBG4.fasta"
        cases = [(b">sp|Q9FBG4|X\nACDEFGHIL\n", url),
                 (b">sp|WRONG|X\nACDEFGHIK\n", url),
                 (b">sp|Q9FBG4|X\nACDEFGHIK\n", "https://example.org/x")]
        for raw, effective in cases:
            with self.subTest(raw=raw, effective=effective):
                with patch("basis.placement_panels.urlopen", return_value=Response(raw, effective)):
                    with self.assertRaises(PanelResourceError):
                        self.prepare(data)

    def test_versioned_ncbi_download_uses_exact_protein_accession(self):
        data = registry()
        ref = data["panels"][0]["references"][0]
        ref["accession"] = "WP_012116915.1"
        sequence = ref.pop("sequence")
        url = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
               "db=protein&id=WP_012116915.1&rettype=fasta&retmode=text")
        ref["source_url"] = url
        with patch("basis.placement_panels.urlopen", return_value=Response(
                f">WP_012116915.1 methyltransferase\n{sequence}\n".encode(), url)) as fetch:
            r = self.prepare(data)
        self.assertTrue(r["adequacy"]["eligible"])
        self.assertEqual(fetch.call_args.args[0].full_url, url)

    def test_local_catalogue_reuse_requires_exact_sequence_hash(self):
        data = registry()
        data["panels"][0]["references"][0].pop("sequence")
        catalog = SimpleNamespace(sequences={"different_accession_same_sequence": "ACDEFGHIK*"})
        r = self.prepare(data, catalogue=catalog, allow_download=False)
        self.assertTrue(r["adequacy"]["eligible"])
        self.assertEqual(r["provenance"]["retrievals"][0]["source"], "verified_local_catalogue")

    def test_disabled_download_is_resource_hold_not_false_negative(self):
        data = registry()
        data["panels"][0]["references"][0].pop("sequence")
        r = self.prepare(data, allow_download=False)
        self.assertFalse(r["adequacy"]["eligible"])
        self.assertIn("pinned_reference_resources_unavailable", r["adequacy"]["reasons"])
        self.assertEqual(r["adequacy"]["missing_accessions"], ["Q9FBG4"])

    def test_corrupt_caches_never_trusted_or_silently_replaced(self):
        data = registry()
        ref = data["panels"][0]["references"][0]
        ref.pop("sequence")
        path = self.root / "sequences" / (ref["sequence_sha256"] + ".json")
        path.parent.mkdir()
        path.write_text('{"schema_version":1,"sequence":"BAD"}')
        with self.assertRaises(PanelResourceError):
            self.prepare(data)
        r = self.prepare()
        Path(r["provenance"]["panel_manifest"]).write_text("{}")
        with self.assertRaises(PanelResourceError):
            self.prepare()

    def test_positive_only_panel_and_invalid_sources_rejected(self):
        data = registry()
        data["panels"][0]["groups"][1]["required"] = False
        with self.assertRaisesRegex(ValueError, "alternative"):
            validate_registry(data)
        data = registry()
        ref = data["panels"][0]["references"][0]
        ref.pop("sequence")
        ref["source_url"] = "https://example.org/protein.fasta"
        with self.assertRaisesRegex(ValueError, "official"):
            validate_registry(data)


if __name__ == "__main__":
    unittest.main()
