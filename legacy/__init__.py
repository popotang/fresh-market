"""Deprecated MySQL-era commands retained for migration and audit compatibility.

Run from the project root with ``python3 -m legacy.<module>``. The path hook keeps
historical absolute imports working without adding wrappers back to the root.
"""
from pathlib import Path
import sys

LEGACY_DIR = Path(__file__).resolve().parent
if str(LEGACY_DIR) not in sys.path:
    sys.path.insert(0, str(LEGACY_DIR))
