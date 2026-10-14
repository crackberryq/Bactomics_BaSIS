import unittest
from basis.deep_alignment_review import anchor_summary,alignment_metrics,comparison_gaps

class AlignmentReviewTests(unittest.TestCase):
    def test_mixed_hsps_are_labelled_separately(self):
        s=anchor_summary({'reference_id':'R','status':'eligible','selected':True,'alignment_rows':[
            {'passes_retrieval_filters':True,'filter_reasons':[]},
            {'passes_retrieval_filters':False,'filter_reasons':['query_coverage_below_threshold']}]})
        self.assertIn('passing alignments: 1',s)
        self.assertIn('rejected alignments: 1',s)
        self.assertIn('rejected-alignment reasons:',s)
    def test_metrics_with_known_counts(self):
        aligned={'Q':'AC-D','R1':'ACED','R2':'AG-D','R3':'AG-D'}
        submitted={k:v.replace('-','') for k,v in aligned.items()}
        m=alignment_metrics(submitted,aligned,'Q')
        self.assertEqual(m['alignment_columns'],4)
        self.assertEqual(m['columns_occupancy_ge_80pct'],3)
        self.assertEqual(m['parsimony_informative_columns_canonical_only'],1)
        self.assertEqual(m['gap_fraction'],3/16)
        self.assertEqual(m['reference_coverage_of_query_residues']['R1'],1)
    def test_corruption_rejected(self):
        with self.assertRaises(ValueError):alignment_metrics({'Q':'AC'},{'Q':'AG'},'Q')
        with self.assertRaises(ValueError):alignment_metrics({'Q':'AC'},{'R':'AC'},'Q')
    def test_only_reviewed_selected_groups_count(self):
        c={'profile':{'required_comparison_groups':['A','B']},'references':[
            {'comparison_group':'A','review_status':'reviewed'}, {'comparison_group':'B','review_status':'unreviewed'}]}
        self.assertEqual(comparison_gaps(c),['B'])

class TerminalStopTests(unittest.TestCase):
    def test_terminal_marker_audited(self):
        from basis.deep_alignment_review import normalize_alignment_sequence
        n,a=normalize_alignment_sequence('MACK*')
        self.assertEqual(n,'MACK');self.assertTrue(a['terminal_stop_removed'])
        self.assertNotEqual(a['source_sha256'],a['alignment_sequence_sha256'])
        self.assertEqual(normalize_alignment_sequence('MACK')[0],n)
    def test_internal_or_repeated_stop_is_not_silently_removed(self):
        from basis.deep_alignment_review import normalize_alignment_sequence
        for seq in ['MA*CK','MA**','*','']:
            with self.assertRaises(ValueError):normalize_alignment_sequence(seq)
    def test_substitution_still_fails_after_normalization(self):
        from basis.deep_alignment_review import normalize_alignment_sequence
        n,_=normalize_alignment_sequence('MACK*')
        with self.assertRaises(ValueError):alignment_metrics({'Q':n},{'Q':'MAGK'},'Q')
    def test_core_phylogeny_normalizes_before_deduplication(self):
        import tempfile,json
        from pathlib import Path
        from basis.deep import _build_phylogeny
        from basis.references import ReferenceCatalog,read_fasta
        with tempfile.TemporaryDirectory() as d:
            cat=ReferenceCatalog({'A':'MACK*','B':'MACK'}, {},Path('x'),Path('y'),[])
            result=_build_phylogeny('Q','MACK*',[{'reference_id':'A'},{'reference_id':'B'}],cat,{'phylogeny':False},Path(d),None)
            self.assertEqual(result['reference_sequences'],1)
            self.assertEqual(read_fasta(Path(result['input'])),{'Q':'MACK','R00001':'MACK'})
            labels=json.loads(Path(result['labels']).read_text())
            self.assertEqual(len(labels['R00001']['reference_aliases']),2)
            self.assertTrue(result['normalization']['references']['A']['terminal_stop_removed'])
