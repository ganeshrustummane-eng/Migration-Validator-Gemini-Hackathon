"""Shared incremental-filter predicate injection.

Per docs/decisions/0030-progressive-decision-report-pack-incremental-sanity-streamlit.md
Decision 1: the incremental filter column is per-table config
(validation_plan.incremental.filter_column in the YAML); from_date/to_date
are runtime values written into that same YAML immediately before a run,
mirroring the existing mismatch_threshold_pct injection in webapp/app.py.

This is the ONLY place that edits sourcequery/targetquery text for
incremental filtering -- never done ad hoc, never a blind string replace.
"""

import re

_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)


def apply_incremental_predicate(query: str, filter_column: str, from_date: str, to_date: str) -> str:
    """Append a BETWEEN predicate on filter_column to an already-complete
    SELECT statement (as read from a generated YAML's sourcequery/targetquery).

    Strips the trailing ';', adds WHERE or AND depending on whether the
    query already has a top-level WHERE clause, then re-appends ';'.
    """
    if not filter_column:
        raise ValueError("apply_incremental_predicate requires a non-empty filter_column")
    if not from_date or not to_date:
        raise ValueError("apply_incremental_predicate requires both from_date and to_date")
    if from_date > to_date:
        raise ValueError(f"from_date ({from_date}) is after to_date ({to_date})")

    stripped = query.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()

    predicate = f"{filter_column} BETWEEN '{from_date}' AND '{to_date}'"
    # ponytail: a bare `\bWHERE\b` search can't tell a top-level WHERE from
    # one inside a subquery, so a custom-SQL YAML with a WHERE-only-in-a-
    # subquery would wrongly get "AND" instead of "WHERE" here. Fine for
    # every generated YAML today (flat SELECT...FROM...[WHERE...], confirmed
    # by inspection); upgrade to a real top-level clause parse if a custom
    # SQL YAML with a sub-SELECT needs incremental filtering.
    clause = "AND" if _WHERE_RE.search(stripped) else "WHERE"
    return f"{stripped} {clause} {predicate};"
