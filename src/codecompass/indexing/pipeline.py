"""
Main indexing pipeline.

Coordinates file discovery, parsing, database writes, and embedding generation.
Supports both full indexing and incremental updates from git diffs.
"""

import hashlib
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from codecompass.config import get_settings
from codecompass.db import get_db
from codecompass.db.repository import (
    create_edge,
    create_file,
    create_repository,
    create_symbol,
    delete_edges_by_file,
    delete_file,
    delete_symbols_by_file,
    get_chunks_without_embeddings,
    get_file_by_path,
    get_index_state,
    get_indexed_files,
    get_repository_by_name,
    list_files,
    list_symbols,
    upsert_chunk_embedding,
    upsert_embedding,
    upsert_index_state,
    upsert_indexed_files,
)
from codecompass.indexing.embeddings import (
    embed_chunks_cached,
    embed_symbols_cached,
)
from codecompass.indexing.resolver import build_import_table, resolve_edges_for_file
from codecompass.indexing.walker import WalkStats, discover_files
from codecompass.parsing import PythonParser, get_language_for_file


@dataclass
class IndexStats:
    files_processed: int = 0
    symbols_created: int = 0
    edges_created: int = 0
    embeddings_generated: int = 0
    chunks_created: int = 0
    files_skipped: int = 0
    errors: List[str] = None
    walk_stats: Optional[WalkStats] = None
    resolved_edges: int = 0
    unresolved_edges: int = 0
    elapsed_seconds: float = 0.0

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    @property
    def resolved_ratio(self) -> float:
        total = self.resolved_edges + self.unresolved_edges
        return self.resolved_edges / total if total > 0 else 0.0

    @property
    def loc_per_sec(self) -> float:
        if self.elapsed_seconds <= 0:
            return 0.0
        # Approximate LOC from symbols (rough estimate)
        return (self.symbols_created * 10) / self.elapsed_seconds


def compute_file_hash(content: bytes) -> bytes:
    """Compute SHA256 hash of file content."""
    return hashlib.sha256(content).digest()


def _get_git_state(repo_root: Path) -> Tuple[Optional[str], Optional[str]]:
    """Get current git commit SHA and tree SHA."""
    try:
        commit_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root, text=True
        ).strip()
        tree_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=repo_root, text=True
        ).strip()
        return commit_sha, tree_sha
    except Exception:
        return None, None


def index_repository(
    name: str,
    root_path: str,
    url: str = "",
    incremental: bool = True,
    show_progress: bool = False,
) -> IndexStats:
    """
    Index a repository (full or incremental).

    Args:
        name: Repository name
        root_path: Path to repository root
        url: Optional repository URL
        incremental: If True, use git diff for incremental update
        show_progress: Whether to show progress bar

    Returns:
        IndexStats with counts
    """
    start_time = time.time()
    stats = IndexStats()
    repo_root = Path(root_path).resolve()

    if not repo_root.exists():
        stats.errors.append(f"Repository path does not exist: {root_path}")
        return stats

    # Get or create repository
    repo = get_repository_by_name(name)
    if repo:
        repository_id = repo["id"]
    else:
        repository_id = create_repository(name, url, str(repo_root))

    # Get current git state
    commit_sha, tree_sha = _get_git_state(repo_root)
    if not commit_sha:
        stats.errors.append("Not a git repository or git not available")
        return stats

    # Check if already indexed at this commit
    if incremental:
        state = get_index_state(repository_id)
        if state and state["commit_sha"] == commit_sha:
            stats.errors.append(f"Already indexed at commit {commit_sha[:8]}")
            return stats

    # Discover files using walker
    settings = get_settings()
    files, walk_stats = discover_files(
        repo_root,
        settings.excluded_dirs,
        settings.excluded_extensions,
        settings.max_file_size_mb,
        respect_gitignore=True
    )
    stats.walk_stats = walk_stats

    # Get previously indexed files for incremental
    indexed_files = set()
    if incremental:
        state = get_index_state(repository_id)
        if state:
            indexed_files = get_indexed_files(repository_id, state["commit_sha"])

    current_files = {f.relative_to(repo_root).as_posix() for f in files}

    # Determine files to process
    if incremental and indexed_files:
        files_to_process = []
        for f in files:
            rel = f.relative_to(repo_root).as_posix()
            if rel not in indexed_files:
                files_to_process.append(f)
            else:
                existing = get_file_by_path(repository_id, rel)
                if existing:
                    content = read_file_safely(f, settings.max_file_size_mb)
                    if content and existing["content_hash"] != compute_file_hash(content):
                        files_to_process.append(f)

        # Files to delete: removed from repo
        files_to_delete = indexed_files - current_files
        for rel_path in files_to_delete:
            existing = get_file_by_path(repository_id, rel_path)
            if existing:
                delete_file(existing["id"])
    else:
        files_to_process = files

    # Process files with progress bar
    processed_file_ids = []
    total_files = len(files_to_process)

    if show_progress:
        from rich.console import Console
        from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

        console = Console()
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Indexing files...", total=total_files)
            for file_path in files_to_process:
                file_id = _index_file_with_resolver(
                    repository_id, file_path, repo_root, stats
                )
                if file_id:
                    processed_file_ids.append(file_id)
                progress.advance(task)
    else:
        for file_path in files_to_process:
            file_id = _index_file_with_resolver(
                repository_id, file_path, repo_root, stats
            )
            if file_id:
                processed_file_ids.append(file_id)

    # Update index state
    all_file_ids = [f["id"] for f in list_files(repository_id)]
    all_symbols = sum(len(list_symbols(fid)) for fid in all_file_ids)

    upsert_index_state(
        repository_id=repository_id,
        commit_sha=commit_sha,
        tree_sha=tree_sha,
        files_count=len(all_file_ids),
        symbols_count=all_symbols,
        edges_count=stats.edges_created  # Now we track this
    )

    upsert_indexed_files(repository_id, all_file_ids, commit_sha)

    stats.elapsed_seconds = time.time() - start_time
    return stats


def _index_file_with_resolver(
    repository_id: int,
    file_path: Path,
    repo_root: Path,
    stats: IndexStats,
) -> Optional[int]:
    """
    Index a single file with edge resolution.

    Returns file_id if successful.
    """
    settings = get_settings()

    rel_path = file_path.relative_to(repo_root).as_posix()
    content = read_file_safely(file_path, settings.max_file_size_mb)

    if content is None:
        stats.files_skipped += 1
        return None

    content_hash = compute_file_hash(content)
    language = get_language_for_file(str(file_path)) or "unknown"

    # Check if file already indexed with same content
    existing = get_file_by_path(repository_id, rel_path)
    if existing and existing["content_hash"] == content_hash:
        stats.files_skipped += 1
        return existing["id"]

    # Create/update file record
    file_id = create_file(repository_id, rel_path, language, content_hash, len(content))

    # Delete old symbols and edges for this file
    delete_symbols_by_file(file_id)
    delete_edges_by_file(file_id)

    # Parse and extract symbols/edges
    try:
        source_code = content.decode("utf-8")
        parser = PythonParser()
        parsed = parser.parse(source_code, Path(rel_path))

        symbols = parsed.symbols
        raw_edges = parsed.edges
    except Exception as e:
        stats.errors.append(f"Parse error in {rel_path}: {e}")
        return file_id

    # Insert symbols (first pass - without scope_symbol_id)
    symbol_id_map = {}  # (name, kind, line) -> symbol_id
    for sym in symbols:
        sym_id = create_symbol(
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
            scope_symbol_id=None  # Will be updated in second pass
        )
        symbol_id_map[(sym.name, sym.kind.value, sym.start_line)] = sym_id
        stats.symbols_created += 1

    # Build index-to-db-id map for edge resolution and parent mapping
    index_to_db_id = {}
    for idx, sym in enumerate(symbols):
        key = (sym.name, sym.kind.value, sym.start_line)
        if key in symbol_id_map:
            index_to_db_id[idx] = symbol_id_map[key]

    # Second pass: update scope_symbol_id with correct database IDs
    db = get_db()
    for idx, sym in enumerate(symbols):
        if sym.parent_symbol_id is not None and sym.parent_symbol_id in index_to_db_id:
            parent_db_id = index_to_db_id[sym.parent_symbol_id]
            key = (sym.name, sym.kind.value, sym.start_line)
            if key in symbol_id_map:
                sym_db_id = symbol_id_map[key]
                db.execute_void(
                    "UPDATE symbols SET scope_symbol_id = %s WHERE id = %s",
                    (parent_db_id, sym_db_id)
                )

    # Build index-to-db-id map for edge resolution
    index_to_db_id = {}
    for idx, sym in enumerate(symbols):
        key = (sym.name, sym.kind.value, sym.start_line)
        if key in symbol_id_map:
            index_to_db_id[idx] = symbol_id_map[key]

    # Build import table for resolution
    import_table = build_import_table(parsed)

    # Get symbol records for resolver
    symbol_records = list_symbols(file_id)

    # Resolve edges
    resolved_edges = resolve_edges_for_file(
        repository_id, file_id, symbol_records, raw_edges, import_table
    )

    # Insert resolved edges
    for edge in resolved_edges:
        if edge.src_symbol_id == 0:
            continue
        create_edge(
            src_symbol_id=edge.src_symbol_id,
            dst_symbol_id=edge.dst_symbol_id,
            dst_name=edge.dst_name,
            edge_type=edge.edge_type,
            src_file_id=edge.src_file_id,
            dst_file_id=edge.dst_file_id,
            line=edge.line,
            col=edge.col
        )
        stats.edges_created += 1
        if edge.dst_symbol_id is not None:
            stats.resolved_edges += 1
        else:
            stats.unresolved_edges += 1

    # Generate embeddings for new symbols
    if symbol_records:
        embeddings = embed_symbols_cached(symbol_records)
        for sym_id, emb in embeddings:
            upsert_embedding(sym_id, emb, get_settings().embedding_model)
            stats.embeddings_generated += 1

    # Generate embeddings for new chunks (batched, resumable, skip unchanged text_hash)
    settings = get_settings()
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


def reindex_file(repository_id: int, file_path: Path, repo_root: Path) -> IndexStats:
    """Re-index a single file."""
    stats = IndexStats()
    _index_file_with_resolver(repository_id, file_path, repo_root, stats)
    return stats


def delete_file(repository_id: int, rel_path: str) -> bool:
    """Delete a file and all its symbols/edges from the index."""
    existing = get_file_by_path(repository_id, rel_path)
    if not existing:
        return False
    return delete_file(existing["id"])