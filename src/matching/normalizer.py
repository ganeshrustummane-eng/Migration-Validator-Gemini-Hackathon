"""
Column Name Normalizer
========================
Deterministic normalization used for column matching.

The normalization collapses all common naming variants to the same key so that
  created_at / created-at / createdAt / CREATED_AT / CREATEDAT
all normalize to:
  createdat

Rules applied (in order):
  1. Lowercase
  2. Remove underscores
  3. Remove hyphens
  4. Remove spaces
  5. Remove all remaining non-alphanumeric characters

The ORIGINAL column name is NEVER modified — normalization is only used for
matching. SQL generation always uses the original name.

Examples
--------
  created_at       → createdat
  CREATED_AT       → createdat
  createdAt        → createdat
  created-at       → createdat
  customer_id      → customerid
  CUSTOMER_ID      → customerid
  customerId       → customerid
  customer-id      → customerid
  TransactionDate  → transactiondate
  TRANSACTIONDATE  → transactiondate
  transaction_date → transactiondate
  _FIVETRAN_ACTIVE → fivetranactive
"""

import re


def normalize_column_name(name: str) -> str:
    """
    Deterministically normalize a column name for matching purposes.

    This function is the single canonical normalization used everywhere:
      - ExactMatcher (normalized-exact matching)
      - FuzzyMatcher (similarity comparison base)
      - ConfidenceScorer (name similarity component)
      - AI prompts (to show AI the normalized form alongside the original)

    Args:
        name: Original column name (any case, any separator style)

    Returns:
        Normalized string: lowercase, alphanumeric only, no separators.
        Returns empty string for empty/None input.

    Examples:
        >>> normalize_column_name("created_at")
        'createdat'
        >>> normalize_column_name("CREATED_AT")
        'createdat'
        >>> normalize_column_name("createdAt")
        'createdat'
        >>> normalize_column_name("customer-id")
        'customerid'
        >>> normalize_column_name("TransactionDate")
        'transactiondate'
    """
    if not name:
        return ""

    # Step 1: lowercase
    result = name.lower()

    # Step 2: remove all non-alphanumeric characters
    # This covers underscores, hyphens, spaces, dots, and any other separators
    result = re.sub(r"[^a-z0-9]", "", result)

    return result


def are_normalized_equal(name_a: str, name_b: str) -> bool:
    """
    Return True if two column names normalize to the same value.

    Args:
        name_a: First column name
        name_b: Second column name

    Returns:
        True if both normalize to the same string.

    Examples:
        >>> are_normalized_equal("created_at", "CREATEDAT")
        True
        >>> are_normalized_equal("customer_id", "CUSTOMER_ID")
        True
        >>> are_normalized_equal("created_at", "updated_at")
        False
    """
    return normalize_column_name(name_a) == normalize_column_name(name_b)
