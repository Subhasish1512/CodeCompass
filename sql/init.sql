-- CodeCompass Database Schema
-- Postgres 16 with pgvector and full-text search

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================================
-- CORE TABLES
-- ============================================================================

-- Repositories being indexed
CREATE TABLE repositories (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    url         TEXT,
    root_path   TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_repositories_name ON repositories(name);

-- Files in the repository
CREATE TABLE files (
    id              BIGSERIAL PRIMARY KEY,
    repository_id   BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    path            TEXT NOT NULL,          -- Relative path from repo root
    language        TEXT NOT NULL,          -- tree-sitter language name
    content_hash    BYTEA NOT NULL,         -- SHA256 of file content
    size_bytes      INT NOT NULL,
    indexed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_files_repo_path ON files(repository_id, path);
CREATE INDEX idx_files_language ON files(language);
CREATE INDEX idx_files_content_hash ON files(content_hash);

-- Symbols (definitions): functions, classes, methods, variables, etc.
CREATE TABLE symbols (
    id              BIGSERIAL PRIMARY KEY,
    file_id         BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,          -- Symbol name
    kind            TEXT NOT NULL,          -- function, class, method, variable, constant, interface, etc.
    signature       TEXT,                   -- Full signature (for functions/methods)
    docstring       TEXT,                   -- Documentation comment
    start_line      INT NOT NULL,
    end_line        INT NOT NULL,
    start_col       INT NOT NULL,
    end_col         INT NOT NULL,
    scope_symbol_id BIGINT REFERENCES symbols(id),  -- Parent symbol (class for methods, etc.)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_symbols_file_id ON symbols(file_id);
CREATE INDEX idx_symbols_name ON symbols(name);
CREATE INDEX idx_symbols_kind ON symbols(kind);
CREATE INDEX idx_symbols_scope ON symbols(scope_symbol_id);

-- Full-text search vector for symbols (name + signature + docstring)
ALTER TABLE symbols ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(signature, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(docstring, '')), 'C')
    ) STORED;
CREATE INDEX idx_symbols_search_vector ON symbols USING GIN(search_vector);

-- Symbol embeddings for vector search
CREATE TABLE symbol_embeddings (
    symbol_id   BIGINT PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
    embedding   vector(384) NOT NULL,       -- Dimension matches EMBEDDING_DIM
    model_name  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- HNSW index for fast ANN search (created after data load for performance)
-- CREATE INDEX idx_symbol_embeddings_hnsw ON symbol_embeddings USING hnsw (embedding vector_cosine_ops);

-- ============================================================================
-- GRAPH TABLES (call/reference relationships)
-- ============================================================================

-- Edges: caller -> callee, or reference -> definition
CREATE TABLE edges (
    id              BIGSERIAL PRIMARY KEY,
    src_symbol_id   BIGINT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    dst_symbol_id   BIGINT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    edge_type       TEXT NOT NULL,          -- 'calls', 'references', 'inherits', 'imports'
    src_file_id     BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    dst_file_id     BIGINT REFERENCES files(id) ON DELETE CASCADE,  -- NULL for external refs
    line            INT NOT NULL,           -- Line in source file where edge originates
    col             INT NOT NULL,           -- Column in source file
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_edges_src ON edges(src_symbol_id);
CREATE INDEX idx_edges_dst ON edges(dst_symbol_id);
CREATE INDEX idx_edges_type ON edges(edge_type);
CREATE INDEX idx_edges_src_file ON edges(src_file_id);
CREATE UNIQUE INDEX idx_edges_unique ON edges(src_symbol_id, dst_symbol_id, edge_type, line, col);

-- ============================================================================
-- INCREMENTAL INDEXING STATE
-- ============================================================================

-- Track indexing state per repository for incremental updates
CREATE TABLE index_state (
    repository_id   BIGINT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE,
    commit_sha      CHAR(40) NOT NULL,      -- Git commit SHA
    tree_sha        CHAR(40) NOT NULL,      -- Git tree SHA
    indexed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    files_count     INT NOT NULL DEFAULT 0,
    symbols_count   INT NOT NULL DEFAULT 0,
    edges_count     INT NOT NULL DEFAULT 0
);

-- Track which files were processed in last index (for diffing)
CREATE TABLE indexed_files (
    repository_id   BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    file_id         BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    commit_sha      CHAR(40) NOT NULL,
    PRIMARY KEY (repository_id, file_id)
);

-- ============================================================================
-- VIEWS FOR COMMON QUERIES
-- ============================================================================

-- Symbol with file info
CREATE VIEW v_symbols_with_file AS
SELECT
    s.id,
    s.name,
    s.kind,
    s.signature,
    s.docstring,
    s.start_line,
    s.end_line,
    s.start_col,
    s.end_col,
    s.scope_symbol_id,
    f.id AS file_id,
    f.path AS file_path,
    f.language,
    f.repository_id,
    r.name AS repo_name
FROM symbols s
JOIN files f ON s.file_id = f.id
JOIN repositories r ON f.repository_id = r.id;

-- Edges with symbol names for readability
CREATE VIEW v_edges_with_names AS
SELECT
    e.id,
    e.edge_type,
    e.line,
    e.col,
    src.id AS src_symbol_id,
    src.name AS src_name,
    src.kind AS src_kind,
    src.signature AS src_signature,
    src.docstring AS src_docstring,
    src.start_line AS src_start_line,
    src.end_line AS src_end_line,
    src.start_col AS src_start_col,
    src.end_col AS src_end_col,
    src.file_id AS src_file_id,
    sf.path AS src_file_path,
    sf.language AS src_language,
    sf.repository_id AS src_repository_id,
    sr.name AS src_repo_name,
    dst.id AS dst_symbol_id,
    dst.name AS dst_name,
    dst.kind AS dst_kind,
    dst.signature AS dst_signature,
    dst.docstring AS dst_docstring,
    dst.start_line AS dst_start_line,
    dst.end_line AS dst_end_line,
    dst.start_col AS dst_start_col,
    dst.end_col AS dst_end_col,
    dst.file_id AS dst_file_id,
    df.path AS dst_file_path,
    df.language AS dst_language,
    df.repository_id AS dst_repository_id,
    dr.name AS dst_repo_name
FROM edges e
JOIN symbols src ON e.src_symbol_id = src.id
JOIN files sf ON src.file_id = sf.id
JOIN repositories sr ON sf.repository_id = sr.id
JOIN symbols dst ON e.dst_symbol_id = dst.id
LEFT JOIN files df ON dst.file_id = df.id
LEFT JOIN repositories dr ON df.repository_id = dr.id;

-- ============================================================================
-- RECURSIVE CTE HELPERS (for transitive queries)
-- ============================================================================

-- Find all transitive callees (functions called directly or indirectly)
-- Usage: SELECT * FROM transitive_callees(<symbol_id>);
CREATE OR REPLACE FUNCTION transitive_callees(start_symbol_id BIGINT, max_depth INT DEFAULT 10)
RETURNS TABLE (
    symbol_id BIGINT,
    name TEXT,
    kind TEXT,
    file_path TEXT,
    depth INT,
    path BIGINT[]
) LANGUAGE sql AS $$
WITH RECURSIVE call_graph AS (
    SELECT
        dst_symbol_id AS symbol_id,
        1 AS depth,
        ARRAY[src_symbol_id] AS path
    FROM edges
    WHERE src_symbol_id = start_symbol_id
      AND edge_type = 'calls'

    UNION ALL

    SELECT
        e.dst_symbol_id,
        cg.depth + 1,
        cg.path || e.src_symbol_id
    FROM edges e
    JOIN call_graph cg ON e.src_symbol_id = cg.symbol_id
    WHERE cg.depth < max_depth
      AND e.edge_type = 'calls'
      AND NOT e.dst_symbol_id = ANY(cg.path)
)
SELECT
    cg.symbol_id,
    s.name,
    s.kind,
    f.path AS file_path,
    cg.depth,
    cg.path
FROM call_graph cg
JOIN symbols s ON cg.symbol_id = s.id
JOIN files f ON s.file_id = f.id
ORDER BY cg.depth, s.name;
$$;

-- Find all transitive callers (functions that call this directly or indirectly)
CREATE OR REPLACE FUNCTION transitive_callers(start_symbol_id BIGINT, max_depth INT DEFAULT 10)
RETURNS TABLE (
    symbol_id BIGINT,
    name TEXT,
    kind TEXT,
    file_path TEXT,
    depth INT,
    path BIGINT[]
) LANGUAGE sql AS $$
WITH RECURSIVE call_graph AS (
    SELECT
        src_symbol_id AS symbol_id,
        1 AS depth,
        ARRAY[dst_symbol_id] AS path
    FROM edges
    WHERE dst_symbol_id = start_symbol_id
      AND edge_type = 'calls'

    UNION ALL

    SELECT
        e.src_symbol_id,
        cg.depth + 1,
        cg.path || e.dst_symbol_id
    FROM edges e
    JOIN call_graph cg ON e.dst_symbol_id = cg.symbol_id
    WHERE cg.depth < max_depth
      AND e.edge_type = 'calls'
      AND NOT e.src_symbol_id = ANY(cg.path)
)
SELECT
    cg.symbol_id,
    s.name,
    s.kind,
    f.path AS file_path,
    cg.depth,
    cg.path
FROM call_graph cg
JOIN symbols s ON cg.symbol_id = s.id
JOIN files f ON s.file_id = f.id
ORDER BY cg.depth, s.name;
$$;

-- Find transitive impact: all symbols that would be affected by changing start_symbol
-- (combines callers and callees transitively)
CREATE OR REPLACE FUNCTION transitive_impact(start_symbol_id BIGINT, max_depth INT DEFAULT 10)
RETURNS TABLE (
    symbol_id BIGINT,
    name TEXT,
    kind TEXT,
    file_path TEXT,
    direction TEXT,  -- 'caller' or 'callee'
    depth INT
) LANGUAGE sql AS $$
SELECT * FROM transitive_callers(start_symbol_id, max_depth)
UNION ALL
SELECT symbol_id, name, kind, file_path, 'callee' AS direction, depth
FROM transitive_callees(start_symbol_id, max_depth)
ORDER BY direction, depth, name;
$$;