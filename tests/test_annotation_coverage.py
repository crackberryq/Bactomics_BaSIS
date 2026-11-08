"""Synthetic cross-export coverage regressions; no biological accuracy claims."""
from pathlib import Path
import sys
import tempfile
import unittest
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import AfterPosition, CompoundLocation, SeqFeature, SimpleLocation
from Bio.SeqRecord import SeqRecord
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'workflow/vendor'))
from basis.inputs import validate_sample

class AnnotationCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sample = {'sample_id': 'synthetic', **{key: str(self.root / name) for key, name in
                       (('assembly', 'assembly.fna'), ('proteins', 'proteins.faa'), ('gff', 'annotation.gff'))}}
        Path(self.sample['assembly']).write_text('>c1\nATGAAATAAATGCCCTAA\n')
        self.faa()
        self.gff()

    def faa(self, second=True):
        Path(self.sample['proteins']).write_text('>p1\nMK\n' + ('>p2\nMP\n' if second else ''))

    def gff(self, attrs='', second=True):
        Path(self.sample['gff']).write_text('##gff-version 3\n'
            'c1\ttest\tCDS\t1\t9\t.\t+\t0\tID=p1;transl_table=11\n' +
            (f'c1\ttest\tCDS\t10\t18\t.\t+\t0\tID=p2;transl_table=11{attrs}\n' if second else ''))

    def genbank(self, second=True, alias=False, mutate=None):
        record = SeqRecord(Seq('ATGAAATAAATGCCCTAA'), id='gb_alias' if alias else 'c1',
                           name='gb_alias' if alias else 'c1', description='Synthetic infrastructure fixture')
        record.annotations['molecule_type'] = 'DNA'
        record.features = [SeqFeature(SimpleLocation(0, 9, strand=1), type='CDS',
                           qualifiers={'locus_tag': ['p1'], 'translation': ['MK']})]
        if second:
            record.features.append(SeqFeature(SimpleLocation(9, 18, strand=1), type='CDS',
                qualifiers={'locus_tag': ['p2'], 'translation': ['MP']}))
        if mutate:
            mutate(record)
        self.sample['genbank'] = str(self.root / 'annotation.gbk')
        SeqIO.write(record, self.sample['genbank'], 'genbank')

    def test_complete_two_cds_and_optional_genbank(self):
        self.assertEqual(validate_sample(self.sample)['annotation_coverage']['eligible_cds_count'], 2)
        self.genbank(alias=True)
        value = validate_sample(self.sample)
        self.assertEqual(value['contig_aliases']['gb_alias'], 'c1')
        self.assertEqual(value['annotation_coverage']['genbank']['matched_eligible_cds_count'], 2)

    def test_missing_ordinary_or_partial_protein_rejected(self):
        self.faa(second=False)
        for attrs in ('', ';partial=true', ';pseudo=false', ';pseudogene=no', ';pseudo=.'):
            with self.subTest(attrs=attrs):
                self.gff(attrs)
                with self.assertRaisesRegex(ValueError, 'incomplete protein annotation'):
                    validate_sample(self.sample)

    def test_empty_faa_rejected(self):
        Path(self.sample['proteins']).write_text('')
        with self.assertRaisesRegex(ValueError, 'FASTA is empty'):
            validate_sample(self.sample)

    def test_omitted_explicit_pseudogene_warns(self):
        self.faa(second=False)
        for attrs in (';pseudo=true', ';pseudogene=unitary', ';pseudo'):
            with self.subTest(attrs=attrs):
                self.gff(attrs)
                value = validate_sample(self.sample)
                self.assertEqual(value['annotation_coverage']['omitted_pseudogene_cds_count'], 1)
                self.assertTrue(any('excluded from protein-based assessment' in w for w in value['warnings']))

    def test_complete_partial_cds_is_supported(self):
        self.gff(';partial=true')
        self.assertEqual(validate_sample(self.sample)['annotation_coverage']['mapped_eligible_cds_count'], 2)

    def test_actual_partial_end_and_fuzzy_genbank_endpoint_supported(self):
        self.gff(';partial=01')
        path = Path(self.sample['gff'])
        path.write_text(path.read_text().replace('CDS\t10\t18', 'CDS\t10\t17'))
        self.genbank(mutate=lambda record: setattr(record.features[1], 'location', SimpleLocation(9, AfterPosition(17), strand=1)))
        value = validate_sample(self.sample)
        self.assertEqual(value['annotation_coverage']['genbank']['matched_eligible_cds_count'], 2)
        self.assertTrue(any('terminal nucleotide(s) omitted' in w for w in value['warnings']))

    def test_multiple_faa_aliases_for_one_cds_rejected(self):
        self.gff(';protein_id=other')
        with Path(self.sample['proteins']).open('a') as out:
            out.write('>other\nMP\n')
        with self.assertRaisesRegex(ValueError, 'ambiguous CDS aliases'):
            validate_sample(self.sample)

    def test_genbank_subset_or_no_cds_rejected(self):
        self.genbank(second=False)
        with self.assertRaisesRegex(ValueError, 'incomplete GenBank annotation'):
            validate_sample(self.sample)
        self.genbank(mutate=lambda record: record.features.clear())
        with self.assertRaisesRegex(ValueError, 'incomplete GenBank annotation'):
            validate_sample(self.sample)

    def test_extra_genbank_cds_rejected(self):
        self.gff(second=False)
        self.faa(second=False)
        self.genbank()
        with self.assertRaisesRegex(ValueError, 'differs from eligible GFF'):
            validate_sample(self.sample)

    def test_genbank_wrong_strand_or_interval_rejected(self):
        for location in (SimpleLocation(9, 18, strand=-1), SimpleLocation(10, 18, strand=1)):
            with self.subTest(location=location):
                self.genbank(mutate=lambda record: setattr(record.features[1], 'location', location))
                with self.assertRaisesRegex(ValueError, 'differs from eligible GFF'):
                    validate_sample(self.sample)

    def test_genbank_wrong_or_missing_translation_rejected(self):
        self.genbank(mutate=lambda record: record.features[1].qualifiers.update(translation=['MK']))
        with self.assertRaisesRegex(ValueError, 'translation differs'):
            validate_sample(self.sample)
        self.genbank(mutate=lambda record: record.features[1].qualifiers.pop('translation'))
        with self.assertRaisesRegex(ValueError, 'requires one supplied translation'):
            validate_sample(self.sample)

    def test_genbank_distinct_alias_and_terminal_stop_supported(self):
        self.genbank(mutate=lambda record: record.features[1].qualifiers.update(locus_tag=['exporter2'], translation=['MP*']))
        self.assertEqual(validate_sample(self.sample)['annotation_coverage']['genbank']['matched_eligible_cds_count'], 2)

    def test_genbank_conflicting_existing_protein_alias_rejected(self):
        self.genbank(mutate=lambda record: record.features[1].qualifiers.update(locus_tag=['p1']))
        with self.assertRaisesRegex(ValueError, 'protein identifier conflicts'):
            validate_sample(self.sample)

    def test_genbank_pseudogene_exclusion_requires_matching_gff(self):
        self.genbank(mutate=lambda record: record.features[1].qualifiers.update(pseudo=['']))
        with self.assertRaisesRegex(ValueError, 'pseudogene annotation disagrees'):
            validate_sample(self.sample)
        self.gff(';pseudo=true')
        self.faa(second=False)
        value = validate_sample(self.sample)
        self.assertEqual(value['annotation_coverage']['genbank']['excluded_pseudogene_cds_count'], 1)
        self.assertTrue(any('downstream tools may not assess' in w for w in value['warnings']))

    def test_joined_location_is_not_silently_flattened(self):
        location = CompoundLocation([SimpleLocation(9, 12, strand=1), SimpleLocation(15, 18, strand=1)])
        self.genbank(mutate=lambda record: setattr(record.features[1], 'location', location))
        with self.assertRaisesRegex(ValueError, 'unsupported joined'):
            validate_sample(self.sample)

if __name__ == '__main__':
    unittest.main()
