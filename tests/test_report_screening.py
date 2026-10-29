"""Readable reporting over synthetic saved annotations; no native analyses."""
from __future__ import annotations

import copy
import csv
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/vendor"), str(ROOT / "workflow/lib")]

from basis.adapters.amr import parse_amrfinder
from basis.report import write_report
from basis.report_screening import describe_finding, render_screening_findings, write_readable_findings
from safety.reporting import render


def synthetic_finding(**changes):
    row = {
        "module": "amrfinder", "tool": "amrfinder", "sample_id": "synthetic-sample",
        "finding_id": "synthetic-1", "protein_id": "synthetic-protein", "gene": "synthetic_marker",
        "family": "synthetic family", "category": "amr", "execution_status": "completed",
        "module_interpretation": "detected", "interpretation": "native_gene_or_variant_call_requires_context_review",
        "evidence_level": "curated_model", "identity": 97.5, "coverage": 81.25,
        "contig": "synthetic_contig", "start": 10, "end": 250, "strand": "+",
        "attributes": {"native_method": "BLASTP", "raw": {
            "Element name": "Synthetic description mentioning azithromycin for a display test",
            "Class": "SYNTHETIC CLASS", "Subclass": "AZITHROMYCIN", "Type": "AMR", "Subtype": "AMR",
        }},
    }
    row.update(changes)
    return row


class Text(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.tags = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, value):
        self.parts.append(value)

    @property
    def text(self):
        return " ".join(self.parts)


class ReadableScreeningTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="basis-readable-report-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def report(self, findings):
        run = {"run_id": "synthetic-readability", "version": "test", "report_view": "screening",
               "options": {"deep": False}, "completeness": "completed_requested_scope",
               "validation_mode": "synthetic_fixture"}
        sample = {"sample_id": "synthetic-sample", "input_mode": "external"}
        records = []
        for module in sorted({f["module"] for f in findings}):
            records.append({"sample_id": sample["sample_id"], "module": module,
                            "execution_status": "completed", "interpretation": "detected",
                            "findings": [f for f in findings if f["module"] == module], "artifacts": []})
        original = copy.deepcopy((run, sample, records))
        with patch("subprocess.Popen", side_effect=AssertionError("Report launched an analysis")):
            artifacts = write_report(run, [sample], records, [], self.root)
        self.assertEqual((run, sample, records), original)
        return artifacts, (self.root / "report.html").read_text()

    def test_amrfinder_native_rows_retain_description_class_and_subclass(self):
        for symbol, description, kind, subtype in (
            ("Element symbol", "Element name", "Type", "Subtype"),
            ("Gene symbol", "Sequence name", "Element type", "Element subtype"),
            ("Gene symbol", "Protein name", "Type", "Subtype"),
        ):
            with self.subTest(description=description):
                path = self.root / "synthetic-amrfinder.tsv"
                row = {"Protein id": "p1", "Contig id": "c1", "Start": "1", "Stop": "90", "Strand": "+",
                       symbol: "synthetic_marker", description: "Synthetic description for azithromycin display",
                       "Scope": "core", kind: "AMR", subtype: "AMR", "Class": "SYNTHETIC CLASS",
                       "Subclass": "AZITHROMYCIN", "Method": "BLASTP", "% Coverage of reference": "80",
                       "% Identity to reference": "95"}
                with path.open("w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=row, delimiter="\t")
                    writer.writeheader()
                    writer.writerow(row)
                finding = parse_amrfinder(path)[0]
                finding.update(module="amrfinder", sample_id="synthetic-sample", execution_status="completed")
                before = copy.deepcopy(finding)
                data = describe_finding(finding)
                self.assertEqual(data["description"], row[description])
                self.assertIn(("Subclass", "AZITHROMYCIN"), data["classifications"])
                self.assertEqual(finding, before)
                _, page = self.report([finding])
                self.assertIn(row[description], Text(page).text)

    def test_missing_fields_are_not_inferred_from_gene_or_drug_class(self):
        row = synthetic_finding(gene="synthetic_macrolide_marker", family=None,
                                attributes={"raw": {"Class": "MACROLIDE"}})
        data = describe_finding(row)
        self.assertEqual(data["description"], "")
        self.assertNotIn("AZITHROMYCIN", str(data))
        self.assertEqual(data["classifications"], [("Class", "MACROLIDE")])
        body = render_screening_findings([row], {}, {})
        self.assertIn("No descriptive annotation recorded.", body)

    def test_amrfinder_plus_categories_do_not_become_drug_results(self):
        row = synthetic_finding(category="stress", attributes={"raw": {
            "Element name": "Synthetic stress annotation", "Type": "STRESS", "Class": "SYNTHETIC METAL"}})
        data = describe_finding(row)
        self.assertEqual(data["category"], "Stress-response annotation")
        self.assertEqual(data["classifications"], [("Class", "SYNTHETIC METAL"), ("Element type", "STRESS")])

    def test_rgi_classifications_and_length_ratio_have_distinct_labels(self):
        row = synthetic_finding(module="rgi", coverage=None, attributes={
            "cutoff": "strict", "model_type": "synthetic model", "reference_length_ratio_percent": 121.0,
            "raw": {"Best_Hit_ARO": "Synthetic CARD annotation", "Drug Class": "synthetic drug class",
                    "Antibiotic": "synthetic drug", "Resistance Mechanism": "synthetic mechanism",
                    "AMR Gene Family": "synthetic family"}})
        data = describe_finding(row)
        self.assertIn(("Antibiotic", "synthetic drug"), data["classifications"])
        self.assertIn(("Resistance Mechanism", "synthetic mechanism"), data["classifications"])
        self.assertIn(("Reference coverage", "Not recorded"), data["matches"])
        self.assertIn(("Length ratio (not alignment coverage)", "121.0%"), data["matches"])

    def test_reference_titles_and_fragment_qualifiers_survive(self):
        title = "synthetic_reference Synthetic reference description (Fragment)"
        for module in ("vfdb", "bacmet"):
            row = synthetic_finding(module=module, reference="synthetic_reference",
                                    attributes={"subject_title": title, "query_coverage": 48.5})
            data = describe_finding(row)
            self.assertEqual(data["description"], title)
            self.assertIn(("Query coverage", "48.5%"), data["matches"])
            if module == "bacmet":
                self.assertTrue(any("Reference annotated as a fragment" in n for n in data["notes"]))

    def test_other_tools_retain_native_features_and_complete_source_fields(self):
        cases = [
            ("antismash", {"native": {"products": ["synthetic product"], "other native field": "retained"}}, "Product classes"),
            ("mobsuite", {"predicted_mobility": "synthetic prediction"}, "Predicted mobility"),
            ("mlst", {"scheme": "synthetic scheme"}, "Allele scheme"),
            ("minced", {"repeat_count": 0}, "Repeat count"),
            ("crisprcasfinder", {"feature_type": "synthetic array"}, "Feature type"),
        ]
        for module, attrs, label in cases:
            with self.subTest(module=module):
                row = synthetic_finding(module=module, attributes=attrs)
                self.assertIn(label, dict(describe_finding(row)["classifications"]))
        row = synthetic_finding(module="isescan", attributes={"native": {"type": "p"}})
        data = describe_finding(row)
        self.assertIn("Partial IS prediction", data["title"])
        self.assertTrue(data["notes"])

    def test_exploratory_provisional_and_nudged_records_keep_qualifications(self):
        for changes in ({"evidence_level": "exploratory"}, {"interpretation": "below_reporting_threshold"},
                        {"attributes": {"passes_threshold": False}}):
            self.assertEqual(describe_finding(synthetic_finding(**changes))["group"], "exploratory")
        for status in ("failed", "not_started", "not_assessed", "unknown"):
            self.assertEqual(describe_finding(synthetic_finding(execution_status=status))["group"], "provisional")
        data = describe_finding(synthetic_finding(module="rgi", evidence_level="exploratory",
                                                 attributes={"nudged": True, "cutoff": "strict"}))
        self.assertEqual(data["group"], "exploratory")
        self.assertTrue(any("nudged" in n for n in data["notes"]))

    def test_native_text_is_escaped_and_not_inserted_as_markup(self):
        attack = '<img src=x onerror="alert(1)"> & </script>'
        row = synthetic_finding(gene=attack, attributes={"raw": {"Element name": attack, "Class": attack}})
        body = render_screening_findings([row], {}, {})
        parsed = Text(body)
        self.assertIn(attack, parsed.text)
        self.assertFalse(any(tag in {"img", "script"} for tag, _ in parsed.tags))
        self.assertIn("&lt;img", body)

    def test_every_record_is_searchable_and_exported_beyond_old_250_limit(self):
        findings = [synthetic_finding(finding_id=f"synthetic-{i}", gene=f"synthetic_gene_{i}") for i in range(301)]
        findings.append(synthetic_finding(module="mlst", gene="last_tool_marker"))
        artifacts, body = self.report(findings)
        parsed = Text(body)
        rows = [a for tag, a in parsed.tags if tag == "tr" and a.get("id", "").startswith("screening-finding-")]
        self.assertEqual(len(rows), 302)
        self.assertIn("synthetic_gene_300", parsed.text)
        self.assertIn('<option value="mlst">MLST</option>', body)
        self.assertNotIn("Showing the first 250", body)
        with Path(artifacts["screening_findings_tsv"]).open() as handle:
            exported = list(csv.DictReader(handle, delimiter="\t"))
        self.assertEqual(len(exported), 302)
        self.assertEqual(exported[300]["gene_or_feature"], "synthetic_gene_300")

    def test_export_preserves_qualifications_and_protects_spreadsheet_cells(self):
        path = self.root / "readable.tsv"
        rows = [synthetic_finding(gene="=1+1", evidence_level="exploratory"),
                synthetic_finding(gene=" @SUM(1)", execution_status="failed")]
        before = copy.deepcopy(rows)
        write_readable_findings(path, rows)
        with path.open() as handle:
            values = list(csv.DictReader(handle, delimiter="\t"))
        self.assertEqual(values[0]["gene_or_feature"], "'=1+1")
        self.assertEqual(values[1]["gene_or_feature"], "' @SUM(1)")
        self.assertEqual([r["review_group"] for r in values], ["exploratory", "provisional"])
        self.assertEqual(rows, before)

    def test_no_findings_does_not_fabricate_an_annotation(self):
        body = render_screening_findings([], {}, {})
        self.assertIn("0 saved findings", body)
        self.assertNotIn('id="screening-finding-1"', body)

    def test_report_export_alias_is_rejected_before_writing(self):
        target = self.root / "unrelated.txt"
        target.write_text("preserve")
        (self.root / "screening-findings.tsv").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            self.report([synthetic_finding()])
        self.assertEqual(target.read_text(), "preserve")
        self.assertFalse((self.root / "report.html").exists())

    def test_workflow_render_relocates_readable_export_and_preserves_evidence(self):
        metadata = self.root / "metadata"
        metadata.mkdir()
        evidence_dir = self.root / "evidence"
        evidence_dir.mkdir()
        sample = metadata / "sample.json"
        config = metadata / "config.json"
        record = evidence_dir / "amrfinder.json"
        sample.write_text(json.dumps({"sample_id": "synthetic-sample", "input_mode": "external"}))
        config.write_text(json.dumps({"version": "test", "modules": {"amrfinder": {"enabled": True}}, "deep": {"enabled": False}}))
        record.write_text(json.dumps({"sample_id": "synthetic-sample", "module": "amrfinder", "execution_status": "completed",
                                     "interpretation": "detected", "findings": [synthetic_finding()], "artifacts": []}))
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (sample, config, record)}
        with patch("subprocess.Popen", side_effect=AssertionError("Native analysis attempted")):
            artifacts = render(sample, config, [record], self.root, view="screening")
        for path, expected in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), expected)
        self.assertEqual(Path(artifacts["screening_findings_tsv"]), self.root / "tables/screening-findings.tsv")
        self.assertIn('href="tables/screening-findings.tsv"', (self.root / "report.html").read_text())
        self.assertFalse((self.root / "screening-findings.tsv").exists())


if __name__ == "__main__":
    unittest.main()
