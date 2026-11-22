"""
Search database operations.

Implements BM25 (full-text), vector (embedding), and hybrid (RRF) search.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from codecompass.config import get_settings
from codecompass.db import get_db


@dataclass
class SearchScoreBreakdown:
    """Score breakdown for a search result."""
    bm25_rank: Optional[int] = None
    vector_rank: Optional[int] = None
    fused_score: Optional[float] = None
    source: str = "unknown"  # "bm25", "vector", "hybrid", "hybrid+graph"


@dataclass
class SearchResult:
    """Search result with score breakdown."""
    id: int
    name: str
    qualified_name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    signature: Optional[str]
    docstring: Optional[str]
    language: str
    repo_id: int
    score_breakdown: SearchScoreBreakdown


def bm25_search(
    query: str,
    repository_id: Optional[int] = None,
    limit: int = 20,
    offset: int = 0
) -> List[Dict]:
    """
    Full-text search using PostgreSQL's tsvector/tsquery with code-aware tokenizer.

    Uses code_plainto_tsquery for natural language queries with camelCase/snake_case splitting.
    The ranking uses ts_rank_cd (cover density ranking).
    """
    db = get_db()
    get_settings()

    where_clause = "WHERE s.search_vector @@ code_plainto_tsquery(%s)"
    params = [query]  # For WHERE clause

    if repository_id:
        where_clause += " AND s.repo_id = %s"
        params.append(repository_id)

    # BM25 ranking using ts_rank_cd (cover density ranking)
    # Note: query appears twice - once in WHERE, once in SELECT for ranking
    sql = f"""
    SELECT
        s.*,
        ts_rank_cd(s.search_vector, code_plainto_tsquery(%s), 32) AS rank
    FROM v_symbols_with_file s
    {where_clause}
    ORDER BY rank DESC, s.name
    LIMIT %s OFFSET %s
    """
    params = [query, query] + params[1:] + [limit, offset]  # query for WHERE, query for SELECT, then repo_id (if any), limit, offset
    return db.execute(sql, tuple(params))


def bm25_search_with_ranks(
    query: str,
    repository_id: Optional[int] = None,
    limit: int = 50
) -> List[Dict]:
    """
    BM25 search that returns results with their rank positions.
    Used for RRF fusion.
    """
    results = bm25_search(query, repository_id, limit, 0)
    for idx, r in enumerate(results):
        r["bm25_rank"] = idx + 1
        r["bm25_score"] = r.get("rank", 0.0)
    return results


def vector_search(
    query_embedding: List[float],
    repository_id: Optional[int] = None,
    limit: int = 50,
    model_name: Optional[str] = None
) -> List[Dict]:
    """
    Vector similarity search using pgvector (cosine distance).
    """
    db = get_db()

    where_conditions = []
    params = []

    if repository_id:
        where_conditions.append("s.repo_id = %s")
        params.append(repository_id)

    if model_name:
        where_conditions.append("se.model_name = %s")
        params.append(model_name)

    where_clause = ""
    if where_conditions:
        where_clause = "WHERE " + " AND ".join(where_conditions)

    # Parameters in order: embedding for SELECT, embedding for ORDER BY (after WHERE), limit
    # But WHERE clause creates placeholders in between, so we need:
    # 1. embedding (for SELECT)
    # 2. repo_id (for WHERE) if present
    # 3. model_name (for WHERE) if present
    # 4. embedding (for ORDER BY)
    # 5. limit

    sql = f"""
    SELECT
        s.*,
        1 - (se.embedding <=> %s::vector) AS similarity
    FROM v_symbols_with_file s
    JOIN symbol_embeddings se ON s.id = se.symbol_id
    {where_clause}
    ORDER BY se.embedding <=> %s::vector
    LIMIT %s
    """
    # Build params in the exact order they appear in SQL
    final_params = [query_embedding]  # 1. SELECT
    if repository_id:
        final_params.append(repository_id)  # 2. WHERE repo_id
    if model_name:
        final_params.append(model_name)  # 3. WHERE model_name
    final_params.extend([query_embedding, limit])  # 4. ORDER BY, 5. LIMIT

    return db.execute(sql, tuple(final_params))


def vector_search_with_ranks(
    query_embedding: List[float],
    repository_id: Optional[int] = None,
    limit: int = 50,
    model_name: Optional[str] = None
) -> List[Dict]:
    """
    Vector search that returns results with their rank positions.
    Used for RRF fusion.
    """
    results = vector_search(query_embedding, repository_id, limit, model_name)
    for idx, r in enumerate(results):
        r["vector_rank"] = idx + 1
        r["vector_score"] = r.get("similarity", 0.0)
    return results


def hybrid_search(
    query: str,
    query_embedding: List[float],
    repository_id: Optional[int] = None,
    limit: int = 20,
    bm25_limit: int = 50,
    vector_limit: int = 50,
    rrf_k: int = 60
) -> List[Dict]:
    """
    Hybrid search using Reciprocal Rank Fusion (RRF).

    Combines BM25 and vector search results by ranking position:
    score = sum(1 / (k + rank_i)) for each result source i

    This is more robust than score normalization because it doesn't
    require calibrating different scoring functions.
    """
    db = get_db()

    # Get BM25 results with ranks
    bm25_results = bm25_search_with_ranks(query, repository_id, bm25_limit)
    bm25_ranked = {r["id"]: r["bm25_rank"] for r in bm25_results}
    bm25_scores = {r["id"]: r["bm25_score"] for r in bm25_results}

    # Get vector results with ranks
    vector_results = vector_search_with_ranks(query_embedding, repository_id, vector_limit)
    vector_ranked = {r["id"]: r["vector_rank"] for r in vector_results}
    vector_scores = {r["id"]: r["vector_score"] for r in vector_results}

    # Combine all unique symbol IDs
    all_ids = set(bm25_ranked.keys()) | set(vector_ranked.keys())

    # Compute RRF scores
    rrf_scores = {}
    for sid in all_ids:
        score = 0.0
        if sid in bm25_ranked:
            score += 1.0 / (rrf_k + bm25_ranked[sid])
        if sid in vector_ranked:
            score += 1.0 / (rrf_k + vector_ranked[sid])
        rrf_scores[sid] = score

    # Sort by RRF score descending
    sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)

    # Fetch full symbol data for top results
    if not sorted_ids:
        return []

    placeholders = ",".join(["%s"] * len(sorted_ids[:limit]))
    sql = f"SELECT * FROM v_symbols_with_file WHERE id IN ({placeholders})"
    rows = db.execute(sql, tuple(sorted_ids[:limit]))

    # Re-sort by RRF score and add score breakdown
    id_to_row = {row["id"]: row for row in rows}
    results = []
    for sid in sorted_ids[:limit]:
        if sid in id_to_row:
            row = id_to_row[sid]
            row["bm25_rank"] = bm25_ranked.get(sid)
            row["vector_rank"] = vector_ranked.get(sid)
            row["bm25_score"] = bm25_scores.get(sid)
            row["vector_score"] = vector_scores.get(sid)
            row["fused_score"] = rrf_scores[sid]
            row["search_type"] = "hybrid"
            results.append(row)

    return results


def hybrid_search_with_explanation(
    query: str,
    query_embedding: List[float],
    repository_id: Optional[int] = None,
    limit: int = 20,
    bm25_limit: int = 50,
    vector_limit: int = 50,
    rrf_k: int = 60
) -> List[Dict]:
    """
    Hybrid search that returns detailed score breakdown for each result.
    """
    results = hybrid_search(
        query=query,
        query_embedding=query_embedding,
        repository_id=repository_id,
        limit=limit,
        bm25_limit=bm25_limit,
        vector_limit=vector_limit,
        rrf_k=rrf_k
    )

    # Add detailed explanation
    for r in results:
        bm25_score = r.get("bm25_score")
        vector_score = r.get("vector_score")
        fused_score = r.get("fused_score")
        r["score_explanation"] = {
            "bm25_rank": r.get("bm25_rank"),
            "bm25_score": round(bm25_score, 4) if bm25_score is not None else None,
            "vector_rank": r.get("vector_rank"),
            "vector_score": round(vector_score, 4) if vector_score is not None else None,
            "fused_score": round(fused_score, 4) if fused_score is not None else None,
            "rrf_k": rrf_k,
        }

    return results


def find_definition(
    name: str,
    repository_id: Optional[int] = None,
    kind: Optional[str] = None
) -> List[Dict]:
    """Find symbol definitions by exact name match (legacy, use find_definition_ranked for disambiguation)."""
    db = get_db()

    where_clause = "WHERE s.name = %s"
    params = [name]

    if repository_id:
        where_clause += " AND s.repo_id = %s"
        params.append(repository_id)

    if kind:
        where_clause += " AND s.kind = %s"
        params.append(kind)

    sql = f"SELECT * FROM v_symbols_with_file s {where_clause} ORDER BY s.kind, s.name"
    return db.execute(sql, tuple(params))


def find_definition_ranked(
    query_name: str,
    repository_id: Optional[int] = None,
    kind_filter: Optional[str] = None,
    limit_count: int = 20
) -> List[Dict]:
    """
    Find symbol definitions with disambiguation ranking.

    Ranking priority:
    1. Exact qualified name match (score 1.0)
    2. Exact name match (score 0.8)
    3. Trigram similarity on qualified_name (score = similarity)
    4. Trigram similarity on name (score = similarity * 0.7)
    """
    db = get_db()
    return db.execute(
        "SELECT * FROM find_definition_ranked(%s, %s, %s, %s)",
        (query_name, repository_id, kind_filter, limit_count)
    )


def find_all_references(
    target_symbol_id: int,
    repository_id: Optional[int] = None,
    min_confidence: float = 0.0,
    limit_count: int = 1000
) -> List[Dict]:
    """Find all references to a symbol (calls, imports, inherits, references)."""
    db = get_db()
    # Handle None repository_id by passing NULL to the SQL function
    if repository_id is None:
        return db.execute(
            "SELECT * FROM find_all_references(%s::BIGINT, NULL::BIGINT, %s::REAL, %s::INT)",
            (target_symbol_id, min_confidence, limit_count)
        )
    else:
        return db.execute(
            "SELECT * FROM find_all_references(%s::BIGINT, %s::BIGINT, %s::REAL, %s::INT)",
            (target_symbol_id, repository_id, min_confidence, limit_count)
        )


def find_callers_depth(
    target_symbol_id: int,
    depth_limit: int = 1,
    min_confidence: float = 0.0,
    limit_count: int = 1000
) -> List[Dict]:
    """Find callers up to a specified depth with confidence filtering."""
    db = get_db()
    return db.execute(
        "SELECT * FROM find_callers_depth(%s::BIGINT, %s::INT, %s::REAL, %s::INT)",
        (target_symbol_id, depth_limit, min_confidence, limit_count)
    )


def find_callees_depth(
    source_symbol_id: int,
    depth_limit: int = 1,
    min_confidence: float = 0.0,
    limit_count: int = 1000
) -> List[Dict]:
    """Find callees up to a specified depth with confidence filtering."""
    db = get_db()
    return db.execute(
        "SELECT * FROM find_callees_depth(%s::BIGINT, %s::INT, %s::REAL, %s::INT)",
        (source_symbol_id, depth_limit, min_confidence, limit_count)
    )


def find_references(symbol_id: int) -> List[Dict]:
    """Find all references to a symbol (edges of type 'references')."""
    db = get_db()
    return db.execute(
        "SELECT * FROM v_edges_with_names WHERE dst_symbol_id = %s AND kind = 'references'",
        (symbol_id,)
    )


def find_callers(symbol_id: int) -> List[Dict]:
    """Find direct callers of a symbol."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM v_edges_with_names WHERE dst_symbol_id = %s AND kind = 'calls'",
        (symbol_id,)
    )
    # Normalize to source symbol (the caller)
    return [
        {
            "id": r.get("src_symbol_id"),
            "symbol_id": r.get("src_symbol_id"),
            "name": r.get("src_name"),
            "qualified_name": r.get("src_qualified_name"),
            "kind": r.get("src_kind"),
            "signature": r.get("src_signature"),
            "docstring": r.get("src_docstring"),
            "start_line": r.get("src_start_line"),
            "end_line": r.get("src_end_line"),
            "start_col": r.get("src_start_col"),
            "end_col": r.get("src_end_col"),
            "file_path": r.get("src_file_path"),
            "language": r.get("src_language"),
            "repository_id": r.get("src_repository_id"),
            "repo_name": r.get("src_repo_name"),
            "edge_type": r.get("kind"),
            "edge_confidence": r.get("confidence"),
            "line": r.get("line"),
            "col": r.get("col"),
        }
        for r in rows
    ]


def find_callees(symbol_id: int) -> List[Dict]:
    """Find direct callees of a symbol."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM v_edges_with_names WHERE src_symbol_id = %s AND kind = 'calls' AND dst_symbol_id IS NOT NULL",
        (symbol_id,)
    )
    # Normalize to destination symbol (the callee)
    return [
        {
            "id": r.get("dst_symbol_id"),
            "symbol_id": r.get("dst_symbol_id"),
            "name": r.get("dst_name"),
            "qualified_name": r.get("dst_qualified_name"),
            "kind": r.get("dst_kind"),
            "signature": r.get("dst_signature"),
            "docstring": r.get("dst_docstring"),
            "start_line": r.get("dst_start_line"),
            "end_line": r.get("dst_end_line"),
            "start_col": r.get("dst_start_col"),
            "end_col": r.get("dst_end_col"),
            "file_path": r.get("dst_file_path"),
            "language": r.get("dst_language"),
            "repository_id": r.get("dst_repository_id"),
            "repo_name": r.get("dst_repo_name"),
            "edge_type": r.get("kind"),
            "edge_confidence": r.get("confidence"),
            "line": r.get("line"),
            "col": r.get("col"),
        }
        for r in rows
    ]


def transitive_callees(symbol_id: int, max_depth: int = 10, min_confidence: float = 0.0, limit_count: int = 1000) -> List[Dict]:
    """Find all transitive callees using recursive CTE."""
    db = get_db()
    return db.execute(
        "SELECT * FROM transitive_callees(%s::BIGINT, %s::INT, %s::REAL, %s::INT)",
        (symbol_id, max_depth, min_confidence, limit_count)
    )


def transitive_callers(symbol_id: int, max_depth: int = 10, min_confidence: float = 0.0, limit_count: int = 1000) -> List[Dict]:
    """Find all transitive callers using recursive CTE."""
    db = get_db()
    return db.execute(
        "SELECT * FROM transitive_callers(%s::BIGINT, %s::INT, %s::REAL, %s::INT)",
        (symbol_id, max_depth, min_confidence, limit_count)
    )


def transitive_impact(symbol_id: int, max_depth: int = 10, min_confidence: float = 0.0, limit_count: int = 1000) -> List[Dict]:
    """Find transitive impact (callers + callees) using recursive CTE."""
    db = get_db()
    return db.execute(
        "SELECT * FROM transitive_impact(%s::BIGINT, %s::INT, %s::REAL, %s::INT)",
        (symbol_id, max_depth, min_confidence, limit_count)
    )