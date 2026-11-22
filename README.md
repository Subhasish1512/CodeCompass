# CodeCompass

**Code intelligence engine — a mini Sourcegraph for local codebases.**

CodeCompass indexes Python repositories into a PostgreSQL database with pgvector embeddings, enabling semantic search, structural graph traversal (callers/callees/impact), and LLM-powered Q&A over your code.

---

## Architecture

```mermaid
graph TB
    subgraph CLI["CLI (Typer + Rich)"]
        A[index] --> B[Walker]
        A --> C[Parser]
        A --> D[Indexer]
        A --> E[Search]
        A --> F[Graph]
        A --> G[Ask]
    end

    subgraph Core["Core Pipeline"]
        B --> H[(Files)]
        C --> I[Tree-sitter AST]
        I --> J[Symbols + Edges]
        D --> K[(PostgreSQL + pgvector)]
        J --> K
    end

    subgraph Search["Search & Graph"]
        K --> L[BM25]
        K --> M[Vector HNSW]
        K --> N[Graph Edges]
        L --> O[RRF Hybrid]
        M --> O
        N --> P[Recursive CTEs]
        O --> Q[Results]
        P --> Q
    end

    subgraph API["FastAPI Server"]
        Q --> R[/search]
        Q --> S[/symbols/*]
        Q --> T[/ask]
        Q --> U[/repos/*/update]
    end

    subgraph MCP["MCP Server (stdio)"]
        R --> V[find_definition]
        S --> W[find_callers/callees]
        P --> X[impact_analysis]
        O --> Y[search_code]
    end

    subgraph LLM["LLM Integration (Optional)"]
        T --> Z[OpenRouter / Fake]
        Z --> AA[Cached Generator]
        AA --> AB[Answer Pipeline]
        AB --> AC[Citation Validation]
    end

    style K fill:#f9f,stroke:#333
    style Z fill:#bbf,stroke:#333
```

---

## One-Command Quickstart

```bash
# 1. Clone and enter
git clone https://github.com/yourname/codecompass
cd codecompass

# 2. Start database + run migrations
make up && make migrate

# 3. Index a repository (uses samples/demo_repo by default)
make index-repo REPO=samples/demo_repo NAME=demo

# 4. Start API server
make serve
# → http://localhost:8000/docs

# 5. Ask a question (requires indexed repo)
codecompass ask "What does UserService do?" --repo demo
```

**For MCP with Claude Code:**
```bash
# Register the MCP server
claude mcp add codecompass "python" -m codecompass.mcp_server
```

**For React UI (optional):**
```bash
# In a separate terminal
cd ui && npm install && npm run dev
# → http://localhost:5173 (proxies API to :8000)
```

---

## Schema Overview

### Core Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `repositories` | Registered codebases | `id`, `name`, `url`, `root_path`, `last_indexed_commit` |
| `files` | Source files per repo | `id`, `repository_id`, `path`, `language`, `size_bytes`, `content_hash` |
| `symbols` | Definitions (functions, classes, etc.) | `id`, `file_id`, `name`, `qualified_name`, `kind`, `start_line`, `end_line`, `signature`, `docstring` |
| `edges` | Relationships (calls, imports, inherits, refs) | `id`, `src_symbol_id`, `dst_symbol_id`, `kind`, `confidence`, `line` |
| `symbol_embeddings` | Vector embeddings for symbols | `symbol_id`, `embedding` (vector), `model_name` |
| `chunks` | Text chunks for semantic search | `id`, `symbol_id`, `text`, `text_hash`, `embedding` |

### Key Indexes

```sql
-- HNSW vector indexes (cosine similarity)
CREATE INDEX idx_symbol_embeddings_embedding ON symbol_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);

-- Trigram for fuzzy matching
CREATE INDEX idx_symbols_name_trgm ON symbols USING gin (name gin_trgm_ops);
CREATE INDEX idx_symbols_qualified_name_trgm ON symbols USING gin (qualified_name gin_trgm_ops);

-- Graph traversal
CREATE INDEX idx_edges_src ON edges (src_symbol_id);
CREATE INDEX idx_edges_dst ON edges (dst_symbol_id);
CREATE INDEX idx_edges_kind ON edges (kind);
```

---

## Design Decisions & Trade-offs

| Decision | Rationale | Trade-off |
|----------|-----------|-----------|
| **Tree-sitter for parsing** | Accurate, incremental, multi-language ready | Python-only currently; adds C dependency |
| **PostgreSQL + pgvector** | ACID, mature, HNSW, single DB for graph + vectors | Not as fast as dedicated vector DBs at scale |
| **Recursive CTEs for graph** | Pure SQL, no external deps, parallelizable | Depth-limited; wide fan-out can be slow |
| **RRF (k=60) for hybrid** | Parameter-free, works well empirically | Less tunable than weighted fusion |
| **Incremental via git diff** | Minimal rework, handles renames | Requires pygit2; cross-file refs need re-resolution |
| **Disk-cached LLM (SHA256)** | Free repeat calls, deterministic | Stale cache if model changes |
| **Optional LLM** | Core features work offline | Answer quality limited without LLM |
| **stdio MCP server** | Works with Claude Code, no network | Single-process, no horizontal scaling |

---

## Known Limitations

### Dynamic Dispatch
- **Issue**: Python's dynamic nature means call graphs are incomplete. `obj.method()` where `obj` type is unknown at index time won't resolve.
- **Mitigation**: Heuristic type inference from assignments; shows confidence scores.
- **Future**: Add abstract interpretation / type stubs.

### Call-Graph Precision
- **Issue**: Edges include low-confidence heuristics (0.3-0.5). False positives in callers/callees.
- **Mitigation**: Filter by `min_confidence` parameter; exact matches scored 1.0.
- **Future**: Integrate mypy/pyright for type-aware resolution.

### Cross-File References
- **Issue**: Incremental update re-resolves edges for changed files + referencing files, but may miss transitive effects.
- **Mitigation**: Full re-index weekly; `codecompass index --full`.

### Language Support
- **Current**: Python only (tree-sitter-python).
- **Future**: Add tree-sitter grammars for JS/TS, Go, Rust.

### Scale Limits
- **Symbols**: ~1M symbols per repo before HNSW slowdown.
- **Latency**: Graph queries >3 hops on 500k edges need tuning.
- **Memory**: Embeddings ~384 dims × 4 bytes × symbols.

---

## Benchmark Results (My Hardware)

**Hardware**: AMD Ryzen 7 7800X3D, 64GB DDR5-6000, Samsung 990 Pro 2TB, PostgreSQL 16 + pgvector 0.7.0 in Docker.

| Mode | Recall@5 | Recall@10 | MRR | Exact Match | P50 Latency | P95 Latency |
|------|----------|-----------|-----|-------------|-------------|-------------|
| grep | 0.45 | 0.55 | 0.32 | 0.20 | 42ms | 68ms |
| BM25 | 0.70 | 0.80 | 0.55 | 0.40 | 12ms | 18ms |
| Vector | 0.60 | 0.75 | 0.48 | 0.30 | 27ms | 42ms |
| Graph | 0.85 | 0.90 | 0.72 | 0.70 | 8ms | 12ms |
| Hybrid | 0.75 | 0.85 | 0.62 | 0.50 | 33ms | 51ms |
| Hybrid+Graph | 0.80 | 0.88 | 0.68 | 0.60 | 40ms | 66ms |

**Index Performance** (Flask ~45k LOC):
- Full index: 12.5s (3,600 LOC/s)
- Peak memory: 245 MB
- Incremental (5 commits): 0.9-1.3s per commit

**MCP Benchmark** (With vs Without CodeCompass tools):

| Agent | Accuracy | Avg Tokens | Avg Steps | Wins |
|-------|----------|------------|-----------|------|
| Without (grep) | 40% | 1,250 | 8.2 | 2 |
| With (MCP) | 80% | 890 | 4.1 | 6 |

*Plain grep wins on simple keyword searches; MCP wins on structural queries.*

---

## How I Would Scale This

### 1. Sharding by Repository
```
┌─────────────────────────────────────────────────────┐
│                   API Gateway                        │
└─────────────┬─────────────────────┬─────────────────┘
              │                     │
    ┌─────────▼─────────┐   ┌───────▼────────┐
    │  Repo Shard 1     │   │  Repo Shard 2  │
    │  (PostgreSQL)     │   │  (PostgreSQL)  │
    │  Flask, Django    │   │  FastAPI, etc. │
    └───────────────────┘   └────────────────┘
```
- Each repo isolated; no cross-repo queries needed
- Horizontal scaling: add shards as repos grow
- Metadata catalog routes queries to correct shard

### 2. Async Workers for Indexing
```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  Git Webhook│────▶│  Task Queue  │────▶│ Worker Pool  │
│  (push)     │     │  (Redis/     │     │  (Celery/    │
└─────────────┘     │   RabbitMQ)  │     │   RQ)        │
                    └──────────────┘     └──────────────┘
```
- Incremental updates async, non-blocking
- Batch embedding generation per worker
- Priority queue for user-triggered re-index

### 3. Caching Layers
| Layer | What | TTL | Invalidation |
|-------|------|-----|--------------|
| **Embedding Cache** | Query→vector | 7 days | Model version change |
| **Search Result Cache** | Query→top-K IDs | 1 hour | Index version bump |
| **LLM Response Cache** | Prompt→answer | 30 days | Prompt/model change |
| **Graph Neighborhood** | Symbol→neighbors | 1 day | Edge changes |

### 4. Vector Search at Scale
- **< 100k vectors**: pgvector HNSW (current)
- **100k - 1M**: pgvector with partitioned HNSW + IVFFlat
- **> 1M**: Dedicated vector DB (Qdrant, Weaviate, Milvus) with filtered search

### 5. Read Replicas for Search
```
                    ┌─────────────┐
                    │   Primary   │  (writes: index, updates)
                    │  (PostgreSQL)│
                    └──────┬──────┘
                           │ Async replication
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         ┌─────────┐ ┌─────────┐ ┌─────────┐
         │ Replica │ │ Replica │ │ Replica │
         │ Search  │ │ Search  │ │ Search  │
         └─────────┘ └─────────┘ └─────────┘
```
- Offload search traffic from primary
- Horizontal read scaling
- Eventual consistency acceptable for search

---

## Commands Reference

```bash
# Database
make up              # Start PostgreSQL + pgvector
make down            # Stop database
make migrate         # Run migrations
make psql            # Open psql shell
make reset           # DANGER: Drop all tables

# Indexing
codecompass index /path/to/repo --name myrepo
codecompass index /path/to/repo --name myrepo --full
codecompass update myrepo

# Search
codecompass search "query" --repo myrepo --mode hybrid+graph
codecompass find "symbol_name" --repo myrepo
codecompass callers 123 --depth 3 --repo myrepo
codecompass impact 123 --depth 5 --repo myrepo

# LLM Q&A (requires OPENROUTER_API_KEY)
codecompass ask "How does auth work?" --repo myrepo

# API Server
codecompass serve --port 8000 --reload

# Benchmarks
make bench           # Search comparison
make bench-index     # Index performance
make bench-mcp       # MCP agent comparison

# Quality
make lint            # Ruff check
make format          # Ruff format
pre-commit run --all-files
```

---

## Project Structure

```
codecompass/
├── .github/workflows/ci.yml      # GitHub Actions CI
├── .pre-commit-config.yaml       # Ruff pre-commit hooks
├── bench/                        # Evaluation suite
│   ├── questions/                # YAML question files
│   ├── results/                  # Output (gitignored)
│   ├── run.py                    # Search benchmark
│   ├── mcp_bench.py              # MCP agent benchmark
│   └── README.md                 # Benchmark docs
├── docs/
│   └── DESIGN.md                 # Architecture deep-dive
├── migrations/
│   └── 001_initial_schema.sql    # Database schema
├── samples/
│   └── demo_repo/                # Demo for smoke tests
├── src/codecompass/
│   ├── api/                      # FastAPI routes
│   ├── bench/                    # (internal)
│   ├── cli.py                    # Typer CLI
│   ├── config.py                 # Pydantic settings
│   ├── db/                       # Database layer
│   ├── graph/                    # Graph traversal
│   ├── indexing/                 # Pipeline + incremental
│   ├── llm/                      # LLM client + pipeline
│   ├── mcp_server.py             # MCP stdio server
│   ├── parsing/                  # Tree-sitter parser
│   └── search/                   # Hybrid search
├── docker-compose.yml            # PostgreSQL + pgvector
├── Makefile                      # Common commands
├── pyproject.toml                # Package config
├── ui/                           # React + TypeScript + Vite frontend
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api.ts                # Typed API client
│       ├── index.css             # Plain CSS (no framework)
│       └── components/
│           ├── SearchPage.tsx
│           ├── SymbolPage.tsx
│           └── AskPage.tsx
└── README.md                     # This file
```

---

## How to Verify Manually (Fresh-Clone Walkthrough)

This section walks through verifying the project works end-to-end using only the README quickstart commands.

### Prerequisites
- Docker & Docker Compose
- Python 3.10+ with `uv` (or `pip`)
- Git

### Steps

```bash
# 1. Fresh clone
git clone https://github.com/yourname/codecompass
cd codecompass

# 2. Start database + run migrations
make up && make migrate
# → Should show: "Database ready" then "Applied 1 migration(s)"

# 3. Index the demo repository
make index-repo REPO=samples/demo_repo NAME=demo
# → Should show: "Indexed 7 files, 115 symbols, 289 edges in ~1.5s"

# 4. Generate embeddings (for vector/hybrid search)
codecompass embed demo
# → Should show: "Generated 115 embeddings in ~2s"

# 5. Start API server
make serve
# → Should show: "Uvicorn running on http://0.0.0.0:8000"

# 6. In another terminal, test /health
curl -s http://localhost:8000/api/v1/health | jq
# → {"status":"ok","repository_count":1,"symbol_count":115,"edge_count":289}

# 7. Test /callers endpoint (find a symbol first, then get its callers)
SYMBOL_ID=$(curl -s "http://localhost:8000/api/v1/symbols/definition?name=main&repository_id=1" | jq -r '.[0].id')
curl -s "http://localhost:8000/api/v1/symbols/$SYMBOL_ID/callers" | jq
# → Should return JSON array (may be empty if main has no callers)

# 8. Test CLI search (in another terminal)
codecompass search "create order" --repo demo --mode hybrid+graph
# → Should show Rich table with results

# 9. Test CLI graph commands
codecompass callers $SYMBOL_ID --repo demo --depth 3
codecompass impact $SYMBOL_ID --repo demo --depth 5

# 10. Run lint (pre-commit / CI check)
make lint
# → Should pass with no errors

# 11. Clean up
make down
```

### UI Verification (Optional)

```bash
# 12. Start UI (in another terminal, with API server running)
cd ui && npm install && npm run dev
# → Vite dev server on http://localhost:5173

# 13. Open http://localhost:5173 in browser
# → Should show CodeCompass header with Search/Symbol Explorer/Ask tabs

# 14. Test Search page
# - Select "demo" repository from dropdown
# - Enter "create order" in query box
# - Select "Hybrid + Graph" mode
# - Click Search
# → Should show results list with file paths, line ranges, score breakdown, and expandable snippets

# 15. Test Symbol Explorer page
# - Click "Symbol Explorer" tab
# - Enter "create_order" in symbol name box
# - Click Find
# → Should show symbol definition with signature/docstring
# - Click "Callers" tab
# → Should show indented call tree with confidence percentages
# - Click "Callees" tab
# → Should show indented callee tree
# - Click "Impact" tab
# → Should show both callers and callees combined
# - Click any symbol name in the tree
# → Should navigate to that symbol's details

# 16. Test Ask page
# - Click "Ask" tab
# - Enter "How does order creation work?"
# - Click Ask
# → Should show plain-text answer with clickable [1], [2] citations
# - Click a citation marker [1]
# → Should expand to show cited snippet with file path and lines
```

### Expected Results Summary

| Step | Command | Success Indicator |
|------|---------|-------------------|
| 2 | `make up && make migrate` | "Database ready" + "Applied 1 migration(s)" |
| 3 | `make index-repo` | "Indexed 7 files, 115 symbols, 289 edges" |
| 4 | `codecompass embed demo` | "Generated 115 embeddings" |
| 6 | `curl /health` | JSON with `status: "ok"` |
| 7 | `curl /callers` | JSON array (200 OK) |
| 8 | `codecompass search` | Rich table with ≥1 result |
| 10 | `make lint` | No errors, exits 0 |

---

## License

MIT License - see LICENSE file.