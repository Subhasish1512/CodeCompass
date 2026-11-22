"""
Database layer for CodeCompass.

Provides connection pooling, transaction management, and common query helpers.
Uses psycopg 3 with connection pooling for sync operations.
"""

from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from codecompass.config import get_settings


class Database:
    """Manages PostgreSQL connection pool and provides query helpers."""

    def __init__(self, dsn: Optional[str] = None, min_size: int = 1, max_size: int = 10):
        self.dsn = dsn or get_settings().database_url
        self.pool = ConnectionPool(
            self.dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row, "autocommit": False}
        )

    def close(self):
        """Close all connections in the pool."""
        self.pool.close()

    @contextmanager
    def connection(self):
        """Get a connection from the pool (context manager)."""
        with self.pool.connection() as conn:
            register_vector(conn)
            yield conn

    @contextmanager
    def transaction(self):
        """Get a connection with an active transaction."""
        with self.pool.connection() as conn:
            with conn.transaction():
                yield conn

    def execute(self, query: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """Execute a query and return all rows as dicts."""
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchall()

    def execute_one(self, query: str, params: Optional[tuple] = None) -> Optional[Dict[str, Any]]:
        """Execute a query and return one row as dict."""
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchone()

    def execute_scalar(self, query: str, params: Optional[tuple] = None) -> Any:
        """Execute a query and return a single scalar value."""
        row = self.execute_one(query, params)
        return row[list(row.keys())[0]] if row else None

    def executemany(self, query: str, params_list: List[tuple]) -> int:
        """Execute a query multiple times with different parameters."""
        with self.transaction() as conn:
            with conn.cursor() as cur:
                cur.executemany(query, params_list)
                return cur.rowcount

    def execute_void(self, query: str, params: Optional[tuple] = None) -> int:
        """Execute a query that doesn't return rows (INSERT, UPDATE, DELETE). Returns rowcount."""
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.rowcount


# Global database instance (initialized lazily)
_db: Optional[Database] = None


def get_db() -> Database:
    """Get the global database instance, creating it if needed."""
    global _db
    if _db is None:
        _db = Database()
    return _db


def init_db(dsn: Optional[str] = None) -> Database:
    """Initialize the global database instance."""
    global _db
    _db = Database(dsn)
    return _db


def close_db():
    """Close the global database instance."""
    global _db
    if _db:
        _db.close()
        _db = None