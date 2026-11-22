"""
Hybrid search combining BM25 + Vector with Reciprocal Rank Fusion (RRF).

Optionally expands results using the call/reference graph.
"""

from typing import Dict, List, Optional

from codecompass.config import get_settings
from codecompass.db.search import (
    bm25_search_with_ranks,
    hybrid_search,
    hybrid_search_with_explanation,
    vector_search_with_ranks,
)
from codecompass.graph.traversal import (
    get_callees,
    get_callers,
)
from codecompass.indexing.embeddings import compute_embedding


def search(
    query: str,
    repository_id: Optional[int] = None,
    limit: int = 20,
    use_graph_expansion: bool = False,
    graph_depth: int = 2,
    explain: bool = False
) -> List[Dict]:
    """
    Hybrid search: BM25 + Vector merged with RRF.

    Args:
        query: Natural language search query
        repository_id: Optional repository filter
        limit: Maximum results to return
        use_graph_expansion: If True, expand results via call graph
        graph_depth: Depth for graph expansion
        explain: If True, include score breakdown in results

    Returns:
        List of symbols with search metadata
    """
    settings = get_settings()

    # Get embeddings for query
    query_embedding = compute_embedding(query)

    # For graph expansion, we need more initial results to allow graph results to appear
    initial_limit = limit * 2 if use_graph_expansion else limit

    # Run hybrid search (RRF) with optional explanation
    if explain:
        results = hybrid_search_with_explanation(
            query=query,
            query_embedding=query_embedding,
            repository_id=repository_id,
            limit=initial_limit,
            bm25_limit=settings.bm25_limit,
            vector_limit=settings.vector_limit,
            rrf_k=settings.rrf_k
        )
    else:
        results = hybrid_search(
            query=query,
            query_embedding=query_embedding,
            repository_id=repository_id,
            limit=initial_limit,
            bm25_limit=settings.bm25_limit,
            vector_limit=settings.vector_limit,
            rrf_k=settings.rrf_k
        )

    # Add search type metadata
    for r in results:
        r["search_type"] = "hybrid"

    # Optional graph expansion
    if use_graph_expansion and results:
        results = _expand_with_graph(results, repository_id, graph_depth, limit)

    return results


def search_with_graph_expansion(
    query: str,
    repository_id: Optional[int] = None,
    limit: int = 20,
    graph_depth: int = 2,
    explain: bool = False
) -> List[Dict]:
    """Hybrid search with graph expansion enabled."""
    return search(query, repository_id, limit, use_graph_expansion=True, graph_depth=graph_depth, explain=explain)


def _expand_with_graph(
    results: List[Dict],
    repository_id: Optional[int],
    depth: int,
    limit: int
) -> List[Dict]:
    """
    Expand search results using the call graph.

    For each result, add its direct callers and callees as secondary results
    with a lower score, clearly labeled "via graph".
    """
    expanded = list(results)
    seen_ids = {r["id"] for r in results}
    graph_results = []

    for result in results[:5]:  # Only expand top 5 to avoid explosion
        symbol_id = result["id"]
        parent_score = result.get("fused_score", 0.0)

        # Get direct callees (what this function calls)
        callees = get_callees(symbol_id)
        for callee in callees:
            if callee.get("symbol_id") and callee["symbol_id"] not in seen_ids:
                callee["search_type"] = "via graph:callee"
                callee["graph_distance"] = 1
                # Use parent's score * 0.5 as graph result score
                callee["fused_score"] = parent_score * 0.5
                graph_results.append(callee)
                seen_ids.add(callee["symbol_id"])

        # Get direct callers (what calls this function)
        callers = get_callers(symbol_id)
        for caller in callers:
            if caller.get("symbol_id") and caller["symbol_id"] not in seen_ids:
                caller["search_type"] = "via graph:caller"
                caller["graph_distance"] = 1
                # Use parent's score * 0.5 as graph result score
                caller["fused_score"] = parent_score * 0.5
                graph_results.append(caller)
                seen_ids.add(caller["symbol_id"])

    # Combine: hybrid results first (preserve their order), then graph results by score
    # This ensures graph expansion results are included even if they have lower scores
    graph_results.sort(key=lambda r: -r.get("fused_score", 0.0))

    # Reserve at least 2 slots for graph results if available
    max_hybrid = min(limit - 2, len(expanded)) if graph_results else limit
    final_results = expanded[:max_hybrid]

    # Fill remaining slots with graph results
    remaining = limit - len(final_results)
    if remaining > 0:
        final_results.extend(graph_results[:remaining])

    return final_results[:limit]


def bm25_search_mode(
    query: str,
    repository_id: Optional[int] = None,
    limit: int = 20,
    explain: bool = False
) -> List[Dict]:
    """Pure BM25 search mode."""
    results = bm25_search_with_ranks(query, repository_id, limit)
    for r in results:
        r["search_type"] = "bm25"
        if explain:
            r["score_explanation"] = {
                "bm25_rank": r.get("bm25_rank"),
                "bm25_score": round(r.get("bm25_score", 0.0), 4),
            }
    return results


def vector_search_mode(
    query: str,
    repository_id: Optional[int] = None,
    limit: int = 20,
    explain: bool = False
) -> List[Dict]:
    """Pure vector search mode."""
    query_embedding = compute_embedding(query)
    results = vector_search_with_ranks(query_embedding, repository_id, limit)
    for r in results:
        r["search_type"] = "vector"
        if explain:
            r["score_explanation"] = {
                "vector_rank": r.get("vector_rank"),
                "vector_score": round(r.get("vector_score", 0.0), 4),
            }
    return results


def multi_stage_search(
    query: str,
    repository_id: Optional[int] = None,
    limit: int = 20,
    explain: bool = False
) -> dict:
    """
    Run all search modes and return results for comparison.

    Returns dict with keys: 'bm25', 'vector', 'hybrid', 'hybrid_graph'
    """
    bm25_results = bm25_search_mode(query, repository_id, limit, explain)
    vector_results = vector_search_mode(query, repository_id, limit, explain)
    hybrid_results = search(query, repository_id, limit, use_graph_expansion=False, explain=explain)
    hybrid_graph_results = search(query, repository_id, limit, use_graph_expansion=True, explain=explain)

    return {
        "bm25": bm25_results,
        "vector": vector_results,
        "hybrid": hybrid_results,
        "hybrid_graph": hybrid_graph_results,
    }