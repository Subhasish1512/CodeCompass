# CodeCompass Makefile

.PHONY: help up down lint migrate psql status reset bench bench-index bench-llm index-repo

help:
	@echo "CodeCompass - Code Intelligence Engine"
	@echo ""
	@echo "Database:"
	@echo "  up            Start PostgreSQL with pgvector"
	@echo "  down          Stop PostgreSQL"
	@echo "  psql          Open psql shell"
	@echo ""
	@echo "Migrations:"
	@echo "  migrate       Run pending migrations (idempotent)"
	@echo "  status        Show migration status"
	@echo "  reset         Drop all tables and re-run migrations (DANGEROUS)"
	@echo ""
	@echo "Indexing:"
	@echo "  index-repo    Index a repository (usage: make index-repo REPO=path NAME=name)"
	@echo ""
	@echo "Quality:"
	@echo "  lint          Run ruff linter"
	@echo "  format        Format code with ruff"
	@echo ""
	@echo "Install:"
	@echo "  install       Install with uv"
	@echo ""
	@echo "Benchmarks:"
	@echo "  bench         Run full benchmark suite (requires indexed repo)"
	@echo "  bench-index   Run index benchmark only"
	@echo "  bench-llm     Run benchmark with LLM agent baseline"

# ============================================================================
# DATABASE
# ============================================================================

up:
	docker compose up -d
	@echo "Waiting for database to be ready..."
	@until docker compose exec -T postgres pg_isready -U codecompass -d codecompass >/dev/null 2>&1; do sleep 1; done
	@echo "Database ready"

down:
	docker compose down

psql:
	docker compose exec postgres psql -U codecompass -d codecompass

# ============================================================================
# MIGRATIONS
# ============================================================================

migrate:
	uv run python -m codecompass.db.migrate migrate

status:
	uv run python -m codecompass.db.migrate status

reset:
	@echo "This will DROP all tables. Continue? (y/N)" && read ans && [ "$$ans" = "y" ]
	uv run python -m codecompass.db.migrate reset

# ============================================================================
# QUALITY
# ============================================================================

lint:
	uv run ruff check src/

format:
	uv run ruff format src/

# ============================================================================
# INDEXING
# ============================================================================

# Usage: make index-repo REPO=samples/demo_repo NAME=demo
index-repo:
	uv run codecompass index $(REPO) --name $(NAME) --full

# ============================================================================
# INSTALL
# ============================================================================

install:
	uv sync --dev

# ============================================================================
# DEVELOPMENT
# ============================================================================

dev: up migrate
	@echo "Ready for development"

serve:
	uv run uvicorn codecompass.api.main:app --host 0.0.0.0 --port 8000 --reload

test-migrate: up
	@echo "=== First migration run ==="
	uv run python -m codecompass.db.migrate migrate
	@echo ""
	@echo "=== Second migration run (should show 'already applied') ==="
	uv run python -m codecompass.db.migrate migrate

# ============================================================================
# BENCHMARKS
# ============================================================================

# Run full benchmark suite with fixed seed for reproducibility
bench:
	uv run python -m bench.run flask --seed 42

# Run index benchmark only
bench-index:
	uv run python -c "from bench.run import run_index_benchmark; r = run_index_benchmark('flask'); print(f'Full index: {r.full_index_seconds}s, {r.loc_per_second:,.0f} LOC/s, Peak: {r.peak_memory_mb:.0f} MB')"

# Run benchmark with LLM agent baseline (requires API key)
bench-llm:
	uv run python -m bench.run flask --llm --seed 42

# Run all benchmarks (search + index + incremental)
bench-all:
	uv run python -m bench.run flask --seed 42

# Run MCP benchmark: agent WITH CodeCompass tools vs WITHOUT (grep + read_file)
bench-mcp:
	uv run python -m bench.mcp_bench flask --seed 42

# Run MCP benchmark with real LLM (requires OPENROUTER_API_KEY)
bench-mcp-llm:
	uv run python -m bench.mcp_bench flask --llm --seed 42