# Benchmark Suite

This directory contains the evaluation framework for comparing CodeCompass search methods.

## Quick Start

```bash
# Run full benchmark (requires Flask repo indexed)
make bench

# Run with LLM agent baseline (requires OPENROUTER_API_KEY)
make bench-llm

# Run index benchmark only
make bench-index

# Run MCP benchmark: LLM agent WITH CodeCompass tools vs WITHOUT (grep + read_file)
make bench-mcp

# Run MCP benchmark with real LLM (requires OPENROUTER_API_KEY)
make bench-mcp-llm
```

## Questions Format

Questions are defined in YAML files under `bench/questions/`. Each question has:

```yaml
- id: "unique-id"              # Required: unique identifier
  repo: "repository-name"      # Required: must match indexed repo name
  type: "definition"           # Required: one of [definition, callers, callees, refs, impact, natural_language]
  question: "natural language" # Required: the query to test
  expected:                    # Required: list of expected results
    - "qualified_name"         #   - symbol qualified name (e.g., "Flask.__init__")
    - "file.py:100-200"        #   - or file:line range
  tags: ["semantic", "class"]  # Optional: for filtering/analysis
```

### Question Types

| Type | Description | Example |
|------|-------------|---------|
| `definition` | Find a symbol by name/description | "function that parses JSON" |
| `callers` | Find functions calling a target | "what calls the auth middleware" |
| `callees` | Find functions called by a target | "what does process_request call" |
| `refs` | Find all references to a symbol | "where is Config class used" |
| `impact` | Transitive impact analysis | "impact of changing User model" |
| `natural_language` | Conceptual/semantic queries | "how does Flask handle CORS" |

### Expected Results Format

Expected results can be:
- **Qualified names**: `"Flask.__init__"`, `"RequestContext"`
- **File:line ranges**: `"flask/app.py:400-500"`, `"flask/ctx.py:200-350"`

Both are matched against search results (name, qualified_name, file_path).

## Search Modes Evaluated

1. **grep** - ripgrep baseline (exact string matching)
2. **bm25** - BM25 keyword search
3. **vector** - Vector similarity search (embeddings)
4. **graph** - Structural graph traversal (callers/callees/refs/impact)
5. **hybrid** - BM25 + Vector with RRF (k=60)
6. **hybrid_graph** - Hybrid + graph expansion (depth=2)
7. **llm_agent** - LLM agent with grep/read tools (optional, requires API key)

## Metrics

| Metric | Description |
|--------|-------------|
| **Recall@K** | Fraction of expected results found in top-K |
| **MRR** | Mean Reciprocal Rank (1/rank of first relevant result) |
| **Exact Match** | Top-1 result exactly matches expected (structural only) |
| **Avg/P50/P95 Latency** | Query latency percentiles |
| **Tokens/Query** | LLM token usage (LLM modes only) |

## Adding Questions

1. Create a new YAML file in `bench/questions/` (e.g., `myrepo_questions.yaml`)
2. Follow the format above
3. Run: `make bench REPO=myrepo QUESTIONS=bench/questions/myrepo_questions.yaml`

Or pass directly:
```bash
uv run python -m bench.run myrepo bench/questions/myrepo_questions.yaml
```

## Running Benchmarks

### Prerequisites
- Repository indexed: `codecompass index /path/to/repo --name myrepo`
- Database running: `make up && make migrate`
- For LLM: Set `OPENROUTER_API_KEY` in `.env` and `llm_provider=openrouter`

### Commands

```bash
# Full benchmark (search + index + incremental)
make bench

# Just search comparison
uv run python -m bench.run flask

# With custom questions
uv run python -m bench.run flask bench/questions/my_questions.yaml

# With LLM agent (requires API key)
make bench-llm

# Index benchmark only
make bench-index

# Deterministic run (fixed seed)
uv run python -m bench.run flask --seed 42
```

## Output Files

Results are saved to `bench/results/` with timestamp:
- `benchmark_<repo>_<timestamp>.json` - Full detailed results
- `benchmark_<repo>_<timestamp>.csv` - Metrics table for analysis
- `benchmark_<repo>_<timestamp>.md` - Human-readable markdown table

### Example Markdown Output

```markdown
# Benchmark Results: flask

**Timestamp:** 20261122_143022

## Search Mode Comparison

| Mode | Avg Latency (ms) | P50 (ms) | P95 (ms) | Total Results | Recall@5 | Recall@10 | Recall@20 | MRR | Exact Match | Tokens/Query |
|------|------------------|----------|----------|---------------|----------|-----------|-----------|-----|-------------|--------------|
| grep | 45.2 | 42.1 | 67.8 | 1250 | 0.450 | 0.550 | 0.650 | 0.320 | 0.200 | 0.0 |
| bm25 | 12.3 | 11.5 | 18.2 | 200 | 0.700 | 0.800 | 0.850 | 0.550 | 0.400 | 0.0 |
| vector | 28.7 | 27.1 | 42.3 | 200 | 0.600 | 0.750 | 0.800 | 0.480 | 0.300 | 0.0 |
| graph | 8.2 | 7.5 | 12.1 | 85 | 0.850 | 0.900 | 0.900 | 0.720 | 0.700 | 0.0 |
| hybrid | 35.1 | 33.2 | 51.4 | 200 | 0.750 | 0.850 | 0.900 | 0.620 | 0.500 | 0.0 |
| hybrid_graph | 42.8 | 40.1 | 65.7 | 200 | 0.800 | 0.880 | 0.920 | 0.680 | 0.600 | 0.0 |

## Index Benchmark

- **Repository:** flask
- **Total LOC:** 45,230
- **Total Files:** 156
- **Full Index Time:** 12.45s
- **Peak Memory:** 245.3 MB
- **Throughput:** 3,633 LOC/s
- **Seconds per 100k LOC:** 27.52s

## Incremental Update Benchmark

| Commit | Files Added | Files Modified | Files Deleted | Files Renamed | Symbols | Edges | Embeddings | Edges Reresolved | Elapsed (s) |
|--------|-------------|----------------|---------------|---------------|---------|-------|------------|------------------|-------------|
| 1 | 0 | 3 | 0 | 0 | 12 | 8 | 12 | 5 | 1.23 |
| 2 | 1 | 2 | 0 | 0 | 8 | 5 | 8 | 3 | 0.89 |
...
```

## Interpreting Results

- **Recall@5/10/20**: Higher = better coverage of relevant results
- **MRR**: Higher = relevant results ranked higher (1.0 = perfect)
- **Exact Match**: For structural questions, top-1 is exactly correct
- **Latency**: Lower = faster; P95 shows tail latency
- **Graph** excels at structural queries (callers/callees/impact)
- **Hybrid+Graph** typically best overall for mixed workloads
- **LLM Agent**: High latency/token cost, but may handle complex NL better

## Reproducibility

- Fixed seed (`--seed 42`) ensures deterministic runs
- LLM calls are cached in `.cache/llm/` (disk cache keyed by prompt hash)
- All search modes use deterministic algorithms
- Set `PYTHONHASHSEED=42` for full determinism if needed

---

# MCP Server Integration

## Connecting CodeCompass MCP Server to Claude Code

The CodeCompass MCP server exposes 5 tools over stdio for code intelligence:

| Tool | Description |
|------|-------------|
| `find_definition` | Find symbol definitions by exact name match |
| `find_callers` | Find transitive callers of a symbol |
| `find_callees` | Find transitive callees of a symbol |
| `impact_analysis` | Transitive impact (callers + callees) |
| `search_code` | Hybrid search (BM25 + Vector + Graph) |

### Installation

```bash
# Ensure you have Python 3.10+ and the CodeCompass environment
cd C:\projects\CodeCompass
# Use the Python 3.11 environment with mcp installed
C:\Users\DELL\miniconda3\envs\codecompass310\python.exe -m pip install -e .
```

### Register with Claude Code

Add to your Claude Code settings (or run the command below):

```bash
claude mcp add codecompass "C:\Users\DELL\miniconda3\envs\codecompass310\python.exe" -m codecompass.mcp_server
```

Or manually add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "codecompass": {
      "command": "C:\\Users\\DELL\\miniconda3\\envs\\codecompass310\\python.exe",
      "args": ["-m", "codecompass.mcp_server"],
      "env": {
        "DATABASE_URL": "postgresql://codecompass:codecompass@localhost:5432/codecompass"
      }
    }
  }
}
```

### Prerequisites

1. **Database running**: `make up && make migrate`
2. **Repository indexed**: `codecompass index /path/to/flask --name flask`
3. **Python 3.10+** with MCP SDK installed

### Example Session with Claude Code

Once connected, you can ask Claude Code questions like:

**Example 1: Find a definition**
```
User: Find the Flask application factory function
Claude: [calls find_definition with name="create_app"]
Result: Found Flask.create_app in flask/app.py:1200-1250
```

**Example 2: Impact analysis**
```
User: What would break if I change the SessionInterface class?
Claude: [calls impact_analysis with symbol_name="SessionInterface"]
Result: Found 15 affected functions across 8 files including flask/sessions.py, flask/app.py
```

**Example 3: Hybrid search**
```
User: How does Flask handle CORS headers?
Claude: [calls search_code with query="CORS headers Flask"]
Result: Found relevant code in flask/cors.py and flask/app.py with after_request handlers
```

---

## MCP Benchmark: With vs Without Tools

This benchmark compares an LLM agent that HAS access to CodeCompass MCP tools versus one that ONLY has grep and read_file.

### Running the Benchmark

```bash
# Using fake LLM (deterministic, no API key needed)
make bench-mcp

# Using real LLM via OpenRouter (requires OPENROUTER_API_KEY in .env)
make bench-mcp-llm
```

### What It Measures

| Metric | Description |
|--------|-------------|
| **Accuracy** | Fraction of questions answered correctly |
| **Avg Tokens** | Total tokens used per question |
| **Avg Steps** | Tool calls per question |
| **Latency** | Avg/P50/P95 response time |
| **Wins/Losses/Ties** | Head-to-head per question |

### Output Files

Results saved to `bench/results/`:
- `mcp_benchmark_<repo>_<timestamp>.json` - Full details
- `mcp_benchmark_<repo>_<timestamp>.csv` - Metrics table
- `mcp_benchmark_<repo>_<timestamp>.md` - Human-readable report

### Example Markdown Output

```markdown
# MCP Benchmark: flask

**Timestamp:** 20261122_143022

Comparison: LLM Agent WITH CodeCompass MCP tools vs WITHOUT (grep + read_file only)

## Summary

| Agent Type | Accuracy | Avg Tokens | Avg Steps | Avg Latency (ms) | P50 (ms) | P95 (ms) | Wins | Losses | Ties |
|------------|----------|------------|-----------|------------------|----------|----------|------|--------|------|
| without_tools | 40.0% | 1,250 | 8.2 | 1,450 | 1,200 | 2,100 | 2 | 6 | 2 |
| with_tools | 80.0% | 890 | 4.1 | 680 | 520 | 1,100 | 6 | 2 | 2 |

## Per-Question Results

| Question | Type | Plain Correct | CC Correct | Plain Tokens | CC Tokens | Plain Steps | CC Steps | Winner |
|----------|------|---------------|------------|--------------|-----------|-------------|----------|--------|
| flask-def-001 | definition | False | True | 1,100 | 650 | 7 | 3 | CodeCompass |
| flask-callers-001 | callers | True | True | 950 | 720 | 6 | 2 | Tie |
| flask-impact-001 | impact | False | True | 2,100 | 980 | 12 | 4 | CodeCompass |
| flask-nl-001 | natural_language | True | False | 890 | 1,200 | 5 | 6 | Plain |

## Tool Call Analysis

| Agent Type | Avg Tool Calls/Question | Tool Breakdown |
|------------|-------------------------|----------------|
| without_tools | 7.2 | grep: 42, read_file: 30 |
| with_tools | 3.1 | search_code: 18, find_callers: 8, impact_analysis: 4 |
```

### Interpreting Results

- **CodeCompass wins** on structural questions (callers, callees, impact, refs) - graph traversal is precise
- **Plain agent may win** on simple keyword searches where grep is faster
- **Token savings** with CodeCompass: ~30-50% fewer tokens due to targeted search vs broad grep
- **Step reduction**: CodeCompass agent takes ~50% fewer steps (direct graph queries vs iterative grep/read)
- **Honest reporting**: Questions where plain agent wins are clearly marked

### Adding Custom Questions

Same format as search benchmark - add YAML to `bench/questions/` with `type` field matching the tool capabilities.