"""
FastAPI route definitions for CodeCompass API.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from codecompass.db.repository import (
    create_repository,
    get_file_by_path,
    get_repository,
    get_repository_by_name,
    list_files,
    list_repositories,
)
from codecompass.db.search import (
    find_callees,
    find_callers,
    find_definition,
    find_references,
    transitive_callees,
    transitive_callers,
    transitive_impact,
)
from codecompass.graph.traversal import (
    find_implementations,
    find_related_symbols,
    get_symbol_context,
)
from codecompass.indexing.incremental import update_repository
from codecompass.indexing.pipeline import index_repository
from codecompass.llm.pipeline import answer_question
from codecompass.search.hybrid import multi_stage_search, search

# Timing middleware logger
timing_logger = logging.getLogger("codecompass.timing")


router = APIRouter(prefix="/api/v1", tags=["CodeCompass"])


def _serialize_repo(repo: dict) -> dict:
    """Convert datetime fields to ISO format strings for Pydantic validation."""
    if repo:
        repo = dict(repo)
        for field in ("created_at", "updated_at"):
            if repo.get(field) and isinstance(repo[field], datetime):
                repo[field] = repo[field].isoformat()
    return repo


def _enrich_search_results(results: List[Dict]) -> List[Dict]:
    """Add repository_id and repo_name to search results."""
    if not results:
        return results

    # Get unique repo_ids
    repo_ids = {r.get("repo_id") for r in results if r.get("repo_id")}
    repo_map = {}

    for repo_id in repo_ids:
        from codecompass.db.repository import get_repository
        repo = get_repository(repo_id)
        if repo:
            repo_map[repo_id] = repo["name"]

    # Enrich results
    for r in results:
        repo_id = r.get("repo_id")
        if repo_id:
            r["repository_id"] = repo_id
            r["repo_name"] = repo_map.get(repo_id, "unknown")

    return results


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class RepositoryCreate(BaseModel):
    name: str = Field(..., description="Unique repository name")
    url: str = Field("", description="Repository URL (optional)")
    root_path: str = Field(..., description="Absolute path to repository root")


class RepositoryResponse(BaseModel):
    id: int
    name: str
    url: str
    root_path: str
    created_at: str
    updated_at: str


class IndexRequest(BaseModel):
    incremental: bool = True


class IndexResponse(BaseModel):
    files_processed: int
    symbols_created: int
    edges_created: int
    embeddings_generated: int
    files_skipped: int
    errors: List[str]


class SearchRequest(BaseModel):
    query: str = Field(..., description="Natural language search query")
    repository_id: Optional[int] = None
    limit: int = 20
    use_graph_expansion: bool = False
    graph_depth: int = 2


class SearchResult(BaseModel):
    id: int
    name: str
    kind: str
    signature: Optional[str] = None
    docstring: Optional[str] = None
    start_line: int
    end_line: int
    file_path: str
    language: str
    repository_id: int
    repo_name: str
    search_type: str = "hybrid"
    graph_distance: Optional[int] = None


class GraphQuery(BaseModel):
    max_depth: int = 10


class SymbolResponse(BaseModel):
    id: int
    name: str
    kind: str
    signature: Optional[str] = None
    docstring: Optional[str] = None
    start_line: int
    end_line: int
    file_path: str
    language: str


# ============================================================================
# ASK ENDPOINTS
# ============================================================================

class AskRequest(BaseModel):
    question: str = Field(..., description="Natural language question about the codebase")
    repository_id: Optional[int] = None
    limit: int = 10
    graph_depth: int = 2


class CitationResponse(BaseModel):
    index: int
    snippet_id: int
    file_path: str
    start_line: int
    end_line: int
    qualified_name: str
    search_type: str
    fused_score: float


class AskResponse(BaseModel):
    question: str
    answer: str
    citations: List[CitationResponse]
    snippets_used: int
    retrieval_score: float
    not_found: bool = False
    error: Optional[str] = None


class UpdateResponse(BaseModel):
    files_added: int
    files_modified: int
    files_deleted: int
    files_renamed: int
    symbols_created: int
    edges_created: int
    embeddings_generated: int
    edges_reresolved: int
    elapsed_seconds: float
    errors: List[str]


# ============================================================================
# REPOSITORY ENDPOINTS
# ============================================================================

@router.post("/repositories", response_model=RepositoryResponse, status_code=201)
def create_repo(repo: RepositoryCreate):
    """Register a new repository for indexing."""
    try:
        repo_id = create_repository(repo.name, repo.url, repo.root_path)
        return _serialize_repo(get_repository(repo_id))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/repositories", response_model=List[RepositoryResponse])
def list_repos():
    """List all registered repositories."""
    return [_serialize_repo(r) for r in list_repositories()]


@router.get("/repositories/{repo_id}", response_model=RepositoryResponse)
def get_repo(repo_id: int):
    """Get repository by ID."""
    repo = get_repository(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    return _serialize_repo(repo)


@router.get("/repositories/by-name/{name}", response_model=RepositoryResponse)
def get_repo_by_name(name: str):
    """Get repository by name."""
    repo = get_repository_by_name(name)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    return _serialize_repo(repo)


# ============================================================================
# INDEXING ENDPOINTS
# ============================================================================

@router.post("/repositories/{repo_id}/index", response_model=IndexResponse)
def index_repo(repo_id: int, request: IndexRequest):
    """Index a repository (full or incremental)."""
    repo = get_repository(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")

    stats = index_repository(
        name=repo["name"],
        root_path=repo["root_path"],
        url=repo["url"],
        incremental=request.incremental
    )

    return IndexResponse(
        files_processed=stats.files_processed,
        symbols_created=stats.symbols_created,
        edges_created=stats.edges_created,
        embeddings_generated=stats.embeddings_generated,
        files_skipped=stats.files_skipped,
        errors=stats.errors
    )


@router.post("/repositories/{repo_id}/update", response_model=UpdateResponse)
def update_repo(repo_id: int):
    """Incremental update: diff last_indexed_commit..HEAD and apply minimal changes."""
    repo = get_repository(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")

    from codecompass.indexing.incremental import HAS_PYGIT2
    if not HAS_PYGIT2:
        raise HTTPException(status_code=500, detail="pygit2 not installed")

    try:
        stats = update_repository(name=repo["name"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return UpdateResponse(
        files_added=stats.files_added,
        files_modified=stats.files_modified,
        files_deleted=stats.files_deleted,
        files_renamed=stats.files_renamed,
        symbols_created=stats.symbols_created,
        edges_created=stats.edges_created,
        embeddings_generated=stats.embeddings_generated,
        edges_reresolved=stats.edges_reresolved,
        elapsed_seconds=stats.elapsed_seconds,
        errors=stats.errors
    )


# ============================================================================
# ASK ENDPOINTS
# ============================================================================

@router.post("/ask", response_model=AskResponse)
def ask_question(request: AskRequest):
    """Ask a natural language question about the codebase (RAG with hybrid+graph)."""
    repo_id = request.repository_id

    if repo_id is None:
        raise HTTPException(status_code=400, detail="repository_id is required")

    repo = get_repository(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")

    result = answer_question(
        question=request.question,
        repository_id=repo_id,
        limit=request.limit,
        graph_depth=request.graph_depth
    )

    citations = [
        CitationResponse(
            index=c.index,
            snippet_id=c.snippet_id,
            file_path=c.file_path,
            start_line=c.start_line,
            end_line=c.end_line,
            qualified_name=c.qualified_name,
            search_type=c.search_type,
            fused_score=c.fused_score
        )
        for c in result.citations
    ]

    return AskResponse(
        question=result.question,
        answer=result.answer,
        citations=citations,
        snippets_used=result.snippets_used,
        retrieval_score=result.retrieval_score,
        not_found=result.not_found,
        error=result.error
    )


# ============================================================================
# SEARCH ENDPOINTS
# ============================================================================

@router.post("/search", response_model=List[SearchResult])
def search_symbols(request: SearchRequest):
    """Hybrid search (BM25 + Vector + RRF) with optional graph expansion."""
    results = search(
        query=request.query,
        repository_id=request.repository_id,
        limit=request.limit,
        use_graph_expansion=request.use_graph_expansion,
        graph_depth=request.graph_depth
    )
    return _enrich_search_results(results)


@router.post("/search/compare")
def compare_search_modes(request: SearchRequest):
    """Compare all search modes: BM25, Vector, Hybrid, Hybrid+Graph."""
    return multi_stage_search(
        query=request.query,
        repository_id=request.repository_id,
        limit=request.limit
    )


@router.get("/search/definition")
def search_definition(
    name: str = Query(..., description="Symbol name"),
    repository_id: Optional[int] = None,
    kind: Optional[str] = None
):
    """Find symbol definitions by exact name."""
    results = find_definition(name, repository_id, kind)
    return results


# ============================================================================
# GRAPH TRAVERSAL ENDPOINTS
# ============================================================================

@router.get("/symbols/{symbol_id}", response_model=SymbolResponse)
def get_symbol(symbol_id: int):
    """Get symbol by ID with file info."""
    from codecompass.db.repository import get_symbol as db_get_symbol
    symbol = db_get_symbol(symbol_id)
    if not symbol:
        raise HTTPException(status_code=404, detail="Symbol not found")
    return symbol


@router.get("/symbols/{symbol_id}/references")
def get_symbol_references(symbol_id: int):
    """Find all references to a symbol."""
    return find_references(symbol_id)


@router.get("/symbols/{symbol_id}/callers")
def get_symbol_callers(symbol_id: int):
    """Find direct callers of a symbol."""
    return find_callers(symbol_id)


@router.get("/symbols/{symbol_id}/callees")
def get_symbol_callees(symbol_id: int):
    """Find direct callees of a symbol."""
    return find_callees(symbol_id)


@router.post("/symbols/{symbol_id}/transitive-callers")
def get_transitive_callers_endpoint(symbol_id: int, query: GraphQuery):
    """Find all transitive callers (recursive)."""
    return transitive_callers(symbol_id, query.max_depth)


@router.post("/symbols/{symbol_id}/transitive-callees")
def get_transitive_callees_endpoint(symbol_id: int, query: GraphQuery):
    """Find all transitive callees (recursive)."""
    return transitive_callees(symbol_id, query.max_depth)


@router.post("/symbols/{symbol_id}/impact")
def get_impact(symbol_id: int, query: GraphQuery):
    """Find transitive impact (callers + callees)."""
    return transitive_impact(symbol_id, query.max_depth)


@router.get("/symbols/{symbol_id}/implementations")
def get_implementations(symbol_id: int, repository_id: Optional[int] = None):
    """Find implementations of an interface/trait."""
    from codecompass.db.repository import get_symbol as db_get_symbol
    symbol = db_get_symbol(symbol_id)
    if not symbol:
        raise HTTPException(status_code=404, detail="Symbol not found")
    return find_implementations(symbol["name"], repository_id)


@router.get("/symbols/{symbol_id}/context")
def get_context(symbol_id: int, radius: int = 2):
    """Get neighborhood context around a symbol."""
    return get_symbol_context(symbol_id, radius)


@router.get("/symbols/{symbol_id}/related")
def get_related(symbol_id: int, max_results: int = 20):
    """Find related symbols via graph structure."""
    return find_related_symbols(symbol_id, max_results)


# ============================================================================
# FILE ENDPOINTS
# ============================================================================

@router.get("/repositories/{repo_id}/files")
def list_repo_files(repo_id: int):
    """List all files in a repository."""
    repo = get_repository(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    return list_files(repo_id)


@router.get("/repositories/{repo_id}/files/{file_path:path}")
def get_repo_file(repo_id: int, file_path: str):
    """Get file by path."""
    repo = get_repository(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    file = get_file_by_path(repo_id, file_path)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    return file


# ============================================================================
# HEALTH CHECK
# ============================================================================

@router.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "codecompass"}