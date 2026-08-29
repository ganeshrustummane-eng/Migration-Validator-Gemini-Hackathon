"""
Puts src/ (and repo root) on sys.path for every test module, so `pytest tests/`
works standalone from a clean checkout. test_security.py already does this
itself; the other test files didn't, so a bare `pytest tests/` (as opposed to
`pytest tests/test_security.py`) failed collection with ModuleNotFoundError
for core/exclusions/generated_queries/validation.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for _p in (str(_SRC), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
