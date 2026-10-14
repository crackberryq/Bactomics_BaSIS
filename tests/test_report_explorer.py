import json
import re
import unittest

from basis.report_explorer import render_genome_explorer


class GenomeExplorerPresentationTests(unittest.TestCase):
    def payload(self):
        return {
            'schema_version': 'basis-genome-view-1', 'coordinate_basis': '1-based inclusive',
            'samples': [{'id': 'isolate1', 'contigs': [{
                'id': '1', 'length': 10000, 'length_source': 'recorded_input_validation',
                'genes': [{'id': 'p1', 'start': 100, 'end': 300, 'strand': '+', 'labels': ['cfr-like']}],
                'regions': [{'id': 'r1', 'start': 100, 'end': 300, 'track': 'amr', 'label': 'cfr-like'}]}],
                'tracks': [{'id': 'amr', 'label': 'AMR candidates', 'count': 1, 'status': 'completed'},
                           {'id': 'genomic_islands', 'label': 'Genomic islands', 'count': 0, 'status': 'not_assessed'}],
                'notes': ['Coordinates from recorded evidence.']}]}

    def test_static_summary_available_without_javascript(self):
        html = render_genome_explorer(self.payload())
        self.assertIn('Genome overview', html)
        self.assertIn('<svg', html)
        self.assertIn('Genomic islands', html)
        self.assertIn('not assessed', html)
        self.assertIn('Mapped features', html)
        self.assertIn('not counts of independent hazards', html)
        self.assertIn('<noscript>', html)

    def test_payload_safe_and_lossless_for_script_and_html_characters(self):
        data = self.payload()
        dangerous = '</script><img src=x onerror=alert(1)> & "unsafe"'
        data['samples'][0]['id'] = dangerous
        data['samples'][0]['notes'] = [dangerous]
        html = render_genome_explorer(data)
        self.assertNotIn(dangerous, html)
        embedded = re.search(r'<script type="application/json" id="basis-genome-data">(.*?)</script>', html, re.S).group(1)
        self.assertNotIn('<', embedded)
        self.assertNotIn('&', embedded)
        self.assertEqual(json.loads(embedded), data)

    def test_unknown_length_not_promoted_to_measured_length(self):
        data = self.payload()
        data['samples'][0]['contigs'][0]['length'] = None
        data['samples'][0]['contigs'][0]['length_source'] = 'unavailable'
        html = render_genome_explorer(data)
        self.assertIn('length not recorded', html)
        self.assertNotIn('10,000 bp', html)

    def test_empty_payload_explained(self):
        html = render_genome_explorer({'samples': []})
        self.assertIn('No genome coordinate data recorded', html)
        self.assertIn('No track metadata recorded', html)

    def test_render_does_not_mutate_payload(self):
        data = self.payload()
        before = json.dumps(data, sort_keys=True)
        render_genome_explorer(data)
        self.assertEqual(json.dumps(data, sort_keys=True), before)


if __name__ == '__main__':
    unittest.main()
