"""
Plan Store
===========
Persistence for the CanonicalValidationPlan.

The plan JSON is the CONTRACT. Everything downstream — SQL, YAML, reports —
is a render target derived from it:

    metadata → matching → AI → CanonicalValidationPlan → plan.json   ← contract
                                                            ├→ SQL
                                                            ├→ YAML   (regenerable)
                                                            └→ reports

Rules this module enforces:
  - Plans are written atomically (temp file + replace) so a crashed run never
    leaves a half-written contract behind.
  - Plans round-trip: load(save(plan)) == plan for every field that matters.
  - YAML is NEVER read back to reconstruct intent. If you need to know what a
    run intended to validate, read the plan.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from core.validation_plan import CanonicalValidationPlan

# Plans live outside config/ on purpose: config/ holds generated render targets
# that may be wiped and rebuilt, plans are the durable record of intent.
_DEFAULT_PLAN_DIR = Path(__file__).resolve().parents[2] / "output" / "plans"

_PLAN_SUFFIX = ".plan.json"


class PlanStoreError(RuntimeError):
    """Raised when a plan cannot be read, written, or parsed."""


class PlanStore:
    """Reads and writes CanonicalValidationPlan JSON files."""

    def __init__(self, plan_dir: Optional[Path] = None):
        self.plan_dir = Path(plan_dir) if plan_dir else _DEFAULT_PLAN_DIR

    # -- paths --------------------------------------------------------------

    def path_for(self, source_table: str, layer: str = "bronze") -> Path:
        """Deterministic on-disk location for a table's plan."""
        return self.plan_dir / layer / f"{source_table.lower()}{_PLAN_SUFFIX}"

    def list_plans(self, layer: Optional[str] = None) -> List[Path]:
        root = self.plan_dir / layer if layer else self.plan_dir
        if not root.exists():
            return []
        return sorted(root.rglob(f"*{_PLAN_SUFFIX}"))

    # -- io -----------------------------------------------------------------

    def save(self, plan: CanonicalValidationPlan, layer: str = "bronze") -> Path:
        """Write the plan atomically and return its path."""
        path = self.path_for(plan.source_table, layer)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(plan.to_dict(), indent=2, ensure_ascii=False)

        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
            os.replace(tmp_name, path)
        except Exception as exc:
            Path(tmp_name).unlink(missing_ok=True)
            raise PlanStoreError(f"Could not write plan to {path}: {exc}") from exc
        return path

    def load(self, path: Path) -> CanonicalValidationPlan:
        """Load a plan from an explicit path."""
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError as exc:
            raise PlanStoreError(f"No plan at {path}") from exc
        except json.JSONDecodeError as exc:
            raise PlanStoreError(f"Plan at {path} is not valid JSON: {exc}") from exc

        try:
            return CanonicalValidationPlan.from_dict(data)
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanStoreError(f"Plan at {path} does not match the plan schema: {exc}") from exc

    def load_for_table(
        self, source_table: str, layer: str = "bronze"
    ) -> Optional[CanonicalValidationPlan]:
        """Load a table's plan, or None when it has never been generated."""
        path = self.path_for(source_table, layer)
        return self.load(path) if path.exists() else None
