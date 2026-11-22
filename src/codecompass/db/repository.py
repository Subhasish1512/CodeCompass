"""
Repository-level database operations.

Handles CRUD for repositories, files, symbols, edges, and index state.
"""

from typing import Dict, List, Optional, Set

from codecompass.db import get_db


def create_repository(name: str, url: str, root_path: str) -> int:
    """Create a new repository and return its ID."""
    db = get_db()
    row = db.execute_one(
        """
        INSERT INTO repositories (name, url, root_path)
        VALUES (%s, %s, %s)
        ON CONFLICT (name) DO UPDATE SET url = EXCLUDED.url, root_path = EXCLUDED.root_path
        RETURNING id
        """,
        (name, url, root_path)
    )
    return row["id"]


def get_repository(repo_id: int) -> Optional[Dict]:
    """Get repository by ID."""
    db = get_db()
    return db.execute_one("SELECT * FROM repositories WHERE id = %s", (repo_id,))


def get_repository_by_name(name: str) -> Optional[Dict]:
    """Get repository by name."""
    db = get_db()
    return db.execute_one("SELECT * FROM repositories WHERE name = %s", (name,))


def list_repositories() -> List[Dict]:
    """List all repositories."""
    db = get_db()
    return db.execute("SELECT * FROM repositories ORDER BY created_at DESC")


# File operations

def create_file(repository_id: int, path: str, language: str, content_hash: bytes, size_bytes: int) -> int:
    """Create a file record and return its ID."""
    db = get_db()
    row = db.execute_one(
        """
        INSERT INTO files (repository_id, path, language, content_hash, size_bytes)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (repository_id, path) DO UPDATE SET
            language = EXCLUDED.language,
            content_hash = EXCLUDED.content_hash,
            size_bytes = EXCLUDED.size_bytes,
            indexed_at = NOW()
        RETURNING id
        """,
        (repository_id, path, language, content_hash, size_bytes)
    )
    return row["id"]


def get_file(file_id: int) -> Optional[Dict]:
    """Get file by ID."""
    db = get_db()
    return db.execute_one("SELECT * FROM files WHERE id = %s", (file_id,))


def get_file_by_path(repository_id: int, path: str) -> Optional[Dict]:
    """Get file by repository and path."""
    db = get_db()
    return db.execute_one(
        "SELECT * FROM files WHERE repository_id = %s AND path = %s",
        (repository_id, path)
    )


def list_files(repository_id: int) -> List[Dict]:
    """List all files in a repository."""
    db = get_db()
    return db.execute(
        "SELECT * FROM files WHERE repository_id = %s ORDER BY path",
        (repository_id,)
    )


def delete_file(file_id: int) -> bool:
    """Delete a file (cascades to symbols and edges)."""
    db = get_db()
    with db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM files WHERE id = %s", (file_id,))
            return cur.rowcount > 0


# Symbol operations

def create_symbol(
    file_id: int,
    name: str,
    kind: str,
    signature: Optional[str],
    docstring: Optional[str],
    start_line: int,
    end_line: int,
    start_col: int,
    end_col: int,
    scope_symbol_id: Optional[int] = None,
    qualified_name: Optional[str] = None
) -> int:
    """Create a symbol and return its ID."""
    db = get_db()
    row = db.execute_one(
        """
        INSERT INTO symbols (file_id, name, qualified_name, kind, signature, docstring,
                             start_line, end_line, start_col, end_col, scope_symbol_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (file_id, name, qualified_name, kind, signature, docstring,
         start_line, end_line, start_col, end_col, scope_symbol_id)
    )
    return row["id"]


def get_symbol(symbol_id: int) -> Optional[Dict]:
    """Get symbol by ID with file info."""
    db = get_db()
    return db.execute_one("SELECT * FROM v_symbols_with_file WHERE id = %s", (symbol_id,))


def get_symbols_by_name(name: str, repository_id: Optional[int] = None) -> List[Dict]:
    """Get symbols by name, optionally filtered by repository."""
    db = get_db()
    if repository_id:
        return db.execute(
            "SELECT * FROM v_symbols_with_file WHERE name = %s AND repo_id = %s ORDER BY kind",
            (name, repository_id)
        )
    return db.execute(
        "SELECT * FROM v_symbols_with_file WHERE name = %s ORDER BY kind",
        (name,)
    )


def list_symbols(file_id: int) -> List[Dict]:
    """List all symbols in a file."""
    db = get_db()
    return db.execute(
        "SELECT * FROM symbols WHERE file_id = %s ORDER BY start_line",
        (file_id,)
    )


def delete_symbols_by_file(file_id: int) -> int:
    """Delete all symbols in a file (cascades to edges and embeddings)."""
    db = get_db()
    with db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM symbols WHERE file_id = %s", (file_id,))
            return cur.rowcount


# Edge operations

def create_edge(
    src_symbol_id: int,
    dst_symbol_id: Optional[int],
    dst_name: str,
    edge_type: str,
    src_file_id: int,
    dst_file_id: Optional[int],
    line: int,
    col: int
) -> int:
    """Create an edge and return its ID."""
    db = get_db()
    row = db.execute_one(
        """
        INSERT INTO edges (src_symbol_id, dst_symbol_id, dst_name, edge_type,
                           src_file_id, dst_file_id, line, col)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (src_symbol_id, dst_symbol_id, dst_name, edge_type, src_file_id, dst_file_id, line, col)
    )
    return row["id"]


def get_edges_from(symbol_id: int, edge_type: Optional[str] = None) -> List[Dict]:
    """Get all edges originating from a symbol."""
    db = get_db()
    if edge_type:
        return db.execute(
            "SELECT * FROM v_edges_with_names WHERE src_symbol_id = %s AND kind = %s",
            (symbol_id, edge_type)
        )
    return db.execute(
        "SELECT * FROM v_edges_with_names WHERE src_symbol_id = %s",
        (symbol_id,)
    )


def get_edges_to(symbol_id: int, edge_type: Optional[str] = None) -> List[Dict]:
    """Get all edges pointing to a symbol."""
    db = get_db()
    if edge_type:
        return db.execute(
            "SELECT * FROM v_edges_with_names WHERE dst_symbol_id = %s AND kind = %s",
            (symbol_id, edge_type)
        )
    return db.execute(
        "SELECT * FROM v_edges_with_names WHERE dst_symbol_id = %s",
        (symbol_id,)
    )


def delete_edges_by_file(file_id: int) -> int:
    """Delete all edges originating from a file."""
    db = get_db()
    with db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM edges WHERE src_file_id = %s", (file_id,))
            return cur.rowcount


# Embedding operations

def upsert_embedding(symbol_id: int, embedding: List[float], model_name: str) -> None:
    """Insert or update a symbol embedding."""
    db = get_db()
    db.execute_void(
        """
        INSERT INTO symbol_embeddings (symbol_id, embedding, model_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (symbol_id) DO UPDATE SET
            embedding = EXCLUDED.embedding,
            model_name = EXCLUDED.model_name,
            created_at = NOW()
        """,
        (symbol_id, embedding, model_name)
    )


def get_embedding(symbol_id: int) -> Optional[List[float]]:
    """Get embedding for a symbol."""
    db = get_db()
    row = db.execute_one(
        "SELECT embedding FROM symbol_embeddings WHERE symbol_id = %s",
        (symbol_id,)
    )
    return row["embedding"] if row else None


def upsert_chunk_embedding(chunk_id: int, embedding: List[float]) -> None:
    """Insert or update a chunk embedding."""
    db = get_db()
    db.execute_void(
        """
        UPDATE chunks SET embedding = %s WHERE id = %s
        """,
        (embedding, chunk_id)
    )


def get_chunk_embedding(chunk_id: int) -> Optional[List[float]]:
    """Get embedding for a chunk."""
    db = get_db()
    row = db.execute_one(
        "SELECT embedding FROM chunks WHERE id = %s",
        (chunk_id,)
    )
    return row["embedding"] if row else None


def get_chunks_without_embeddings(repository_id: int, limit: int = 1000) -> List[Dict]:
    """Get chunks that don't have embeddings yet."""
    db = get_db()
    return db.execute(
        """
        SELECT c.* FROM chunks c
        JOIN symbols s ON c.symbol_id = s.id
        JOIN files f ON s.file_id = f.id
        WHERE f.repository_id = %s AND c.embedding IS NULL
        ORDER BY c.id
        LIMIT %s
        """,
        (repository_id, limit)
    )


def get_chunks_needing_reembedding(repository_id: int, limit: int = 1000) -> List[Dict]:
    """Get chunks whose text_hash has changed (need re-embedding)."""
    db = get_db()
    return db.execute(
        """
        SELECT c.* FROM chunks c
        JOIN symbols s ON c.symbol_id = s.id
        JOIN files f ON s.file_id = f.id
        WHERE f.repository_id = %s
        AND c.embedding IS NOT NULL
        ORDER BY c.id
        LIMIT %s
        """,
        (repository_id, limit)
    )


# Index state operations

def get_index_state(repository_id: int) -> Optional[Dict]:
    """Get the current index state for a repository."""
    db = get_db()
    return db.execute_one(
        "SELECT * FROM index_state WHERE repository_id = %s",
        (repository_id,)
    )


def upsert_index_state(
    repository_id: int,
    commit_sha: str,
    tree_sha: str,
    files_count: int,
    symbols_count: int,
    edges_count: int
) -> None:
    """Update the index state for a repository."""
    db = get_db()
    db.execute_void(
        """
        INSERT INTO index_state (repository_id, commit_sha, tree_sha,
                                 files_count, symbols_count, edges_count)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (repository_id) DO UPDATE SET
            commit_sha = EXCLUDED.commit_sha,
            tree_sha = EXCLUDED.tree_sha,
            files_count = EXCLUDED.files_count,
            symbols_count = EXCLUDED.symbols_count,
            edges_count = EXCLUDED.edges_count,
            indexed_at = NOW()
        """,
        (repository_id, commit_sha, tree_sha, files_count, symbols_count, edges_count)
    )


def get_indexed_files(repository_id: int, commit_sha: str) -> Set[str]:
    """Get set of file paths indexed at a specific commit."""
    db = get_db()
    rows = db.execute(
        """
        SELECT f.path
        FROM indexed_files iff
        JOIN files f ON iff.file_id = f.id
        WHERE iff.repository_id = %s AND iff.commit_sha = %s
        """,
        (repository_id, commit_sha)
    )
    return {row["path"] for row in rows}


def upsert_indexed_files(repository_id: int, file_ids: List[int], commit_sha: str) -> None:
    """Record which files were indexed at a commit."""
    if not file_ids:
        return
    db = get_db()
    params = [(repository_id, fid, commit_sha) for fid in file_ids]
    db.executemany(
        """
        INSERT INTO indexed_files (repository_id, file_id, commit_sha)
        VALUES (%s, %s, %s)
        ON CONFLICT (repository_id, file_id) DO UPDATE SET
            commit_sha = EXCLUDED.commit_sha
        """,
        params
    )


def delete_indexed_files(repository_id: int, file_ids: List[int]) -> None:
    """Remove indexed file records."""
    if not file_ids:
        return
    db = get_db()
    params = [(repository_id, fid) for fid in file_ids]
    db.executemany(
        "DELETE FROM indexed_files WHERE repository_id = %s AND file_id = %s",
        params
    )