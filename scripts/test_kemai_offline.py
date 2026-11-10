"""Run the existing unittest suite with default artifact, lock and gate paths isolated.

No mock replaces a source or database transport here; tests own their fake adapters.
Only production-default local filesystem destinations are redirected.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from kemai_pipeline.artifacts import CaptureStore
import kemai_pipeline.locking as locking


def main():
    actual = (ROOT / '.kemai-artifacts').resolve()
    original = CaptureStore.__init__
    with tempfile.TemporaryDirectory(prefix='kemai-offline-regression-') as tmp:
        root = Path(tmp)

        def isolated_init(self, path):
            path = Path(path).resolve()
            if path.is_relative_to(actual):
                path = root / 'artifacts' / path.relative_to(actual)
            original(self, path)

        env = {'KEMAI_SOURCE_GATE_DIR': str(root / 'gate'),
               'KEMAI_RATE_LIMIT_STATE': str(root / 'limit.json'),
               'KEMAI_RATE_LIMIT_LOG': str(root / 'limit.log')}
        with patch.object(CaptureStore, '__init__', isolated_init), \
                patch.object(locking, 'RUNTIME_LOCKS', root / 'locks'), patch.dict(os.environ, env):
            suite = unittest.defaultTestLoader.discover('.', pattern='test_kemai*.py')
            result = unittest.TextTestRunner(verbosity=1).run(suite)
        return not result.wasSuccessful()


if __name__ == '__main__':
    raise SystemExit(main())
