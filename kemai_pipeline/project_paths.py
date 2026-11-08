"""Canonical project and private runtime paths shared by old and new entry points."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = PROJECT_ROOT / ".runtime"
RUNTIME_STATE = RUNTIME_ROOT / "state"
RUNTIME_LOGS = RUNTIME_ROOT / "logs"
RUNTIME_LOCKS = RUNTIME_ROOT / "locks"
RUNTIME_PID = RUNTIME_ROOT / "pid"
