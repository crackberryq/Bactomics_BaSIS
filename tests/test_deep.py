"""DeepSafe evidence integrity tests; no external tools or internet required."""
import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from basis.deep import run_deep, select_candidates, parse_similarity, parse_domtbl
from basis.references import (load_catalog, read_fasta, prepare_accession_catalog,
                              profile_for_candidate, validate_profiles, write_fasta)


class StubRunner:
    def __init__(self, fail=False, lose_query=False, fail_hmm=False):
        self.fail = fail
        self.lose_query = lose_query
        self.fail_hmm = fail_hmm
        self.calls = []

    def run(self, argv, stdout_path=None, cwd=None):
        self.calls.append(argv)
        if self.fail:
            raise RuntimeError("Synthetic external-tool failure")
        if argv[0] == "blastp":
            queries = read_fasta(Path(argv[argv.index("-query") + 1]))
            references = read_fasta(Path(argv[argv.index("-subject") + 1]))
            with Path(argv[argv.index("-out") + 1]).open("w") as out:
                for query, sequence in queries.items():
                    for reference, refseq in references.items():
                        n = min(len(sequence), len(refseq))
                        out.write(f"{query}\t{reference}\t90\t{n}\t{len(sequence)}\t{len(refseq)}\t1\t{n}\t1\t{n}\t1e-30\t100\n")
        elif argv[0] == "mafft":
            records = read_fasta(Path(argv[-1]))
            if self.lose_query:
                records.pop(next(iter(records)))
            width = max(map(len, records.values()))
            write_fasta({k: v + "-" * (width - len(v)) for k, v in records.items()}, Path(stdout_path))
        elif argv[0] == "iqtree":
            records = read_fasta(Path(argv[argv.index("-s") + 1]))
            prefix = argv[argv.index("-pre") + 1]
            Path(prefix + ".treefile").write_text("(" + ",".join(k + ":0.1" for k in records) + ");\n")
        elif argv[0] == "hmmscan":
            if self.fail_hmm:
                raise RuntimeError("Synthetic HMMER failure")
            records = read_fasta(Path(argv[-1]))
            query = next(iter(records))
            length = len(records[query])
            Path(argv[argv.index("--domtblout") + 1]).write_text(
                f"Example PFTEST.1 10 {query} - {length} 1e-20 40 0 1 1 1e-20 1e-20 40 0 1 10 1 10 1 10 0.99 Synthetic domain\n# Program: hmmscan\n# [ok]\n")
            Path(stdout_path).write_text("Synthetic native output\n")
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")


class DeepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_fasta({"c1": "ACGT" * 200}, self.root / "assembly.fna")
        write_fasta({"p1": "MCKACGACAAAA", "p2": "MCKACGACAAAA"}, self.root / "proteins.faa")
        self.sample = {"sample_id": "A", "assembly": str(self.root / "assembly.fna"), "proteins": str(self.root / "proteins.faa")}

    def tearDown(self):
        self.tmp.cleanup()

    def finding(self, **changes):
        value = {"finding_id": "f1", "tool": "amrfinder", "category": "amr", "gene": "example",
                 "family": "example", "protein_id": "p1", "contig": "c1", "start": 10, "end": 45,
                 "strand": "+", "evidence_level": "curated_model", "attributes": {}}
        value.update(changes)
        return value

    def catalog(self):
        sequences = {"r1": "MCKACGACAAAA", "r1_duplicate": "MCKACGACAAAA",
                     "r2": "MCQACGACAAAT", "r3": "MCDACGACAATT", "r4": "MCRACGACATTT"}
        write_fasta(sequences, self.root / "refs.faa")
        fields = ["reference_id", "accession", "family", "comparison_group", "role", "review_status", "source"]
        with (self.root / "refs.tsv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            for i, key in enumerate(sequences):
                writer.writerow(dict(zip(fields, [key, f"TEST{i}.1", "example", "test_group", "reference", "unreviewed", "synthetic_test_fixture"])))
        return load_catalog(self.root / "refs.faa", self.root / "refs.tsv")

    def config(self, **changes):
        value = {"reference_fasta": str(self.root / "refs.faa"), "reference_metadata": str(self.root / "refs.tsv"),
                 "maximum_candidates": 10, "maximum_attention_candidates": 2, "phylogeny": True,
                 "min_identity": 0, "min_coverage": 0}
        value.update(changes)
        return value

    def domain_database(self):
        path = self.root / "synthetic.hmm"
        path.write_text("HMMER3/f synthetic test data\n")
        for suffix in (".h3f", ".h3i", ".h3m", ".h3p"):
            Path(str(path) + suffix).write_text("synthetic index fixture")
        return str(path)

    def test_merge_evidence_but_keep_identical_sequence_gene_copies(self):
        findings = [self.finding(), self.finding(finding_id="f2", tool="rgi"),
                    self.finding(finding_id="f3", protein_id="p2", start=100, end=135),
                    self.finding(finding_id="f4", contig=None, start=None, end=None, tool="vfdb")]
        candidates = select_candidates(findings, {})
        self.assertEqual(len(candidates), 2)
        first = next(c for c in candidates if c["protein_id"] == "p1")
        self.assertEqual(set(first["finding_ids"]), {"f1", "f2", "f4"})

    def test_attention_candidates_do_not_displace_direct_evidence(self):
        findings = [self.finding(finding_id=f"a{i}", category="ml_attention", protein_id=f"att{i}", start=100 + 10*i, end=105 + 10*i, attributes={"rank": 5 - i}) for i in range(5)]
        findings.append(self.finding())
        candidates = select_candidates(findings, {"maximum_candidates": 3, "maximum_attention_candidates": 1})
        chosen = [c for c in candidates if c["selected"]]
        self.assertEqual(len(chosen), 2)
        self.assertIn("amr_screen", chosen[0]["selection_reasons"])
        self.assertEqual(chosen[1]["protein_id"], "att4")
        self.assertEqual(sum(c["execution"] == "deferred" for c in candidates), 4)

    def test_large_loose_pool_cannot_displace_native_amr_or_virulence(self):
        findings = []
        native_ids = set()
        for cohort, count, tool, attributes in (
            ("core_hmm", 6, "amrfinder", {"native_method": "HMM", "scope": "core"}),
            ("strict", 9, "rgi", {"cutoff": "strict", "nudged": False}),
            ("virulence", 1, "vfdb", {}),
            ("loose", 3552, "rgi", {"cutoff": "loose", "nudged": False}),
        ):
            for index in range(count):
                identifier = f"{cohort}_{index}"
                findings.append(self.finding(
                    finding_id=identifier, protein_id=identifier, tool=tool,
                    category="virulence" if cohort == "virulence" else "amr",
                    contig=None, start=None, end=None, strand=None,
                    evidence_level="exploratory" if cohort == "loose" else "curated_model",
                    attributes=dict(attributes)))
                if cohort != "loose":
                    native_ids.add(identifier)
        candidates = select_candidates(findings, {"maximum_candidates": 100})
        selected = [candidate for candidate in candidates if candidate["selected"]]
        self.assertEqual(len(candidates), len(findings))
        self.assertEqual({c["protein_id"] for c in selected if c["priority"] == 0}, native_ids)
        self.assertEqual(sum(c["priority"] == 1 for c in selected), 20)
        self.assertEqual(sum(c["execution"] == "deferred" for c in candidates), 3532)
        self.assertEqual({f["finding_id"] for c in candidates for f in c["source_evidence"]},
                         {f["finding_id"] for f in findings})

    def test_native_and_loose_merge_before_budgets_in_either_input_order(self):
        native = self.finding(attributes={"native_method": "HMM", "scope": "core"})
        loose = self.finding(finding_id="loose", tool="rgi", gene="another_label",
                             contig=None, start=None, end=None, strand=None,
                             evidence_level="exploratory", attributes={"cutoff": "loose"})
        unrelated = self.finding(finding_id="other", tool="rgi", protein_id="p2",
                                 contig=None, start=None, end=None, strand=None,
                                 evidence_level="exploratory", attributes={"cutoff": "loose"})
        for rows in ([native, loose, unrelated], [unrelated, loose, native]):
            with self.subTest(first_finding=rows[0]["finding_id"]):
                candidates = select_candidates(rows, {
                    "maximum_candidates": 1, "maximum_exploratory_candidates": 0,
                    "include_exploratory": False})
                self.assertEqual(len(candidates), 2)
                selected = [c for c in candidates if c["selected"]]
                self.assertEqual(len(selected), 1)
                self.assertEqual(selected[0]["priority"], 0)
                self.assertEqual(selected[0]["protein_id"], "p1")
                self.assertEqual(set(selected[0]["finding_ids"]), {"f1", "loose"})
                self.assertEqual({f["finding_id"]: f for f in selected[0]["source_evidence"]},
                                 {"f1": native, "loose": loose})

    def test_exploratory_followup_respects_its_cap_and_global_cap(self):
        rows = [self.finding()]
        rows.extend(self.finding(finding_id=f"loose{i}", tool="rgi", protein_id=f"loose{i}",
                                 contig=None, start=None, end=None, strand=None,
                                 evidence_level="exploratory", attributes={"cutoff": "loose"})
                    for i in range(4))
        for maximum, exploratory_maximum, expected in ((10, 2, 2), (2, 4, 1), (10, 0, 0)):
            with self.subTest(maximum=maximum, exploratory_maximum=exploratory_maximum):
                candidates = select_candidates(rows, {"maximum_candidates": maximum,
                    "maximum_exploratory_candidates": exploratory_maximum})
                self.assertTrue(next(c for c in candidates if c["protein_id"] == "p1")["selected"])
                self.assertEqual(sum(c["selected"] and c["priority"] == 1 for c in candidates), expected)
                for candidate in candidates:
                    if not candidate["selected"]:
                        self.assertEqual(candidate["execution"], "deferred")
                        self.assertEqual(candidate["reason"], "candidate_resource_budget")

    def test_candidate_budget_and_exploration_config_reject_invalid_types(self):
        invalid = []
        for key in ("maximum_candidates", "maximum_attention_candidates", "maximum_exploratory_candidates"):
            invalid.extend((key, value) for value in (True, False, 0.5, 1.0, "2", None, -1))
        invalid.append(("maximum_candidates", 0))
        invalid.extend(("include_exploratory", value) for value in ("false", "true", 0, None))
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(ValueError, key):
                    select_candidates([self.finding()], {key: value})

    def test_exploratory_disabled_preserves_candidates_evidence_and_defer_reason(self):
        rows = [self.finding(finding_id="loose", tool="rgi", evidence_level="exploratory",
                             attributes={"cutoff": "loose"}),
                self.finding(finding_id="vir", category="virulence", protein_id="p2",
                             start=100, end=135, evidence_level="exploratory")]
        runner = StubRunner()
        result = run_deep(self.sample, rows, {"include_exploratory": False},
                          self.root / "disabled", runner)
        self.assertEqual(len(result["candidates"]), 2)
        for candidate in result["candidates"]:
            self.assertFalse(candidate["selected"])
            self.assertEqual(candidate["priority"], 1)
            self.assertEqual(candidate["execution"], "deferred")
            self.assertEqual(candidate["reason"], "exploratory_followup_disabled")
            self.assertEqual(candidate["analysis_plan"]["decision_trace"][0]["reason"],
                             "exploratory_followup_disabled")
            self.assertEqual(candidate["interpretation"], "inconclusive")
        self.assertEqual({f["finding_id"]: f for c in result["candidates"] for f in c["source_evidence"]},
                         {f["finding_id"]: f for f in rows})
        self.assertEqual(runner.calls, [])

    def test_rgi_cutoff_and_nudged_override_mislabeled_native_evidence(self):
        for attributes in ({"cutoff": "Loose", "nudged": False},
                           {"cutoff": "strict", "nudged": True},
                           {"cutoff": "perfect", "nudged": True}):
            with self.subTest(attributes=attributes):
                finding = self.finding(tool="rgi", evidence_level="curated_model", attributes=attributes)
                candidate = select_candidates([finding], {"maximum_exploratory_candidates": 0})[0]
                self.assertEqual(candidate["priority"], 1)
                self.assertFalse(candidate["selected"])
                self.assertEqual(candidate["source_evidence"], [finding])

    def test_loose_plus_attention_uses_exploratory_budget_without_native_promotion(self):
        rows = [self.finding(),
                self.finding(finding_id="native_attention", category="ml_attention",
                             attributes={"rank": 1}),
                self.finding(finding_id="loose", tool="rgi", protein_id="p2", start=100, end=135,
                             evidence_level="exploratory", attributes={"cutoff": "loose"}),
                self.finding(finding_id="loose_attention", category="ml_attention", protein_id="p2",
                             start=100, end=135, attributes={"rank": 2}),
                self.finding(finding_id="attention", category="ml_attention", protein_id="p3",
                             start=200, end=235, attributes={"rank": 3})]
        for exploratory_maximum, attention_maximum in ((0, 1), (1, 0)):
            with self.subTest(exploratory_maximum=exploratory_maximum):
                candidates = select_candidates(rows, {"maximum_candidates": 10,
                    "maximum_exploratory_candidates": exploratory_maximum,
                    "maximum_attention_candidates": attention_maximum})
                by_protein = {c["protein_id"]: c for c in candidates}
                self.assertEqual(len(by_protein), 3)
                self.assertEqual([by_protein[p]["priority"] for p in ("p1", "p2", "p3")], [0, 1, 2])
                self.assertTrue(by_protein["p1"]["selected"])
                self.assertEqual(by_protein["p2"]["selected"], bool(exploratory_maximum))
                self.assertEqual(by_protein["p3"]["selected"], bool(attention_maximum))
                self.assertEqual(set(by_protein["p2"]["finding_ids"]), {"loose", "loose_attention"})

    def test_explicit_mixed_attention_policy_preserves_native_and_budgets(self):
        rows=[self.finding(),
              self.finding(finding_id="a1",category="ml_attention",attributes={"rank":1}),
              self.finding(finding_id="loose",protein_id="p2",start=100,end=135,
                           evidence_level="exploratory",attributes={"cutoff":"loose"}),
              self.finding(finding_id="a2",category="ml_attention",protein_id="p2",start=100,end=135,
                           evidence_level="exploratory",attributes={"rank":2}),
              self.finding(finding_id="a3",category="ml_attention",protein_id="p3",start=200,end=235,
                           attributes={"rank":3})]
        cfg={"maximum_candidates":10,"maximum_attention_candidates":1,"include_exploratory":False,
             "attention_queue_for_mixed_evidence":True}
        cs={c['protein_id']:c for c in select_candidates(rows,cfg)}
        self.assertEqual(len(cs),3)
        self.assertEqual(cs['p1']['selection_queue'],'native_screen');self.assertTrue(cs['p1']['selected'])
        self.assertEqual(cs['p2']['selection_queue'],'ml_attention');self.assertTrue(cs['p2']['selected'])
        self.assertFalse(cs['p3']['selected'])
        self.assertEqual(cs['p2']['priority'],2)
        self.assertTrue(all(f['evidence_level']=='exploratory' for f in cs['p2']['source_evidence']))
        cfg['maximum_attention_candidates']=0
        self.assertFalse(next(c for c in select_candidates(rows,cfg) if c['protein_id']=='p2')['selected'])

    def test_missing_catalogue_preserves_query_and_is_not_assessed(self):
        result = run_deep(self.sample, [self.finding()], {}, self.root / "deep", StubRunner())
        candidate = result["candidates"][0]
        self.assertEqual(candidate["execution"], "not_assessed")
        self.assertEqual(candidate["interpretation"], "inconclusive")
        self.assertTrue(Path(candidate["query"]["path"]).is_file())
        self.assertFalse(result["complete"])

    def test_reference_search_failure_never_becomes_no_hit(self):
        self.catalog()
        with self.assertRaisesRegex(RuntimeError, "failure"):
            run_deep(self.sample, [self.finding()], self.config(), self.root / "deep", StubRunner(fail=True))

    def test_missing_and_malformed_similarity_outputs_fail(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            parse_similarity(self.root / "absent.tsv")
        path = self.root / "bad.tsv"
        path.write_text("NO_HIT\n")
        with self.assertRaisesRegex(ValueError, "12 columns"):
            parse_similarity(path)
        path.write_text("")
        self.assertEqual(parse_similarity(path), {})

    def test_automatic_references_keep_query_and_duplicate_reference_provenance(self):
        self.catalog()
        runner = StubRunner()
        result = run_deep(self.sample, [self.finding()], self.config(), self.root / "deep", runner)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["execution"], "completed")
        self.assertEqual(candidate["profile"]["validation_status"], "unsupported")
        self.assertEqual(candidate["interpretation"], "inconclusive")
        phylo = candidate["analyses"]["phylogeny"]
        self.assertEqual(phylo["status"], "completed")
        labels = json.loads(Path(phylo["labels"]).read_text())
        self.assertIn(candidate["query"]["id"], labels)
        self.assertEqual(len(labels), 5)  # query + four unique references
        self.assertEqual(len(labels["R00001"]["reference_aliases"]), 2)
        self.assertEqual(len(candidate["references"]), 5)
        self.assertIn(candidate["query"]["id"], Path(phylo["tree"]).read_text())
        self.assertTrue(any(call[0] == "blastp" for call in runner.calls))

    def test_alignment_losing_query_is_candidate_failure(self):
        self.catalog()
        result = run_deep(self.sample, [self.finding()], self.config(), self.root / "deep", StubRunner(lose_query=True))
        self.assertEqual(result["candidates"][0]["execution"], "failed")
        self.assertFalse(result["complete"])
        self.assertEqual(result["metrics"]["failed_candidates"], 1)

    def test_insufficient_reference_sequences_does_not_run_tree(self):
        self.catalog()
        runner = StubRunner()
        result = run_deep(self.sample, [self.finding()], self.config(max_references=2), self.root / "deep", runner)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["analyses"]["phylogeny"]["reason"], "fewer_than_four_distinct_sequences")
        self.assertEqual(candidate["interpretation"], "inconclusive")
        self.assertFalse(any(c[0] == "iqtree" for c in runner.calls))

    def test_missing_protein_is_not_translated_without_cds_evidence(self):
        result = run_deep(self.sample, [self.finding(protein_id="absent")], {}, self.root / "deep", StubRunner())
        self.assertEqual(result["candidates"][0]["reason"], "no_linked_protein_sequence")
        self.assertEqual(result["candidates"][0]["analyses"]["context"]["status"], "completed")

    def test_circular_context_wraps_and_keeps_context_evidence(self):
        self.sample["circular_contigs"] = ["c1"]
        records = [self.finding(), self.finding(finding_id="IS1", category="is", start=780, end=795, protein_id=None)]
        result = run_deep(self.sample, records, {"context_flank": 30}, self.root / "deep", StubRunner())
        context = result["candidates"][0]["analyses"]["context"]
        self.assertEqual(context["coordinate_segments"], [(780, 800), (1, 75)])
        self.assertEqual(context["evidence"][0]["finding_id"], "IS1")

    def test_profile_registry_rejects_unsubstantiated_validated_status(self):
        profiles = self.root / "profiles"
        profiles.mkdir()
        (profiles / "bad.yaml").write_text("schema_version: 1\nprofile_id: bad\nvalidation_status: validated\ninterpretation_policy: exploratory_only\ncandidate_aliases: [example]\nrequired_comparison_groups: []\nanchor_reference_ids: []\n")
        loaded, errors = validate_profiles(profiles)
        self.assertEqual(loaded, [])
        self.assertIn("requires reviewer", errors[0])

    def test_accession_preparer_validates_response_and_preserves_provenance(self):
        request = self.root / "requests.tsv"
        request.write_text("accession\tfamily\nWP_012345678.1\texample\n")
        output = prepare_accession_catalog(request, self.root / "out.faa", self.root / "out.tsv",
                                           fetcher=lambda a: f">{a} synthetic fixture\nMACDEFGHIK\n")
        catalog = load_catalog(Path(output["fasta"]), Path(output["metadata"]))
        meta = catalog.metadata["WP_012345678.1"]
        self.assertEqual(meta["review_status"], "unreviewed")
        self.assertEqual(len(meta["sequence_sha256"]), 64)
        with self.assertRaisesRegex(ValueError, "does not match"):
            prepare_accession_catalog(request, self.root / "bad.faa", self.root / "bad.tsv",
                                       fetcher=lambda a: ">WP_999999999.1\nMACDEFGHIK\n")
        self.assertFalse((self.root / "bad.faa").exists())

    def test_exact_native_amr_routes_to_context_without_reference_or_tree(self):
        finding = self.finding(identity=100.0, coverage=100.0,
            attributes={"native_method": "EXACTP", "scope": "core", "flags": []})
        runner = StubRunner()
        result = run_deep(self.sample, [finding], {"phylogeny": True}, self.root / "exact", runner)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["analysis_plan"]["route"], "exact_curated_amr_context_review")
        self.assertEqual(candidate["execution"], "completed")
        self.assertEqual(candidate["analyses"]["phylogeny"]["reason"], "not_requested_by_route")
        self.assertEqual(candidate["interpretation"], "inconclusive")
        self.assertEqual(runner.calls, [])

    def test_loose_labels_do_not_override_native_exact_route_in_either_order(self):
        native = self.finding(identity=100.0, coverage=100.0,
            attributes={"native_method": "EXACTP", "scope": "core", "flags": []})
        loose = self.finding(finding_id="loose", tool="rgi", gene="conflicting_loose_label",
            contig=None, start=None, end=None, strand=None, evidence_level="exploratory",
            attributes={"cutoff": "loose", "nudged": False})
        for index, rows in enumerate(([native, loose], [loose, native])):
            with self.subTest(first_finding=rows[0]["finding_id"]):
                runner = StubRunner()
                result = run_deep(self.sample, rows, {"phylogeny": True},
                                  self.root / f"exact_mixed_{index}", runner)
                candidate = result["candidates"][0]
                self.assertEqual(candidate["analysis_plan"]["route"], "exact_curated_amr_context_review")
                self.assertEqual(candidate["execution"], "completed")
                self.assertEqual(set(candidate["finding_ids"]), {"f1", "loose"})
                self.assertEqual(candidate["interpretation"], "inconclusive")
                self.assertEqual(runner.calls, [])

    def test_exploratory_amr_and_virulence_use_exploratory_route(self):
        cases = [
            self.finding(tool="rgi", evidence_level="exploratory", attributes={"cutoff": "loose"}),
            self.finding(tool="rgi", evidence_level="curated_model", attributes={"cutoff": "Loose"}),
            self.finding(tool="rgi", evidence_level="curated_model", identity=100.0, coverage=100.0,
                         attributes={"cutoff": "perfect", "nudged": True,
                                     "model_type": "protein homolog model"}),
            self.finding(category="virulence", evidence_level="exploratory"),
        ]
        for index, finding in enumerate(cases):
            with self.subTest(finding=finding):
                attention = self.finding(finding_id="attention", category="ml_attention",
                                         attributes={"rank": 1})
                result = run_deep(self.sample, [finding, attention], {},
                                  self.root / f"exploratory_route_{index}", StubRunner())
                candidate = result["candidates"][0]
                self.assertEqual(candidate["analysis_plan"]["route"], "exploratory_family_investigation")
                self.assertEqual(candidate["priority"], 1)
                self.assertEqual(candidate["interpretation"], "inconclusive")

    def test_native_virulence_with_loose_amr_keeps_virulence_route(self):
        native = self.finding(category="virulence", tool="vfdb", evidence_level="homology")
        loose = self.finding(finding_id="loose", tool="rgi", evidence_level="exploratory",
                             attributes={"cutoff": "loose"})
        result = run_deep(self.sample, [loose, native], {}, self.root / "vir_loose", StubRunner())
        candidate = result["candidates"][0]
        self.assertEqual(candidate["priority"], 0)
        self.assertEqual(candidate["analysis_plan"]["route"], "virulence_homology_investigation")
        self.assertEqual(set(candidate["finding_ids"]), {"f1", "loose"})

    def test_integrity_flags_disable_exact_shortcut(self):
        finding = self.finding(identity=100.0, coverage=100.0,
            attributes={"native_method": "EXACTP", "scope": "core", "flags": ["partial_sequence"]})
        write_fasta({"p1": "MCKAC*ACAAAA"}, self.root / "proteins.faa")
        result = run_deep(self.sample, [finding], {}, self.root / "deep", StubRunner())
        candidate = result["candidates"][0]
        self.assertEqual(candidate["analysis_plan"]["route"], "ambiguous_amr_family_investigation")
        self.assertIn("native_integrity_concern", candidate["analyses"]["integrity"]["flags"])
        self.assertEqual(candidate["analyses"]["integrity"]["internal_stop_positions"], [6])

    def test_virulence_and_attention_routes_are_distinct(self):
        vir = run_deep(self.sample, [self.finding(category="virulence")], {}, self.root / "vir", StubRunner())
        ml = run_deep(self.sample, [self.finding(category="ml_attention")], {}, self.root / "ml", StubRunner())
        self.assertEqual(vir["candidates"][0]["analysis_plan"]["route"], "virulence_homology_investigation")
        self.assertEqual(ml["candidates"][0]["analysis_plan"]["route"], "bounded_ml_attention_exploration")

    def test_sample_ids_distinguish_identical_loci(self):
        a = run_deep(self.sample, [self.finding()], {}, self.root / "a", StubRunner())
        b = run_deep({**self.sample, "sample_id": "B"}, [self.finding()], {}, self.root / "b", StubRunner())
        self.assertNotEqual(a["candidates"][0]["candidate_id"], b["candidates"][0]["candidate_id"])

    def test_attention_maximum_is_used_when_rank_absent(self):
        rows = [self.finding(category="ml_attention", protein_id=f"p{i}", start=i*10, end=i*10+9,
                            finding_id=f"f{i}", attributes={"attention_max": i / 10}) for i in (1, 2, 3)]
        candidates = select_candidates(rows, {"maximum_attention_candidates": 1})
        self.assertEqual(candidates[0]["protein_id"], "p3")
        self.assertTrue(candidates[0]["selected"])

    def test_optional_hmmer_records_domains_and_failure_distinctly(self):
        self.catalog()
        config = self.config(domain_database=self.domain_database())
        result = run_deep(self.sample, [self.finding()], config, self.root / "ok", StubRunner())
        domains = result["candidates"][0]["analyses"]["domains"]
        self.assertEqual(domains["status"], "completed")
        self.assertEqual(domains["hits"][0]["domain_accession"], "PFTEST.1")
        failed = run_deep(self.sample, [self.finding()], config, self.root / "failed", StubRunner(fail_hmm=True))
        self.assertEqual(failed["candidates"][0]["execution"], "failed")
        self.assertEqual(failed["candidates"][0]["analyses"]["domains"]["status"], "failed")
        self.assertFalse(failed["complete"])

    def test_hmmer_disabled_and_valid_zero_hits_are_not_failures(self):
        result = run_deep(self.sample, [self.finding()], {"domain_scan": False}, self.root / "off", StubRunner())
        self.assertEqual(result["candidates"][0]["analyses"]["domains"]["status"], "not_assessed")
        path = self.root / "zero.domtbl"
        path.write_text("# Program: hmmscan\n# [ok]\n")
        self.assertEqual(parse_domtbl(path), [])
        path.write_text("")
        with self.assertRaisesRegex(ValueError, "completion"):
            parse_domtbl(path)

    def test_profile_requests_native_cutoff_without_invented_numeric_threshold(self):
        database = self.domain_database()
        profiles = self.root / "profiles"
        profiles.mkdir()
        (profiles / "example.yaml").write_text(
            "schema_version: 1\nprofile_id: example\nvalidation_status: unvalidated_template\n"
            "interpretation_policy: exploratory_only\ncandidate_aliases: [example]\n"
            "required_comparison_groups: []\nanchor_reference_ids: []\n"
            "analyses: [integrity, context, domains]\nresources:\n  domains:\n"
            f"    database: {database}\n    native_cutoff: ga\n")
        runner = StubRunner()
        result = run_deep(self.sample, [self.finding()], {"profiles_dir": str(profiles)}, self.root / "deep", runner)
        self.assertIn("--cut_ga", runner.calls[0])
        self.assertEqual(result["candidates"][0]["execution"], "completed")
        self.assertEqual(result["candidates"][0]["interpretation"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
