"""
Benchmark questions for evaluation.

Each question tests a specific capability:
- grep: exact string matching
- BM25: keyword-based relevance
- Vector: semantic similarity
- Graph: structural relationships (callers/callees)
- Hybrid: combined keyword + semantic
- Hybrid+Graph: hybrid + graph expansion
"""

from dataclasses import dataclass, asdict
from typing import Optional, List, Dict
from pathlib import Path
import json
import yaml


@dataclass
class Question:
    """A benchmark question with expected answer criteria."""
    id: str
    repo: str
    type: str  # 'definition', 'callers', 'callees', 'refs', 'impact', 'natural_language'
    question: str
    expected: List[str] = None  # Qualified names or file:line ranges
    tags: List[str] = None

    # Legacy fields for backward compatibility
    query: str = None
    description: str = None
    category: str = None
    repository: str = None
    expected_symbols: List[str] = None
    expected_files: List[str] = None
    min_recall_at_k: Dict = None

    def __post_init__(self):
        if self.expected is None:
            self.expected = []
        if self.tags is None:
            self.tags = []

        # Backward compatibility
        if self.query is None:
            self.query = self.question
        if self.description is None:
            self.description = ""
        if self.category is None:
            self.category = self.type
        if self.repository is None:
            self.repository = self.repo
        if self.expected_symbols is None:
            self.expected_symbols = self.expected
        if self.expected_files is None:
            self.expected_files = []
        if self.min_recall_at_k is None:
            self.min_recall_at_k = {}


# Sample questions for different repositories
# These should be customized per repository being benchmarked

SAMPLE_QUESTIONS = [
    # Definition finding
    Question(
        id="def-001",
        repo="sample",
        type="definition",
        question="function that parses JSON configuration",
        expected=["parse_config", "load_config"],
        tags=["exact", "keyword-heavy"]
    ),
    Question(
        id="def-002",
        repo="sample",
        type="definition",
        question="class representing a database connection pool",
        expected=["ConnectionPool", "Pool"],
        tags=["semantic", "class"]
    ),

    # Caller/callee traversal
    Question(
        id="graph-001",
        repo="sample",
        type="callers",
        question="functions that call the authentication middleware",
        expected=["auth_middleware", "authenticate"],
        tags=["graph", "transitive"]
    ),
    Question(
        id="graph-002",
        repo="sample",
        type="callees",
        question="what does the process_request function call",
        expected=["process_request"],
        tags=["graph", "direct"]
    ),

    # Impact analysis
    Question(
        id="impact-001",
        repo="sample",
        type="impact",
        question="impact of changing the User model schema",
        expected=["User", "user"],
        tags=["graph", "transitive", "impact"]
    ),

    # Semantic search
    Question(
        id="semantic-001",
        repo="sample",
        type="natural_language",
        question="error handling for database timeouts",
        expected=["timeout", "retry", "deadline"],
        tags=["semantic", "conceptual"]
    ),
    Question(
        id="semantic-002",
        repo="sample",
        type="natural_language",
        question="caching layer implementation",
        expected=["cache", "memoize", "lru"],
        tags=["semantic", "architectural"]
    ),

    # Reference finding
    Question(
        id="refs-001",
        repo="sample",
        type="refs",
        question="where is the Config class used",
        expected=["Config"],
        tags=["graph", "references"]
    ),
]


def load_questions(repository: str = None, path: str = None) -> List[Question]:
    """
    Load benchmark questions.

    Args:
        repository: Filter questions by repository name
        path: Optional path to custom questions YAML/JSON file

    Returns:
        List of Question objects
    """
    questions = list(SAMPLE_QUESTIONS)

    if path:
        # Load from custom file
        with open(path) as f:
            if path.endswith('.yaml') or path.endswith('.yml'):
                data = yaml.safe_load(f)
            else:
                data = json.load(f)
            questions = [Question(**q) for q in data]

    if repository:
        questions = [q for q in questions if q.repository == repository or q.repository == "sample"]

    return questions


def save_questions(questions: List[Question], path: str):
    """Save questions to JSON file."""
    data = [
        {
            "id": q.id,
            "query": q.query,
            "description": q.description,
            "category": q.category,
            "repository": q.repository,
            "expected_symbols": q.expected_symbols,
            "expected_files": q.expected_files,
            "min_recall_at_k": q.min_recall_at_k,
            "tags": q.tags,
        }
        for q in questions
    ]
    with open(path, "w") as f:
        json.dump(data, f, indent=2)