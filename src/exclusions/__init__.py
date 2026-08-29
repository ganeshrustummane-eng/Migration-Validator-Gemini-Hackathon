"""
Exclusions Package
===================
Column exclusion management for Migration Validator.

Public API:
    from exclusions import exclusion_manager
    
    decision = exclusion_manager.should_exclude(
        column_name="uTS",
        source_table="AcctSoftware",
        source_type="timestamp",
        target_type="BINARY",
        source_database="mssql",
    )
"""

from exclusions.exclusion_manager import ExclusionDecision, ExclusionManager, exclusion_manager

__all__ = ["ExclusionDecision", "ExclusionManager", "exclusion_manager"]
