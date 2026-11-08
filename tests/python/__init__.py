"""Python tests; expose the project and legacy modules to historical test imports."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "legacy"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
