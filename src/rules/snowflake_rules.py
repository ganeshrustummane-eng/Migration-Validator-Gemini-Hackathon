"""
Snowflake Source Rules
=======================
When Snowflake is used as a source (not just target), the same normalization
rules apply since types are already compatible. Re-exports from postgres_base_rules.
"""
from rules.postgres_base_rules import (
    BaseValidationRule, RuleRegistry, NULL_PLACEHOLDER,
    BooleanRule, IntegerRule, NumericRule,
    TimestampTZRule, TimestampNTZRule, DateRule,
    TextRule, UUIDRule, JSONRule, ByteaRule, HStoreRule,
    NullPlaceholderRule, DEFAULT_DECIMAL_PLACES,
)

__all__ = [
    "BaseValidationRule", "RuleRegistry", "NULL_PLACEHOLDER",
    "BooleanRule", "IntegerRule", "NumericRule",
    "TimestampTZRule", "TimestampNTZRule", "DateRule",
    "TextRule", "UUIDRule", "JSONRule", "ByteaRule",
    "HStoreRule", "NullPlaceholderRule", "DEFAULT_DECIMAL_PLACES",
]


