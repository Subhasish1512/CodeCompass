"""
Parsing module for CodeCompass.

Provides language-agnostic parsing interface and Python implementation
using tree-sitter.
"""

from .dataclasses import EdgeKind, ParsedFile, ParsedSymbol, RawEdge, SymbolKind
from .languages import SUPPORTED_LANGUAGES, get_language_for_file, is_supported
from .protocol import LanguageParser
from .python_parser import PythonParser

__all__ = [
    "LanguageParser",
    "PythonParser",
    "ParsedFile",
    "ParsedSymbol",
    "RawEdge",
    "SymbolKind",
    "EdgeKind",
    "get_language_for_file",
    "is_supported",
    "SUPPORTED_LANGUAGES",
]