"""
Gemini Migration Intelligence Connector
=========================================
Turns Migration Validator into a Gemini-powered connector by exposing
purpose-built tools that Gemini Enterprise can call to perform governed
migration-validation workflows.

Architecture:
    Gemini Enterprise
          ↓
    Migration Intelligence Connector  (this package)
          ↓
    Migration Validator APIs / tools
          ↓
    PostgreSQL / MSSQL / Athena  →  Snowflake  →  Validation Engine
          ↓
    Results  →  Gemini explanation

Design principle:
    - Gemini = conversational intelligence layer
    - Migration Validator = governed execution and data-validation layer
    - AI recommends; humans approve high-risk decisions
    - Every write action is audited
"""

from .audit import AuditLogger, AuditRecord
from .approval_store import ApprovalStore, ApprovalRecord, ApprovalStatus
from .metrics import MetricsTracker

__all__ = [
    "AuditLogger",
    "AuditRecord",
    "ApprovalStore",
    "ApprovalRecord",
    "ApprovalStatus",
    "MetricsTracker",
]
