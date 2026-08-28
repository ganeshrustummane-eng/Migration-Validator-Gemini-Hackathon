"""
Mismatch Feedback Workflow
===========================
Records human corrections to column mapping decisions and persists them
as learned examples in rule_book_learned.json.

When the CLI presents a generated mapping and the human says "wrong rule"
or "wrong target column", the FeedbackRecorder captures:
  - Which source → target pair was corrected
  - What the correct rule should have been
  - Why (free text reason)
  - When (ISO timestamp)
  - Which table it came from

These corrections are stored in rule_book_learned.json under the key
"learned_corrections" and are retrieved at matching time by LearnedRuleRetriever
to give correctly-mapped pairs a confidence boost.

Security:
  - No SQL is executed by this module
  - Corrections store only column metadata (name, type) — no actual data values
  - The learned file is safe to commit to version control
"""

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from learning.retrieval import LearnedExample, LearnedRuleRetriever


_LEARNED_PATH = Path(__file__).parent.parent / "rule_book_learned.json"


@dataclass
class MismatchFeedback:
    """
    One human correction to a column mapping decision.

    Attributes:
        source_column  : PG column name that was mismatched
        target_column  : The CORRECT SF target column (human-chosen)
        source_type    : PG data type
        target_type    : SF data type of the correct target
        correct_rule   : The correct transformation rule ID
        reason         : Human explanation (free text, optional)
        table_name     : Table name this correction applies to
        was_ai_decision: True if the incorrect decision came from the AI
    """
    source_column:   str
    target_column:   str
    source_type:     str
    target_type:     str
    correct_rule:    str
    reason:          str = ""
    table_name:      str = ""
    was_ai_decision: bool = False


class FeedbackRecorder:
    """
    Records human corrections and persists them as learned examples.

    All corrections are written to rule_book_learned.json so that future
    pipeline runs benefit from them automatically.

    Usage (in CLI)
    -----
        recorder = FeedbackRecorder()
        recorder.record(MismatchFeedback(
            source_column="created_at",
            target_column="CREATEDAT",
            source_type="timestamp without time zone",
            target_type="VARCHAR",
            correct_rule="text",
            reason="Fivetran serialized timestamp to string in this table",
            table_name="orders",
        ))
    """

    def __init__(self, learned_path: Optional[Path] = None):
        self._path     = learned_path or _LEARNED_PATH
        self._retriever = LearnedRuleRetriever(learned_path=self._path)

    def record(self, feedback: MismatchFeedback) -> bool:
        """
        Record a human correction and persist it to disk.

        If a correction for the same (source_column, target_column) pair
        already exists, it is UPDATED (not duplicated).

        Args:
            feedback: The human correction to record

        Returns:
            True on success, False on write failure.
        """
        example = LearnedExample(
            source_column=feedback.source_column,
            target_column=feedback.target_column,
            source_type=  feedback.source_type,
            target_type=  feedback.target_type,
            correct_rule= feedback.correct_rule,
            reason=       feedback.reason,
            table_name=   feedback.table_name,
            corrected_at= datetime.now().isoformat(),
        )
        return self._persist(example)

    def record_batch(self, feedbacks: List[MismatchFeedback]) -> int:
        """
        Record a list of corrections. Returns the count of successful writes.
        """
        count = 0
        for fb in feedbacks:
            if self.record(fb):
                count += 1
        return count

    # -----------------------------------------------------------------------
    # Internal persistence
    # -----------------------------------------------------------------------

    def _persist(self, new_example: LearnedExample) -> bool:
        """
        Load the file, remove any existing entry for the same source+target,
        append the new one, and write back atomically.
        """
        existing_data = self._load_raw()

        # Remove duplicate entry for same source+target pair
        corrections = existing_data.get("learned_corrections", [])
        corrections = [
            c for c in corrections
            if not (
                c.get("source_column", "").upper() == new_example.source_column.upper()
                and c.get("target_column", "").upper() == new_example.target_column.upper()
            )
        ]
        corrections.append(new_example.to_dict())

        existing_data["learned_corrections"] = corrections
        existing_data["last_updated"] = datetime.now().isoformat()

        return self._write_raw(existing_data)

    def _load_raw(self) -> dict:
        """Load the current state of rule_book_learned.json (or return empty structure)."""
        if not self._path.exists():
            return self._empty_structure()
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            # Ensure learned_corrections key exists
            if "learned_corrections" not in data:
                data["learned_corrections"] = []
            return data
        except Exception as exc:
            print(
                f"  [FeedbackRecorder] Could not read {self._path}: {exc}",
                file=sys.stderr,
            )
            return self._empty_structure()

    def _write_raw(self, data: dict) -> bool:
        """Write the full data dict back to disk."""
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as exc:
            print(
                f"  [FeedbackRecorder] Could not write {self._path}: {exc}",
                file=sys.stderr,
            )
            return False

    @staticmethod
    def _empty_structure() -> dict:
        return {
            "_comment": (
                "Auto-generated by FeedbackRecorder. "
                "Stores human corrections to column mappings. "
                "This file is safe to commit to Git."
            ),
            "version": "1.0",
            "last_updated": datetime.now().isoformat(),
            "learned_corrections": [],
            "learned_rules": [],
        }


# ---------------------------------------------------------------------------
# CLI-friendly interactive feedback prompt
# ---------------------------------------------------------------------------

def prompt_for_feedback(
    source_column: str,
    source_type:   str,
    current_target: str,
    current_rule:  str,
    candidates:    List[str],
    table_name:    str = "unknown",
) -> Optional[MismatchFeedback]:
    """
    Interactive CLI prompt to collect a human correction.

    Returns a MismatchFeedback if the human confirms a correction,
    or None if they confirm the current mapping is correct.

    Args:
        source_column   : The PG source column name
        source_type     : The PG data type
        current_target  : The target column the pipeline chose
        current_rule    : The rule the pipeline assigned
        candidates      : Other candidate target columns to offer
        table_name      : Table name for context

    Returns:
        MismatchFeedback or None.
    """
    print(f"\n  Mapping review for '{source_column}' in table '{table_name}'")
    print(f"    Current:  {source_column} ({source_type}) → {current_target}  rule={current_rule}")

    answer = input("  Is this mapping correct? [Y/n]: ").strip().lower()
    if answer in ("", "y", "yes"):
        return None

    # Offer candidates
    print(f"\n  Choose correct target column:")
    all_options = list({current_target} | set(candidates))
    for i, opt in enumerate(all_options, 1):
        marker = " (current)" if opt == current_target else ""
        print(f"    [{i}] {opt}{marker}")
    print(f"    [0] Enter manually")

    choice_raw = input("  Choice: ").strip()
    try:
        choice_idx = int(choice_raw)
        if choice_idx == 0:
            correct_target = input("  Enter correct target column name: ").strip()
        else:
            correct_target = all_options[choice_idx - 1]
    except (ValueError, IndexError):
        correct_target = choice_raw or current_target

    correct_rule = input(
        f"  Correct rule? (press Enter to keep '{current_rule}'): "
    ).strip() or current_rule

    reason = input("  Reason (optional): ").strip()

    return MismatchFeedback(
        source_column=source_column,
        target_column=correct_target,
        source_type=source_type,
        target_type="",  # caller fills this in from schema metadata
        correct_rule=correct_rule,
        reason=reason,
        table_name=table_name,
        was_ai_decision=False,
    )
