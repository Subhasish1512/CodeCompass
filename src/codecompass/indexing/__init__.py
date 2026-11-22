"""
Indexing pipeline: coordinates parsing, database writes, and embeddings.
"""

from .pipeline import delete_file, index_repository, reindex_file

__all__ = ["index_repository", "reindex_file", "delete_file"]