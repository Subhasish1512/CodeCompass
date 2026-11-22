"""
Benchmark harness for comparing search methods.

Evaluates: grep, BM25, Vector, Graph, Hybrid, Hybrid+Graph
on a set of hand-crafted questions for multiple repositories.
"""

from .questions import load_questions, Question

__all__ = ["run_benchmark", "load_questions", "Question"]