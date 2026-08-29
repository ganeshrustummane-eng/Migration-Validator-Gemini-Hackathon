"""
Confidence Scorer
==================
Computes an explainable multi-factor confidence score for a proposed
source → target column mapping.

IMPORTANT: These are confidence scores, not calibrated probabilities.
They express how strongly the available evidence supports a mapping decision.

Factors (each contributes a weight to the final score)
-------------------------------------------------------
  name_similarity      (weight 0.40) — normalized fuzzy name similarity
  type_compatibility   (weight 0.35) — are the types compatible per catalog?
  position_proximity   (weight 0.10) — how close the ordinal positions are
  learned_example      (weight 0.15) — bonus if a matching learned example exists

Final score = weighted sum, clamped to [0.0, 1.0].

The weights reflect empirical importance:
  - Name similarity is the strongest signal for renamed columns.
  - Type compatibility is the second strongest — wrong type = wrong rule.
  - Position is a weak tie-breaker for symmetrical schemas.
  - A matched learned example provides a strong boost.

Explainability
--------------
ConfidenceBreakdown exposes each factor individually so the CLI and YAML
can show WHY a mapping was accepted or flagged for review.

Example output:
  Name similarity  : 0.98  (weight 0.40 → 0.392)
  Type compat.     : 0.90  (weight 0.35 → 0.315)
  Position proxim. : 0.50  (weight 0.10 → 0.050)
  Learned example  : 1.00  (weight 0.15 → 0.150)
  ─────────────────────────────────────────
  Final confidence : 0.907
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from sql_extractor.extractors import ColumnMetadata
from matching.normalizer import normalize_column_name


# ---------------------------------------------------------------------------
# Known type-compatibility pairs (PostgreSQL → Snowflake)
# ---------------------------------------------------------------------------
# These express SEMANTIC compatibility — whether the types are logically
# the same after migration transformation. A high score means a known
# valid transformation exists; a low score means types are unusual together.

_TYPE_COMPAT_PAIRS: List[Tuple[str, str, float]] = [
    # Exact / near-exact type matches
    ("boolean",                  "BOOLEAN",      1.0),
    ("bool",                     "BOOLEAN",      1.0),
    ("integer",                  "NUMBER",        1.0),
    ("integer",                  "INTEGER",       1.0),
    ("int",                      "NUMBER",        1.0),
    ("bigint",                   "NUMBER",        1.0),
    ("smallint",                 "NUMBER",        1.0),
    ("serial",                   "NUMBER",        1.0),
    ("bigserial",                "NUMBER",        1.0),
    ("numeric",                  "NUMBER",        1.0),
    ("numeric",                  "NUMERIC",       1.0),
    ("decimal",                  "NUMBER",        1.0),
    ("decimal",                  "DECIMAL",       1.0),
    ("real",                     "FLOAT",         1.0),
    ("double precision",         "FLOAT",         1.0),
    ("float",                    "FLOAT",         1.0),
    ("money",                    "NUMBER",        0.9),
    ("character varying",        "TEXT",          1.0),
    ("character varying",        "VARCHAR",       1.0),
    ("character varying",        "STRING",        1.0),
    ("varchar",                  "VARCHAR",       1.0),
    ("varchar",                  "TEXT",          1.0),
    ("text",                     "TEXT",          1.0),
    ("text",                     "VARCHAR",       1.0),
    ("text",                     "STRING",        1.0),
    ("char",                     "CHAR",          1.0),
    ("uuid",                     "TEXT",          1.0),
    ("uuid",                     "VARCHAR",       1.0),
    ("date",                     "DATE",          1.0),
    ("timestamp without time zone", "TIMESTAMP_NTZ", 1.0),
    ("timestamp",                "TIMESTAMP_NTZ", 1.0),
    ("timestamp",                "TIMESTAMP",     1.0),
    ("timestamp with time zone", "TIMESTAMP_TZ",  1.0),
    ("timestamptz",              "TIMESTAMP_TZ",  1.0),
    ("json",                     "VARIANT",       1.0),
    ("jsonb",                    "VARIANT",       1.0),
    ("json",                     "VARCHAR",       0.9),
    ("jsonb",                    "VARCHAR",       0.9),
    ("bytea",                    "BINARY",        1.0),
    ("hstore",                   "VARCHAR",       0.9),
    ("hstore",                   "TEXT",          0.9),
    # Common migration transformations (type changed intentionally)
    ("timestamp",                "TEXT",          0.80),
    ("timestamp",                "VARCHAR",       0.80),
    ("timestamp without time zone", "TEXT",       0.80),
    ("timestamp without time zone", "VARCHAR",    0.80),
    ("integer",                  "TEXT",          0.75),
    ("bigint",                   "TEXT",          0.75),
    ("numeric",                  "TEXT",          0.75),
    ("boolean",                  "TEXT",          0.75),
    ("boolean",                  "VARCHAR",       0.75),
    ("uuid",                     "UUID",          1.0),
]

# Build a lookup dict: (pg_norm, sf_norm) → score
_COMPAT_LOOKUP: dict = {}
for _pg, _sf, _score in _TYPE_COMPAT_PAIRS:
    _key = (_pg.upper(), _sf.upper())
    _COMPAT_LOOKUP[_key] = _score


def _type_compatibility(pg_type: str, sf_type: str) -> float:
    """
    Return a compatibility score [0.0, 1.0] for a PG→SF type pair.
    Uses normalized type names (strips precision/size modifiers).
    Returns 0.6 for unknown pairs (neutral — type not in catalog).
    """
    import re

    def _normalize_type(t: str) -> str:
        # Strip precision/size: 'CHARACTER VARYING(100)' → 'CHARACTER VARYING'
        t = re.sub(r"\s*\([^)]*\)", "", t).strip().upper()
        return t

    pg_norm = _normalize_type(pg_type)
    sf_norm = _normalize_type(sf_type)

    # Try exact lookup
    score = _COMPAT_LOOKUP.get((pg_norm, sf_norm))
    if score is not None:
        return score

    # Try prefix match (handles e.g. 'CHARACTER VARYING' matching 'TEXT' pairs)
    for (pg_key, sf_key), val in _COMPAT_LOOKUP.items():
        if pg_norm.startswith(pg_key) and sf_norm.startswith(sf_key):
            return val

    # Unknown combination — penalise mildly but don't reject
    return 0.60


def _position_proximity(src_pos: int, tgt_pos: int, total: int) -> float:
    """
    Return a score [0.0, 1.0] based on how close the ordinal positions are.
    Identical position → 1.0; maximum possible difference → 0.0.
    """
    if total <= 1:
        return 1.0
    diff = abs(src_pos - tgt_pos)
    max_diff = max(total - 1, 1)
    return max(0.0, 1.0 - (diff / max_diff))


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ConfidenceBreakdown:
    """
    Explainable breakdown of a confidence score for one column mapping.

    Attributes (each 0.0–1.0):
        name_similarity     : Normalized fuzzy name match score
        type_compatibility  : Source/target type pair compatibility
        position_proximity  : Ordinal position closeness
        learned_example     : Bonus from matching learned example (0.0 or 1.0)
        final_score         : Weighted sum clamped to [0.0, 1.0]
        reason              : Human-readable explanation
    """
    name_similarity:    float
    type_compatibility: float
    position_proximity: float
    learned_example:    float
    final_score:        float
    reason:             str

    def as_dict(self) -> dict:
        return {
            "name_similarity":    round(self.name_similarity, 3),
            "type_compatibility": round(self.type_compatibility, 3),
            "position_proximity": round(self.position_proximity, 3),
            "learned_example":    round(self.learned_example, 3),
            "final_score":        round(self.final_score, 3),
            "reason":             self.reason,
        }

    def format_display(self, indent: int = 0) -> str:
        """Format for CLI/YAML display."""
        pad = " " * indent
        return (
            f"{pad}Name similarity  : {self.name_similarity:.3f}  (weight 0.40)\n"
            f"{pad}Type compat.     : {self.type_compatibility:.3f}  (weight 0.35)\n"
            f"{pad}Position proxim. : {self.position_proximity:.3f}  (weight 0.10)\n"
            f"{pad}Learned example  : {self.learned_example:.3f}  (weight 0.15)\n"
            f"{pad}─────────────────────────────────────────\n"
            f"{pad}Final confidence : {self.final_score:.3f}\n"
            f"{pad}Reason           : {self.reason}"
        )


# ---------------------------------------------------------------------------
# Confidence Scorer
# ---------------------------------------------------------------------------

class ConfidenceScorer:
    """
    Computes multi-factor confidence scores for source→target column mappings.

    Weights (must sum to 1.0):
        name_similarity    : 0.40
        type_compatibility : 0.35
        position_proximity : 0.10
        learned_example    : 0.15
    """

    WEIGHT_NAME    = 0.40
    WEIGHT_TYPE    = 0.35
    WEIGHT_POS     = 0.10
    WEIGHT_LEARNED = 0.15

    def score(
        self,
        source_col: ColumnMetadata,
        target_col: ColumnMetadata,
        fuzzy_score: float,
        total_columns: int = 50,
        has_learned_example: bool = False,
    ) -> ConfidenceBreakdown:
        """
        Compute a confidence score for mapping source_col → target_col.

        Args:
            source_col          : Source (PostgreSQL) column
            target_col          : Target (Snowflake) column
            fuzzy_score         : Pre-computed normalized name similarity [0,1]
            total_columns       : Approximate total column count for position scoring
            has_learned_example : True if a matching learned example was found

        Returns:
            ConfidenceBreakdown with all factors and final score.
        """
        name_sim  = fuzzy_score
        type_comp = _type_compatibility(source_col.data_type, target_col.data_type)
        pos_prox  = _position_proximity(
            source_col.ordinal_position,
            target_col.ordinal_position,
            total_columns,
        )
        learned = 1.0 if has_learned_example else 0.0

        final = (
            name_sim  * self.WEIGHT_NAME
            + type_comp * self.WEIGHT_TYPE
            + pos_prox  * self.WEIGHT_POS
            + learned   * self.WEIGHT_LEARNED
        )
        final = min(1.0, max(0.0, final))

        reason = self._build_reason(
            source_col, target_col, name_sim, type_comp, pos_prox,
            has_learned_example, final
        )

        return ConfidenceBreakdown(
            name_similarity=name_sim,
            type_compatibility=type_comp,
            position_proximity=pos_prox,
            learned_example=learned,
            final_score=final,
            reason=reason,
        )

    def _build_reason(
        self,
        src: ColumnMetadata,
        tgt: ColumnMetadata,
        name_sim: float,
        type_comp: float,
        pos_prox: float,
        learned: bool,
        final: float,
    ) -> str:
        src_norm = normalize_column_name(src.column_name)
        tgt_norm = normalize_column_name(tgt.column_name)

        parts = []

        if src_norm == tgt_norm:
            parts.append(f"normalized names are identical ('{src_norm}')")
        elif name_sim >= 0.90:
            parts.append(
                f"normalized names are very similar "
                f"('{src_norm}' ~ '{tgt_norm}', score {name_sim:.2f})"
            )
        elif name_sim >= 0.75:
            parts.append(
                f"normalized names are similar "
                f"('{src_norm}' ~ '{tgt_norm}', score {name_sim:.2f})"
            )
        else:
            parts.append(
                f"normalized name similarity is low "
                f"('{src_norm}' vs '{tgt_norm}', score {name_sim:.2f})"
            )

        if type_comp >= 0.95:
            parts.append(
                f"{src.data_type} → {tgt.data_type} is a known compatible type pair"
            )
        elif type_comp >= 0.75:
            parts.append(
                f"{src.data_type} → {tgt.data_type} is a known migration transformation"
            )
        else:
            parts.append(
                f"type pair {src.data_type} → {tgt.data_type} is not in the catalog"
            )

        if learned:
            parts.append("a matching learned example exists in rule_book_learned.json")

        return ". ".join(parts) + "."
