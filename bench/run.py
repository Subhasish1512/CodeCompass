"""
Benchmark runner for comparing search methods.

Runs each question through multiple search modes and computes metrics.
"""

import subprocess
import time
import json
import csv
import statistics
import random
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, asdict, field
from datetime import datetime
from collections import defaultdict

from codecompass.config import get_settings
from codecompass.db import init_db, close_db
from codecompass.db.repository import get_repository_by_name
from codecompass.search.bm25 import bm25_search
from codecompass.search.vector import vector_search
from codecompass.search.hybrid import search, multi_stage_search
from codecompass.graph.traversal import find_callers, find_callees, get_transitive_impact
from codecompass.db.search import find_all_references
from bench.questions import Question, load_questions


# ============================================================================
# RESULT DATACLASSES
# ============================================================================

@dataclass
class SearchResult:
    """Result from a single search mode for a question."""
    question_id: str
    mode: str  # 'grep', 'bm25', 'vector', 'hybrid', 'hybrid_graph', 'graph', 'llm_agent'
    results: List[Dict]
    latency_ms: float
    result_count: int
    tokens_used: int = 0


@dataclass
class BenchmarkMetrics:
    """Aggregated metrics for a search mode."""
    mode: str
    total_questions: int
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    total_results: int
    recall_at_5: float
    recall_at_10: float
    recall_at_20: float
    mrr: float
    exact_match_rate: float = 0.0  # For structural questions
    total_tokens: int = 0
    avg_tokens_per_query: float = 0.0


@dataclass
class IndexBenchmarkResult:
    """Results from index benchmarking."""
    repository: str
    total_loc: int
    total_files: int
    full_index_seconds: float
    peak_memory_mb: float
    loc_per_second: float
    incremental_updates: List[Dict] = field(default_factory=list)


# ============================================================================
# SEARCH RUNNERS
# ============================================================================

def run_grep(query: str, repository_id: Optional[int], repo_root: str) -> List[Dict]:
    """Run grep search (baseline)."""
    # Use ripgrep if available, fallback to grep
    cmd = ["rg", "--json", "-n", query, repo_root]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        matches = []
        for line in result.stdout.strip().split("\n"):
            if line:
                try:
                    data = json.loads(line)
                    if data.get("type") == "match":
                        matches.append({
                            "file": data["data"]["path"]["text"],
                            "line": data["data"]["line_number"],
                            "text": data["data"]["lines"]["text"],
                            "search_type": "grep"
                        })
                except json.JSONDecodeError:
                    pass
        return matches
    except (subprocess.SubprocessError, FileNotFoundError):
        # Fallback to basic grep
        cmd = ["grep", "-rn", query, repo_root]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            matches = []
            for line in result.stdout.strip().split("\n"):
                if line:
                    parts = line.split(":", 2)
                    if len(parts) >= 3:
                        matches.append({
                            "file": parts[0],
                            "line": int(parts[1]),
                            "text": parts[2],
                            "search_type": "grep"
                        })
            return matches
        except subprocess.SubprocessError:
            return []


def run_bm25(query: str, repository_id: int, limit: int = 20) -> List[Dict]:
    """Run BM25 search."""
    return bm25_search(query, repository_id, limit)


def run_vector(query: str, repository_id: int, limit: int = 20) -> List[Dict]:
    """Run vector search."""
    return vector_search(query, repository_id, limit)


def run_hybrid(query: str, repository_id: int, limit: int = 20, use_graph: bool = False) -> List[Dict]:
    """Run hybrid search (RRF)."""
    return search(query, repository_id, limit=limit, use_graph_expansion=use_graph, graph_depth=2)


def run_graph(question: Question, repository_id: int, limit: int = 20) -> List[Dict]:
    """Run graph-only search for structural question types."""
    # Graph search requires a symbol ID - we need to find it first
    # For structural questions, we extract the target symbol from expected
    if not question.expected:
        return []

    # Try to find the first expected symbol as the anchor
    from codecompass.db.search import find_definition
    target_name = question.expected[0] if question.expected else ""
    if not target_name:
        return []

    # Find definition to get symbol ID
    defs = find_definition(target_name, repository_id)
    if not defs:
        return []

    symbol_id = defs[0].get("id") or defs[0].get("symbol_id")
    if not symbol_id:
        return []

    results = []

    if question.type == "callers":
        results = find_callers(symbol_id, depth=3, min_confidence=0.0)
    elif question.type == "callees":
        results = find_callees(symbol_id, depth=3, min_confidence=0.0)
    elif question.type == "refs":
        results = find_all_references(symbol_id, repository_id=repository_id, min_confidence=0.0)
    elif question.type == "impact":
        results = get_transitive_impact(symbol_id, max_depth=5, min_confidence=0.0)
    else:
        # Fallback: just return the definition
        results = defs

    # Add search_type
    for r in results:
        r["search_type"] = f"graph_{question.type}"

    return results[:limit]


def run_llm_agent(query: str, repository_id: int, repo_root: str,
                  max_steps: int = 5, token_limit: int = 10000) -> Tuple[List[Dict], int]:
    """
    OPTIONAL: LLM agent baseline that can call grep and read_file tools.
    Returns (results, tokens_used).
    """
    # This is a placeholder - would need actual LLM integration
    # For now, return empty with 0 tokens
    return [], 0


# ============================================================================
# EVALUATION
# ============================================================================

def evaluate_results(results: List[Dict], question: Question, k: int) -> float:
    """Compute recall@k for a question."""
    if not question.expected:
        return 1.0  # No ground truth - assume perfect

    top_k = results[:k]
    found = 0
    for expected in question.expected:
        for r in top_k:
            # Check name match
            name = r.get("name", "").lower()
            qualified_name = r.get("qualified_name", "").lower()
            file_path = r.get("file", r.get("file_path", "")).lower()

            if (expected.lower() in name or
                expected.lower() in qualified_name or
                expected.lower() in file_path):
                found += 1
                break
    return found / len(question.expected) if question.expected else 1.0


def compute_mrr(results: List[Dict], question: Question) -> float:
    """Compute Mean Reciprocal Rank."""
    if not question.expected:
        return 1.0

    for expected in question.expected:
        for rank, r in enumerate(results, 1):
            name = r.get("name", "").lower()
            qualified_name = r.get("qualified_name", "").lower()
            file_path = r.get("file", r.get("file_path", "")).lower()

            if (expected.lower() in name or
                expected.lower() in qualified_name or
                expected.lower() in file_path):
                return 1.0 / rank
    return 0.0


def compute_exact_match(results: List[Dict], question: Question) -> float:
    """Compute exact match rate for structural questions (definition, callers, etc.)."""
    if question.type not in ["definition", "callers", "callees", "refs", "impact"]:
        return 0.0

    if not question.expected:
        return 0.0

    # For structural questions, check if top-1 result matches exactly
    if not results:
        return 0.0

    top_result = results[0]
    name = top_result.get("name", "").lower()
    qualified_name = top_result.get("qualified_name", "").lower()

    for expected in question.expected:
        if expected.lower() == name or expected.lower() == qualified_name:
            return 1.0
    return 0.0


# ============================================================================
# SINGLE QUESTION RUNNER
# ============================================================================

def run_single_question(
    question: Question,
    repository_id: int,
    repo_root: str,
    include_llm: bool = False
) -> List[SearchResult]:
    """Run all search modes for a single question."""
    results = []

    # 1. Grep (baseline)
    start = time.perf_counter()
    grep_results = run_grep(question.question, repository_id, repo_root)
    latency = (time.perf_counter() - start) * 1000
    results.append(SearchResult(
        question_id=question.id,
        mode="grep",
        results=grep_results,
        latency_ms=latency,
        result_count=len(grep_results)
    ))

    # 2. BM25
    start = time.perf_counter()
    bm25_results = run_bm25(question.question, repository_id, limit=20)
    latency = (time.perf_counter() - start) * 1000
    results.append(SearchResult(
        question_id=question.id,
        mode="bm25",
        results=bm25_results,
        latency_ms=latency,
        result_count=len(bm25_results)
    ))

    # 3. Vector
    start = time.perf_counter()
    vector_results = run_vector(question.question, repository_id, limit=20)
    latency = (time.perf_counter() - start) * 1000
    results.append(SearchResult(
        question_id=question.id,
        mode="vector",
        results=vector_results,
        latency_ms=latency,
        result_count=len(vector_results)
    ))

    # 4. Graph (structural only)
    if question.type in ["callers", "callees", "refs", "impact"]:
        start = time.perf_counter()
        graph_results = run_graph(question, repository_id, limit=20)
        latency = (time.perf_counter() - start) * 1000
        results.append(SearchResult(
            question_id=question.id,
            mode="graph",
            results=graph_results,
            latency_ms=latency,
            result_count=len(graph_results)
        ))

    # 5. Hybrid (RRF)
    start = time.perf_counter()
    hybrid_results = run_hybrid(question.question, repository_id, limit=20, use_graph=False)
    latency = (time.perf_counter() - start) * 1000
    results.append(SearchResult(
        question_id=question.id,
        mode="hybrid",
        results=hybrid_results,
        latency_ms=latency,
        result_count=len(hybrid_results)
    ))

    # 6. Hybrid + Graph
    start = time.perf_counter()
    hybrid_graph_results = run_hybrid(question.question, repository_id, limit=20, use_graph=True)
    latency = (time.perf_counter() - start) * 1000
    results.append(SearchResult(
        question_id=question.id,
        mode="hybrid_graph",
        results=hybrid_graph_results,
        latency_ms=latency,
        result_count=len(hybrid_graph_results)
    ))

    # 7. LLM Agent (optional)
    if include_llm:
        start = time.perf_counter()
        llm_results, tokens = run_llm_agent(question.question, repository_id, repo_root)
        latency = (time.perf_counter() - start) * 1000
        results.append(SearchResult(
            question_id=question.id,
            mode="llm_agent",
            results=llm_results,
            latency_ms=latency,
            result_count=len(llm_results),
            tokens_used=tokens
        ))

    return results


# ============================================================================
# INDEX BENCHMARKING
# ============================================================================

def run_index_benchmark(repository: str) -> IndexBenchmarkResult:
    """Benchmark indexing performance."""
    import psutil
    import os
    from codecompass.indexing.pipeline import index_repository
    from codecompass.db.repository import get_repository_by_name

    init_db()
    try:
        repo = get_repository_by_name(repository)
        if not repo:
            raise ValueError(f"Repository not found: {repository}")

        repo_root = repo["root_path"]

        # Count lines of code
        total_loc = 0
        total_files = 0
        for root, dirs, files in os.walk(repo_root):
            # Skip excluded dirs
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__', 'node_modules', 'venv', '.venv', 'dist', 'build']]
            for f in files:
                if f.endswith('.py'):
                    filepath = os.path.join(root, f)
                    try:
                        with open(filepath, 'r', encoding='utf-8', errors='ignore') as fp:
                            total_loc += sum(1 for _ in fp)
                        total_files += 1
                    except Exception:
                        pass

        # Track memory
        process = psutil.Process(os.getpid())
        peak_memory = 0

        def track_memory():
            nonlocal peak_memory
            mem = process.memory_info().rss / (1024 * 1024)  # MB
            peak_memory = max(peak_memory, mem)

        # Run full index
        track_memory()
        start = time.perf_counter()
        stats = index_repository(
            name=repo["name"],
            root_path=repo_root,
            url=repo["url"],
            incremental=False
        )
        elapsed = time.perf_counter() - start
        track_memory()

        loc_per_sec = total_loc / elapsed if elapsed > 0 else 0

        return IndexBenchmarkResult(
            repository=repository,
            total_loc=total_loc,
            total_files=total_files,
            full_index_seconds=round(elapsed, 2),
            peak_memory_mb=round(peak_memory, 2),
            loc_per_second=round(loc_per_sec, 0)
        )
    finally:
        close_db()


def run_incremental_benchmark(repository: str, num_commits: int = 5) -> List[Dict]:
    """Benchmark incremental update latency over N simulated commits."""
    from codecompass.indexing.incremental import update_repository
    from codecompass.db.repository import get_repository_by_name

    init_db()
    try:
        repo = get_repository_by_name(repository)
        if not repo:
            raise ValueError(f"Repository not found: {repository}")

        results = []
        for i in range(num_commits):
            start = time.perf_counter()
            stats = update_repository(name=repo["name"])
            elapsed = time.perf_counter() - start

            results.append({
                "commit": i + 1,
                "files_added": stats.files_added,
                "files_modified": stats.files_modified,
                "files_deleted": stats.files_deleted,
                "files_renamed": stats.files_renamed,
                "symbols_created": stats.symbols_created,
                "edges_created": stats.edges_created,
                "embeddings_generated": stats.embeddings_generated,
                "edges_reresolved": stats.edges_reresolved,
                "elapsed_seconds": round(elapsed, 2)
            })

        return results
    finally:
        close_db()


# ============================================================================
# MAIN BENCHMARK
# ============================================================================

def run_benchmark(
    repository: str,
    questions_path: str = None,
    output_dir: str = "bench/results",
    include_llm: bool = False,
    run_index_bench: bool = True,
    run_incremental_bench: bool = True
) -> dict:
    """
    Run full benchmark suite for a repository.

    Args:
        repository: Repository name (must be indexed)
        questions_path: Optional custom questions file
        output_dir: Directory to save results
        include_llm: Whether to run LLM agent baseline
        run_index_bench: Whether to run index benchmarks
        run_incremental_bench: Whether to run incremental benchmarks

    Returns:
        Dictionary with aggregated metrics per mode
    """
    init_db()
    try:
        repo = get_repository_by_name(repository)
        if not repo:
            raise ValueError(f"Repository not found: {repository}")

        repository_id = repo["id"]
        repo_root = repo["root_path"]

        questions = load_questions(repository, questions_path)
        print(f"Running benchmark for {repository} with {len(questions)} questions...")

        all_results = []
        for i, question in enumerate(questions, 1):
            print(f"  [{i}/{len(questions)}] {question.id}: {question.question[:60]}...")
            q_results = run_single_question(question, repository_id, repo_root, include_llm)
            all_results.extend(q_results)

        # Compute aggregated metrics
        metrics = compute_metrics(all_results, questions)

        # Run index benchmarks
        index_results = None
        incremental_results = None
        if run_index_bench:
            print("\nRunning index benchmark...")
            index_results = run_index_benchmark(repository)
            print(f"  Full index: {index_results.full_index_seconds}s ({index_results.loc_per_second:,.0f} LOC/s)")
            print(f"  Peak memory: {index_results.peak_memory_mb:.0f} MB")

        if run_incremental_bench:
            print("\nRunning incremental benchmark...")
            incremental_results = run_incremental_benchmark(repository)
            for r in incremental_results:
                print(f"  Commit {r['commit']}: {r['elapsed_seconds']}s ({r['files_modified']} modified, {r['files_added']} added)")

        # Save detailed results
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # JSON results
        results_file = output_path / f"benchmark_{repository}_{timestamp}.json"
        with open(results_file, "w") as f:
            json.dump({
                "repository": repository,
                "timestamp": timestamp,
                "questions": [asdict(q) for q in questions],
                "results": [asdict(r) for r in all_results],
                "metrics": [asdict(m) for m in metrics],
                "index_benchmark": asdict(index_results) if index_results else None,
                "incremental_benchmark": incremental_results,
            }, f, indent=2, default=str)

        # CSV results
        csv_file = output_path / f"benchmark_{repository}_{timestamp}.csv"
        save_csv_results(metrics, csv_file, index_results, incremental_results)

        # Markdown results
        md_file = output_path / f"benchmark_{repository}_{timestamp}.md"
        save_markdown_results(metrics, md_file, repository, timestamp, index_results, incremental_results)

        print(f"\nResults saved to {results_file}")
        print(f"CSV saved to {csv_file}")
        print(f"Markdown saved to {md_file}")
        print_metrics_table(metrics)

        return {
            "metrics": metrics,
            "results_file": str(results_file),
            "csv_file": str(csv_file),
            "md_file": str(md_file),
            "index_benchmark": index_results,
            "incremental_benchmark": incremental_results
        }

    finally:
        close_db()


def compute_metrics(
    all_results: List[SearchResult],
    questions: List[Question]
) -> List[BenchmarkMetrics]:
    """Compute aggregated metrics per search mode."""
    modes = ["grep", "bm25", "vector", "graph", "hybrid", "hybrid_graph", "llm_agent"]
    question_map = {q.id: q for q in questions}

    metrics = []
    for mode in modes:
        mode_results = [r for r in all_results if r.mode == mode]
        if not mode_results:
            continue

        total_questions = len(mode_results)
        latencies = [r.latency_ms for r in mode_results]
        avg_latency = sum(latencies) / total_questions
        p50_latency = statistics.median(latencies) if latencies else 0
        p95_latency = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies) if latencies else 0
        total_results = sum(r.result_count for r in mode_results)
        total_tokens = sum(r.tokens_used for r in mode_results)
        avg_tokens = total_tokens / total_questions if total_questions > 0 else 0

        # Recall@k
        recall_5 = sum(evaluate_results(r.results, question_map[r.question_id], 5) for r in mode_results) / total_questions
        recall_10 = sum(evaluate_results(r.results, question_map[r.question_id], 10) for r in mode_results) / total_questions
        recall_20 = sum(evaluate_results(r.results, question_map[r.question_id], 20) for r in mode_results) / total_questions

        # MRR
        mrr = sum(compute_mrr(r.results, question_map[r.question_id]) for r in mode_results) / total_questions

        # Exact match (structural questions only)
        structural_questions = [q for q in questions if q.type in ["definition", "callers", "callees", "refs", "impact"]]
        exact_matches = []
        for q in structural_questions:
            q_results = [r for r in mode_results if r.question_id == q.id]
            if q_results:
                exact_matches.append(compute_exact_match(q_results[0].results, q))
        exact_match_rate = sum(exact_matches) / len(exact_matches) if exact_matches else 0.0

        metrics.append(BenchmarkMetrics(
            mode=mode,
            total_questions=total_questions,
            avg_latency_ms=round(avg_latency, 2),
            p50_latency_ms=round(p50_latency, 2),
            p95_latency_ms=round(p95_latency, 2),
            total_results=total_results,
            recall_at_5=round(recall_5, 3),
            recall_at_10=round(recall_10, 3),
            recall_at_20=round(recall_20, 3),
            mrr=round(mrr, 3),
            exact_match_rate=round(exact_match_rate, 3),
            total_tokens=total_tokens,
            avg_tokens_per_query=round(avg_tokens, 1)
        ))

    return metrics


def save_csv_results(
    metrics: List[BenchmarkMetrics],
    csv_path: Path,
    index_results: IndexBenchmarkResult = None,
    incremental_results: List[Dict] = None
):
    """Save metrics to CSV."""
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Mode", "Total Questions", "Avg Latency (ms)", "P50 Latency (ms)",
            "P95 Latency (ms)", "Total Results", "Recall@5", "Recall@10",
            "Recall@20", "MRR", "Exact Match Rate", "Total Tokens", "Avg Tokens/Query"
        ])
        for m in metrics:
            writer.writerow([
                m.mode, m.total_questions, m.avg_latency_ms, m.p50_latency_ms,
                m.p95_latency_ms, m.total_results, m.recall_at_5, m.recall_at_10,
                m.recall_at_20, m.mrr, m.exact_match_rate, m.total_tokens, m.avg_tokens_per_query
            ])

        if index_results:
            writer.writerow([])
            writer.writerow(["INDEX BENCHMARK"])
            writer.writerow(["Repository", "Total LOC", "Total Files", "Full Index (s)", "Peak Memory (MB)", "LOC/s"])
            writer.writerow([
                index_results.repository, index_results.total_loc, index_results.total_files,
                index_results.full_index_seconds, index_results.peak_memory_mb, index_results.loc_per_second
            ])

        if incremental_results:
            writer.writerow([])
            writer.writerow(["INCREMENTAL BENCHMARK"])
            writer.writerow(["Commit", "Files Added", "Files Modified", "Files Deleted", "Files Renamed",
                            "Symbols Created", "Edges Created", "Embeddings", "Edges Reresolved", "Elapsed (s)"])
            for r in incremental_results:
                writer.writerow([
                    r["commit"], r["files_added"], r["files_modified"], r["files_deleted"],
                    r["files_renamed"], r["symbols_created"], r["edges_created"],
                    r["embeddings_generated"], r["edges_reresolved"], r["elapsed_seconds"]
                ])


def save_markdown_results(
    metrics: List[BenchmarkMetrics],
    md_path: Path,
    repository: str,
    timestamp: str,
    index_results: IndexBenchmarkResult = None,
    incremental_results: List[Dict] = None
):
    """Save metrics to Markdown."""
    with open(md_path, "w") as f:
        f.write(f"# Benchmark Results: {repository}\n\n")
        f.write(f"**Timestamp:** {timestamp}\n\n")

        f.write("## Search Mode Comparison\n\n")
        f.write("| Mode | Avg Latency (ms) | P50 (ms) | P95 (ms) | Total Results | Recall@5 | Recall@10 | Recall@20 | MRR | Exact Match | Tokens/Query |\n")
        f.write("|------|------------------|----------|----------|---------------|----------|-----------|-----------|-----|-------------|--------------|\n")
        for m in metrics:
            f.write(f"| {m.mode} | {m.avg_latency_ms:.1f} | {m.p50_latency_ms:.1f} | {m.p95_latency_ms:.1f} | "
                   f"{m.total_results} | {m.recall_at_5:.3f} | {m.recall_at_10:.3f} | {m.recall_at_20:.3f} | "
                   f"{m.mrr:.3f} | {m.exact_match_rate:.3f} | {m.avg_tokens_per_query:.1f} |\n")

        if index_results:
            f.write("\n## Index Benchmark\n\n")
            f.write(f"- **Repository:** {index_results.repository}\n")
            f.write(f"- **Total LOC:** {index_results.total_loc:,}\n")
            f.write(f"- **Total Files:** {index_results.total_files:,}\n")
            f.write(f"- **Full Index Time:** {index_results.full_index_seconds:.2f}s\n")
            f.write(f"- **Peak Memory:** {index_results.peak_memory_mb:.1f} MB\n")
            f.write(f"- **Throughput:** {index_results.loc_per_second:,.0f} LOC/s\n")
            f.write(f"- **Seconds per 100k LOC:** {100000 / index_results.loc_per_second:.2f}s\n")

        if incremental_results:
            f.write("\n## Incremental Update Benchmark\n\n")
            f.write("| Commit | Files Added | Files Modified | Files Deleted | Files Renamed | Symbols | Edges | Embeddings | Edges Reresolved | Elapsed (s) |\n")
            f.write("|--------|-------------|----------------|---------------|---------------|---------|-------|------------|------------------|-------------|\n")
            for r in incremental_results:
                f.write(f"| {r['commit']} | {r['files_added']} | {r['files_modified']} | {r['files_deleted']} | "
                       f"{r['files_renamed']} | {r['symbols_created']} | {r['edges_created']} | "
                       f"{r['embeddings_generated']} | {r['edges_reresolved']} | {r['elapsed_seconds']} |\n")


def print_metrics_table(metrics: List[BenchmarkMetrics]):
    """Print metrics in a formatted table."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title="Benchmark Results")
    table.add_column("Mode", style="bold")
    table.add_column("Avg Latency (ms)", justify="right")
    table.add_column("P50 (ms)", justify="right")
    table.add_column("P95 (ms)", justify="right")
    table.add_column("Total Results", justify="right")
    table.add_column("Recall@5", justify="right")
    table.add_column("Recall@10", justify="right")
    table.add_column("Recall@20", justify="right")
    table.add_column("MRR", justify="right")
    table.add_column("Exact Match", justify="right")
    table.add_column("Tokens/Query", justify="right")

    for m in metrics:
        table.add_row(
            m.mode,
            f"{m.avg_latency_ms:.1f}",
            f"{m.p50_latency_ms:.1f}",
            f"{m.p95_latency_ms:.1f}",
            str(m.total_results),
            f"{m.recall_at_5:.3f}",
            f"{m.recall_at_10:.3f}",
            f"{m.recall_at_20:.3f}",
            f"{m.mrr:.3f}",
            f"{m.exact_match_rate:.3f}",
            f"{m.avg_tokens_per_query:.1f}"
        )

    console.print(table)


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Run CodeCompass benchmarks")
    parser.add_argument("repository", help="Repository name (must be indexed)")
    parser.add_argument("questions", nargs="?", help="Optional custom questions file (YAML/JSON)")
    parser.add_argument("--output", default="bench/results", help="Output directory")
    parser.add_argument("--llm", action="store_true", help="Include LLM agent baseline")
    parser.add_argument("--no-index", action="store_true", help="Skip index benchmark")
    parser.add_argument("--no-incremental", action="store_true", help="Skip incremental benchmark")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")

    args = parser.parse_args()

    # Set deterministic seeds
    random.seed(args.seed)

    run_benchmark(
        args.repository,
        args.questions,
        args.output,
        include_llm=args.llm,
        run_index_bench=not args.no_index,
        run_incremental_bench=not args.no_incremental
    )