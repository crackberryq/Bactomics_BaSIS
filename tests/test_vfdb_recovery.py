import json
from pathlib import Path
import tempfile
import unittest

from basis.util import sha256_file
from basis.vfdb_recovery import verify_legacy_vfdb_snapshot


class VfdbRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'vfdb_setA.dmnd').write_bytes(b'fixture index')
        (self.root / 'VFDB_setA_pro.utf8.fas').write_text('>VFG1(gb|WP_1) fixture\nMAMAM\n')
        self.manifest = {'database': 'VFDB', 'dataset': 'Set A core proteins',
                         'source_url': 'https://www.mgc.ac.cn/VFs/Down/VFDB_setA_pro.fas.gz',
                         'database_version': 'fixture snapshot', 'protein_count': 1,
                         'files': {p.name: {'sha256': sha256_file(p)} for p in self.root.iterdir()}}
        self.write_manifest()

    def write_manifest(self):
        (self.root / 'provenance.json').write_text(json.dumps(self.manifest))

    def test_verified_paths_and_honest_audit(self):
        updates, audit = verify_legacy_vfdb_snapshot(self.root)
        self.assertEqual(updates['reference_fasta'], str(self.root / 'VFDB_setA_pro.utf8.fas'))
        self.assertEqual(len(audit['verified_files']), 2)
        self.assertFalse(audit['download_performed'])
        self.assertIn('not independent vendor authentication', audit['integrity_basis'])

    def test_changed_index_or_sequence_rejected(self):
        for name in ('vfdb_setA.dmnd', 'VFDB_setA_pro.utf8.fas'):
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(original + b'changed')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                verify_legacy_vfdb_snapshot(self.root)
            path.write_bytes(original)

    def test_wrong_dataset_source_missing_digest_and_count_rejected(self):
        for key, value in [('dataset', 'Set B'), ('source_url', 'https://example.org/mirror'),
                           ('protein_count', 2), ('files', {})]:
            original = self.manifest[key]
            self.manifest[key] = value
            self.write_manifest()
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_legacy_vfdb_snapshot(self.root)
            self.manifest[key] = original


if __name__ == '__main__':
    unittest.main()
