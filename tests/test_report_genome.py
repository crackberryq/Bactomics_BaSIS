"""Genomic report payload checks: coordinates, provenance and evidence scope."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest

from basis.report_genome import build_genome_data


def record(module, findings=(), status="completed", sid="s"):
    return {"sample_id": sid, "module": module, "execution_status": status,
            "findings": list(findings)}


def finding(ident="f1", pid="p1", gene="cfr", **kwargs):
    result = {"finding_id": ident, "protein_id": pid, "gene": gene,
              "category": "amr", "contig": "chr", "start": 10, "end": 100,
              "strand": "+", "evidence_level": "curated_model"}
    result.update(kwargs)
    return result


def validation(length=1000):
    return {"protein_coordinates": {"p1": {"contig": "chr", "start": 10, "end": 100, "strand": "+"}},
            "contig_lengths": {"chr": length}}


def candidate(pid="p1", sid="s", **kwargs):
    result = {"sample_id": sid, "protein_id": pid, "selected": True,
              "selection_queue": "native_screen", "contig": "chr", "start": 10, "end": 100,
              "finding_ids": ["f1"], "execution": "completed"}
    result.update(kwargs)
    return result


def deep(candidates, sid="s", status="completed"):
    result = record("deepsafe", status=status, sid=sid)
    result["candidates"] = candidates
    return result


def get_track(sample, ident):
    return next(t for t in sample["tracks"] if t["id"] == ident)


class GenomePayloadTests(unittest.TestCase):
    def test_deduplicates_same_protein_but_preserves_source_calls_without_mutation(self):
        run = {"input_validations": {"s": validation()}}
        records = [record("amrfinder", [finding()]),
                   record("rgi", [finding("f2", gene="clbA"),
                                  finding("weak", gene="Loose label", evidence_level="exploratory")])]
        original = copy.deepcopy((run, records))
        data = build_genome_data(run, [{"sample_id": "s"}], records, [])
        sample = data["samples"][0]
        regions = sample["contigs"][0]["regions"]
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["source_ids"], ["f1", "f2"])
        self.assertEqual(regions[0]["label"], "amrfinder: cfr; rgi: clbA")
        self.assertNotIn("Loose label", str(data))
        self.assertEqual(get_track(sample, "amr")["count"], 1)
        self.assertEqual((run, records), original)

    def test_zeros_unassessed_and_failed_are_distinct_and_bgc_is_not_an_island(self):
        records = [record("genomad"), record("mobsuite"), record("integronfinder", status="failed"),
                   record("vfdb", [finding(category="virulence")], status="failed"),
                   record("antismash", [finding(pid=None, category="bgc", family="terpene")])]
        sample = build_genome_data({"input_validations": {"s": validation()}}, [], records, [])["samples"][0]
        self.assertEqual(get_track(sample, "plasmid"), {"id": "plasmid", "label": "Predicted plasmid regions", "status": "completed", "count": 0})
        self.assertEqual(get_track(sample, "integron")["status"], "failed")
        self.assertEqual(get_track(sample, "vfdb")["count"], 0)
        self.assertEqual(get_track(sample, "bgc")["count"], 1)
        self.assertEqual(get_track(sample, "genomic_islands")["status"], "not_assessed")
        self.assertEqual(get_track(sample, "genomic_islands")["count"], 0)

    def test_rejects_invalid_coordinates_and_does_not_repair_conflicting_calls(self):
        invalids = [(0, 10), (20, 10), (5, 1001), (True, 50), (1.5, 5), (1, float("inf"))]
        records = [record("rgi", [finding(str(i), pid="bad" + str(i), start=a, end=b)
                                   for i, (a, b) in enumerate(invalids)] +
                                 [finding("conflict", start=11), finding("good")])]
        val = validation()
        val["protein_coordinates"]["invalid"] = {"contig": "chr", "start": 1, "end": 1001}
        sample = build_genome_data({"input_validations": {"s": val}}, [], records, [])["samples"][0]
        self.assertEqual(len(sample["contigs"][0]["regions"]), 1)
        self.assertEqual(len(sample["contigs"][0]["genes"]), 1)
        self.assertTrue(any("conflicting finding coordinates" in n for n in sample["notes"]))

    def test_unknown_contig_length_is_never_inferred_from_a_gene_endpoint(self):
        val = validation()
        del val["contig_lengths"]
        sample = build_genome_data({"input_validations": {"s": val}}, [{"sample_id": "s"}], [], [])["samples"][0]
        contig = sample["contigs"][0]
        self.assertIsNone(contig["length"])
        self.assertEqual(contig["length_source"], "unavailable")
        self.assertEqual(contig["genes"][0]["end"], 100)
        self.assertTrue(any("not assembly length" in n for n in sample["notes"]))

    def test_viral_contigs_are_not_mislabeled_as_integrated_proviruses(self):
        calls = [finding("integrated", pid=None, category="prophage", attributes={"integrated": True}),
                 finding("viral", pid=None, category="prophage", start=200, end=900, attributes={"integrated": False})]
        sample = build_genome_data({"input_validations": {"s": validation()}}, [],
                                  [record("genomad", calls)], [])["samples"][0]
        self.assertEqual(get_track(sample, "provirus")["label"], "Virus / provirus predictions")
        regions = sample["contigs"][0]["regions"]
        self.assertEqual([r["kind"] for r in regions], ["integrated_provirus", "viral_contig"])
        self.assertTrue(regions[0]["integrated"])
        self.assertFalse(regions[1]["integrated"])
        self.assertNotIn("provirus", regions[1]["label"])

    def test_selection_anchors_are_sample_scoped_and_attention_labels_ignore_loose_gene(self):
        val = validation()
        run = {"input_validations": {"s": val, "other": copy.deepcopy(val)}}
        c = candidate(selection_queue="ml_attention", gene="Loose resistance name",
                      assessment={"interpretation": "unresolved", "followup": "hold_for_taxonomic_review"})
        records = [deep([c, candidate("deferred", selected=False), candidate("wrong", sid="other")]),
                   deep([candidate(sid="other")], sid="other")]
        payload = build_genome_data(run, [], [], records)
        samples = {s["id"]: s for s in payload["samples"]}
        g1 = samples["s"]["contigs"][0]["genes"][0]
        g2 = samples["other"]["contigs"][0]["genes"][0]
        self.assertNotEqual(g1["candidate_anchor"], g2["candidate_anchor"])
        self.assertEqual(get_track(samples["s"], "selected")["count"], 1)
        self.assertEqual(get_track(samples["s"], "ml_attention")["count"], 1)
        self.assertEqual(g1["review_hold"], "hold_for_taxonomic_review")
        self.assertEqual(g1["labels"], [])
        self.assertNotIn("Loose resistance name", str(payload))
        self.assertNotIn("deferred", str(payload))
        self.assertTrue(any("cross-sample candidate" in n for n in samples["s"]["notes"]))

    def test_missing_finding_coordinates_use_only_explicit_input_protein_mapping(self):
        calls = [finding(contig=None, start=None, end=None),
                 finding("f2", pid="unknown", contig=None, start=None, end=None)]
        sample = build_genome_data({"input_validations": {"s": validation()}}, [], [record("rgi", calls)], [])["samples"][0]
        self.assertEqual(get_track(sample, "amr")["count"], 1)
        self.assertEqual(sample["contigs"][0]["regions"][0]["start"], 10)

    def test_only_hash_verified_inputs_enrich_gc_and_annotation_products(self):
        with tempfile.TemporaryDirectory() as tmp:
            fasta, gff = Path(tmp) / "a.fna", Path(tmp) / "a.gff"
            fasta.write_text(">chr\n" + "GC" * 500 + "N" * 1000 + "\n")
            gff.write_text("##gff-version 3\nchr\ttest\tCDS\t10\t100\t.\t+\t0\tID=p1;product=Input%20annotation\n")
            val = validation(2000)
            val["input_hashes"] = {"assembly": hashlib.sha256(fasta.read_bytes()).hexdigest(),
                                   "gff": hashlib.sha256(gff.read_bytes()).hexdigest()}
            sample_input = {"sample_id": "s", "assembly": str(fasta), "gff": str(gff)}
            run = {"input_validations": {"s": val}}
            sample = build_genome_data(run, [sample_input], [], [])["samples"][0]
            contig = sample["contigs"][0]
            self.assertEqual(contig["genes"][0]["product"], "Input annotation")
            self.assertEqual(contig["gc"][0]["gc_pct"], 100.0)
            self.assertEqual(contig["gc"][0]["gc_skew"], 0.0)
            self.assertIsNone(contig["gc"][1]["gc_pct"])
            self.assertIsNone(contig["gc"][1]["gc_skew"])
            fasta.write_text(">chr\n" + "A" * 2000 + "\n")
            gff.write_text(gff.read_text().replace("Input%20annotation", "Changed"))
            stale = build_genome_data(run, [sample_input], [], [])["samples"][0]
            self.assertNotIn("gc", stale["contigs"][0])
            self.assertNotIn("product", stale["contigs"][0]["genes"][0])
            self.assertEqual([s["status"] for s in stale["sources"] if s["role"] in ("assembly", "gff")],
                             ["checksum_mismatch", "checksum_mismatch"])

    def test_conflicting_recorded_hashes_prevent_read_enrichment(self):
        with tempfile.TemporaryDirectory() as tmp:
            fasta = Path(tmp) / "a.fna"
            fasta.write_text(">chr\n" + "A" * 1000 + "\n")
            val = validation()
            val["input_hashes"] = {"assembly": hashlib.sha256(fasta.read_bytes()).hexdigest()}
            r = record("rgi")
            r["input_hashes"] = {"assembly": "0" * 64}
            sample = build_genome_data({"input_validations": {"s": val}},
                                       [{"sample_id": "s", "assembly": str(fasta)}], [r], [])["samples"][0]
            self.assertNotIn("gc", sample["contigs"][0])
            source = next(s for s in sample["sources"] if s["role"] == "assembly")
            self.assertEqual(source["status"], "recorded_hash_unavailable_or_conflicting")


if __name__ == "__main__":
    unittest.main()
