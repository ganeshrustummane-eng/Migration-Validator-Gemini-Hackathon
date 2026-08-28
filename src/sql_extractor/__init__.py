"""
SQL Extractor Package — Universal Database Schema Extractor
============================================================
All extractors (PostgreSQL, MSSQL, Snowflake, Athena) and shared data
structures are consolidated in extractors.py. Import from here.

Usage:
    from sql_extractor import PostgresExtractor, SnowflakeExtractor, ExtractorFactory
    pg = PostgresExtractor()
    columns = pg.extract_columns(schema="public", table="events")
"""

from sql_extractor.extractors import (
    BaseExtractor,
    ColumnMetadata,
    PrimaryKeyInfo,
    TableMetadata,
    ExtractionError,
    PostgresExtractor,
    MSSQLExtractor,
    SnowflakeExtractor,
    AthenaExtractor,
    ExtractorFactory,
)

__all__ = [
    "BaseExtractor",
    "ColumnMetadata",
    "PrimaryKeyInfo",
    "TableMetadata",
    "ExtractionError",
    "PostgresExtractor",
    "MSSQLExtractor",
    "SnowflakeExtractor",
    "AthenaExtractor",
    "ExtractorFactory",
]
