"""Release provenance checks on synthetic files, never biological analyses."""
import json
from pathlib import Path
import tempfile
import unittest

from basis import __version__
from basis.release import MANIFEST, runtime_hashes, verify_install


class ReleaseIdentityTests(unittest.TestCase):
    def test_resource_changes_and_extra_code_fail_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'resources').mkdir()
            (root / 'module.py').write_text('x = 1\n')
            (root / 'resources/Snakefile').write_text('rule all:\n    input: []\n')
            expected = runtime_hashes(root)
            (root / MANIFEST).write_text(json.dumps({'schema': 'basis-package-manifest-v1',
                'version': __version__, 'sha256': expected}))
            self.assertEqual(verify_install(root)['status'], 'passed')
            (root / '__pycache__').mkdir()
            (root / '__pycache__/module.pyc').write_bytes(b'bytecode')
            self.assertEqual(verify_install(root)['status'], 'passed')
            (root / 'resources/Snakefile').write_text('modified\n')
            (root / 'extra.py').write_text('x = 2\n')
            result = verify_install(root)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['changed'], ['resources/Snakefile'])
            self.assertEqual(result['unexpected'], ['extra.py'])

    def test_unsealed_checkout_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(verify_install(tmp)['status'], 'unsealed_source_checkout')

    def test_missing_asset_and_version_mismatch_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'resources').mkdir()
            (root / MANIFEST).write_text(json.dumps({'schema':'basis-package-manifest-v1',
                'version':'unrelated', 'sha256': {'resources/required.json':'unavailable'}}))
            result = verify_install(root)
            self.assertEqual(result['status'], 'failed')
            self.assertFalse(result['version_matches'])
            self.assertEqual(result['missing'], ['resources/required.json'])
