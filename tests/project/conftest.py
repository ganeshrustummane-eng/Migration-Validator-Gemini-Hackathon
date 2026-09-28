"""Test path setup for Project (validation engine) tests."""

import sys
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parents[2] / "Project"
for _p in (_PROJECT_DIR, _PROJECT_DIR / "utils"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
