"""
Answer pipeline: RAG with hybrid+graph search, prompt building, and citation validation.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from codecompass.config import get_settings
from codecompass.db.search import hybrid_search_with_explanation
from codecompass.indexing.embeddings import compute_embedding
from codecompass.llm import get_llm_client
from codecompass.search.hybrid import search_with_graph_expansion


@dataclass
class Citation:
    """A citation reference in an answer."""
    index: int
    snippet_id: int
    snippet_text: str
    file_path: str
    start_line: int
    end_line: int
    qualified_name: str
    search_type: str
    fused_score: float


@dataclass
class AnswerResult:
    """Result of the answer pipeline."""
    question: str
    answer: str
    citations: List[Citation]
    snippets_used: int
    retrieval_score: float
    not_found: bool = False
    error: Optional[str] = None


def _format_snippet(snippet: Dict, index: int) -> str:
    """Format a code snippet for the prompt."""
    lines = []
    lines.append(f"[{index}] {snippet.get('qualified_name', 'unknown')}")
    lines.append(f"    File: {snippet.get('file_path', 'unknown')}:{snippet.get('start_line', '?')}-{snippet.get('end_line', '?')}")
    lines.append(f"    Type: {snippet.get('search_type', 'unknown')}")
    if snippet.get('signature'):
        lines.append(f"    Signature: {snippet['signature']}")
    if snippet.get('docstring'):
        doc = snippet['docstring'][:200]
        lines.append(f"    Docstring: {doc}")
    # Add text content (chunk or symbol)
    text = snippet.get('text') or snippet.get('docstring') or ''
    if text:
        lines.append(f"    Content: {text[:500]}")
    return "\n".join(lines)


def build_prompt(question: str, snippets: List[Dict]) -> str:
    """Build a prompt with numbered code snippets and citation instructions."""
    parts = [
        "You are a code intelligence assistant. Answer the question using ONLY the provided code snippets.",
        "Each snippet is numbered [n] and includes file path, line numbers, and the code content.",
        "Rules:",
        "1. Cite every factual claim using [n] notation referencing the snippet numbers.",
        "2. If the snippets don't contain enough information to answer, respond with exactly: \"Not found in the indexed code.\"",
        "3. Do not use external knowledge. Only use the provided snippets.",
        "4. Keep answers concise and technical.",
        "",
        f"Question: {question}",
        "",
        "Code Snippets:",
        ""
    ]

    for i, snippet in enumerate(snippets, 1):
        parts.append(_format_snippet(snippet, i))

    parts.append("")
    parts.append("Answer:")

    return "\n".join(parts)


def validate_citations(answer: str, snippets: List[Dict]) -> tuple:
    """
    Validate that all cited indices in the answer exist in the snippets.

    Returns (cleaned_answer, valid_citations, invalid_citations)
    """
    # Find all [n] patterns
    citation_pattern = r'\[(\d+)\]'
    cited_indices = [int(m) for m in re.findall(citation_pattern, answer)]

    max_index = len(snippets)
    valid_citations = []
    invalid_citations = []

    for idx in cited_indices:
        if 1 <= idx <= max_index:
            valid_citations.append(idx)
        else:
            invalid_citations.append(idx)

    # Remove invalid citations from answer
    cleaned_answer = answer
    if invalid_citations:
        # Replace invalid [n] with [INVALID n]
        for idx in invalid_citations:
            cleaned_answer = re.sub(
                rf'\[{idx}\]',
                f'[INVALID {idx}]',
                cleaned_answer
            )

    return cleaned_answer, valid_citations, invalid_citations


def build_citation_objects(
    valid_indices: List[int],
    snippets: List[Dict]
) -> List[Citation]:
    """Build Citation objects from validated indices."""
    citations = []
    for idx in valid_indices:
        snippet = snippets[idx - 1]  # 1-indexed
        citations.append(Citation(
            index=idx,
            snippet_id=snippet.get('id', 0),
            snippet_text=snippet.get('text', '')[:200] or snippet.get('docstring', '')[:200] or '',
            file_path=snippet.get('file_path', 'unknown'),
            start_line=snippet.get('start_line', 0),
            end_line=snippet.get('end_line', 0),
            qualified_name=snippet.get('qualified_name', 'unknown'),
            search_type=snippet.get('search_type', 'unknown'),
            fused_score=snippet.get('fused_score', 0.0)
        ))
    return citations


def answer_question(
    question: str,
    repository_id: int,
    min_score_threshold: float = None,
    limit: int = 10,
    graph_depth: int = 2
) -> AnswerResult:
    """
    Run the full answer pipeline: retrieve -> prompt -> LLM -> validate citations.

    Args:
        question: Natural language question
        repository_id: Repository to search
        min_score_threshold: Minimum fused_score to consider retrieval sufficient
        limit: Max snippets to retrieve
        graph_depth: Graph expansion depth

    Returns:
        AnswerResult with answer, citations, and metadata
    """
    settings = get_settings()
    threshold = min_score_threshold or settings.min_retrieval_score

    # Step 1: Retrieve with hybrid+graph
    results = search_with_graph_expansion(
        query=question,
        repository_id=repository_id,
        limit=limit,
        graph_depth=graph_depth,
        explain=False
    )

    if not results:
        return AnswerResult(
            question=question,
            answer="Not found in the indexed code.",
            citations=[],
            snippets_used=0,
            retrieval_score=0.0,
            not_found=True
        )

    # Filter by score threshold
    top_results = [r for r in results if r.get('fused_score', 0) >= threshold]
    if not top_results:
        # No results above threshold
        return AnswerResult(
            question=question,
            answer="Not found in the indexed code.",
            citations=[],
            snippets_used=len(results),
            retrieval_score=max(r.get('fused_score', 0) for r in results),
            not_found=True
        )

    # Use top results (limit to max)
    snippets = top_results[:limit]
    max_score = max(s.get('fused_score', 0) for s in snippets)

    # Step 2: Build prompt
    prompt = build_prompt(question, snippets)

    # Step 3: Call LLM
    llm_client = get_llm_client()
    if llm_client is None:
        return AnswerResult(
            question=question,
            answer="LLM not configured. Set OPENROUTER_API_KEY in .env or use LLM_PROVIDER=fake.",
            citations=[],
            snippets_used=len(snippets),
            retrieval_score=max_score,
            not_found=False,
            error="no_llm"
        )

    try:
        answer = llm_client.generate(prompt)
    except Exception as e:
        return AnswerResult(
            question=question,
            answer=f"Error generating answer: {e}",
            citations=[],
            snippets_used=len(snippets),
            retrieval_score=max_score,
            not_found=False,
            error=str(e)
        )

    # Step 4: Validate citations
    cleaned_answer, valid_indices, invalid_indices = validate_citations(answer, snippets)

    if invalid_indices:
        # Add warning about invalid citations
        cleaned_answer += f"\n\n[Warning: Invalid citation indices {invalid_indices} were flagged]"

    citations = build_citation_objects(valid_indices, snippets)

    # Check if answer says "not found"
    not_found = "not found in the indexed code" in cleaned_answer.lower()

    return AnswerResult(
        question=question,
        answer=cleaned_answer.strip(),
        citations=citations,
        snippets_used=len(snippets),
        retrieval_score=max_score,
        not_found=not_found
    )


def search_symbols(
    query: str,
    repository_id: int,
    mode: str = "hybrid+graph",
    limit: int = 20,
    explain: bool = False
) -> List[Dict]:
    """Search symbols with various modes."""
    from codecompass.db.search import vector_search_with_ranks
    from codecompass.search.hybrid import bm25_search_mode

    if mode == "bm25":
        from codecompass.search.hybrid import bm25_search_mode
        return bm25_search_mode(query, repository_id, limit, explain)
    elif mode == "vector":
        query_embedding = compute_embedding(query)
        from codecompass.db.search import vector_search_with_ranks
        results = vector_search_with_ranks(query_embedding, repository_id, limit)
        for r in results:
            r["search_type"] = "vector"
        if explain:
            for r in results:
                r["score_explanation"] = {
                    "vector_rank": r.get("vector_rank"),
                    "vector_score": round(r.get("vector_score", 0.0), 4),
                }
        return results
    elif mode == "hybrid":
        query_embedding = compute_embedding(query)
        results = hybrid_search_with_explanation(
            query=query,
            query_embedding=query_embedding,
            repository_id=repository_id,
            limit=limit,
            bm25_limit=50,
            vector_limit=50,
            rrf_k=60
        )
        return results
    elif mode == "hybrid+graph":
        return search_with_graph_expansion(
            query=query,
            repository_id=repository_id,
            limit=limit,
            graph_depth=2,
            explain=explain
        )
    else:
        raise ValueError(f"Unknown search mode: {mode}")