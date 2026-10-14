"""Presentation-only integration checks for recorded candidate navigation."""
import copy
import csv
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

from basis.control_panel import collect_run
from basis.report import write_report
from basis.report_dashboard import _features
from basis.report_links import candidate_anchor, candidate_section_anchor
from basis.report_navigation import candidate_fingerprint, render_candidate_files


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Node:
    def __init__(self, tag='document', attrs=(), parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    @property
    def text(self):
        return ''.join(c.text if isinstance(c, Node) else c for c in self.children)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def find(self, tag=None, cls=None, ident=None):
        return [n for n in self.walk() if (tag is None or n.tag == tag)
                and (cls is None or cls in n.attrs.get('class', '').split())
                and (ident is None or n.attrs.get('id') == ident)]


class Document(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(n)
        if tag not in self.VOID:
            self.stack.append(n)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def candidate(sid='A', pid='shared'):
    return {'sample_id': sid, 'candidate_id': 'candidate_' + sid + '_' + pid,
        'protein_id': pid, 'gene': 'Demo label', 'selected': True, 'execution': 'completed',
        'selection_queue': 'native_screen', 'source_evidence': [],
        'query': {'id': 'Q_123', 'length': 3, 'sequence_sha256': sha(b'ACD')},
        'assessment': {'interpretation': 'unresolved', 'followup': 'hold_for_comparator_review',
                       'reason': 'missing_reviewed_comparison_groups', 'missing_comparison_groups': ['Demo comparator']},
        'analyses': {
            'domains': {'status': 'completed', 'hits': [
                {'passes_ga': True, 'domain_name': 'Demo_domain', 'query_start': 1, 'query_end': 3}]},
            'motifs': {'status': 'completed', 'rules': [
                {'name': 'Demo motif', 'rule_id': 'demo_rule', 'pattern': 'A-x-D', 'status': 'present',
                 'matches': [{'start': 1, 'end': 3, 'sequence': 'ACD'}]}]},
            'prosite_patterns': {'status': 'completed', 'hits': [
                {'accession': 'PS_TEST_PATTERN', 'name': 'Demo pattern', 'start': 1, 'end': 3}]},
            'prosite_profiles': {'status': 'completed', 'hits': [
                {'accession': 'PS_TEST_PROFILE', 'name': 'Demo profile', 'start': 1, 'end': 3}]},
            'phylogeny': {'status': 'not_assessed', 'reason': 'missing_reviewed_comparison_groups'}},
        'references': [{'accession': 'DEMO_REF', 'review_status': 'source_reviewed',
                        'selection_reason': 'profile_anchor', 'comparison_group': 'Demo group'}],
        'consolidated_evidence': {'residue_observations': [
            {'query_position': 1, 'observed_residue': 'A', 'conditions': [
                {'rule': 'demo', 'status': 'condition_satisfied', 'expected_residues': 'A'}]}]}}


def record(c, status='completed'):
    return {'sample_id': c['sample_id'], 'module': 'deepsafe', 'execution_status': status,
            'findings': [], 'candidates': [c]}


class ReportNavigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.serial = 0

    def render(self, records):
        self.serial += 1
        out = self.root / ('report-' + str(self.serial))
        samples = [{'sample_id': sid} for sid in sorted({r['sample_id'] for r in records})]
        run = {'run_id': 'navigation-test', 'version': 'test', 'completeness': 'incomplete',
               'enabled_modules': [], 'options': {'deep': True, 'extend': False}}
        before = copy.deepcopy((run, samples, records))
        paths = write_report(run, samples, [], records, out)
        self.assertEqual(before, (run, samples, records))
        text = (out / 'report.html').read_text()
        return out, paths, Document(text).root

    def add_tree(self, c, rec):
        native = self.root / ('native-' + c['sample_id'])
        native.mkdir(exist_ok=True)
        tree = native / 'tree.treefile'
        alignment = native / 'alignment.faa'
        labels = native / 'labels.json'
        tree.write_text('(Q_123:0.1,(R00001:0.2,R00002:0.3)95/99:0.1);')
        alignment.write_text('>Q_123\nAC-D\n>R00001\nACE-\n>R00002\nACED\n')
        labels.write_text(json.dumps({'Q_123': {'role': 'query'}, 'R00001': {'references': [
            {'accession': 'DEMO_REF', 'review_status': 'reviewed'}]}}))
        c['analyses']['phylogeny'] = {'status': 'completed', 'runs': [
            {'name': 'baseline', 'execution': 'completed', 'selected_model': 'LG+G4', 'tree': str(tree),
             'tree_sha256': sha(tree.read_bytes()), 'alignment_sha256': sha(alignment.read_bytes())}],
            'labels': str(labels), 'artifacts': [str(tree), str(alignment), str(labels)]}
        rec['artifact_checksums'] = {str(labels): sha(labels.read_bytes())}
        return tree, alignment, labels

    def test_manifest_exports_sections_and_shared_protein_ids_are_coherent(self):
        ca, cb = candidate('A'), candidate('B')
        cb['selection_queue'] = 'ml_attention'
        cb['attention_rank'] = 2
        cb['gene'] = 'Loose label retained only in source'
        out, paths, doc = self.render([record(ca), record(cb)])
        manifest = json.loads(Path(paths['report_navigation_json']).read_text())
        self.assertEqual(manifest['schema'], 'basis-report-navigation-v1')
        self.assertEqual(manifest['report_sha256'], sha((out / 'report.html').read_bytes()))
        self.assertEqual(manifest['evidence_sha256'], sha((out / 'evidence.json').read_bytes()))
        self.assertEqual(manifest['run_id'], 'navigation-test')
        self.assertEqual(len(manifest['candidates']), 2)
        self.assertNotEqual(manifest['candidates'][0]['anchor'], manifest['candidates'][1]['anchor'])
        for entry, original in zip(manifest['candidates'], (ca, cb)):
            self.assertEqual(entry['candidate_sha256'], candidate_fingerprint(original))
            exported = json.loads((out / entry['evidence_href']).read_text())
            self.assertEqual(exported, original)
            self.assertEqual(len(doc.find(ident=entry['anchor'])), 1)
            for name, ident in entry['sections'].items():
                self.assertEqual(ident, candidate_section_anchor(original['sample_id'], 'shared', name))
                self.assertEqual(len(doc.find(ident=ident)), 1)
        self.assertIn('Attention rank 2', manifest['candidates'][1]['label'])
        self.assertNotIn('Loose label', manifest['candidates'][1]['label'])
        self.assertIn('loose label', manifest['candidates'][1]['search_text'])
        panel = self.root / 'panel'
        panel.mkdir()
        indexed = collect_run(out, 'r1', panel)
        self.assertEqual(indexed['navigation_source'], 'verified_report_snapshot')
        self.assertFalse(indexed['warnings'])

    def test_completed_tree_and_features_link_to_checked_exports(self):
        c = candidate()
        rec = record(c)
        native = self.add_tree(c, rec)
        checksums = {str(p): sha(p.read_bytes()) for p in native}
        out, _, doc = self.render([rec])
        card = doc.find(ident=candidate_anchor('A', 'shared'))[0]
        self.assertTrue(card.find(cls='feature-map'))
        tree_section = card.find(ident=candidate_section_anchor('A', 'shared', 'trees'))[0]
        self.assertTrue(tree_section.find(cls='tree-svg'))
        links = tree_section.find(tag='a')
        checked = [n.attrs['href'] for n in links if 'report-data/' in n.attrs.get('href', '')]
        self.assertGreaterEqual(len(checked), 4)
        self.assertTrue(all((out / unquote(urlsplit(href).path)).is_file() for href in checked))
        self.assertEqual({str(p): sha(p.read_bytes()) for p in native}, checksums)

    def test_failed_parent_or_candidate_withholds_retained_display_and_preserves_raw(self):
        for where in ('candidate', 'parent'):
            with self.subTest(where=where):
                c = candidate()
                rec = record(c)
                self.add_tree(c, rec)
                if where == 'candidate':
                    c['execution'] = 'failed'
                else:
                    rec['execution_status'] = 'failed'
                out, paths, doc = self.render([rec])
                card = doc.find(ident=candidate_anchor('A', 'shared'))[0]
                self.assertFalse(card.find(cls='feature-map'))
                self.assertFalse(card.find(cls='tree-svg'))
                self.assertIn('Tree display withheld', card.text)
                motifs = card.find(ident=candidate_section_anchor('A', 'shared', 'motifs'))[0]
                self.assertIn('provisional', motifs.text)
                self.assertNotIn('Mapped residue conditions', motifs.text)
                self.assertFalse(list((out / 'report-data').glob('tree-*.nwk')))
                manifest = json.loads(Path(paths['report_navigation_json']).read_text())
                entry = manifest['candidates'][0]
                self.assertEqual(json.loads((out / entry['evidence_href']).read_text()), c)
                self.assertEqual(entry['parent_execution'], rec['execution_status'])

    def test_failed_or_unassessed_layer_hits_are_not_accepted_feature_intervals(self):
        c = candidate()
        self.assertEqual(len(_features(c)), 4)
        for layer in ('domains', 'motifs', 'prosite_patterns', 'prosite_profiles'):
            for status in ('failed', 'not_assessed'):
                with self.subTest(layer=layer, status=status):
                    changed = copy.deepcopy(c)
                    changed['analyses'][layer]['status'] = status
                    self.assertEqual(len(_features(changed)), 3)
                    self.assertEqual(changed['analyses'][layer]['hits'] if 'hits' in changed['analyses'][layer]
                                     else changed['analyses'][layer]['rules'],
                                     c['analyses'][layer]['hits'] if 'hits' in c['analyses'][layer] else c['analyses'][layer]['rules'])

    def test_missing_tree_retains_reason_without_fabricated_export(self):
        c = candidate()
        rec = record(c)
        tree, _, _ = self.add_tree(c, rec)
        tree.unlink()
        out, _, doc = self.render([rec])
        trees = doc.find(ident=candidate_section_anchor('A', 'shared', 'trees'))[0]
        self.assertIn('native file unavailable', trees.text)
        self.assertFalse(trees.find(cls='tree-svg'))
        self.assertFalse(list((out / 'report-data').glob('tree-*.nwk')))

    def test_file_inventory_links_only_existing_in_report_files(self):
        out = self.root / 'files-report'
        out.mkdir()
        local = out / 'local "# sample.faa'
        local.write_text('synthetic saved file')
        outside = self.root / 'outside.txt'
        outside.write_text('outside')
        symlink = out / 'outside-link.txt'
        symlink.symlink_to(outside)
        paths = [str(local), str(out / 'missing.txt'), str(outside), str(symlink),
                 'https://example.org/a', 'javascript:alert(1)', '../outside.txt']
        c = candidate()
        c['query']['path'] = paths[0]
        c['artifacts'] = paths[1:]
        before = copy.deepcopy(c)
        text = render_candidate_files(c, out, ['A', 'shared'], 'candidate.json')
        doc = Document(text).root
        rows = doc.find(tag='tbody')[0].children
        rows = [r for r in rows if isinstance(r, Node)]
        self.assertEqual(len(rows), len(paths))
        self.assertEqual(len(rows[0].find(tag='a')), 1)
        for row in rows[1:]:
            self.assertFalse(row.find(tag='a'))
            self.assertIn('Unavailable in this report bundle', row.text)
        href = rows[0].find(tag='a')[0].attrs['href']
        self.assertNotIn('#', href)
        self.assertNotIn('"', href)
        self.assertEqual((out / unquote(href)).resolve(), local)
        self.assertEqual(before, c)

    def test_labels_motif_content_reasons_and_search_are_escaped(self):
        c = candidate()
        hostile = '<img src=x onerror="alert(1)">'
        c['gene'] = hostile
        c['assessment']['reason'] = 'held ' + hostile
        c['assessment']['missing_comparison_groups'] = [hostile]
        c['analyses']['motifs']['rules'][0]['name'] = hostile
        c['analyses']['motifs']['rules'][0]['matches'][0]['sequence'] = hostile
        out, paths, doc = self.render([record(c)])
        self.assertFalse(doc.find(tag='img'))
        self.assertNotIn(hostile, (out / 'report.html').read_text())
        card = doc.find(ident=candidate_anchor('A', 'shared'))[0]
        self.assertIn(hostile, card.text)
        self.assertIn(hostile.lower(), card.attrs['data-search'])
        manifest = json.loads(Path(paths['report_navigation_json']).read_text())
        self.assertIn(hostile.lower(), manifest['candidates'][0]['search_text'])
        navigator = doc.find(ident='deep-navigator')[0]
        self.assertIn(hostile, navigator.text)
        self.assertFalse(navigator.find(tag='img'))

    def test_explicit_deferred_and_cross_sample_candidates_have_no_navigation_cards(self):
        c = candidate()
        rec = record(c)
        deferred = candidate('A', 'deferred')
        deferred.update(selected=False, execution='deferred', reason='budget_zero')
        cross = candidate('OTHER', 'wrong_sample')
        rec['candidates'].extend([deferred, cross])
        out, paths, doc = self.render([rec])
        index = json.loads(Path(paths['report_navigation_json']).read_text())
        self.assertEqual([e['protein_id'] for e in index['candidates']], ['shared'])
        self.assertEqual(len(doc.find(cls='candidate')), 1)
        self.assertFalse(doc.find(ident=candidate_anchor('A', 'deferred')))
        with (out / 'deferred-candidates.tsv').open() as handle:
            rows = list(csv.DictReader(handle, delimiter='\t'))
        self.assertEqual([r['protein_id'] for r in rows], ['deferred'])
        self.assertEqual(rows[0]['reason'], 'budget_zero')
        source = json.loads((out / 'evidence.json').read_text())
        self.assertEqual(source['deep_records'][0]['candidates'], [c, deferred, cross])


if __name__ == '__main__':
    unittest.main()
