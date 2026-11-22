"""
BM25 full-text search using PostgreSQL's tsvector/tsquery.
"""

from typing import Dict, List, Optional

from codecompass.config import get_settings
from codecompass.db.search import bm25_search as db_bm25_search


def bm25_search(
    query: str,
    repository_id: Optional[int] = None,
    limit: int = 20,
    offset: int = 0
) -> List[Dict]:
    """
    Full-text search using PostgreSQL's built-in ranking (ts_rank_cd).

    Uses plainto_tsquery for natural language queries.
    The ranking uses cover density (32) which considers term proximity.
    """
    get_settings()
    return db_bm25_search(
        query=query,
        repository_id=repository_id,
        limit=limit,
        offset=offset
    )


def bm25_search_raw(
    query: str,
    repository_id: Optional[int] = None,
    limit: int = 20,
    k1: float = 1.2,
    b: float = 0.75
) -> List[Dict]:
    """
    BM25 with explicit parameters (for experimentation).

    Note: PostgreSQL's ts_rank_cd doesn't expose k1/b directly.
    This is a placeholder for custom ranking if needed.
    """
    return bm25_search(query, repository_id, limit)