"""
Graph traversal operations using recursive CTEs.

Wraps the database functions for transitive closure queries.
"""

from typing import Dict, List, Optional

from codecompass.db import get_db
from codecompass.db.repository import get_symbol
from codecompass.db.search import (
    find_all_references,
    find_callees,
    find_callees_depth,
    find_callers,
    find_callers_depth,
    find_definition_ranked,
    transitive_callees,
    transitive_callers,
    transitive_impact,
)
from codecompass.db.search import (
    find_definition as db_find_definition,
)


def _normalize_caller_result(row: Dict) -> Dict:
    """Normalize caller result to common format."""
    return {
        "id": row.get("caller_id"),
        "symbol_id": row.get("caller_id"),
        "name": row.get("caller_name"),
        "qualified_name": row.get("caller_qualified_name"),
        "kind": row.get("caller_kind"),
        "file_path": row.get("caller_file_path"),
        "start_line": row.get("caller_start_line"),
        "end_line": row.get("caller_end_line"),
        "depth": row.get("depth"),
        "edge_confidence": row.get("edge_confidence"),
        "call_line": row.get("call_line"),
    }


def _normalize_callee_result(row: Dict) -> Dict:
    """Normalize callee result to common format."""
    return {
        "id": row.get("callee_id"),
        "symbol_id": row.get("callee_id"),
        "name": row.get("callee_name"),
        "qualified_name": row.get("callee_qualified_name"),
        "kind": row.get("callee_kind"),
        "file_path": row.get("callee_file_path"),
        "start_line": row.get("callee_start_line"),
        "end_line": row.get("callee_end_line"),
        "depth": row.get("depth"),
        "edge_confidence": row.get("edge_confidence"),
        "call_line": row.get("call_line"),
    }


def _normalize_reference_result(row: Dict) -> Dict:
    """Normalize reference result to common format."""
    return {
        "id": row.get("reference_id"),
        "symbol_id": row.get("source_symbol_id"),
        "name": row.get("source_name"),
        "qualified_name": row.get("source_qualified_name"),
        "kind": row.get("source_kind"),
        "file_path": row.get("source_file_path"),
        "start_line": row.get("source_start_line"),
        "end_line": row.get("source_end_line"),
        "edge_type": row.get("edge_type"),
        "edge_confidence": row.get("edge_confidence"),
        "line": row.get("line"),
        "col": row.get("col"),
    }


def get_callers(symbol_id: int, depth: int = 1, min_confidence: float = 0.0) -> List[Dict]:
    """Get callers of a symbol up to specified depth."""
    if depth <= 1:
        return find_callers(symbol_id)
    results = find_callers_depth(symbol_id, depth, min_confidence)
    return [_normalize_caller_result(r) for r in results]


def get_callees(symbol_id: int, depth: int = 1, min_confidence: float = 0.0) -> List[Dict]:
    """Get callees of a symbol up to specified depth."""
    if depth <= 1:
        return find_callees(symbol_id)
    results = find_callees_depth(symbol_id, depth, min_confidence)
    return [_normalize_callee_result(r) for r in results]


def get_references(symbol_id: int, repository_id: Optional[int] = None, min_confidence: float = 0.0) -> List[Dict]:
    """Get all references to a symbol."""
    results = find_all_references(symbol_id, repository_id, min_confidence)
    return [_normalize_reference_result(r) for r in results]


def get_transitive_callers(symbol_id: int, max_depth: int = 10, min_confidence: float = 0.0) -> List[Dict]:
    """Get all transitive callers (functions that call this directly or indirectly)."""
    return transitive_callers(symbol_id, max_depth, min_confidence)


def get_transitive_callees(symbol_id: int, max_depth: int = 10, min_confidence: float = 0.0) -> List[Dict]:
    """Get all transitive callees (functions called directly or indirectly)."""
    return transitive_callees(symbol_id, max_depth, min_confidence)


def get_transitive_impact(symbol_id: int, max_depth: int = 10, min_confidence: float = 0.0) -> List[Dict]:
    """Get transitive impact - all symbols affected by changing this symbol."""
    return transitive_impact(symbol_id, max_depth, min_confidence, limit_count=1000)


def find_definition(
    name: str,
    repository_id: Optional[int] = None,
    kind: Optional[str] = None,
    use_ranking: bool = True
) -> List[Dict]:
    """
    Find symbol definitions by name.

    If use_ranking is True, uses disambiguation ranking:
    1. Exact qualified name match
    2. Exact name match
    3. Trigram similarity
    """
    if use_ranking:
        return find_definition_ranked(name, repository_id, kind)
    return db_find_definition(name, repository_id, kind)


def find_implementations(
    interface_name: str,
    repository_id: Optional[int] = None
) -> List[Dict]:
    """Find all implementations of an interface/trait."""
    db = get_db()

    # Find the interface symbol
    interfaces = find_definition_ranked(interface_name, repository_id, "interface")
    if not interfaces:
        # Also check for traits, abstract classes
        interfaces = find_definition_ranked(interface_name, repository_id, "trait")
    if not interfaces:
        interfaces = find_definition_ranked(interface_name, repository_id, "class")

    if not interfaces:
        return []

    # For each interface, find classes that inherit/implement it
    results = []
    for iface in interfaces:
        # Find edges of type 'inherits' or 'implements' pointing to this interface
        # find_definition_ranked returns 'symbol_id' not 'id'
        iface_id = iface.get("symbol_id") or iface.get("id")
        if iface_id is None:
            continue
        edges = db.execute(
            """
            SELECT * FROM v_edges_with_names
            WHERE dst_symbol_id = %s AND kind IN ('inherits', 'implements')
            """,
            (iface_id,)
        )
        for edge in edges:
            # Get the implementing class
            impl = get_symbol(edge["src_symbol_id"])
            if impl:
                results.append(impl)

    return results


def get_symbol_context(symbol_id: int, radius: int = 2, min_confidence: float = 0.0) -> dict:
    """
    Get a neighborhood view around a symbol for context.

    Returns:
        Dict with 'center', 'callers', 'callees', 'references', 'siblings'
    """
    center = get_symbol(symbol_id)
    if not center:
        return {}

    return {
        "center": center,
        "callers": get_callers(symbol_id, depth=radius, min_confidence=min_confidence)[:radius * 2],
        "callees": get_callees(symbol_id, depth=radius, min_confidence=min_confidence)[:radius * 2],
        "references": get_references(symbol_id)[:radius * 2],
        "transitive_callers": get_transitive_callers(symbol_id, radius, min_confidence)[:radius * 4],
        "transitive_callees": get_transitive_callees(symbol_id, radius, min_confidence)[:radius * 4],
    }


def find_related_symbols(
    symbol_id: int,
    max_results: int = 20
) -> List[Dict]:
    """
    Find symbols related to the given symbol via graph structure.

    Combines callers, callees, and siblings (same file/class).
    """
    db = get_db()
    center = get_symbol(symbol_id)
    if not center:
        return []

    related = []

    # Direct callers and callees
    related.extend(get_callers(symbol_id))
    related.extend(get_callees(symbol_id))

    # Siblings: other symbols in same file
    file_symbols = db.execute(
        "SELECT * FROM v_symbols_with_file WHERE file_id = %s AND id != %s",
        (center["file_id"], symbol_id)
    )
    related.extend(file_symbols)

    # Siblings: other methods in same class (if this is a method)
    if center.get("scope_symbol_id"):
        class_methods = db.execute(
            "SELECT * FROM v_symbols_with_file WHERE scope_symbol_id = %s AND id != %s",
            (center["scope_symbol_id"], symbol_id)
        )
        related.extend(class_methods)

    # Deduplicate and limit
    seen = set()
    unique = []
    for r in related:
        if r["id"] not in seen:
            seen.add(r["id"])
            unique.append(r)
            if len(unique) >= max_results:
                break

    return unique