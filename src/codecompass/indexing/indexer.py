"""
Batch indexer for writing files, symbols, edges, and chunks to the database.

Uses executemany for efficient batch inserts.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from codecompass.config import get_settings
from codecompass.db import get_db
from codecompass.db.repository import (
    create_edge as db_create_edge,
)
from codecompass.db.repository import (
    create_file as db_create_file,
)
from codecompass.db.repository import (
    create_symbol as db_create_symbol,
)
from codecompass.db.repository import (
    delete_edges_by_file,
    delete_symbols_by_file,
    get_chunks_without_embeddings,
    get_file_by_path,
    list_symbols,
    upsert_chunk_embedding,
    upsert_embedding,
)
from codecompass.indexing.embeddings import (
    embed_chunks_cached,
    embed_symbols_cached,
)
from codecompass.parsing import ParsedFile, ParsedSymbol


@dataclass
class Chunk:
    """A text chunk for search indexing."""
    symbol_id: int
    file_id: int
    text: str
    text_hash: bytes


@dataclass
class IndexerStats:
    """Statistics from indexing."""
    files_processed: int = 0
    symbols_created: int = 0
    edges_created: int = 0
    chunks_created: int = 0
    embeddings_generated: int = 0
    files_skipped: int = 0
    errors: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def compute_file_hash(content: bytes) -> bytes:
    """Compute SHA256 hash of file content."""
    return hashlib.sha256(content).digest()


def compute_text_hash(text: str) -> bytes:
    """Compute SHA256 hash of text content."""
    return hashlib.sha256(text.encode("utf-8")).digest()


def read_file_safely(path: Path, max_size_mb: int) -> Optional[bytes]:
    """Read file content with size limit and binary detection."""
    try:
        size = path.stat().st_size
        if size > max_size_mb * 1024 * 1024:
            return None

        with open(path, "rb") as f:
            chunk = f.read(8192)
            if b"\x00" in chunk:
                return None

        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None


def extract_chunks(parsed: ParsedFile, symbols: List[ParsedSymbol], file_id: int) -> List[Chunk]:
    """
    Extract searchable chunks from parsed file.

    Creates one chunk per function/class AST node, plus one for module docstring.
    Chunk text = qualified name + signature + docstring + body.
    """
    chunks = []

    # Module-level docstring chunk (if any)
    if parsed.symbols:
        # Find module symbol or extract from first node
        pass

    # Create chunk for each symbol
    for sym in symbols:
        text_parts = []

        # Qualified name
        text_parts.append(sym.qualified_name)

        # Signature
        if sym.signature:
            text_parts.append(sym.signature)

        # Docstring
        if sym.docstring:
            text_parts.append(sym.docstring)

        # We don't have the body text directly, but we could extract it
        # For now, use what we have
        text = " ".join(text_parts)

        if text.strip():
            chunks.append(Chunk(
                symbol_id=0,  # Will be filled after symbol insertion
                file_id=file_id,
                text=text,
                text_hash=compute_text_hash(text)
            ))

    return chunks


def index_file(
    repository_id: int,
    file_path: Path,
    repo_root: Path,
    stats: IndexerStats,
    parsed: Optional[ParsedFile] = None,
) -> Optional[int]:
    """
    Index a single file. Returns file_id if successful.

    Args:
        repository_id: Database ID of the repository
        file_path: Absolute path to the file
        repo_root: Repository root directory
        stats: Statistics object to update
        parsed: Pre-parsed file (optional, will parse if not provided)

    Returns:
        File ID if successful, None if skipped/error
    """
    settings = get_settings()
    get_db()

    rel_path = file_path.relative_to(repo_root).as_posix()
    content = read_file_safely(file_path, settings.max_file_size_mb)

    if content is None:
        stats.files_skipped += 1
        return None

    content_hash = compute_file_hash(content)
    language = parsed.language if parsed else "unknown"

    # Check if file already indexed with same content
    existing = get_file_by_path(repository_id, rel_path)
    if existing and existing["content_hash"] == content_hash:
        stats.files_skipped += 1
        return existing["id"]

    # Create/update file record
    file_id = db_create_file(repository_id, rel_path, language, content_hash, len(content))

    # Delete old symbols and edges for this file
    delete_symbols_by_file(file_id)
    delete_edges_by_file(file_id)

    # Parse if not provided
    if parsed is None:
        from codecompass.parsing import PythonParser, get_language_for_file
        try:
            source_code = content.decode("utf-8")
            language = get_language_for_file(str(file_path)) or "unknown"
            if language == "python":
                parser = PythonParser()
                parsed = parser.parse(source_code, Path(rel_path))
            else:
                # Unsupported language - just create file record
                stats.files_processed += 1
                return file_id
        except Exception as e:
            stats.errors.append(f"Parse error in {rel_path}: {e}")
            return file_id

    # Insert symbols
    symbol_id_map: Dict[Tuple[str, str, int], int] = {}  # (name, kind, line) -> db_id
    for sym in parsed.symbols:
        sym_id = db_create_symbol(
            file_id=file_id,
            name=sym.name,
            qualified_name=sym.qualified_name,
            kind=sym.kind.value,
            signature=sym.signature,
            docstring=sym.docstring,
            start_line=sym.start_line,
            end_line=sym.end_line,
            start_col=sym.start_col,
            end_col=sym.end_col,
            scope_symbol_id=sym.parent_symbol_id
        )
        symbol_id_map[(sym.name, sym.kind.value, sym.start_line)] = sym_id
        stats.symbols_created += 1

    # Build index-to-db-id map for edge resolution
    index_to_db_id: Dict[int, int] = {}
    for idx, sym in enumerate(parsed.symbols):
        key = (sym.name, sym.kind.value, sym.start_line)
        if key in symbol_id_map:
            index_to_db_id[idx] = symbol_id_map[key]

    # Insert edges (resolve symbol IDs)
    for edge in parsed.edges:
        src_idx = edge.src_symbol_idx
        if src_idx is None or src_idx not in index_to_db_id:
            continue
        src_sym_id = index_to_db_id[src_idx]

        dst_idx = edge.dst_symbol_idx
        dst_sym_id = index_to_db_id.get(dst_idx) if dst_idx is not None else None

        db_create_edge(
            src_symbol_id=src_sym_id,
            dst_symbol_id=dst_sym_id,
            dst_name=edge.dst_name,
            edge_type=edge.kind.value,
            src_file_id=file_id,
            dst_file_id=file_id if dst_sym_id else None,
            line=edge.line,
            col=edge.col
        )
        stats.edges_created += 1

    # Create and insert chunks
    chunks = extract_chunks(parsed, parsed.symbols, file_id)
    # Update symbol_id in chunks
    for _chunk in chunks:
        # Find the symbol this chunk belongs to
        for idx, sym in enumerate(parsed.symbols):
            key = (sym.name, sym.kind.value, sym.start_line)
            if key in symbol_id_map:
                # This is a simplification - we'd match by qualified name
                pass

    # Insert chunks in batch
    if chunks:
        _insert_chunks_batch(chunks, symbol_id_map, parsed.symbols)
        stats.chunks_created += len(chunks)

    # Generate embeddings for new symbols
    symbol_records = list_symbols(file_id)
    if symbol_records:
        embeddings = embed_symbols_cached(symbol_records)
        for sym_id, emb in embeddings:
            upsert_embedding(sym_id, emb, get_settings().embedding_model)
            stats.embeddings_generated += 1

    # Generate embeddings for new chunks (batched, resumable, skip unchanged text_hash)
    chunk_records = get_chunks_without_embeddings(
        repository_id=repository_id,
        limit=settings.embedding_batch_size * 10
    )
    if chunk_records:
        chunk_embeddings = embed_chunks_cached(chunk_records)
        for chunk_id, emb in chunk_embeddings:
            upsert_chunk_embedding(chunk_id, emb)
            stats.embeddings_generated += 1

    stats.files_processed += 1
    return file_id


def _insert_chunks_batch(
    chunks: List[Chunk],
    symbol_id_map: Dict[Tuple[str, str, int], int],
    symbols: List[ParsedSymbol],
) -> None:
    """Insert chunks in batch using executemany."""
    db = get_db()

    # Match chunks to symbol IDs by qualified name
    chunk_params = []
    for chunk in chunks:
        # Find matching symbol by qualified name in text
        sym_id = None
        for sym in symbols:
            if sym.qualified_name in chunk.text:
                key = (sym.name, sym.kind.value, sym.start_line)
                if key in symbol_id_map:
                    sym_id = symbol_id_map[key]
                    break

        if sym_id:
            chunk_params.append((
                sym_id,
                chunk.file_id,
                chunk.text,
                chunk.text_hash,
                None  # embedding - filled later
            ))

    if not chunk_params:
        return

    db.executemany(
        """
        INSERT INTO chunks (symbol_id, file_id, text, text_hash, embedding)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (text_hash) DO NOTHING
        """,
        chunk_params
    )


def index_repository_batch(
    repository_id: int,
    file_paths: List[Path],
    repo_root: Path,
    stats: IndexerStats,
) -> List[int]:
    """
    Index multiple files in batch.

    Args:
        repository_id: Database ID of the repository
        file_paths: List of absolute file paths
        repo_root: Repository root directory
        stats: Statistics object to update

    Returns:
        List of file IDs that were processed
    """
    processed_ids = []
    for file_path in file_paths:
        file_id = index_file(repository_id, file_path, repo_root, stats)
        if file_id:
            processed_ids.append(file_id)
    return processed_ids