"""
Typer CLI for CodeCompass.

Commands for indexing, searching, and graph traversal.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from codecompass.config import get_settings
from codecompass.db import close_db, get_db, init_db
from codecompass.db.repository import (
    create_repository,
    get_repository_by_name,
    list_repositories,
)
from codecompass.db.search import (
    hybrid_search_with_explanation,
    vector_search_with_ranks,
)
from codecompass.graph.traversal import (
    find_definition,
    find_implementations,
    get_callees,
    get_callers,
    get_references,
    get_symbol_context,
    get_transitive_impact,
)
from codecompass.indexing.embeddings import embed_chunks_cached, embed_symbols_cached
from codecompass.indexing.incremental import update_repository
from codecompass.indexing.pipeline import index_repository
from codecompass.llm.pipeline import answer_question
from codecompass.parsing import PythonParser
from codecompass.parsing.dataclasses import EdgeKind
from codecompass.search.hybrid import (
    bm25_search_mode,
    multi_stage_search,
)

app = typer.Typer(
    name="codecompass",
    help="CodeCompass - Code intelligence engine",
    add_completion=False,
    no_args_is_help=True
)
console = Console()


def _init_db():
    """Initialize database connection."""
    init_db()


def _close_db():
    """Close database connection."""
    close_db()


# ============================================================================
# REPOSITORY COMMANDS
# ============================================================================

@app.command("repo-add")
def repo_add(
    name: str = typer.Argument(..., help="Repository name"),
    path: str = typer.Argument(..., help="Absolute path to repository root"),
    url: str = typer.Option("", help="Repository URL (optional)"),
):
    """Register a new repository."""
    _init_db()
    try:
        repo_id = create_repository(name, url, path)
        console.print(f"[green]Created repository[/green] [bold]{name}[/bold] (ID: {repo_id})")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    finally:
        _close_db()


@app.command("repo-list")
def repo_list():
    """List all registered repositories."""
    _init_db()
    try:
        repos = list_repositories()
        if not repos:
            console.print("[yellow]No repositories registered[/yellow]")
            return

        table = Table(title="Repositories")
        table.add_column("ID", style="cyan")
        table.add_column("Name", style="bold")
        table.add_column("URL")
        table.add_column("Root Path")
        table.add_column("Created")

        for repo in repos:
            table.add_row(
                str(repo["id"]),
                repo["name"],
                repo["url"] or "-",
                repo["root_path"],
                str(repo["created_at"])
            )
        console.print(table)
    finally:
        _close_db()


@app.command("repo-index")
def repo_index(
    name: str = typer.Argument(..., help="Repository name"),
    full: bool = typer.Option(False, "--full", help="Force full re-index (ignore incremental)"),
):
    """Index a repository (incremental by default)."""
    _init_db()
    try:
        repo = get_repository_by_name(name)
        if not repo:
            console.print(f"[red]Repository not found:[/red] {name}")
            sys.exit(1)

        console.print(f"Indexing [bold]{name}[/bold]...")
        stats = index_repository(
            name=repo["name"],
            root_path=repo["root_path"],
            url=repo["url"],
            incremental=not full
        )

        console.print(Panel.fit(
            f"[green]Files processed:[/green] {stats.files_processed}\n"
            f"[green]Symbols created:[/green] {stats.symbols_created}\n"
            f"[green]Edges created:[/green] {stats.edges_created}\n"
            f"[green]Embeddings generated:[/green] {stats.embeddings_generated}\n"
            f"[green]Chunks created:[/green] {stats.chunks_created}\n"
            f"[yellow]Files skipped:[/yellow] {stats.files_skipped}\n"
            f"[green]Resolved edges:[/green] {stats.resolved_edges}\n"
            f"[yellow]Unresolved edges:[/yellow] {stats.unresolved_edges}\n"
            f"[cyan]Resolved ratio:[/cyan] {stats.resolved_ratio:.1%}\n"
            f"[blue]Elapsed:[/blue] {stats.elapsed_seconds:.1f}s\n"
            f"[blue]LOC/sec:[/blue] {stats.loc_per_sec:.0f}\n"
            f"[red]Errors:[/red] {len(stats.errors)}",
            title="Index Complete"
        ))

        if stats.errors:
            for err in stats.errors[:5]:
                console.print(f"[red]  - {err}[/red]")
            if len(stats.errors) > 5:
                console.print(f"[red]  ... and {len(stats.errors) - 5} more[/red]")

    finally:
        _close_db()


@app.command("update")
def update_cmd(
    name: str = typer.Argument(..., help="Repository name"),
    progress: bool = typer.Option(False, "--progress/--no-progress", help="Show progress bar"),
):
    """Incremental update: diff last_indexed_commit..HEAD and apply minimal changes."""
    _init_db()
    try:
        repo = get_repository_by_name(name)
        if not repo:
            console.print(f"[red]Repository not found:[/red] {name}")
            sys.exit(1)

        console.print(f"Updating [bold]{name}[/bold]...")
        from codecompass.indexing.incremental import HAS_PYGIT2
        if not HAS_PYGIT2:
            console.print("[red]pygit2 not installed. Install with: pip install pygit2[/red]")
            sys.exit(1)

        stats = update_repository(name=repo["name"], show_progress=progress)

        console.print(Panel.fit(
            f"[green]Files added:[/green] {stats.files_added}\n"
            f"[green]Files modified:[/green] {stats.files_modified}\n"
            f"[red]Files deleted:[/red] {stats.files_deleted}\n"
            f"[yellow]Files renamed:[/yellow] {stats.files_renamed}\n"
            f"[green]Symbols created:[/green] {stats.symbols_created}\n"
            f"[green]Edges created:[/green] {stats.edges_created}\n"
            f"[green]Embeddings generated:[/green] {stats.embeddings_generated}\n"
            f"[green]Edges re-resolved:[/green] {stats.edges_reresolved}\n"
            f"[blue]Elapsed:[/blue] {stats.elapsed_seconds:.1f}s",
            title="Update Complete"
        ))

        if stats.errors:
            for err in stats.errors[:5]:
                console.print(f"[red]  - {err}[/red]")
            if len(stats.errors) > 5:
                console.print(f"[red]  ... and {len(stats.errors) - 5} more[/red]")

    finally:
        _close_db()


@app.command("index")
def index_cmd(
    path: str = typer.Argument(..., help="Path to repository root"),
    name: str = typer.Option(None, "--name", "-n", help="Repository name (defaults to directory name)"),
    url: str = typer.Option("", "--url", "-u", help="Repository URL"),
    full: bool = typer.Option(False, "--full", help="Force full re-index"),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show progress bar"),
):
    """Index a repository directly from a path (creates repo if needed)."""
    _init_db()
    try:
        repo_path = Path(path).resolve()
        if not repo_path.exists():
            console.print(f"[red]Path not found:[/red] {path}")
            sys.exit(1)

        repo_name = name or repo_path.name
        console.print(f"Indexing [bold]{repo_name}[/bold] from [cyan]{repo_path}[/cyan]...")

        stats = index_repository(
            name=repo_name,
            root_path=str(repo_path),
            url=url,
            incremental=not full,
            show_progress=progress
        )

        console.print(Panel.fit(
            f"[green]Files processed:[/green] {stats.files_processed}\n"
            f"[green]Symbols created:[/green] {stats.symbols_created}\n"
            f"[green]Edges created:[/green] {stats.edges_created}\n"
            f"[green]Embeddings generated:[/green] {stats.embeddings_generated}\n"
            f"[green]Chunks created:[/green] {stats.chunks_created}\n"
            f"[yellow]Files skipped:[/yellow] {stats.files_skipped}\n"
            f"[green]Resolved edges:[/green] {stats.resolved_edges}\n"
            f"[yellow]Unresolved edges:[/yellow] {stats.unresolved_edges}\n"
            f"[cyan]Resolved ratio:[/cyan] {stats.resolved_ratio:.1%}\n"
            f"[blue]Elapsed:[/blue] {stats.elapsed_seconds:.1f}s\n"
            f"[blue]LOC/sec:[/blue] {stats.loc_per_sec:.0f}\n"
            f"[red]Errors:[/red] {len(stats.errors)}",
            title="Index Complete"
        ))

        if stats.walk_stats:
            ws = stats.walk_stats
            console.print(Panel.fit(
                f"[bold]Walk Statistics[/bold]\n"
                f"Total files: {ws.total_files}\n"
                f"Indexable: {ws.indexable_files}\n"
                f"Skipped (gitignore): {ws.skipped_ignored}\n"
                f"Skipped (excluded dirs): {ws.skipped_excluded_dir}\n"
                f"Skipped (excluded ext): {ws.skipped_excluded_ext}\n"
                f"Skipped (unsupported): {ws.skipped_unsupported}\n"
                f"Skipped (binary): {ws.skipped_binary}\n"
                f"Skipped (too large): {ws.skipped_too_large}",
                title="File Discovery"
            ))

        if stats.errors:
            for err in stats.errors[:5]:
                console.print(f"[red]  - {err}[/red]")
            if len(stats.errors) > 5:
                console.print(f"[red]  ... and {len(stats.errors) - 5} more[/red]")

    finally:
        _close_db()


# ============================================================================
# EMBED COMMAND
# ============================================================================

@app.command("embed")
def embed_cmd(
    repo: str = typer.Argument(..., help="Repository name"),
    batch_size: int = typer.Option(32, "--batch-size", "-b", help="Batch size for embedding generation"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-embedding even if cached"),
):
    """Generate embeddings for all symbols and chunks in a repository."""
    _init_db()
    try:
        r = get_repository_by_name(repo)
        if not r:
            console.print(f"[red]Repository not found:[/red] {repo}")
            sys.exit(1)
        repo_id = r["id"]

        db = get_db()
        settings = get_settings()

        # Get all symbols without embeddings
        console.print(f"[bold]Embedding symbols for {repo}...[/bold]")
        symbols = db.execute("""
            SELECT s.* FROM v_symbols_with_file s
            LEFT JOIN symbol_embeddings se ON s.id = se.symbol_id
            WHERE s.repo_id = %s AND se.symbol_id IS NULL
            ORDER BY s.id
        """, (repo_id,))

        if not symbols:
            console.print("[green]All symbols already have embeddings[/green]")
        else:
            console.print(f"Found {len(symbols)} symbols without embeddings")
            with console.status("[bold green]Generating embeddings..."):
                results = embed_symbols_cached(symbols)
                # Insert embeddings
                for symbol_id, embedding in results:
                    db.execute("""
                        INSERT INTO symbol_embeddings (symbol_id, embedding, model_name)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (symbol_id) DO UPDATE SET embedding = EXCLUDED.embedding, model_name = EXCLUDED.model_name
                    """, (symbol_id, embedding, settings.embedding_model))
            console.print(f"[green]Generated embeddings for {len(results)} symbols[/green]")

        # Get all chunks without embeddings
        console.print(f"[bold]Embedding chunks for {repo}...[/bold]")
        chunks = db.execute("""
            SELECT c.* FROM chunks c
            JOIN symbols s ON c.symbol_id = s.id
            JOIN files f ON s.file_id = f.id
            WHERE f.repository_id = %s AND c.embedding IS NULL
            ORDER BY c.id
        """, (repo_id,))

        if not chunks:
            console.print("[green]All chunks already have embeddings[/green]")
        else:
            console.print(f"Found {len(chunks)} chunks without embeddings")
            with console.status("[bold green]Generating embeddings..."):
                results = embed_chunks_cached(chunks)
                # Insert embeddings
                for chunk_id, embedding in results:
                    db.execute("""
                        UPDATE chunks SET embedding = %s WHERE id = %s
                    """, (embedding, chunk_id))
            console.print(f"[green]Generated embeddings for {len(results)} chunks[/green]")

        console.print(Panel.fit(
            f"[green]Embedding complete for {repo}[/green]",
            title="Done"
        ))

    finally:
        _close_db()


# ============================================================================
# SEARCH COMMANDS
# ============================================================================

@app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help="Search query"),
    repo: Optional[str] = typer.Option(None, "--repo", "-r", help="Repository name filter"),
    limit: int = typer.Option(20, "--limit", "-l", help="Maximum results"),
    mode: str = typer.Option("hybrid", "--mode", "-m", help="Search mode: bm25, vector, hybrid, hybrid+graph"),
    graph: bool = typer.Option(False, "--graph", "-g", help="Enable graph expansion (legacy, use --mode hybrid+graph)"),
    depth: int = typer.Option(2, "--depth", "-d", help="Graph expansion depth"),
    explain: bool = typer.Option(False, "--explain", "-e", help="Show score breakdown"),
    compare: bool = typer.Option(False, "--compare", "-c", help="Compare all search modes"),
):
    """Search for symbols using multiple modes: bm25, vector, hybrid, hybrid+graph."""
    _init_db()
    try:
        repo_id = None
        if repo:
            r = get_repository_by_name(repo)
            if not r:
                console.print(f"[red]Repository not found:[/red] {repo}")
                sys.exit(1)
            repo_id = r["id"]

        if compare:
            console.print(f"Comparing search modes for: [bold]{query}[/bold]")
            results = multi_stage_search(query, repo_id, limit, explain=explain)
            for mode_name, res in results.items():
                console.print(f"\n[bold cyan]{mode_name.upper()}[/bold cyan] ({len(res)} results)")
                _print_results(res[:5])
                if explain:
                    _print_explanations(res[:5])
            return

        # Handle legacy --graph flag

        if mode == "bm25":
            results = bm25_search_mode(query, repo_id, limit, explain)
        elif mode == "vector":
            from codecompass.indexing.embeddings import compute_embedding
            query_embedding = compute_embedding(query)
            results = vector_search_with_ranks(query_embedding, repo_id, limit)
            for r in results:
                r["search_type"] = "vector"
            if explain:
                for r in results:
                    r["score_explanation"] = {
                        "vector_rank": r.get("vector_rank"),
                        "vector_score": round(r.get("vector_score", 0.0), 4),
                    }
        elif mode == "hybrid":
            from codecompass.indexing.embeddings import compute_embedding
            query_embedding = compute_embedding(query)
            results = hybrid_search_with_explanation(
                query=query,
                query_embedding=query_embedding,
                repository_id=repo_id,
                limit=limit,
                bm25_limit=50,
                vector_limit=50,
                rrf_k=60
            )
        elif mode == "hybrid+graph":
            from codecompass.indexing.embeddings import compute_embedding
            query_embedding = compute_embedding(query)
            # Get more initial results to allow graph expansion to contribute
            initial_limit = limit * 2
            results = hybrid_search_with_explanation(
                query=query,
                query_embedding=query_embedding,
                repository_id=repo_id,
                limit=initial_limit,
                bm25_limit=50,
                vector_limit=50,
                rrf_k=60
            )
            # Apply graph expansion
            if results:
                from codecompass.search.hybrid import _expand_with_graph
                results = _expand_with_graph(results, repo_id, depth, limit)
        else:
            console.print(f"[red]Unknown mode:[/red] {mode}. Use: bm25, vector, hybrid, hybrid+graph")
            sys.exit(1)

        console.print(f"Found [bold]{len(results)}[/bold] results for: [bold]{query}[/bold] [dim](mode: {mode})[/dim]")
        _print_results(results)
        if explain:
            _print_explanations(results)

    finally:
        _close_db()


def _print_explanations(results: List[Dict], max_rows: int = 10):
    """Print score explanations for search results."""
    if not results:
        return

    console.print("\n[bold]Score Explanations:[/bold]")
    for r in results[:max_rows]:
        se = r.get("score_explanation", {})
        if not se:
            continue
        symbol_name = r.get("name") or r.get("qualified_name") or f"id:{r.get('id')}"
        console.print(f"  [bold]{symbol_name}[/bold]")
        for key, val in se.items():
            console.print(f"    {key}: {val}")


@app.command("find")
def find_cmd(
    name: str = typer.Argument(..., help="Symbol name"),
    repo: Optional[str] = typer.Option(None, "--repo", "-r", help="Repository name filter"),
    kind: Optional[str] = typer.Option(None, "--kind", "-k", help="Symbol kind filter"),
    ranked: bool = typer.Option(True, "--ranked/--exact", help="Use ranked disambiguation (default: true)"),
):
    """Find symbol definitions by name with disambiguation ranking."""
    _init_db()
    try:
        repo_id = None
        if repo:
            r = get_repository_by_name(repo)
            if not r:
                console.print(f"[red]Repository not found:[/red] {repo}")
                sys.exit(1)
            repo_id = r["id"]

        results = find_definition(name, repo_id, kind, use_ranking=ranked)
        console.print(f"Found [bold]{len(results)}[/bold] definitions for: [bold]{name}[/bold]")
        _print_results(results)

    finally:
        _close_db()


# ============================================================================
# PARSE COMMAND
# ============================================================================

@app.command("parse")
def parse_cmd(
    file_path: str = typer.Argument(..., help="Path to Python file to parse"),
    show_errors: bool = typer.Option(True, "--show-errors/--no-errors", help="Show syntax errors"),
):
    """Parse a Python file and display symbols and raw edges."""
    path = Path(file_path)
    if not path.exists():
        console.print(f"[red]File not found:[/red] {file_path}")
        sys.exit(1)

    parser = PythonParser()
    content = path.read_text(encoding="utf-8", errors="replace")
    result = parser.parse(content, path)

    # Print symbols table
    if result.symbols:
        table = Table(title=f"Symbols in {path.name} ({len(result.symbols)} found)")
        table.add_column("#", style="cyan", width=4)
        table.add_column("Name", style="bold", width=25)
        table.add_column("Qualified Name", width=35)
        table.add_column("Kind", style="magenta", width=10)
        table.add_column("Lines", style="green", width=12)
        table.add_column("Signature", width=40)
        table.add_column("Docstring", width=30)

        for idx, sym in enumerate(result.symbols):
            lines = f"{sym.start_line}-{sym.end_line}"
            sig = sym.signature or ""
            doc = (sym.docstring[:27] + "...") if sym.docstring and len(sym.docstring) > 30 else (sym.docstring or "")
            table.add_row(
                str(idx),
                sym.name,
                sym.qualified_name,
                sym.kind.value,
                lines,
                sig[:37] + "..." if len(sig) > 40 else sig,
                doc
            )
        console.print(table)
    else:
        console.print("[yellow]No symbols found[/yellow]")

    # Print edges table
    if result.edges:
        edge_table = Table(title=f"Raw Edges in {path.name} ({len(result.edges)} found)")
        edge_table.add_column("#", style="cyan", width=4)
        edge_table.add_column("Kind", style="yellow", width=10)
        edge_table.add_column("Source", style="bold", width=25)
        edge_table.add_column("Target", width=30)
        edge_table.add_column("Line", style="green", width=6)
        edge_table.add_column("Details", width=40)

        for idx, edge in enumerate(result.edges):
            src_name = "?"
            if 0 <= edge.src_symbol_idx < len(result.symbols):
                src_name = result.symbols[edge.src_symbol_idx].name
            elif edge.src_symbol_idx == -1:
                src_name = "(module)"

            details = ""
            if edge.kind == EdgeKind.IMPORTS:
                mod = edge.import_module or ""
                alias = f" as {edge.import_alias}" if edge.import_alias else ""
                details = f"from {mod}{alias} (level={edge.import_level})"
            elif edge.kind == EdgeKind.INHERITS:
                details = f"class inherits {edge.dst_name}"

            edge_table.add_row(
                str(idx),
                edge.kind.value,
                src_name,
                edge.dst_name,
                str(edge.line),
                details
            )
        console.print(edge_table)
    else:
        console.print("[yellow]No edges found[/yellow]")

    # Print syntax errors
    if show_errors and result.syntax_errors:
        console.print(f"\n[red]Syntax Errors ({len(result.syntax_errors)}):[/red]")
        for err in result.syntax_errors:
            console.print(f"  [red]•[/red] {err}")


# ============================================================================
# GRAPH COMMANDS
# ============================================================================

@app.command("callers")
def callers_cmd(
    symbol_id: int = typer.Argument(..., help="Symbol ID"),
    depth: int = typer.Option(1, "--depth", "-d", help="Max depth for callers (1 = direct only)"),
    min_confidence: float = typer.Option(0.0, "--min-confidence", "-c", help="Minimum edge confidence (0.0-1.0)"),
    repo: Optional[str] = typer.Option(None, "--repo", "-r", help="Repository name filter"),
):
    """Find callers of a symbol (supports depth and confidence filtering)."""
    _init_db()
    try:
        if repo:
            r = get_repository_by_name(repo)
            if not r:
                console.print(f"[red]Repository not found:[/red] {repo}")
                sys.exit(1)
            r["id"]

        results = get_callers(symbol_id, depth=depth, min_confidence=min_confidence)

        if depth <= 1:
            console.print(f"Direct callers (confidence >= {min_confidence}):")
        else:
            console.print(f"Callers up to depth {depth} (confidence >= {min_confidence}):")

        _print_results(results)

    finally:
        _close_db()


@app.command("callees")
def callees_cmd(
    symbol_id: int = typer.Argument(..., help="Symbol ID"),
    depth: int = typer.Option(1, "--depth", "-d", help="Max depth for callees (1 = direct only)"),
    min_confidence: float = typer.Option(0.0, "--min-confidence", "-c", help="Minimum edge confidence (0.0-1.0)"),
    repo: Optional[str] = typer.Option(None, "--repo", "-r", help="Repository name filter"),
):
    """Find callees of a symbol (supports depth and confidence filtering)."""
    _init_db()
    try:
        if repo:
            r = get_repository_by_name(repo)
            if not r:
                console.print(f"[red]Repository not found:[/red] {repo}")
                sys.exit(1)
            r["id"]

        results = get_callees(symbol_id, depth=depth, min_confidence=min_confidence)

        if depth <= 1:
            console.print(f"Direct callees (confidence >= {min_confidence}):")
        else:
            console.print(f"Callees up to depth {depth} (confidence >= {min_confidence}):")

        _print_results(results)

    finally:
        _close_db()


@app.command("impact")
def impact_cmd(
    symbol_id: int = typer.Argument(..., help="Symbol ID"),
    depth: int = typer.Option(10, "--depth", "-d", help="Max depth for transitive impact"),
    min_confidence: float = typer.Option(0.0, "--min-confidence", "-c", help="Minimum edge confidence (0.0-1.0)"),
    repo: Optional[str] = typer.Option(None, "--repo", "-r", help="Repository name filter"),
):
    """Show transitive impact (callers + callees) of a symbol."""
    _init_db()
    try:
        if repo:
            r = get_repository_by_name(repo)
            if not r:
                console.print(f"[red]Repository not found:[/red] {repo}")
                sys.exit(1)
            r["id"]

        results = get_transitive_impact(symbol_id, max_depth=depth, min_confidence=min_confidence)
        console.print(f"Transitive impact (depth <= {depth}, confidence >= {min_confidence}):")
        _print_results(results)
    finally:
        _close_db()


@app.command("refs")
def refs_cmd(
    symbol_id: int = typer.Argument(..., help="Symbol ID"),
    repo: Optional[str] = typer.Option(None, "--repo", "-r", help="Repository name filter"),
    min_confidence: float = typer.Option(0.0, "--min-confidence", "-c", help="Minimum edge confidence (0.0-1.0)"),
):
    """Find all references to a symbol (calls, imports, inherits, references)."""
    _init_db()
    try:
        repo_id = None
        if repo:
            r = get_repository_by_name(repo)
            if not r:
                console.print(f"[red]Repository not found:[/red] {repo}")
                sys.exit(1)
            repo_id = r["id"]

        results = get_references(symbol_id, repository_id=repo_id, min_confidence=min_confidence)
        console.print(f"References to symbol {symbol_id} (confidence >= {min_confidence}):")
        _print_results(results)
    finally:
        _close_db()


@app.command("context")
def context_cmd(
    symbol_id: int = typer.Argument(..., help="Symbol ID"),
    radius: int = typer.Option(2, "--radius", "-r", help="Context radius"),
):
    """Show neighborhood context around a symbol."""
    _init_db()
    try:
        ctx = get_symbol_context(symbol_id, radius)
        if not ctx:
            console.print(f"[red]Symbol not found:[/red] {symbol_id}")
            sys.exit(1)

        center = ctx["center"]
        console.print(Panel(
            f"[bold]{center['name']}[/bold] ({center['kind']})\n"
            f"{center['file_path']}:{center['start_line']}-{center['end_line']}\n"
            f"{center['signature'] or ''}",
            title="Center Symbol"
        ))

        for key, label in [
            ("callers", "Callers"),
            ("callees", "Callees"),
            ("references", "References"),
            ("transitive_callers", "Transitive Callers"),
            ("transitive_callees", "Transitive Callees"),
        ]:
            items = ctx.get(key, [])
            if items:
                console.print(f"\n[bold]{label}[/bold] ({len(items)}):")
                _print_results(items[:5])

    finally:
        _close_db()


@app.command("impls")
def impls_cmd(
    name: str = typer.Argument(..., help="Interface/trait name"),
    repo: Optional[str] = typer.Option(None, "--repo", "-r", help="Repository name filter"),
):
    """Find implementations of an interface/trait."""
    _init_db()
    try:
        repo_id = None
        if repo:
            r = get_repository_by_name(repo)
            if not r:
                console.print(f"[red]Repository not found:[/red] {repo}")
                sys.exit(1)
            repo_id = r["id"]

        results = find_implementations(name, repo_id)
        console.print(f"Implementations of [bold]{name}[/bold]:")
        _print_results(results)

    finally:
        _close_db()


# ============================================================================
# ASK COMMAND
# ============================================================================

@app.command("ask")
def ask_cmd(
    question: str = typer.Argument(..., help="Natural language question about the codebase"),
    repo: str = typer.Option(..., "--repo", "-r", help="Repository name"),
    limit: int = typer.Option(10, "--limit", "-l", help="Maximum snippets to retrieve"),
    depth: int = typer.Option(2, "--depth", "-d", help="Graph expansion depth"),
    threshold: Optional[float] = typer.Option(None, "--threshold", "-t", help="Minimum fused score threshold"),
):
    """Ask a natural language question about the codebase (RAG with hybrid+graph)."""
    _init_db()
    try:
        r = get_repository_by_name(repo)
        if not r:
            console.print(f"[red]Repository not found:[/red] {repo}")
            sys.exit(1)
        repo_id = r["id"]

        console.print(f"[bold]Question:[/bold] {question}")
        console.print(f"[bold]Repository:[/bold] {repo} (ID: {repo_id})")
        console.print()

        with console.status("[bold green]Searching and generating answer..."):
            result = answer_question(
                question=question,
                repository_id=repo_id,
                min_score_threshold=threshold,
                limit=limit,
                graph_depth=depth
            )

        # Print answer
        if result.error:
            console.print(Panel(
                f"[red]Error:[/red] {result.error}",
                title="Error",
                border_style="red"
            ))
            sys.exit(1)

        if result.not_found:
            console.print(Panel(
                "[yellow]Not found in the indexed code.[/yellow]",
                title="Answer",
                border_style="yellow"
            ))
            if result.citations:
                console.print(f"[dim]Retrieved {result.snippets_used} snippets (top score: {result.retrieval_score:.3f})[/dim]")
            return

        console.print(Panel(
            result.answer,
            title="Answer",
            border_style="green"
        ))

        # Print citations
        if result.citations:
            console.print(f"\n[bold]Citations ({len(result.citations)}):[/bold]")
            cite_table = Table()
            cite_table.add_column("#", style="cyan", width=4)
            cite_table.add_column("File", width=40)
            cite_table.add_column("Lines", style="green", width=10)
            cite_table.add_column("Symbol", style="bold", width=30)
            cite_table.add_column("Type", style="yellow", width=12)
            cite_table.add_column("Score", style="magenta", width=8)

            for c in result.citations:
                cite_table.add_row(
                    str(c.index),
                    f"{c.file_path}:{c.start_line}-{c.end_line}",
                    f"{c.start_line}-{c.end_line}",
                    c.qualified_name[:30],
                    c.search_type,
                    f"{c.fused_score:.3f}"
                )
            console.print(cite_table)

        console.print(f"\n[dim]Snippets used: {result.snippets_used} | Top score: {result.retrieval_score:.3f}[/dim]")

    finally:
        _close_db()


# ============================================================================
# SERVER COMMAND
# ============================================================================

@app.command("serve")
def serve_cmd(
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload"),
):
    """Start the FastAPI server."""
    import uvicorn
    console.print(f"Starting CodeCompass API on [bold]{host}:{port}[/bold]")
    uvicorn.run(
        "codecompass.api.main:app",
        host=host,
        port=port,
        reload=reload
    )


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _print_results(results: List[Dict], max_rows: int = 20):
    """Print search results in a table."""
    if not results:
        console.print("[yellow]No results[/yellow]")
        return

    table = Table()
    table.add_column("ID", style="cyan", width=8)
    table.add_column("Name", style="bold", width=30)
    table.add_column("Kind", style="magenta", width=12)
    table.add_column("File", width=40)
    table.add_column("Line", style="green", width=6)
    table.add_column("Type", style="yellow", width=15)

    for r in results[:max_rows]:
        file_path = r.get("file_path") or r.get("src_file_path") or r.get("dst_file_path") or "-"
        line = r.get("start_line") or r.get("line") or r.get("depth") or "-"
        search_type = r.get("search_type", "")
        table.add_row(
            str(r.get("id") or r.get("symbol_id") or "-"),
            r.get("name") or r.get("src_name") or r.get("dst_name") or "-",
            r.get("kind") or "-",
            file_path[:40],
            str(line),
            search_type
        )

    console.print(table)
    if len(results) > max_rows:
        console.print(f"[dim]... and {len(results) - max_rows} more[/dim]")


if __name__ == "__main__":
    app()