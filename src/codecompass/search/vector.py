"""
Vector similarity search using pgvector (cosine distance).
"""

from typing import Dict, List, Optional

from codecompass.config import get_settings
from codecompass.db.search import vector_search as db_vector_search
from codecompass.indexing.embeddings import compute_embedding


def vector_search(
    query: str,
    repository_id: Optional[int] = None,
    limit: int = 50,
    model_name: Optional[str] = None
) -> List[Dict]:
    """
    Vector similarity search using embeddings.

    Computes query embedding and searches for nearest neighbors
    using pgvector's cosine distance operator (<=>).
    """
    settings = get_settings()
    model = model_name or settings.embedding_model

    query_embedding = compute_embedding(query)
    return db_vector_search(
        query_embedding=query_embedding,
        repository_id=repository_id,
        limit=limit,
        model_name=model
    )


def vector_search_by_embedding(
    query_embedding: List[float],
    repository_id: Optional[int] = None,
    limit: int = 50,
    model_name: Optional[str] = None
) -> List[Dict]:
    """Search using a pre-computed query embedding."""
    settings = get_settings()
    model = model_name or settings.embedding_model
    return db_vector_search(
        query_embedding=query_embedding,
        repository_id=repository_id,
        limit=limit,
        model_name=model
    )