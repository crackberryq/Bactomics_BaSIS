import unittest,tempfile
from pathlib import Path
from basis.deep import pfam_ga_thresholds, filter_domains_ga, parse_similarity, partial_reference_review

class DomainReviewTests(unittest.TestCase):
    def test_ga_both_scores_and_equality(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'db.hmm';p.write_text('NAME VanW\nACC PF00001.1\nGA 25 20;\n//\n')
            thresholds=pfam_ga_thresholds(p)
            base={'domain_name':'VanW','domain_accession':'PF00001.1','full_score':25,'domain_score':20}
            accepted,rejected=filter_domains_ga([base,{**base,'full_score':24},{**base,'domain_score':19}],thresholds)
            self.assertEqual(len(accepted),1);self.assertEqual(len(rejected),2)
            self.assertNotIn('passes_ga',base)
            p.write_text('NAME VanW\n//\n')
            with self.assertRaises(ValueError):pfam_ga_thresholds(p)

    def test_vanw_partial_not_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'hits.tsv'
            p.write_text('Q\tR\t39.884\t173\t332\t373\t162\t319\t175\t347\t6.23e-36\t132\n')
            audit=[];hits=parse_similarity(p,30,70,audit=audit)
            self.assertEqual(hits,{})
            self.assertEqual(audit[0]['filter_reasons'],['query_coverage_below_threshold'])
            self.assertAlmostEqual(audit[0]['query_coverage'],100*158/332)
            domain={'domain_name':'VanW','query_start':192,'query_end':319,'passes_ga':True}
            partial=partial_reference_review(audit,[domain],{})
            self.assertEqual(len(partial),1)
            self.assertFalse(partial[0]['eligible_for_whole_protein_tree'])
            self.assertEqual(partial_reference_review(audit,[{**domain,'passes_ga':False}],{}),[])
            self.assertEqual(partial_reference_review([{**audit[0],'filter_reasons':['identity_below_threshold']}],[domain],{}),[])
            self.assertEqual(partial_reference_review(audit,[{**domain,'query_start':1,'query_end':100}],{}),[])
