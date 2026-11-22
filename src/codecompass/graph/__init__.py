"""
Graph operations for call/reference traversal.

Provides high-level functions for common graph queries using the database's recursive CTEs.
"""

from .traversal import (
    find_definition,
    find_implementations,
    get_callees,
    get_callers,
    get_references,
    get_transitive_callees,
    get_transitive_callers,
    get_transitive_impact,
)

__all__ = [
    "get_callers",
    "get_callees",
    "get_references",
    "get_transitive_callers",
    "get_transitive_callees",
    "get_transitive_impact",
    "find_definition",
    "find_implementations",
]