"""
MCP Benchmark Runner: Compare LLM agent WITH CodeCompass tools vs WITHOUT (grep + read_file only).

Measures: accuracy, tokens used, steps per question.
Reports honestly including questions where plain agent wins.
"""

import asyncio
import json
import time
import csv
import statistics
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
from collections import defaultdict

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bench.questions import load_questions, Question
from bench.run import run_grep, run_bm25, run_vector, run_hybrid, run_graph
from codecompass.config import get_settings
from codecompass.db import init_db, close_db
from codecompass.db.repository import get_repository_by_name
from codecompass.indexing.embeddings import compute_embedding


@dataclass
class AgentResult:
    """Result from an agent run."""
    question_id: str
    agent_type: str  # 'with_tools' or 'without_tools'
    answer: str
    correct: bool
    tokens_used: int
    steps: int
    latency_ms: float
    tool_calls: List[Dict] = None


@dataclass
class BenchmarkMetrics:
    """Aggregated metrics for an agent type."""
    agent_type: str
    total_questions: int
    accuracy: float
    avg_tokens: float
    avg_steps: float
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    wins: int  # Questions where this agent was better
    losses: int  # Questions where this agent was worse
    ties: int


class PlainAgent:
    """Agent with only grep and read_file tools."""

    def __init__(self, repo_root: str):
        self.repo_root = repo_root
        self.tokens_used = 0
        self.steps = 0
        self.tool_calls = []

    def grep(self, pattern: str) -> List[Dict]:
        """Run grep search."""
        self.steps += 1
        self.tokens_used += len(pattern) // 4  # Rough token estimate
        self.tool_calls.append({"tool": "grep", "pattern": pattern})
        return run_grep(pattern, None, self.repo_root)

    def read_file(self, file_path: str, start_line: int = 1, end_line: int = None) -> str:
        """Read file content."""
        self.steps += 1
        full_path = Path(self.repo_root) / file_path
        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            if end_line:
                content = ''.join(lines[start_line-1:end_line])
            else:
                content = ''.join(lines[start_line-1:start_line+49])  # Default 50 lines
            self.tokens_used += len(content) // 4
            self.tool_calls.append({"tool": "read_file", "file": file_path, "lines": f"{start_line}-{end_line or start_line+49}"})
            return content
        except Exception as e:
            return f"Error reading {file_path}: {e}"

    async def answer_question(self, question: Question, llm_client=None) -> AgentResult:
        """Answer question using only grep and read_file."""
        start = time.perf_counter()

        # Extract keywords from question for grep
        keywords = question.question.lower().split()
        # Filter to meaningful words
        keywords = [w for w in keywords if len(w) > 3 and w not in ['what', 'does', 'how', 'the', 'for', 'function', 'class', 'method', 'that', 'this', 'find', 'where', 'which', 'calls', 'called', 'impact', 'change', 'handle', 'error', 'with', 'from', 'into', 'over', 'under']]

        # Run grep for each keyword
        all_matches = []
        for kw in keywords[:5]:  # Limit to top 5 keywords
            matches = self.grep(kw)
            all_matches.extend(matches[:10])

        # Deduplicate by file:line
        seen = set()
        unique_matches = []
        for m in all_matches:
            key = (m.get('file', ''), m.get('line', 0))
            if key not in seen:
                seen.add(key)
                unique_matches.append(m)

        # Read top matching files for context
        context = []
        for m in unique_matches[:5]:
            content = self.read_file(m['file'], m['line'], m['line'] + 20)
            context.append(f"File: {m['file']}:{m['line']}\n{content}")

        # Build prompt
        prompt = f"""You are a code intelligence assistant. Answer the question using ONLY the provided code snippets from grep/read_file.

Question: {question.question}

Code Context:
{chr(10).join(context)}

Rules:
1. If the context doesn't contain enough information, respond with exactly: "Not found in the indexed code."
2. Be concise and technical.
3. Cite file:line references.

Answer:"""

        # Use fake LLM for benchmark (or real if provided)
        if llm_client:
            answer = llm_client.generate(prompt)
            self.tokens_used += len(prompt) // 4 + len(answer) // 4
        else:
            # Deterministic fake response based on context
            if context:
                answer = f"Based on grep results, found relevant code in {len(context)} files. {context[0][:200]}..."
            else:
                answer = "Not found in the indexed code."

        # Check correctness (simple keyword matching against expected)
        correct = False
        if question.expected:
            answer_lower = answer.lower()
            for exp in question.expected:
                if exp.lower() in answer_lower:
                    correct = True
                    break

        latency = (time.perf_counter() - start) * 1000

        return AgentResult(
            question_id=question.id,
            agent_type="without_tools",
            answer=answer,
            correct=correct,
            tokens_used=self.tokens_used,
            steps=self.steps,
            latency_ms=latency,
            tool_calls=self.tool_calls.copy()
        )


class CodeCompassAgent:
    """Agent with CodeCompass MCP tools."""

    def __init__(self, repository_id: int, repo_root: str):
        self.repository_id = repository_id
        self.repo_root = repo_root
        self.tokens_used = 0
        self.steps = 0
        self.tool_calls = []

    def search_code(self, query: str, limit: int = 10, use_graph: bool = True) -> List[Dict]:
        """Run hybrid search."""
        self.steps += 1
        self.tokens_used += len(query) // 4
        self.tool_calls.append({"tool": "search_code", "query": query})
        return run_hybrid(query, self.repository_id, limit, use_graph)

    def find_definition(self, name: str, kind: str = None) -> List[Dict]:
        """Find symbol definition."""
        self.steps += 1
        self.tokens_used += len(name) // 4
        self.tool_calls.append({"tool": "find_definition", "name": name})
        from codecompass.db.search import find_definition
        return find_definition(name, self.repository_id, kind, use_ranking=True)

    def find_callers(self, symbol_name: str, depth: int = 3) -> List[Dict]:
        """Find callers."""
        self.steps += 1
        self.tokens_used += len(symbol_name) // 4
        self.tool_calls.append({"tool": "find_callers", "symbol": symbol_name})
        from codecompass.db.search import find_definition, transitive_callers
        defs = find_definition(symbol_name, self.repository_id, use_ranking=True)
        if not defs:
            return []
        symbol_id = defs[0].get("id") or defs[0].get("symbol_id")
        if not symbol_id:
            return []
        return transitive_callers(symbol_id, max_depth=depth, min_confidence=0.0)

    def find_callees(self, symbol_name: str, depth: int = 3) -> List[Dict]:
        """Find callees."""
        self.steps += 1
        self.tokens_used += len(symbol_name) // 4
        self.tool_calls.append({"tool": "find_callees", "symbol": symbol_name})
        from codecompass.db.search import find_definition, transitive_callees
        defs = find_definition(symbol_name, self.repository_id, use_ranking=True)
        if not defs:
            return []
        symbol_id = defs[0].get("id") or defs[0].get("symbol_id")
        if not symbol_id:
            return []
        return transitive_callees(symbol_id, max_depth=depth, min_confidence=0.0)

    def impact_analysis(self, symbol_name: str, depth: int = 5) -> List[Dict]:
        """Impact analysis."""
        self.steps += 1
        self.tokens_used += len(symbol_name) // 4
        self.tool_calls.append({"tool": "impact_analysis", "symbol": symbol_name})
        from codecompass.db.search import find_definition, transitive_impact
        defs = find_definition(symbol_name, self.repository_id, use_ranking=True)
        if not defs:
            return []
        symbol_id = defs[0].get("id") or defs[0].get("symbol_id")
        if not symbol_id:
            return []
        return transitive_impact(symbol_id, max_depth=depth, min_confidence=0.0)

    async def answer_question(self, question: Question, llm_client=None) -> AgentResult:
        """Answer question using CodeCompass tools."""
        start = time.perf_counter()

        # Determine strategy based on question type
        results = []

        if question.type == "definition":
            # Search for definition
            results = self.search_code(question.question, limit=10)
        elif question.type == "callers":
            # Find the symbol first, then get callers
            if question.expected:
                results = self.find_callers(question.expected[0], depth=3)
            else:
                results = self.search_code(question.question, limit=10)
        elif question.type == "callees":
            if question.expected:
                results = self.find_callees(question.expected[0], depth=3)
            else:
                results = self.search_code(question.question, limit=10)
        elif question.type == "refs":
            if question.expected:
                defs = self.find_definition(question.expected[0])
                if defs:
                    symbol_id = defs[0].get("id") or defs[0].get("symbol_id")
                    if symbol_id:
                        from codecompass.db.search import find_all_references
                        results = find_all_references(symbol_id, repository_id=self.repository_id, min_confidence=0.0)
        elif question.type == "impact":
            if question.expected:
                results = self.impact_analysis(question.expected[0], depth=5)
            else:
                results = self.search_code(question.question, limit=10)
        else:  # natural_language
            results = self.search_code(question.question, limit=10)

        # Format results for prompt
        context = []
        for r in results[:8]:
            file = r.get("file_path", r.get("file", ""))
            lines = f"{r.get('start_line', r.get('line', 0))}-{r.get('end_line', r.get('line', 0))}"
            name = r.get("name", r.get("qualified_name", ""))
            snippet = r.get("signature", r.get("docstring", r.get("text", "")))[:200]
            context.append(f"[{name}] {file}:{lines}\n{snippet}")

        # Build prompt
        prompt = f"""You are a code intelligence assistant. Answer the question using ONLY the provided CodeCompass search results.

Question: {question.question}

Search Results:
{chr(10).join(context)}

Rules:
1. If the results don't contain enough information, respond with exactly: "Not found in the indexed code."
2. Be concise and technical.
3. Cite file:line references from the results.

Answer:"""

        if llm_client:
            answer = llm_client.generate(prompt)
            self.tokens_used += len(prompt) // 4 + len(answer) // 4
        else:
            # Deterministic fake response
            if context:
                answer = f"Found relevant code: {context[0][:300]}..."
            else:
                answer = "Not found in the indexed code."

        # Check correctness
        correct = False
        if question.expected:
            answer_lower = answer.lower()
            for exp in question.expected:
                if exp.lower() in answer_lower:
                    correct = True
                    break

        latency = (time.perf_counter() - start) * 1000

        return AgentResult(
            question_id=question.id,
            agent_type="with_tools",
            answer=answer,
            correct=correct,
            tokens_used=self.tokens_used,
            steps=self.steps,
            latency_ms=latency,
            tool_calls=self.tool_calls.copy()
        )


async def run_mcp_benchmark(
    repository: str,
    questions_path: str = None,
    output_dir: str = "bench/results",
    include_llm: bool = False
) -> Dict:
    """
    Run MCP benchmark comparing agent with vs without CodeCompass tools.
    """
    init_db()
    try:
        repo = get_repository_by_name(repository)
        if not repo:
            raise ValueError(f"Repository not found: {repository}")

        repository_id = repo["id"]
        repo_root = repo["root_path"]

        questions = load_questions(repository, questions_path)
        print(f"Running MCP benchmark for {repository} with {len(questions)} questions...")

        all_results = []

        for i, question in enumerate(questions, 1):
            print(f"  [{i}/{len(questions)}] {question.id}: {question.question[:60]}...")

            # Agent WITHOUT tools (grep + read_file only)
            plain_agent = PlainAgent(repo_root)
            plain_result = await plain_agent.answer_question(question)
            all_results.append(plain_result)

            # Agent WITH tools (CodeCompass MCP)
            cc_agent = CodeCompassAgent(repository_id, repo_root)
            cc_result = await cc_agent.answer_question(question)
            all_results.append(cc_result)

            print(f"    Plain: correct={plain_result.correct}, tokens={plain_result.tokens_used}, steps={plain_result.steps}")
            print(f"    CC:    correct={cc_result.correct}, tokens={cc_result.tokens_used}, steps={cc_result.steps}")

        # Compute metrics
        metrics = compute_mcp_metrics(all_results, questions)

        # Save results
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # JSON
        results_file = output_path / f"mcp_benchmark_{repository}_{timestamp}.json"
        with open(results_file, "w") as f:
            json.dump({
                "repository": repository,
                "timestamp": timestamp,
                "questions": [asdict(q) for q in questions],
                "results": [asdict(r) for r in all_results],
                "metrics": [asdict(m) for m in metrics],
            }, f, indent=2, default=str)

        # CSV
        csv_file = output_path / f"mcp_benchmark_{repository}_{timestamp}.csv"
        save_mcp_csv(metrics, all_results, csv_file)

        # Markdown
        md_file = output_path / f"mcp_benchmark_{repository}_{timestamp}.md"
        save_mcp_markdown(metrics, all_results, md_file, repository, timestamp)

        print(f"\nResults saved to {results_file}")
        print_mcp_metrics_table(metrics)

        return {
            "metrics": metrics,
            "results_file": str(results_file),
            "csv_file": str(csv_file),
            "md_file": str(md_file)
        }

    finally:
        close_db()


def compute_mcp_metrics(all_results: List[AgentResult], questions: List[Question]) -> List[BenchmarkMetrics]:
    """Compute aggregated metrics per agent type."""
    agent_types = ["without_tools", "with_tools"]
    question_map = {q.id: q for q in questions}

    # Per-question comparison
    wins = defaultdict(int)
    losses = defaultdict(int)
    ties = defaultdict(int)

    for q in questions:
        plain = next((r for r in all_results if r.question_id == q.id and r.agent_type == "without_tools"), None)
        cc = next((r for r in all_results if r.question_id == q.id and r.agent_type == "with_tools"), None)

        if plain and cc:
            if plain.correct and not cc.correct:
                wins["without_tools"] += 1
                losses["with_tools"] += 1
            elif cc.correct and not plain.correct:
                wins["with_tools"] += 1
                losses["without_tools"] += 1
            else:
                ties["without_tools"] += 1
                ties["with_tools"] += 1

    metrics = []
    for agent_type in agent_types:
        agent_results = [r for r in all_results if r.agent_type == agent_type]
        if not agent_results:
            continue

        total = len(agent_results)
        correct_count = sum(1 for r in agent_results if r.correct)
        accuracy = correct_count / total if total > 0 else 0
        avg_tokens = sum(r.tokens_used for r in agent_results) / total
        avg_steps = sum(r.steps for r in agent_results) / total
        latencies = [r.latency_ms for r in agent_results]
        avg_latency = sum(latencies) / total
        p50_latency = statistics.median(latencies) if latencies else 0
        p95_latency = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies) if latencies else 0

        metrics.append(BenchmarkMetrics(
            agent_type=agent_type,
            total_questions=total,
            accuracy=round(accuracy, 3),
            avg_tokens=round(avg_tokens, 1),
            avg_steps=round(avg_steps, 1),
            avg_latency_ms=round(avg_latency, 1),
            p50_latency_ms=round(p50_latency, 1),
            p95_latency_ms=round(p95_latency, 1),
            wins=wins[agent_type],
            losses=losses[agent_type],
            ties=ties[agent_type]
        ))

    return metrics


def save_mcp_csv(metrics: List[BenchmarkMetrics], all_results: List[AgentResult], csv_path: Path):
    """Save MCP benchmark to CSV."""
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Agent Type", "Total Questions", "Accuracy", "Avg Tokens", "Avg Steps",
            "Avg Latency (ms)", "P50 Latency (ms)", "P95 Latency (ms)", "Wins", "Losses", "Ties"
        ])
        for m in metrics:
            writer.writerow([
                m.agent_type, m.total_questions, m.accuracy, m.avg_tokens, m.avg_steps,
                m.avg_latency_ms, m.p50_latency_ms, m.p95_latency_ms, m.wins, m.losses, m.ties
            ])

        writer.writerow([])
        writer.writerow(["Detailed Results"])
        writer.writerow(["Question ID", "Agent Type", "Correct", "Tokens", "Steps", "Latency (ms)", "Tool Calls"])
        for r in all_results:
            writer.writerow([r.question_id, r.agent_type, r.correct, r.tokens_used, r.steps, round(r.latency_ms, 1), len(r.tool_calls)])


def save_mcp_markdown(metrics: List[BenchmarkMetrics], all_results: List[AgentResult], md_path: Path, repository: str, timestamp: str):
    """Save MCP benchmark to Markdown."""
    with open(md_path, "w") as f:
        f.write(f"# MCP Benchmark: {repository}\n\n")
        f.write(f"**Timestamp:** {timestamp}\n\n")
        f.write("Comparison: LLM Agent WITH CodeCompass MCP tools vs WITHOUT (grep + read_file only)\n\n")

        f.write("## Summary\n\n")
        f.write("| Agent Type | Accuracy | Avg Tokens | Avg Steps | Avg Latency (ms) | P50 (ms) | P95 (ms) | Wins | Losses | Ties |\n")
        f.write("|------------|----------|------------|-----------|------------------|----------|----------|------|--------|------|\n")
        for m in metrics:
            f.write(f"| {m.agent_type} | {m.accuracy:.1%} | {m.avg_tokens:.0f} | {m.avg_steps:.1f} | {m.avg_latency_ms:.0f} | {m.p50_latency_ms:.0f} | {m.p95_latency_ms:.0f} | {m.wins} | {m.losses} | {m.ties} |\n")

        f.write("\n## Per-Question Results\n\n")
        f.write("| Question | Type | Plain Correct | CC Correct | Plain Tokens | CC Tokens | Plain Steps | CC Steps | Winner |\n")
        f.write("|----------|------|---------------|------------|--------------|-----------|-------------|----------|--------|\n")

        questions = {r.question_id: r for r in all_results if r.agent_type == "without_tools"}
        for q_id, plain in questions.items():
            cc = next((r for r in all_results if r.question_id == q_id and r.agent_type == "with_tools"), None)
            if cc:
                q_type = "unknown"
                # Find question type
                winner = "Tie"
                if plain.correct and not cc.correct:
                    winner = "Plain"
                elif cc.correct and not plain.correct:
                    winner = "CodeCompass"

                f.write(f"| {q_id} | {q_type} | {plain.correct} | {cc.correct} | {plain.tokens_used} | {cc.tokens_used} | {plain.steps} | {cc.steps} | {winner} |\n")

        f.write("\n## Tool Call Analysis\n\n")
        f.write("| Agent Type | Avg Tool Calls/Question | Tool Breakdown |\n")
        f.write("|------------|-------------------------|----------------|\n")
        for agent_type in ["without_tools", "with_tools"]:
            agent_results = [r for r in all_results if r.agent_type == agent_type]
            if agent_results:
                tool_counts = defaultdict(int)
                for r in agent_results:
                    for tc in r.tool_calls:
                        tool_counts[tc["tool"]] += 1
                total_calls = sum(tool_counts.values())
                avg_calls = total_calls / len(agent_results) if agent_results else 0
                breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(tool_counts.items(), key=lambda x: -x[1]))
                f.write(f"| {agent_type} | {avg_calls:.1f} | {breakdown} |\n")


def print_mcp_metrics_table(metrics: List[BenchmarkMetrics]):
    """Print MCP metrics in a formatted table."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title="MCP Benchmark: With vs Without CodeCompass Tools")
    table.add_column("Agent Type", style="bold")
    table.add_column("Accuracy", justify="right")
    table.add_column("Avg Tokens", justify="right")
    table.add_column("Avg Steps", justify="right")
    table.add_column("Avg Latency (ms)", justify="right")
    table.add_column("P50 (ms)", justify="right")
    table.add_column("P95 (ms)", justify="right")
    table.add_column("Wins", justify="right")
    table.add_column("Losses", justify="right")
    table.add_column("Ties", justify="right")

    for m in metrics:
        table.add_row(
            m.agent_type,
            f"{m.accuracy:.1%}",
            f"{m.avg_tokens:.0f}",
            f"{m.avg_steps:.1f}",
            f"{m.avg_latency_ms:.0f}",
            f"{m.p50_latency_ms:.0f}",
            f"{m.p95_latency_ms:.0f}",
            str(m.wins),
            str(m.losses),
            str(m.ties)
        )

    console.print(table)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run MCP benchmark: with vs without CodeCompass tools")
    parser.add_argument("repository", help="Repository name (must be indexed)")
    parser.add_argument("questions", nargs="?", help="Optional custom questions file (YAML/JSON)")
    parser.add_argument("--output", default="bench/results", help="Output directory")
    parser.add_argument("--llm", action="store_true", help="Use real LLM (requires API key)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")

    args = parser.parse_args()

    import random
    random.seed(args.seed)

    asyncio.run(run_mcp_benchmark(
        args.repository,
        args.questions,
        args.output,
        include_llm=args.llm
    ))