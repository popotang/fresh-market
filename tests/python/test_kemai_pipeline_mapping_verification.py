import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from kemai_pipeline.export_mappings import HISTORY_EXPORT_FIELDS, MAPPING_VERSION
from kemai_pipeline.mapping_verification import (canonical_artifact_digest,
                                                 expected_header_digest,
                                                 load_verification)
from kemai_pipeline.normalize import dump

STREAM = 'purchase-receive-history'
REQUIRED_FIELDS = {'schema_version', 'stream', 'mapping_version', 'header_sha256',
                   'sample_rows', 'key_coverage', 'verifier', 'verified_at', 'artifact_sha256'}


def valid_artifact():
    artifact = {'schema_version': 1, 'stream': STREAM, 'mapping_version': MAPPING_VERSION,
                'header_sha256': expected_header_digest(), 'sample_rows': 3, 'key_coverage': 1.0,
                'verifier': 'scripts/verify_kemai_history_export_mapping.py',
                'verified_at': '2026-11-09T10:00:00+08:00'}
    artifact['artifact_sha256'] = canonical_artifact_digest(artifact)
    return artifact


class LoadVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'export-mapping').mkdir()
        self.path = self.root / 'export-mapping' / f'{STREAM}.json'

    def write(self, value):
        self.path.write_text(value if isinstance(value, str) else json.dumps(value))

    def test_missing_artifact_returns_none(self):
        self.assertIsNone(load_verification(STREAM, self.root))

    def test_valid_artifact_returns_complete_dict(self):
        self.write(valid_artifact())
        loaded = load_verification(STREAM, self.root)
        self.assertIsInstance(loaded, dict)
        self.assertTrue(REQUIRED_FIELDS <= set(loaded))
        self.assertEqual(loaded['mapping_version'], MAPPING_VERSION)

    def test_stale_mapping_version_returns_none(self):
        artifact = valid_artifact()
        artifact['mapping_version'] = 'history-export-mapping-v0'
        artifact['artifact_sha256'] = canonical_artifact_digest(artifact)
        self.write(artifact)
        self.assertIsNone(load_verification(STREAM, self.root))

    def test_tampered_artifact_sha256_returns_none(self):
        artifact = valid_artifact()
        artifact['artifact_sha256'] = 'b' * 64
        self.write(artifact)
        self.assertIsNone(load_verification(STREAM, self.root))

    def test_tampered_field_breaks_self_check(self):
        artifact = valid_artifact()
        artifact['sample_rows'] = 999  # digest no longer matches
        self.write(artifact)
        self.assertIsNone(load_verification(STREAM, self.root))

    def test_partial_key_coverage_returns_none(self):
        artifact = valid_artifact()
        artifact['key_coverage'] = 0.99
        artifact['artifact_sha256'] = canonical_artifact_digest(artifact)
        self.write(artifact)
        self.assertIsNone(load_verification(STREAM, self.root))

    def test_bad_json_returns_none(self):
        self.write('{not json')
        self.assertIsNone(load_verification(STREAM, self.root))

    def test_bad_header_sha256_returns_none(self):
        artifact = valid_artifact()
        artifact['header_sha256'] = 'not-hex'
        artifact['artifact_sha256'] = canonical_artifact_digest(artifact)
        self.write(artifact)
        self.assertIsNone(load_verification(STREAM, self.root))

    def test_nonpositive_sample_rows_returns_none(self):
        artifact = valid_artifact()
        artifact['sample_rows'] = 0
        artifact['artifact_sha256'] = canonical_artifact_digest(artifact)
        self.write(artifact)
        self.assertIsNone(load_verification(STREAM, self.root))

    def test_self_consistent_zero_header_digest_returns_none(self):
        # F5: a forged artifact whose header_sha256 is well-formed and whose
        # artifact_sha256 self-check passes is still rejected when the header
        # digest does not match the current HISTORY_EXPORT_FIELDS mapping.
        artifact = valid_artifact()
        artifact['header_sha256'] = '0' * 64
        artifact['artifact_sha256'] = canonical_artifact_digest(artifact)
        self.write(artifact)
        self.assertIsNone(load_verification(STREAM, self.root))

    def test_header_digest_matches_verify_script_algorithm(self):
        # F5: the canonical digest is exactly what
        # scripts/verify_kemai_history_export_mapping.py writes into artifacts.
        expected = hashlib.sha256(dump(list(HISTORY_EXPORT_FIELDS.keys())).encode()).hexdigest()
        self.assertEqual(expected_header_digest(), expected)
        self.write(valid_artifact())
        loaded = load_verification(STREAM, self.root)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['header_sha256'], expected)


if __name__ == '__main__':
    unittest.main()
