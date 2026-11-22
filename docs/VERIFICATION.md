# CodeCompass Verification Guide

This document documents the verification of all search modes on two repositories:
- **demo_repo**: 7 files, 115 symbols, 289 edges (small sample)
- **Flask**: 83 files, 1,123 symbols, 3,939 edges (real-world)

## Search Mode Comparison

### Query: "create order" (demo_repo)

| Mode | Results | Characteristics |
|------|---------|-----------------|
| **BM25** | 6 | Exact token matching: `create_order`, `_create_order`, `create_order_endpoint`, `async_create_order`, `get_order_handler`, `process_user_order` |
| **Vector** | 20 | Semantic similarity: Includes `Order`, `OrderItem`, `OrderService`, `OrderHandler`, `process_order`, `format_order` - related concepts not containing exact tokens |
| **Hybrid** | 20 | RRF fusion: Top results blend both signals - `_create_order` (rank 1 vector, 2 BM25), `create_order` (1 BM25, 2 vector), `async_create_order` (4 BM25, 3 vector) |
| **Hybrid+Graph** | 20 | Same as hybrid for this query (graph results already in hybrid top-K) |

**Key Finding**: BM25 favors exact function names; Vector favors semantic concepts (class names like `Order`, `OrderService`); Hybrid balances both via RRF (k=60).

---

### Query: "create_order_endpoint" (demo_repo)

| Mode | Results | Characteristics |
|------|---------|-----------------|
| **BM25** | 1 | Only exact match: `create_order_endpoint` |
| **Vector** | 10 | Semantic neighbors: `get_order_endpoint`, `_create_order`, `_order_handler`, `async_create_order` |
| **Hybrid** | 10 | RRF ranks exact match #1, then semantic neighbors |
| **Hybrid+Graph** | 10 | **Adds graph expansion**: `get_user_handler` (via graph:callee - called by endpoint), `main` (via graph:caller - calls endpoint) |

**Key Finding**: Graph expansion adds callers/callees of top results that weren't in pure hybrid results, clearly labeled `via graph:caller` / `via graph:callee`.

---

### Query: "full_dispatch_request" (Flask)

| Mode | Results | Characteristics |
|------|---------|-----------------|
| **BM25** | 3 | Exact: `full_dispatch_request`, `dispatch_request`, `test_request_context` |
| **Vector** | 20 | Many `dispatch_request` from test files (high similarity) |
| **Hybrid** | 20 | RRF promotes exact match #1, test variants lower |
| **Hybrid+Graph** | 20 | **Adds 7 graph results**: `preprocess_request`, `finalize_request`, `handle_user_exception`, `wsgi_app` (callers), `raise_routing_exception`, `make_default_options_response`, `ensure_sync` (callees) |

**Key Finding**: Graph expansion surfaces the Flask request lifecycle methods that call/are called by `full_dispatch_request`.

---

### Query: "Flask" (Flask)

| Mode | Top Results | Characteristics |
|------|-------------|-----------------|
| **BM25** | `Flask`, `FlaskCliRunner`, `FlaskClient`, `FlaskGroup`, `test_client` | Exact class/function names containing "Flask" |
| **Vector** | `flask_app`, test functions, `Flask`, `App`, `__init__` | Semantic: variables named `flask_app`, test patterns, base `App` class |
| **Hybrid** | Blend of both | `flask_app` (vector #1), `Flask` (BM25 #1, vector #20) |
| **Hybrid+Graph** | Similar to hybrid | Graph results already covered |

---

## Score Breakdown (RRF Explanation)

For query "create_order_endpoint" (demo_repo):

| Result | BM25 Rank | BM25 Score | Vector Rank | Vector Score | Fused Score |
|--------|-----------|------------|-------------|--------------|-------------|
| create_order_endpoint | 1 | 0.7317 | 1 | 0.8775 | **0.0328** |
| get_order_endpoint | - | - | 2 | 0.8137 | 0.0161 |
| _create_order | - | - | 3 | 0.8075 | 0.0159 |
| _order_handler | - | - | 4 | 0.7992 | 0.0156 |

Formula: `fused_score = 1/(60 + bm25_rank) + 1/(60 + vector_rank)` (RRF with k=60)

---

## Graph Expansion Details

The `_expand_with_graph` function in `src/codecompass/search/hybrid.py`:

1. Takes top 5 hybrid results
2. For each, fetches direct callers (`get_callers`) and callees (`get_callees`)
3. Adds them with `search_type = "via graph:caller"` or `"via graph:callee"`
4. Scores them at `parent_fused_score * 0.5`
5. Merges and re-sorts all results by `fused_score` descending
6. Returns top `limit` results

**Labels used**:
- `hybrid` - from RRF fusion
- `via graph:caller` - function that calls the result
- `via graph:callee` - function called by the result

---

## CLI Commands Tested

```bash
# Index repositories
codecompass index /path/to/repo --name demo_repo --full
codecompass index /tmp/flask --name flask --full

# Generate embeddings
codecompass embed demo_repo
codecompass embed flask

# Search modes
codecompass search "query" --repo demo_repo --mode bm25
codecompass search "query" --repo demo_repo --mode vector
codecompass search "query" --repo demo_repo --mode hybrid
codecompass search "query" --repo demo_repo --mode hybrid+graph

# Compare all modes with explanations
codecompass search "query" --repo demo_repo --compare --explain --limit 10

# Graph traversal (separate commands)
codecompass callers <symbol_id> --repo demo_repo --depth 10
codecompass callees <symbol_id> --repo demo_repo --depth 10
codecompass impact <symbol_id> --repo demo_repo --depth 10
codecompass refs <symbol_id> --repo demo_repo
codecompass context <symbol_id> --repo demo_repo --radius 2
codecompass impls <symbol_id> --repo demo_repo
codecompass find "name" --repo demo_repo
```

---

## Verification Summary

✅ **All four search modes operational**:
- BM25: PostgreSQL tsvector/tsquery with code-aware tokenizer
- Vector: pgvector cosine similarity (HNSW index)
- Hybrid: Reciprocal Rank Fusion (k=60)
- Hybrid+Graph: RRF + call graph expansion

✅ **Graph expansion verified**:
- Adds callers/callees of top-5 hybrid results
- Labeled clearly with `via graph:caller` / `via graph:callee`
- Scored at 0.5x parent fused score
- Mixed with hybrid results by score

✅ **Real-world testing**:
- demo_repo (7 files): Fast, precise results
- Flask (83 files, 1123 symbols): Scales well, 407s indexing, sub-second search

✅ **Score explanations work**: `--explain` flag shows BM25 rank, vector rank, fused score, RRF k

---

## Performance Notes

| Operation | demo_repo | Flask |
|-----------|-----------|-------|
| Indexing | ~1.5s | ~407s |
| Files | 7 | 83 |
| Symbols | 115 | 1,123 |
| Edges | 289 | 3,939 |
| Embeddings | 115 | 1,123 |
| Search (any mode) | <100ms | <200ms |

The HNSW index on `symbol_embeddings.embedding` provides fast vector search at scale.