"""Focused tests for the schema additions in docs/decisions/0030-progressive-decision-report-pack-incremental-sanity-streamlit.md:
IntegrityCheckBlock (source-only, no target side) and its wiring into
TableValidations.

Run: python -m pytest tests/src/test_config_schema.py -q
"""

import pytest
from pydantic import ValidationError

from validation.config_schema import IntegrityCheckBlock, TableValidations


def test_integrity_check_block_accepts_source_only():
    block = IntegrityCheckBlock(
        source_table_name="orders",
        source="postgresql",
        sourcequery="SELECT * FROM orders WHERE customer_id NOT IN (SELECT id FROM customers);",
        test_case="orphan_customer_id",
        summary="Orders referencing a missing customer",
    )
    assert block.source_table_name == "orders"


def test_integrity_check_block_rejects_target_fields():
    with pytest.raises(ValidationError):
        IntegrityCheckBlock(
            source_table_name="orders",
            source="postgresql",
            sourcequery="SELECT 1;",
            target="snowflake",  # extra="forbid" -- integrity checks have no target
        )


def test_integrity_check_block_rejects_non_select():
    with pytest.raises(ValidationError):
        IntegrityCheckBlock(
            source_table_name="orders",
            source="postgresql",
            sourcequery="DELETE FROM orders;",
        )


def test_table_validations_accepts_integrity_check_alongside_data_validation():
    doc = TableValidations(
        integrity_check={
            "source_table_name": "orders",
            "source": "postgresql",
            "sourcequery": "SELECT 1;",
        }
    )
    assert doc.integrity_check is not None
    assert doc.data_validation is None
