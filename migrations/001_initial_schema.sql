-- CodeCompass Database Schema - Migration 001
-- Idempotent: uses IF NOT EXISTS, CREATE OR REPLACE, ON CONFLICT
-- Run with: python -m codecompass.db.migrate migrate

-- ============================================================================
-- EXTENSIONS
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================================
-- CODE-AWARE FULL-TEXT SEARCH HELPERS (must be before tables using them)
-- ============================================================================

-- Function to generate code-aware tsvector from text
-- Splits camelCase, snake_case, and dot.notation into separate tokens
CREATE OR REPLACE FUNCTION code_tsvector(text_input TEXT)
RETURNS tsvector LANGUAGE sql IMMUTABLE AS $$
SELECT to_tsvector('english', regexp_replace(
    regexp_replace(
        regexp_replace(
            regexp_replace(text_input, '(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ', 'g'),
        '_', ' ', 'g'),
    '\.', ' ', 'g'),
'[^a-zA-Z0-9]+', ' ', 'g'))
$$;

-- Wrapper for plainto_tsquery with code-aware tokenization
CREATE OR REPLACE FUNCTION code_plainto_tsquery(text_input TEXT)
RETURNS tsquery LANGUAGE sql IMMUTABLE AS $$
SELECT plainto_tsquery('english', regexp_replace(
    regexp_replace(
        regexp_replace(
            regexp_replace(text_input, '(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ', 'g'),
        '_', ' ', 'g'),
    '\.', ' ', 'g'),
'[^a-zA-Z0-9]+', ' ', 'g'))
$$;

-- ============================================================================
-- CORE TABLES
-- ============================================================================

-- repositories: each indexed repository
-- last_indexed_commit tracks the git SHA for incremental indexing
CREATE TABLE IF NOT EXISTS repositories (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    url             TEXT,
    root_path       TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- files: source files in a repository
-- content_hash (SHA256) for change detection
-- last_commit for incremental re-indexing
CREATE TABLE IF NOT EXISTS files (
    id              BIGSERIAL PRIMARY KEY,
    repository_id   BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    path            TEXT NOT NULL,
    language        TEXT NOT NULL,
    content_hash    BYTEA NOT NULL,   -- SHA256 of file content
    size_bytes      BIGINT NOT NULL DEFAULT 0,
    last_commit     TEXT,             -- git commit SHA that last modified this file
    indexed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (repository_id, path)
);

-- symbols: definitions (functions, classes, methods, variables, modules)
-- qualified_name: fully qualified name (e.g., "mypackage.MyClass.my_method")
-- parent_symbol_id: for nesting (methods in classes, nested functions)
-- search_vector: tsvector for full-text search on qualified_name + name + signature + docstring
-- Uses code_tsvector for code-aware tokenization (camelCase, snake_case, dot.notation)
CREATE TABLE IF NOT EXISTS symbols (
    id                  BIGSERIAL PRIMARY KEY,
    file_id             BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    qualified_name      TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('function','class','method','variable','module')),
    start_line          INT NOT NULL,
    end_line            INT NOT NULL,
    start_col           INT NOT NULL DEFAULT 0,
    end_col             INT NOT NULL DEFAULT 0,
    signature           TEXT,
    docstring           TEXT,
    scope_symbol_id     BIGINT REFERENCES symbols(id) ON DELETE SET NULL,
    search_vector       tsvector GENERATED ALWAYS AS (
                            setweight(code_tsvector(coalesce(qualified_name, '')), 'A') ||
                            setweight(code_tsvector(coalesce(name, '')), 'B') ||
                            setweight(code_tsvector(coalesce(signature, '')), 'C') ||
                            setweight(code_tsvector(coalesce(docstring, '')), 'D')
                          ) STORED,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- edges: relationships between symbols
-- dst_symbol_id is NULL when the target is unresolved (external or not yet indexed)
-- dst_name preserves the name for later resolution
-- edge_type: calls | imports | inherits | references
-- confidence: 0.0-1.0, for fuzzy matches (e.g., dynamic dispatch)
CREATE TABLE IF NOT EXISTS edges (
    id                  BIGSERIAL PRIMARY KEY,
    src_symbol_id       BIGINT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    dst_symbol_id       BIGINT REFERENCES symbols(id) ON DELETE SET NULL,
    dst_name            TEXT NOT NULL,
    edge_type           TEXT NOT NULL CHECK (edge_type IN ('calls','imports','inherits','references')),
    src_file_id         BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    dst_file_id         BIGINT REFERENCES files(id) ON DELETE CASCADE,
    line                INT NOT NULL,
    col                 INT NOT NULL DEFAULT 0,
    confidence          REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- symbol_embeddings: vector embeddings for symbols
CREATE TABLE IF NOT EXISTS symbol_embeddings (
    symbol_id       BIGINT PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
    embedding       vector(384) NOT NULL,
    model_name      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- HNSW index for symbol embeddings
CREATE INDEX IF NOT EXISTS idx_symbol_embeddings_embedding ON symbol_embeddings USING hnsw (embedding vector_cosine_ops);

-- chunks: searchable text segments attached to symbols
-- text_hash for deduplication
-- tsv: generated tsvector for full-text search (BM25)
-- embedding: vector(384) for semantic search (pgvector)
CREATE TABLE IF NOT EXISTS chunks (
    id              BIGSERIAL PRIMARY KEY,
    symbol_id       BIGINT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    file_id         BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    text            TEXT NOT NULL,
    text_hash       BYTEA NOT NULL,   -- SHA256 of text for dedup
    tsv             tsvector GENERATED ALWAYS AS (
                        setweight(code_tsvector(coalesce(text, '')), 'A')
                      ) STORED,
    embedding       vector(384),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chunks_text_hash_unique UNIQUE (text_hash)
);

-- index_state: tracks git commit state for incremental indexing
CREATE TABLE IF NOT EXISTS index_state (
    repository_id   BIGINT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE,
    commit_sha      TEXT NOT NULL,
    tree_sha        TEXT NOT NULL,
    files_count     INT NOT NULL DEFAULT 0,
    symbols_count   INT NOT NULL DEFAULT 0,
    edges_count     INT NOT NULL DEFAULT 0,
    indexed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- indexed_files: tracks which files were indexed at which commit
CREATE TABLE IF NOT EXISTS indexed_files (
    repository_id   BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    file_id         BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    commit_sha      TEXT NOT NULL,
    PRIMARY KEY (repository_id, file_id)
);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- symbols: name lookups (exact and fuzzy via pg_trgm)
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified_name ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_name_trgm ON symbols USING GIN (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified_name_trgm ON symbols USING GIN (qualified_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_scope ON symbols(scope_symbol_id);
CREATE INDEX IF NOT EXISTS idx_symbols_search_vector ON symbols USING GIN (search_vector);

-- files: repo-scoped lookups
CREATE INDEX IF NOT EXISTS idx_files_repo_path ON files(repository_id, path);

-- edges: graph traversal
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_symbol_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_symbol_id);
CREATE INDEX IF NOT EXISTS idx_edges_src_file ON edges(src_file_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst_file ON edges(dst_file_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_src_type ON edges(src_symbol_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_dst_type ON edges(dst_symbol_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_confidence ON edges(confidence);

-- chunks: full-text search (BM25 via ts_rank)
CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON chunks USING GIN (tsv);

-- chunks: vector similarity search (HNSW, cosine distance)
-- HNSW index for pgvector cosine distance
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);

-- chunks: deduplication
CREATE INDEX IF NOT EXISTS idx_chunks_text_hash ON chunks(text_hash);
CREATE INDEX IF NOT EXISTS idx_chunks_symbol ON chunks(symbol_id);

-- ============================================================================
-- HELPER VIEWS
-- ============================================================================

-- symbols with file path for easier queries
CREATE OR REPLACE VIEW v_symbols_with_file AS
SELECT
    s.id,
    s.name,
    s.qualified_name,
    s.kind,
    s.start_line,
    s.end_line,
    s.start_col,
    s.end_col,
    s.signature,
    s.docstring,
    s.scope_symbol_id,
    s.search_vector,
    f.id AS file_id,
    f.path AS file_path,
    f.language,
    f.repository_id AS repo_id
FROM symbols s
JOIN files f ON s.file_id = f.id;

-- edges with symbol names for readability
CREATE OR REPLACE VIEW v_edges_with_names AS
SELECT
    e.id,
    e.edge_type AS kind,
    e.confidence,
    e.line,
    e.col,
    src.id AS src_symbol_id,
    src.name AS src_name,
    src.qualified_name AS src_qualified_name,
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
    dst.qualified_name AS dst_qualified_name,
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
LEFT JOIN symbols dst ON e.dst_symbol_id = dst.id
LEFT JOIN files df ON dst.file_id = df.id
LEFT JOIN repositories dr ON df.repository_id = dr.id;

-- ============================================================================
-- RECURSIVE CTE FUNCTIONS FOR GRAPH TRAVERSAL
-- ============================================================================

-- Transitive callees: all functions called directly or indirectly
-- Returns: symbol info, depth, call path, confidence, file path, line range
CREATE OR REPLACE FUNCTION transitive_callees(
    start_symbol_id BIGINT,
    max_depth INT DEFAULT 10,
    min_confidence REAL DEFAULT 0.0,
    result_limit INT DEFAULT 1000
)
RETURNS TABLE (
    symbol_id BIGINT,
    name TEXT,
    qualified_name TEXT,
    kind TEXT,
    file_path TEXT,
    start_line INT,
    end_line INT,
    depth INT,
    call_path BIGINT[],
    edge_confidence REAL,
    source_symbol_id BIGINT,
    source_name TEXT,
    source_qualified_name TEXT
) LANGUAGE sql STABLE AS $$
WITH RECURSIVE call_graph AS (
    -- Base case: direct callees
    SELECT
        e.dst_symbol_id AS symbol_id,
        1 AS depth,
        ARRAY[e.src_symbol_id] AS path,
        e.confidence AS edge_confidence,
        e.src_symbol_id AS source_symbol_id,
        e.line AS call_line
    FROM edges e
    WHERE e.src_symbol_id = start_symbol_id
      AND e.edge_type = 'calls'
      AND e.dst_symbol_id IS NOT NULL
      AND e.confidence >= min_confidence

    UNION ALL

    -- Recursive step: follow calls
    SELECT
        e.dst_symbol_id,
        cg.depth + 1,
        cg.path || e.src_symbol_id,
        e.confidence,
        e.src_symbol_id,
        e.line
    FROM edges e
    JOIN call_graph cg ON e.src_symbol_id = cg.symbol_id
    WHERE cg.depth < max_depth
      AND e.edge_type = 'calls'
      AND e.dst_symbol_id IS NOT NULL
      AND e.confidence >= min_confidence
      AND NOT e.dst_symbol_id = ANY(cg.path)
)
SELECT
    cg.symbol_id,
    s.name,
    s.qualified_name,
    s.kind,
    f.path AS file_path,
    s.start_line,
    s.end_line,
    cg.depth,
    cg.path AS call_path,
    cg.edge_confidence,
    cg.source_symbol_id,
    src.name AS source_name,
    src.qualified_name AS source_qualified_name
FROM call_graph cg
JOIN symbols s ON cg.symbol_id = s.id
JOIN files f ON s.file_id = f.id
JOIN symbols src ON cg.source_symbol_id = src.id
ORDER BY cg.depth, s.name
LIMIT result_limit;
$$;

-- Transitive callers: all functions that call this directly or indirectly
CREATE OR REPLACE FUNCTION transitive_callers(
    start_symbol_id BIGINT,
    max_depth INT DEFAULT 10,
    min_confidence REAL DEFAULT 0.0,
    result_limit INT DEFAULT 1000
)
RETURNS TABLE (
    symbol_id BIGINT,
    name TEXT,
    qualified_name TEXT,
    kind TEXT,
    file_path TEXT,
    start_line INT,
    end_line INT,
    depth INT,
    call_path BIGINT[],
    edge_confidence REAL,
    target_symbol_id BIGINT,
    target_name TEXT,
    target_qualified_name TEXT
) LANGUAGE sql STABLE AS $$
WITH RECURSIVE call_graph AS (
    -- Base case: direct callers
    SELECT
        e.src_symbol_id AS symbol_id,
        1 AS depth,
        ARRAY[e.dst_symbol_id] AS path,
        e.confidence AS edge_confidence,
        e.dst_symbol_id AS target_symbol_id,
        e.line AS call_line
    FROM edges e
    WHERE e.dst_symbol_id = start_symbol_id
      AND e.edge_type = 'calls'
      AND e.confidence >= min_confidence

    UNION ALL

    -- Recursive step: follow callers
    SELECT
        e.src_symbol_id,
        cg.depth + 1,
        cg.path || e.dst_symbol_id,
        e.confidence,
        e.dst_symbol_id,
        e.line
    FROM edges e
    JOIN call_graph cg ON e.dst_symbol_id = cg.symbol_id
    WHERE cg.depth < max_depth
      AND e.edge_type = 'calls'
      AND e.confidence >= min_confidence
      AND NOT e.src_symbol_id = ANY(cg.path)
)
SELECT
    cg.symbol_id,
    s.name,
    s.qualified_name,
    s.kind,
    f.path AS file_path,
    s.start_line,
    s.end_line,
    cg.depth,
    cg.path AS call_path,
    cg.edge_confidence,
    cg.target_symbol_id,
    dst.name AS target_name,
    dst.qualified_name AS target_qualified_name
FROM call_graph cg
JOIN symbols s ON cg.symbol_id = s.id
JOIN files f ON s.file_id = f.id
JOIN symbols dst ON cg.target_symbol_id = dst.id
ORDER BY cg.depth, s.name
LIMIT result_limit;
$$;

-- Transitive impact: all symbols affected by changing start_symbol (callers + callees)
-- Grouped by depth with direction
CREATE OR REPLACE FUNCTION transitive_impact(
    start_symbol_id BIGINT,
    max_depth INT DEFAULT 10,
    min_confidence REAL DEFAULT 0.0,
    result_limit INT DEFAULT 1000
)
RETURNS TABLE (
    symbol_id BIGINT,
    name TEXT,
    qualified_name TEXT,
    kind TEXT,
    file_path TEXT,
    start_line INT,
    end_line INT,
    direction TEXT,
    depth INT,
    call_path BIGINT[],
    edge_confidence REAL,
    related_symbol_id BIGINT,
    related_name TEXT,
    related_qualified_name TEXT
) LANGUAGE sql STABLE AS $$
SELECT
    symbol_id, name, qualified_name, kind, file_path,
    start_line, end_line, 'callee' AS direction,
    depth, call_path, edge_confidence,
    source_symbol_id AS related_symbol_id,
    source_name AS related_name,
    source_qualified_name AS related_qualified_name
FROM transitive_callees(start_symbol_id, max_depth, min_confidence, result_limit)
UNION ALL
SELECT
    symbol_id, name, qualified_name, kind, file_path,
    start_line, end_line, 'caller' AS direction,
    depth, call_path, edge_confidence,
    target_symbol_id AS related_symbol_id,
    target_name AS related_name,
    target_qualified_name AS related_qualified_name
FROM transitive_callers(start_symbol_id, max_depth, min_confidence, result_limit)
ORDER BY direction, depth, name
LIMIT result_limit;
$$;

-- Find definition with disambiguation ranking
-- Exact qualified match > exact name match > trigram similarity
CREATE OR REPLACE FUNCTION find_definition_ranked(
    query_name TEXT,
    repo_id BIGINT DEFAULT NULL,
    kind_filter TEXT DEFAULT NULL,
    limit_count INT DEFAULT 20
)
RETURNS TABLE (
    symbol_id BIGINT,
    name TEXT,
    qualified_name TEXT,
    kind TEXT,
    file_path TEXT,
    start_line INT,
    end_line INT,
    signature TEXT,
    docstring TEXT,
    match_type TEXT,
    match_score REAL
) LANGUAGE sql STABLE AS $$
WITH matches AS (
    -- Exact qualified name match (highest priority)
    SELECT
        s.id AS symbol_id,
        s.name,
        s.qualified_name,
        s.kind,
        f.path AS file_path,
        s.start_line,
        s.end_line,
        s.signature,
        s.docstring,
        'exact_qualified' AS match_type,
        1.0 AS match_score
    FROM symbols s
    JOIN files f ON s.file_id = f.id
    WHERE s.qualified_name = query_name
      AND (repo_id IS NULL OR f.repository_id = repo_id)
      AND (kind_filter IS NULL OR s.kind = kind_filter)

    UNION ALL

    -- Exact name match
    SELECT
        s.id AS symbol_id,
        s.name,
        s.qualified_name,
        s.kind,
        f.path AS file_path,
        s.start_line,
        s.end_line,
        s.signature,
        s.docstring,
        'exact_name' AS match_type,
        0.8 AS match_score
    FROM symbols s
    JOIN files f ON s.file_id = f.id
    WHERE s.name = query_name
      AND s.qualified_name != query_name  -- Avoid duplicates from first query
      AND (repo_id IS NULL OR f.repository_id = repo_id)
      AND (kind_filter IS NULL OR s.kind = kind_filter)

    UNION ALL

    -- Trigram similarity on qualified_name
    SELECT
        s.id AS symbol_id,
        s.name,
        s.qualified_name,
        s.kind,
        f.path AS file_path,
        s.start_line,
        s.end_line,
        s.signature,
        s.docstring,
        'trigram_qualified' AS match_type,
        similarity(s.qualified_name, query_name) AS match_score
    FROM symbols s
    JOIN files f ON s.file_id = f.id
    WHERE similarity(s.qualified_name, query_name) > 0.3
      AND s.qualified_name != query_name
      AND s.name != query_name
      AND (repo_id IS NULL OR f.repository_id = repo_id)
      AND (kind_filter IS NULL OR s.kind = kind_filter)

    UNION ALL

    -- Trigram similarity on name
    SELECT
        s.id AS symbol_id,
        s.name,
        s.qualified_name,
        s.kind,
        f.path AS file_path,
        s.start_line,
        s.end_line,
        s.signature,
        s.docstring,
        'trigram_name' AS match_type,
        similarity(s.name, query_name) * 0.7 AS match_score
    FROM symbols s
    JOIN files f ON s.file_id = f.id
    WHERE similarity(s.name, query_name) > 0.3
      AND s.name != query_name
      AND (repo_id IS NULL OR f.repository_id = repo_id)
      AND (kind_filter IS NULL OR s.kind = kind_filter)
)
SELECT *
FROM matches
ORDER BY match_score DESC, match_type, name
LIMIT limit_count;
$$;

-- Find references to a symbol (including calls, imports, inherits)
CREATE OR REPLACE FUNCTION find_all_references(
    target_symbol_id BIGINT,
    repo_id BIGINT DEFAULT NULL,
    min_confidence REAL DEFAULT 0.0,
    limit_count INT DEFAULT 1000
)
RETURNS TABLE (
    reference_id BIGINT,
    source_symbol_id BIGINT,
    source_name TEXT,
    source_qualified_name TEXT,
    source_kind TEXT,
    source_file_path TEXT,
    source_start_line INT,
    source_end_line INT,
    edge_type TEXT,
    edge_confidence REAL,
    line INT,
    col INT
) LANGUAGE sql STABLE AS $$
SELECT
    e.id AS reference_id,
    src.id AS source_symbol_id,
    src.name AS source_name,
    src.qualified_name AS source_qualified_name,
    src.kind AS source_kind,
    sf.path AS source_file_path,
    src.start_line AS source_start_line,
    src.end_line AS source_end_line,
    e.edge_type,
    e.confidence AS edge_confidence,
    e.line,
    e.col
FROM edges e
JOIN symbols src ON e.src_symbol_id = src.id
JOIN files sf ON src.file_id = sf.id
WHERE e.dst_symbol_id = target_symbol_id
  AND e.confidence >= min_confidence
  AND (repo_id IS NULL OR sf.repository_id = repo_id)
ORDER BY e.confidence DESC, src.qualified_name
LIMIT limit_count;
$$;

-- Find callers with depth limit and confidence filter
CREATE OR REPLACE FUNCTION find_callers_depth(
    target_symbol_id BIGINT,
    depth_limit INT DEFAULT 1,
    min_confidence REAL DEFAULT 0.0,
    limit_count INT DEFAULT 1000
)
RETURNS TABLE (
    caller_id BIGINT,
    caller_name TEXT,
    caller_qualified_name TEXT,
    caller_kind TEXT,
    caller_file_path TEXT,
    caller_start_line INT,
    caller_end_line INT,
    depth INT,
    edge_confidence REAL,
    call_line INT
) LANGUAGE sql STABLE AS $$
WITH RECURSIVE caller_graph AS (
    -- Direct callers (depth 1)
    SELECT
        e.src_symbol_id AS caller_id,
        1 AS depth,
        ARRAY[e.dst_symbol_id] AS path,
        e.confidence AS edge_confidence,
        e.line AS call_line
    FROM edges e
    WHERE e.dst_symbol_id = target_symbol_id
      AND e.edge_type = 'calls'
      AND e.confidence >= min_confidence

    UNION ALL

    -- Indirect callers (depth > 1)
    SELECT
        e.src_symbol_id,
        cg.depth + 1,
        cg.path || e.dst_symbol_id,
        e.confidence,
        e.line
    FROM edges e
    JOIN caller_graph cg ON e.dst_symbol_id = cg.caller_id
    WHERE cg.depth < depth_limit
      AND e.edge_type = 'calls'
      AND e.confidence >= min_confidence
      AND NOT e.src_symbol_id = ANY(cg.path)
)
SELECT
    cg.caller_id,
    s.name AS caller_name,
    s.qualified_name AS caller_qualified_name,
    s.kind AS caller_kind,
    f.path AS caller_file_path,
    s.start_line AS caller_start_line,
    s.end_line AS caller_end_line,
    cg.depth,
    cg.edge_confidence,
    cg.call_line
FROM caller_graph cg
JOIN symbols s ON cg.caller_id = s.id
JOIN files f ON s.file_id = f.id
ORDER BY cg.depth, s.qualified_name
LIMIT limit_count;
$$;

-- Find callees with depth limit and confidence filter
CREATE OR REPLACE FUNCTION find_callees_depth(
    source_symbol_id BIGINT,
    depth_limit INT DEFAULT 1,
    min_confidence REAL DEFAULT 0.0,
    limit_count INT DEFAULT 1000
)
RETURNS TABLE (
    callee_id BIGINT,
    callee_name TEXT,
    callee_qualified_name TEXT,
    callee_kind TEXT,
    callee_file_path TEXT,
    callee_start_line INT,
    callee_end_line INT,
    depth INT,
    edge_confidence REAL,
    call_line INT
) LANGUAGE sql STABLE AS $$
WITH RECURSIVE callee_graph AS (
    -- Direct callees (depth 1)
    SELECT
        e.dst_symbol_id AS callee_id,
        1 AS depth,
        ARRAY[e.src_symbol_id] AS path,
        e.confidence AS edge_confidence,
        e.line AS call_line
    FROM edges e
    WHERE e.src_symbol_id = source_symbol_id
      AND e.edge_type = 'calls'
      AND e.dst_symbol_id IS NOT NULL
      AND e.confidence >= min_confidence

    UNION ALL

    -- Indirect callees (depth > 1)
    SELECT
        e.dst_symbol_id,
        cg.depth + 1,
        cg.path || e.src_symbol_id,
        e.confidence,
        e.line
    FROM edges e
    JOIN callee_graph cg ON e.src_symbol_id = cg.callee_id
    WHERE cg.depth < depth_limit
      AND e.edge_type = 'calls'
      AND e.dst_symbol_id IS NOT NULL
      AND e.confidence >= min_confidence
      AND NOT e.dst_symbol_id = ANY(cg.path)
)
SELECT
    cg.callee_id,
    s.name AS callee_name,
    s.qualified_name AS callee_qualified_name,
    s.kind AS callee_kind,
    f.path AS callee_file_path,
    s.start_line AS callee_start_line,
    s.end_line AS callee_end_line,
    cg.depth,
    cg.edge_confidence,
    cg.call_line
FROM callee_graph cg
JOIN symbols s ON cg.callee_id = s.id
JOIN files f ON s.file_id = f.id
ORDER BY cg.depth, s.qualified_name
LIMIT limit_count;
$$;