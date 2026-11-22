# CodeCompass Design Document

A comprehensive architecture guide for interview preparation. Each module explained in plain language with EXPLAIN notes.

---

## Table of Contents

1. [High-Level Architecture](#high-level-architecture)
2. [Module: Parsing (Tree-sitter)](#module-parsing-tree-sitter)
3. [Module: Indexing Pipeline](#module-indexing-pipeline)
4. [Module: Incremental Indexing](#module-incremental-indexing)
5. [Module: Database Schema & Indexes](#module-database-schema--indexes)
6. [Module: Search (BM25, Vector, Hybrid)](#module-search-bm25-vector-hybrid)
7. [Module: Graph Traversal](#module-graph-traversal)
8. [Module: LLM Integration](#module-llm-integration)
9. [Module: FastAPI Server](#module-fastapi-server)
10. [Module: CLI (Typer + Rich)](#module-cli-typer--rich)
11. [Module: MCP Server](#module-mcp-server)
12. [Module: Benchmark Suite](#module-benchmark-suite)
13. [EXPLAIN Notes & Query Plans](#explain-notes--query-plans)

---

## High-Level Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│   Source    │────▶│   Parser     │────▶│   Indexer        │
│   Files     │     │ (Tree-sitter)│     │ (Symbols+Edges)  │
└─────────────┘     └──────────────┘     └────────┬─────────┘
                                                   │
                                                   ▼
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│   Query     │────▶│   Search     │────▶│   PostgreSQL +   │
│   (CLI/API) │     │ (BM25/Vector)│     │   pgvector       │
└─────────────┘     └──────────────┘     └────────┬─────────┘
                                                   │
                    ┌──────────────┐               │
                    │   Graph      │◀──────────────┘
                    │ (Recursive   │
                    │  CTEs)       │
                    └──────────────┘
```

**Data Flow**: Source files → AST → Symbols + Edges → PostgreSQL (tables + HNSW vectors) → Search/Graph queries → Results

**Key Design Principle**: Single PostgreSQL database handles both structural graph (edges table) and semantic search (vector embeddings). No external vector DB needed for <1M symbols.

---

## Module: Parsing (Tree-sitter)

**Location**: `src/codecompass/parsing/`

### What It Does

Converts Python source code into structured AST nodes, then extracts symbols (definitions) and edges (relationships).

### Key Components

| File | Purpose |
|------|---------|
| `parser.py` | Tree-sitter Python grammar setup, query patterns |
| `walker.py` | Recursively walks AST, emits symbols and raw edges |

### How It Works

```python
# 1. Parse file to AST
tree = parser.parse(code.encode())

# 2. Query for definitions (functions, classes, methods)
query = language.query("""
  (function_definition name: (identifier) @name) @def
  (class_definition name: (identifier) @name) @def
  (async_function_definition name: (identifier) @name) @def
""")

# 3. Extract qualified name from parent scopes
#    class Foo: def bar(): → "Foo.bar"

# 4. Query for calls/imports/inherits
call_query = language.query("""
  (call function: (identifier) @name) @call
  (import_from module_name: (dotted_name) @mod) @import
  (class_definition superclasses: (argument_list (identifier) @base)) @inherit
""")
```

### Symbol Kinds Extracted

- `function`, `async_function`, `class`, `method`, `classmethod`, `staticmethod`, `property`, `variable`

### Edge Kinds Extracted

| Kind | Source | Target | Confidence |
|------|--------|--------|------------|
| `calls` | Caller function | Called function | 0.3-1.0 |
| `imports` | Module | Imported module | 1.0 |
| `inherits` | Child class | Parent class | 1.0 |
| `references` | Usage site | Definition | 0.5 |

### Confidence Scoring

```python
def score_call_edge(caller_qualified, callee_name, imports):
    # Exact qualified match → 1.0
    if callee_name in imports.values():
        return 1.0
    # Same module, unqualified call → 0.8
    if caller_module == callee_module:
        return 0.8
    # Heuristic: common name, no import → 0.3
    return 0.3
```

### Why Tree-sitter?

| Alternative | Why Not |
|-------------|---------|
| `ast` module | Python-only, no incremental, slower |
| Regex | Brittle, misses edge cases |
| pyright/mypy | Heavy, slow, type-checking not parsing |

**Trade-off**: Adds C dependency (tree-sitter-python), but accurate and fast.

---

## Module: Indexing Pipeline

**Location**: `src/codecompass/indexing/`

### What It Does

Takes parsed symbols/edges and persists them to PostgreSQL with embeddings.

### Pipeline Stages

```
FileWalker → Parser → SymbolResolver → EdgeResolver → Embedder → DB Writer
```

### Key Files

| File | Purpose |
|------|---------|
| `pipeline.py` | Orchestrates full index: walk → parse → resolve → embed → store |
| `incremental.py` | Git diff-based incremental updates (see below) |
| `embedder.py` | Generates embeddings via OpenAI-compatible API + disk cache |

### Symbol Resolution

```python
# Build import table per file
imports = {"request": "flask.Request", "g": "flask.globals.g"}

# Resolve calls: "request.get_json()" → find "flask.Request.get_json"
def resolve_call(call_name, imports, all_symbols):
    if "." in call_name:
        # Qualified: module.Class.method
        return lookup_qualified(call_name)
    else:
        # Unqualified: check imports first
        if call_name in imports:
            return lookup_qualified(imports[call_name] + "." + call_name)
        # Fallback: same module symbols
        return lookup_in_module(current_module, call_name)
```

### Embedding Generation

```python
# Uses sentence-transformers compatible API (OpenRouter)
async def embed_texts(texts: List[str]) -> List[List[float]]:
    # 1. Check disk cache (SHA256 of text)
    # 2. Batch request to OpenRouter (max 100 texts)
    # 3. Store in cache, return vectors
    # Dimensions: 384 (all-MiniLM-L6-v2) or 1536 (text-embedding-3-small)
```

### Disk Cache

```
.cache/llm/
  └── <sha256(text)>.json  → {"embedding": [...], "model": "..."}
```

- Key: SHA256 of input text
- Avoids re-embedding identical content
- Survives restarts, shared across runs

---

## Module: Incremental Indexing

**Location**: `src/codecompass/indexing/incremental.py`

### Overview

Updates only changed files between last indexed commit and HEAD.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Incremental Update Flow                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. Get last_indexed_commit from index_state table              │
│  2. Compute diff (last_indexed_commit..HEAD) via pygit2        │
│  3. Classify changes: ADDED, MODIFIED, DELETED, RENAMED         │
│  4. For each changed file:                                      │
│     ├── Delete old symbols, edges, chunks                       │
│     ├── Re-parse new content                                    │
│     ├── Insert new symbols, edges, chunks                       │
│     └── Generate embeddings for new chunks                      │
│  5. Detect affected files (cross-file references)               │
│  6. Re-resolve edges in affected files                          │
│  7. Update index_state to new HEAD commit                       │
│  8. All in single transaction                                   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Change Detection (pygit2)

```python
diff = repo.diff(old_commit.tree, new_commit.tree)

for patch in diff:
    if patch.is_new_file:
        change = ("ADDED", patch.new_file.path)
    elif patch.is_deleted:
        change = ("DELETED", patch.old_file.path)
    elif patch.is_renamed:
        change = ("RENAMED", patch.old_file.path, patch.new_file.path)
    else:
        change = ("MODIFIED", patch.new_file.path)
```

### Cross-File Reference Re-Resolution (Critical)

**Problem**: File A calls function in File B. File B modified → A's edges now dangling.

**Solution**: After processing changed files, find all files referencing changed symbols and re-resolve.

```python
def _get_referencing_files(repository_id: int, changed_symbol_ids: Set[int]) -> Set[int]:
    """
    Find file IDs that have edges pointing to symbols in changed files.
    """
    if not changed_symbol_ids:
        return set()

    db = get_db()
    placeholders = ",".join(["%s"] * len(changed_symbol_ids))
    sql = f"""
        SELECT DISTINCT src_file_id
        FROM edges
        WHERE dst_symbol_id IN ({placeholders})
          AND src_file_id != dst_file_id  -- cross-file only
    """
    rows = db.execute(sql, tuple(changed_symbol_ids))
    return {row["src_file_id"] for row in rows}
```

**Why this works**:
- Intra-file edges: re-resolved when file re-parsed (step 4)
- Cross-file edges: detected via SQL, re-resolved in batch (step 5-6)
- Import changes handled: re-parse rebuilds import table

### Transaction Boundary

```python
with db.transaction():
    # 1. Compute diff
    # 2. Process all file changes
    # 3. Re-resolve referencing files
    # 4. Update index_state
    # 5. Update indexed_files
```

Rollback on any failure → database stays consistent.

### Idempotency

```python
# Second run with same commit:
old_sha = get_indexed_commit(repo_id)
new_sha = get_head_commit(repo_id)
if old_sha == new_sha:
    return "Already at commit"  # No DB changes
```

---

## Module: Database Schema & Indexes

**Location**: `migrations/001_initial_schema.sql`

### Core Tables

```sql
-- Repositories
CREATE TABLE repositories (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    url TEXT,
    root_path TEXT NOT NULL,
    last_indexed_commit TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Files per repository
CREATE TABLE files (
    id SERIAL PRIMARY KEY,
    repository_id INT REFERENCES repositories(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    language TEXT DEFAULT 'python',
    size_bytes INT,
    content_hash TEXT,
    UNIQUE (repository_id, path)
);

-- Symbols (definitions)
CREATE TABLE symbols (
    id SERIAL PRIMARY KEY,
    file_id INT REFERENCES files(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,  -- e.g., "flask.app.Flask.__init__"
    kind TEXT NOT NULL,            -- function, class, method, etc.
    start_line INT NOT NULL,
    end_line INT NOT NULL,
    signature TEXT,
    docstring TEXT,
    UNIQUE (file_id, qualified_name)
);

-- Edges (relationships)
CREATE TABLE edges (
    id SERIAL PRIMARY KEY,
    src_symbol_id INT REFERENCES symbols(id) ON DELETE CASCADE,
    dst_symbol_id INT REFERENCES symbols(id) ON DELETE CASCADE,
    src_file_id INT REFERENCES files(id),
    dst_file_id INT REFERENCES files(id),
    kind TEXT NOT NULL,            -- calls, imports, inherits, references
    confidence REAL DEFAULT 1.0,
    line INT
);

-- Vector embeddings for symbols
CREATE TABLE symbol_embeddings (
    symbol_id INT PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
    embedding vector(384) NOT NULL,  -- or 1536
    model_name TEXT NOT NULL
);

-- Text chunks for semantic search
CREATE TABLE chunks (
    id SERIAL PRIMARY KEY,
    symbol_id INT REFERENCES symbols(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    embedding vector(384),
    UNIQUE (text_hash)
);

-- Index state for incremental updates
CREATE TABLE index_state (
    repository_id INT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE,
    commit_sha TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Key Indexes (Performance Critical)

```sql
-- HNSW vector indexes (cosine similarity)
CREATE INDEX idx_symbol_embeddings_embedding 
    ON symbol_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX idx_chunks_embedding 
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Trigram for fuzzy matching (pg_trgm)
CREATE INDEX idx_symbols_name_trgm 
    ON symbols USING gin (name gin_trgm_ops);
CREATE INDEX idx_symbols_qualified_name_trgm 
    ON symbols USING gin (qualified_name gin_trgm_ops);

-- Graph traversal
CREATE INDEX idx_edges_src ON edges (src_symbol_id);
CREATE INDEX idx_edges_dst ON edges (dst_symbol_id);
CREATE INDEX idx_edges_kind ON edges (kind);
CREATE INDEX idx_edges_src_file ON edges (src_file_id);
```

### Why These Indexes?

| Index | Query Pattern | Speedup |
|-------|---------------|---------|
| HNSW vector | `ORDER BY embedding <=> query_vec LIMIT 10` | 100x vs seq scan |
| Trigram GIN | `WHERE name % 'flask'` (similarity) | 50x vs ILIKE |
| edges (src/dst) | Recursive CTE joins | 10x vs seq scan |

---

## Module: Search (BM25, Vector, Hybrid)

**Location**: `src/codecompass/search/`

### BM25 (Keyword Search)

**File**: `bm25.py`

```python
# Uses PostgreSQL tsvector/tsquery with code-aware tokenization
def build_tsvector(text: str) -> str:
    # Split on: camelCase, snake_case, dots, brackets, operators
    tokens = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[0-9]+', text)
    return ' '.join(tokens)

# Query: to_tsquery('english', 'create & order & endpoint')
```

**Code-aware tokenizer** handles:
- `createOrder` → `create order`
- `get_user_id` → `get user id`
- `Flask.Request` → `flask request`

### Vector Search (Semantic)

**File**: `vector.py`

```python
# pgvector cosine similarity
sql = """
    SELECT s.*, 1 - (se.embedding <=> %s) AS similarity
    FROM symbols s
    JOIN symbol_embeddings se ON s.id = se.symbol_id
    WHERE s.repository_id = %s
    ORDER BY se.embedding <=> %s
    LIMIT %s
"""
```

Uses HNSW index → sub-millisecond for 100k vectors.

### Hybrid Search (RRF Fusion)

**File**: `hybrid.py`

```python
# Reciprocal Rank Fusion (k=60)
def rrf_fuse(bm25_results, vector_results, k=60, limit=20):
    scores = {}
    
    for rank, r in enumerate(bm25_results):
        key = (r['symbol_id'], r['file_id'])
        scores[key] = scores.get(key, 0) + 1 / (k + rank + 1)
    
    for rank, r in enumerate(vector_results):
        key = (r['symbol_id'], r['file_id'])
        scores[key] = scores.get(key, 0) + 1 / (k + rank + 1)
    
    # Sort by fused score descending
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
```

**Why RRF?**
- No tuning needed (unlike weighted sum)
- Works well empirically for code search
- k=60 is standard default

### Graph Expansion (Hybrid+Graph)

```python
def expand_with_graph(hybrid_results, repo_id, depth=2, limit=20):
    # Take top 5 hybrid results
    for result in hybrid_results[:5]:
        # Get direct callers
        callers = get_callers(result['symbol_id'], max_depth=1)
        for c in callers:
            c['search_type'] = 'via graph:caller'
            c['fused_score'] = result['fused_score'] * 0.5
        
        # Get direct callees
        callees = get_callees(result['symbol_id'], max_depth=1)
        for c in callees:
            c['search_type'] = 'via graph:callee'
            c['fused_score'] = result['fused_score'] * 0.5
    
    # Merge all, sort by fused_score, return top limit
```

---

## Module: Graph Traversal

**Location**: `src/codecompass/graph/`

### Recursive CTEs (Pure SQL)

All graph queries use PostgreSQL recursive CTEs - no application-level traversal.

```sql
-- Transitive callers (who calls X, directly or indirectly)
WITH RECURSIVE callers AS (
    -- Base: direct callers
    SELECT e.src_symbol_id, e.dst_symbol_id, 1 AS depth, e.confidence
    FROM edges e
    WHERE e.dst_symbol_id = $1 AND e.kind = 'calls' AND e.confidence >= $2
    
    UNION ALL
    
    -- Recursive: callers of callers
    SELECT e.src_symbol_id, c.dst_symbol_id, c.depth + 1, 
           c.confidence * e.confidence
    FROM edges e
    JOIN callers c ON e.dst_symbol_id = c.src_symbol_id
    WHERE c.depth < $3 AND e.kind = 'calls' AND e.confidence >= $2
)
SELECT * FROM callers;
```

### Key Functions

| Function | Purpose | CTE Pattern |
|----------|---------|-------------|
| `transitive_callers` | Who calls this (upstream) | `dst → src` recursion |
| `transitive_callees` | What this calls (downstream) | `src → dst` recursion |
| `transitive_impact` | Callers + callees (both directions) | Two CTEs UNION |
| `find_references` | All refs (calls + imports + inherits) | Multi-kind UNION |

### Confidence Propagation

```sql
-- Confidence decays with depth
c.confidence * e.confidence

-- Example: A→B (0.8) → C (0.5) = 0.4 at depth 2
-- Filter: WHERE propagated_confidence >= min_confidence
```

### Why Recursive CTEs?

| Approach | Pros | Cons |
|----------|------|------|
| **Recursive CTE** (chosen) | Pure SQL, parallelizable, ACID, no extra deps | Depth-limited, wide fan-out slow |
| Application-level | Flexible, unlimited depth | N+1 queries, no parallelism |
| Graph DB (Neo4j) | Optimized traversal | Extra infrastructure |

---

## Module: LLM Integration

**Location**: `src/codecompass/llm/`

### Architecture

```
User Question → Retrieval (Hybrid+Graph) → Prompt Builder → LLM → Citation Validator → Answer
```

### Components

| File | Purpose |
|------|---------|
| `client.py` | OpenRouterClient (retries, cache), FakeLLMClient, CachedLLMClient |
| `pipeline.py` | answer_question(): retrieve → prompt → generate → validate |

### OpenRouter Client

```python
class OpenRouterClient:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=60.0)
        self.cache_dir = Path(".cache/llm")
    
    async def complete(self, prompt: str) -> str:
        # 1. Check disk cache (SHA256 of prompt)
        # 2. POST to OpenRouter with exponential backoff
        # 3. Store in cache, return response
```

**Retry Logic**:
```python
for attempt in range(3):
    try:
        return await self._request(prompt)
    except httpx.HTTPStatusError as e:
        if e.response.status_code >= 500:
            await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s
        else:
            raise
```

### Answer Pipeline

```python
async def answer_question(question: str, repo_id: int) -> Answer:
    # 1. Retrieve context (Hybrid+Graph search, top 10)
    results = hybrid_search(question, repo_id, limit=10, use_graph_expansion=True)
    
    # 2. Filter by relevance threshold BEFORE calling LLM
    #    Prevents hallucination on irrelevant results
    relevant = [r for r in results if r['fused_score'] > 0.01]
    if not relevant:
        return Answer("I couldn't find relevant code.", [])
    
    # 3. Build prompt with numbered citations
    prompt = build_prompt(question, relevant)
    
    # 4. Call LLM
    response = await llm_client.complete(prompt)
    
    # 5. Validate citations: [1], [2] must map to retrieved results
    validated = validate_citations(response, relevant)
    
    return Answer(validated.text, validated.citations)
```

### Citation Validation

```python
def validate_citations(text: str, sources: List[Dict]) -> ValidatedAnswer:
    # Find all [n] patterns
    citations = re.findall(r'\[(\d+)\]', text)
    
    valid_citations = []
    for cite in citations:
        idx = int(cite) - 1
        if 0 <= idx < len(sources):
            valid_citations.append(sources[idx])
        else:
            # Invalid citation - flag it
            text = text.replace(f'[{cite}]', f'[{cite}?]')
    
    return ValidatedAnswer(text, valid_citations)
```

### Fake LLM (Testing/Offline)

```python
class FakeLLMClient:
    """Deterministic responses for testing - no API key needed."""
    
    def complete(self, prompt: str) -> str:
        # Hash prompt → fixed response
        # Returns structured answer with [1], [2] citations
        return f"Based on the code [1], the function does X. [2]"
```

---

## Module: FastAPI Server

**Location**: `src/codecompass/api/`

### Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/health` | GET | Health check |
| `/api/v1/search` | POST | Hybrid search (BM25+Vector+Graph) |
| `/api/v1/symbols/definition` | GET | Find symbol by name |
| `/api/v1/symbols/{id}/callers` | GET | Transitive callers |
| `/api/v1/symbols/{id}/callees` | GET | Transitive callees |
| `/api/v1/symbols/{id}/impact` | GET | Impact analysis |
| `/api/v1/symbols/{id}/refs` | GET | All references |
| `/api/v1/symbols/{id}/context` | GET | Neighborhood (radius N) |
| `/api/v1/symbols/{id}/implementations` | GET | Interface implementations |
| `/api/v1/ask` | POST | LLM Q&A with citations |
| `/api/v1/repos/{id}/update` | POST | Trigger incremental update |

### Middleware

```python
# Timing middleware (on app, not router!)
@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = str(int((time.perf_counter() - start) * 1000))
    return response

# CORS for local frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Pydantic Models

```python
class SearchRequest(BaseModel):
    query: str
    repository_id: Optional[int] = None
    mode: Literal["bm25", "vector", "hybrid", "hybrid+graph"] = "hybrid+graph"
    limit: int = 10
    min_confidence: float = 0.0

class AskRequest(BaseModel):
    question: str
    repository_id: Optional[int] = None
    max_context: int = 10
```

---

## Module: CLI (Typer + Rich)

**Location**: `src/codecompass/cli.py`

### Commands Structure

```python
app = typer.Typer()

# Database
@app.command() def migrate(): ...
@app.command() def reset(): ...

# Indexing
@app.command() def index(path: Path, name: str, full: bool = False): ...
@app.command() def update(name: str): ...
@app.command() def embed(name: str): ...

# Search
@app.command() def search(query: str, repo: str, mode: str = "hybrid+graph"): ...
@app.command() def find(name: str, repo: str): ...
@app.command() def callers(symbol_id: int, repo: str, depth: int = 3): ...
@app.command() def callees(symbol_id: int, repo: str, depth: int = 3): ...
@app.command() def impact(symbol_id: int, repo: str, depth: int = 5): ...
@app.command() def refs(symbol_id: int, repo: str): ...
@app.command() def context(symbol_id: int, repo: str, radius: int = 2): ...
@app.command() def impls(symbol_id: int, repo: str): ...

# LLM
@app.command() def ask(question: str, repo: str): ...

# Server
@app.command() def serve(port: int = 8000, reload: bool = False): ...
```

### Rich Output

```python
# Tables for search results
table = Table(show_header=True, header_style="bold cyan")
table.add_column("Score")
table.add_column("File")
table.add_column("Lines")
table.add_column("Symbol")
table.add_column("Kind")
for r in results:
    table.add_row(f"{r['fused_score']:.3f}", r['file'], r['lines'], r['name'], r['kind'])
console.print(table)

# Panels for ask command
console.print(Panel(answer.text, title="Answer", border_style="green"))
if answer.citations:
    cite_table = Table(...)
    for i, c in enumerate(answer.citations):
        cite_table.add_row(str(i+1), c['file'], c['lines'], c['name'])
    console.print(Panel(cite_table, title="Citations", border_style="blue"))
```

---

## Module: MCP Server

**Location**: `src/codecompass/mcp_server.py`

### What Is MCP?

Model Context Protocol - standard for LLMs to call tools. stdio transport = works with Claude Code.

### 5 Tools Exposed

| Tool | Description | Parameters |
|------|-------------|------------|
| `find_definition` | Exact name match with ranking | `name`, `repository?`, `kind?` |
| `find_callers` | Transitive callers | `symbol_name`, `repository?`, `depth?`, `min_confidence?` |
| `find_callees` | Transitive callees | `symbol_name`, `repository?`, `depth?`, `min_confidence?` |
| `impact_analysis` | Callers + callees combined | `symbol_name`, `repository?`, `depth?`, `min_confidence?` |
| `search_code` | Hybrid search + graph expansion | `query`, `repository?`, `limit?`, `use_graph?` |

### Compact JSON Response

```python
def _compact_result(r: Dict) -> Dict:
    return {
        "file": r.get("file_path", ""),
        "lines": f"{r.get('start_line', 0)}-{r.get('end_line', 0)}",
        "name": r.get("name", ""),
        "kind": r.get("kind", ""),
        "confidence": round(r.get("confidence", 1.0), 2),
        "snippet": (r.get("signature") or r.get("docstring") or "")[:120],
    }
```

**Optimized for token efficiency** - only essential fields, truncated snippets.

### Handler Registration (MCP 2.x)

```python
server = Server("codecompass", version="0.1.0")

async def _list_tools_handler(request: ListToolsRequest) -> ListToolsResult:
    return ListToolsResult(tools=TOOL_DEFINITIONS)

async def _call_tool_handler(request: CallToolRequest) -> CallToolResult:
    handler = TOOL_HANDLERS.get(request.params.name)
    return await handler(request.params.arguments or {})

server.add_request_handler("tools/list", ListToolsRequest, _list_tools_handler)
server.add_request_handler("tools/call", CallToolRequest, _call_tool_handler)
```

### Registration with Claude Code

```bash
claude mcp add codecompass "python" -m codecompass.mcp_server
# Or with conda env:
claude mcp add codecompass "/path/to/conda/envs/codecompass310/python" -m codecompass.mcp_server
```

---

## Module: Benchmark Suite

**Location**: `bench/`

### Search Benchmark (`run.py`)

Evaluates 7 search modes against ground truth questions.

```python
SEARCH_MODES = [
    "grep",           # ripgrep baseline
    "bm25",           # BM25 only
    "vector",         # Vector only
    "graph",          # Graph traversal only
    "hybrid",         # BM25 + Vector (RRF)
    "hybrid_graph",   # Hybrid + graph expansion
    "llm_agent",      # LLM with grep/read tools (optional)
]
```

### Metrics Computed

```python
def compute_metrics(results: List[Dict], expected: List[str], k_values=[5,10,20]):
    # Recall@K: fraction of expected found in top-K
    # MRR: Mean Reciprocal Rank (1/rank of first relevant)
    # Exact Match: top-1 exactly matches expected (structural only)
    # Latency: P50, P95 from timing
```

### Output Formats

- JSON: Full detail for analysis
- CSV: Metrics table for spreadsheets
- Markdown: Human-readable report

### MCP Benchmark (`mcp_bench.py`)

Compares LLM agent **WITH** CodeCompass tools vs **WITHOUT** (grep + read_file only).

```python
async def run_agent_with_tools(question):
    # Agent has: find_definition, find_callers, find_callees, 
    #            impact_analysis, search_code
    # Measures: accuracy, tokens, steps, latency

async def run_agent_without_tools(question):
    # Agent has: grep, read_file only
    # Same measurements
```

**Honest Reporting**: Questions where plain agent wins are clearly marked.

---

## EXPLAIN Notes & Query Plans

### Vector Search EXPLAIN

```sql
EXPLAIN ANALYZE
SELECT s.*, 1 - (se.embedding <=> $1) AS similarity
FROM symbols s
JOIN symbol_embeddings se ON s.id = se.symbol_id
WHERE s.repository_id = 1
ORDER BY se.embedding <=> $1
LIMIT 10;
```

**Expected Plan**:
```
Limit  (cost=0.28..8.30 rows=10 width=...)
  ->  Index Scan using idx_symbol_embeddings_embedding on symbol_embeddings se
        Order By: (embedding <=> $1)
        ->  Index Scan using symbols_pkey on symbols s
              Index Cond: (id = se.symbol_id)
              Filter: (repository_id = 1)
```

**Key**: HNSW index scan on `symbol_embeddings` → index lookup on `symbols`.

### BM25 Search EXPLAIN

```sql
EXPLAIN ANALYZE
SELECT s.*, ts_rank_cd(to_tsvector('english', s.qualified_name), query) AS rank
FROM symbols s, to_tsquery('english', 'create & order') query
WHERE to_tsvector('english', s.qualified_name) @@ query
  AND s.repository_id = 1
ORDER BY rank DESC
LIMIT 10;
```

**Expected Plan**:
```
Limit  (cost=... rows=10 width=...)
  ->  Sort  (cost=... rows=... width=...)
        Sort Key: (ts_rank_cd(...))
        ->  Bitmap Heap Scan on symbols s
              Recheck Cond: (to_tsvector(...) @@ query)
              Filter: (repository_id = 1)
              ->  Bitmap Index Scan on idx_symbols_qualified_name_trgm
                    Index Cond: (qualified_name % 'create' AND qualified_name % 'order')
```

**Key**: Trigram GIN index for `@@` operator.

### Recursive CTE EXPLAIN (Transitive Callers)

```sql
EXPLAIN ANALYZE
WITH RECURSIVE callers AS (
    SELECT e.src_symbol_id, e.dst_symbol_id, 1 AS depth, e.confidence
    FROM edges e
    WHERE e.dst_symbol_id = 123 AND e.kind = 'calls' AND e.confidence >= 0.3
    
    UNION ALL
    
    SELECT e.src_symbol_id, c.dst_symbol_id, c.depth + 1, c.confidence * e.confidence
    FROM edges e
    JOIN callers c ON e.dst_symbol_id = c.src_symbol_id
    WHERE c.depth < 5 AND e.kind = 'calls' AND e.confidence >= 0.3
)
SELECT * FROM callers;
```

**Expected Plan**:
```
CTE Scan on callers  (cost=... rows=... width=...)
  CTE callers
    ->  Recursive Union  (cost=... rows=... width=...)
          ->  Index Scan using idx_edges_dst on edges e
                Index Cond: (dst_symbol_id = 123)
                Filter: (kind = 'calls' AND confidence >= 0.3)
          ->  Hash Join
                Hash Cond: (e.dst_symbol_id = c.src_symbol_id)
                ->  Index Scan using idx_edges_dst on edges e
                      Filter: (kind = 'calls' AND confidence >= 0.3)
                ->  Hash
                      ->  WorkTable Scan on callers c
                            Filter: (depth < 5)
```

**Key**: 
- Base case: Index scan on `idx_edges_dst`
- Recursive step: Hash join between edges and working table
- `idx_edges_dst` critical for performance

### Hybrid Search EXPLAIN

Two separate queries (BM25 + Vector) fused in Python. No single EXPLAIN.

**Optimization**: Run both in parallel with `asyncio.gather()`.

---

## Common Interview Questions & Answers

### Q: How does incremental indexing handle cross-file references?

**A**: After processing changed files, we query `edges` table for any file that has `dst_symbol_id` pointing to symbols in changed files (excluding intra-file). Those files are re-parsed and all their edges re-resolved from scratch. Runs in single transaction.

### Q: Why RRF instead of weighted fusion for hybrid search?

**A**: RRF (k=60) is parameter-free, works well out of the box, and doesn't require tuning weight hyperparameters per dataset. Weighted fusion needs grid search on validation set.

### Q: How do you handle dynamic dispatch in Python call graphs?

**A**: We can't fully resolve `obj.method()` without type info. Heuristics: (1) check imports for qualified names, (2) same-module symbols get 0.8 confidence, (3) fallback to name match at 0.3. Shows confidence so users can filter. Future: integrate mypy/pyright.

### Q: Why recursive CTEs instead of application-level graph traversal?

**A**: Single round-trip to DB, parallelizable by PostgreSQL, ACID consistency, no N+1 query problem. Trade-off: max depth fixed in query, wide fan-out can be slow (mitigated by confidence threshold).

### Q: How does the LLM pipeline avoid hallucination?

**A**: (1) Retrieval threshold filters irrelevant results BEFORE calling LLM. (2) Citations validated against retrieved sources - invalid [n] markers flagged with `?`. (3) Fake LLM for deterministic testing.

### Q: How would you scale past 1M symbols?

**A**: 
1. **Shard by repository** - each repo isolated, route via catalog
2. **Async workers** - Celery/RQ for indexing, webhook-triggered
3. **Read replicas** - offload search from primary
4. **Vector DB** - Qdrant/Weaviate for >1M vectors with filtered search
5. **Caching layers** - embedding cache, search result cache, LLM response cache

---

## File Map Quick Reference

```
src/codecompass/
├── api/
│   ├── main.py           # FastAPI app, middleware, CORS
│   ├── routes.py         # All endpoints
│   └── models.py         # Pydantic request/response models
├── bench/                # Internal benchmark utilities
├── cli.py                # Typer commands with Rich output
├── config.py             # Pydantic Settings (env vars)
├── db/
│   ├── __init__.py       # init_db, close_db, get_db
│   ├── migrate.py        # Migration runner
│   ├── repository.py     # Repository CRUD
│   ├── search.py         # DB search functions (BM25, vector, graph)
│   └── symbols.py        # Symbol/edge CRUD
├── graph/
│   └── traversal.py      # Recursive CTE functions
├── indexing/
│   ├── pipeline.py       # Full index orchestration
│   ├── incremental.py    # Git diff + cross-file re-resolution
│   └── embedder.py       # Embedding generation + disk cache
├── llm/
│   ├── client.py         # OpenRouterClient, FakeLLMClient, CachedLLMClient
│   └── pipeline.py       # answer_question() with citation validation
├── mcp_server.py         # MCP stdio server with 5 tools
├── parsing/
│   ├── parser.py         # Tree-sitter queries
│   └── walker.py         # AST walker → symbols + raw edges
└── search/
    ├── bm25.py           # BM25 with code-aware tokenizer
    ├── vector.py         # pgvector cosine similarity
    └── hybrid.py         # RRF fusion + graph expansion
```