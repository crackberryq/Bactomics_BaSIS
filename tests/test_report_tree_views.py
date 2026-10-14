"""Bounded, checksum-gated presentation of native tree evidence."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from basis.report_tree_views import render_tree_alignment


def sha(content):
    return hashlib.sha256(content).hexdigest()


class TreeViews(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.native = self.root / 'native' / 'baseline'
        self.native.mkdir(parents=True)
        self.outdir = self.root / 'report'
        self.outdir.mkdir()
        self.tree = self.native / 'tree.treefile'
        self.alignment = self.native / 'alignment.faa'
        self.labels = self.native / 'labels.json'
        self.tree.write_text('(Q_123:0.1,(R00001:0.2,R00002:0.3)95/99:0.1);')
        self.alignment.write_text('>Q_123\nAC-D\n>R00001\nACE-\n>R00002\nACED\n')
        self.labels.write_text(json.dumps({'Q_123': {'role':'query'}, 'R00001': {'references':[
            {'accession':'UP_REVIEWED', 'review_status':'reviewed'}]}}))
        self.run = {'name':'baseline', 'execution':'completed', 'tree':str(self.tree),
                    'tree_sha256':sha(self.tree.read_bytes()), 'alignment_sha256':sha(self.alignment.read_bytes()),
                    'selected_model':'LG+G4'}
        self.candidate = {'protein_id':'P123', 'query':{'id':'Q_123'}, 'analyses':{'phylogeny':{
            'status':'completed', 'runs':[self.run], 'labels':str(self.labels),
            'artifacts':[str(self.tree), str(self.alignment), str(self.labels)]}}}
        self.record = {'artifact_checksums':{str(self.labels):sha(self.labels.read_bytes())}}

    def render(self):
        return render_tree_alignment(self.candidate, self.record, self.outdir)

    def test_tree_alignment_and_checked_downloads(self):
        original = copy.deepcopy(self.candidate)
        result = self.render()
        self.assertIn('95/99', result)
        self.assertIn('P123 · QUERY', result)
        self.assertIn('UP_REVIEWED [reviewed]', result)
        self.assertIn('Display orientation does not establish a root', result)
        self.assertIn('substitutions / site', result)
        self.assertIn('<iframe', result)
        self.assertEqual(original, self.candidate)
        self.assertEqual(next((self.outdir/'report-data').glob('tree-*.nwk')).read_bytes(), self.tree.read_bytes())
        alignment_html = next((self.outdir/'report-data').glob('alignment-view-*.html')).read_text()
        self.assertIn('Columns 1–4', alignment_html)
        self.assertIn('class="same"', alignment_html)
        self.assertIn('class="gap"', alignment_html)
        self.assertIn('class="different"', alignment_html)

    def test_checksum_mismatch_withholds_tree_and_alignment_export(self):
        self.tree.write_text('(Q_123:9,R00001:2,R00002:2);')
        result = self.render()
        self.assertIn('checksum mismatch', result)
        self.assertNotIn('<svg', result)
        self.assertFalse(list((self.outdir/'report-data').glob('alignment-*.faa')))

    def test_conflicting_checksums_fail_closed(self):
        self.record['artifact_checksums'][str(self.tree)] = '0' * 64
        result = self.render()
        self.assertIn('conflicting recorded checksums', result)
        self.assertNotIn('<svg', result)

    def test_missing_native_outputs_short_note(self):
        self.tree.unlink()
        result = self.render()
        self.assertIn('native file unavailable on this computer', result)
        self.assertNotIn('<svg', result)

    def test_exact_query_identity_required(self):
        self.candidate['query']['id'] = 'Q_other'
        result = self.render()
        self.assertIn('recorded query ID absent', result)
        self.assertNotIn('<svg', result)

    def test_baseline_query_sequence_checksum_required_when_recorded(self):
        self.candidate['query']['sequence_sha256'] = sha(b'OTHER')
        result = self.render()
        self.assertIn('baseline query sequence differs', result)
        self.assertNotIn('<svg', result)
        self.candidate['query']['sequence_sha256'] = sha(b'ACD*')
        self.assertIn('<svg', self.render())

    def test_unverified_label_file_is_not_used(self):
        self.record = {}
        result = self.render()
        self.assertIn('<svg', result)
        self.assertNotIn('UP_REVIEWED', result)
        self.assertIn('R00001', result)

    def test_embedded_labels_require_alignment_sequence_hash(self):
        tree = self.candidate['analyses']['phylogeny']
        tree['reference_labels'] = {'R00001': {'references':[
            {'accession':'MATCHING', 'sequence_sha256':sha(b'ACE')},
            {'accession':'WRONG', 'sequence_sha256':sha(b'BAD')}]}}
        self.record = {}
        result = self.render()
        self.assertIn('MATCHING', result)
        self.assertNotIn('WRONG', result)

    def test_labels_are_escaped_in_svg_and_alignment_page(self):
        self.candidate['protein_id'] = '<script>alert(1)</script>'
        self.labels.write_text(json.dumps({'R00001':{'references':[{
            'accession':'<img src=x onerror=alert(1)>', 'review_status':'<script>bad</script>'}]}}))
        self.record['artifact_checksums'][str(self.labels)] = sha(self.labels.read_bytes())
        result = self.render()
        self.assertNotIn('<script>', result)
        self.assertNotIn('<img', result)
        self.assertIn('&lt;script&gt;', result)
        page = next((self.outdir/'report-data').glob('alignment-view-*.html')).read_text()
        self.assertNotIn('<script>', page)
        self.assertNotIn('<img', page)

    def test_tip_labels_align_and_pipe_metadata_stays_in_title(self):
        self.labels.write_text(json.dumps({'R00001':{'references':[{
            'accession':'WP_0123456.1|length=230|native metadata', 'review_status':'unreviewed'}]}}))
        self.record['artifact_checksums'][str(self.labels)] = sha(self.labels.read_bytes())
        result = self.render()
        self.assertIn('x="650"', result)
        self.assertIn('stroke-dasharray="3 4"', result)
        self.assertIn('<title>R00001 · WP_0123456.1|length=230|native metadata [unreviewed]</title>R00001 · WP_0123456.1 [unreviewed]', result)
        self.assertIn('fill-opacity=".92"', result)

    def test_tip_set_mismatch_not_displayed(self):
        self.tree.write_text('(Q_123:0.1,R00001:0.2,R00009:0.3);')
        self.run['tree_sha256'] = sha(self.tree.read_bytes())
        result = self.render()
        self.assertIn('tree tip set differs', result)
        self.assertNotIn('<svg', result)

    def test_historical_artifact_manifest(self):
        tree = self.candidate['analyses']['phylogeny']
        tree.pop('runs'); tree.pop('labels')
        tree['followup_evidence'] = {'source_directory':str(self.native.parent), 'runs':[
            {'name':'baseline', 'execution':'completed'}], 'artifact_hashes':{
                'baseline/' + p.name:sha(p.read_bytes()) for p in [self.tree,self.alignment,self.labels]}}
        self.record = {}
        result = self.render()
        self.assertIn('<svg', result)
        self.assertIn('UP_REVIEWED', result)

    def test_no_directory_scanning_or_neighbor_file_inference(self):
        # Real neighboring file exists, but is not an explicitly recorded artifact.
        self.candidate['analyses']['phylogeny']['artifacts'].remove(str(self.alignment))
        result = self.render()
        self.assertIn('not recorded', result)
        self.assertNotIn('<svg', result)

    def test_missing_branch_lengths_are_not_invented(self):
        self.tree.write_text('(Q_123,R00001,R00002);')
        self.run['tree_sha256'] = sha(self.tree.read_bytes())
        result = self.render()
        self.assertIn('branch lengths not fully recorded', result)
        self.assertNotIn('<svg', result)


if __name__ == '__main__':
    unittest.main()
