"""
Tree-sitter language definitions and parser management.

Supports multiple languages via tree-sitter grammars.
Python uses tree-sitter-python directly; others use tree-sitter-languages.
"""

from functools import lru_cache
from typing import Optional

from tree_sitter import Language, Parser

# Try to import tree-sitter-languages for other languages
try:
    import tree_sitter_languages
    HAS_TREE_SITTER_LANGUAGES = True
except ImportError:
    HAS_TREE_SITTER_LANGUAGES = False

# File extension -> language name
EXTENSION_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".h": "cpp",
    ".c": "c",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".lua": "lua",
}

# Languages supported
SUPPORTED_LANGUAGES = [
    "python", "javascript", "typescript", "tsx", "go", "rust",
    "java", "cpp", "c", "c_sharp", "ruby", "php", "swift",
    "kotlin", "scala", "lua"
]


@lru_cache(maxsize=32)
def get_language(name: str) -> Optional[Language]:
    """Get a tree-sitter Language object by name."""
    if name not in SUPPORTED_LANGUAGES:
        return None

    # Python uses tree-sitter-python directly
    if name == "python":
        try:
            import tree_sitter_python
            return Language(tree_sitter_python.language())
        except Exception:
            return None

    # Other languages use tree-sitter-languages
    if HAS_TREE_SITTER_LANGUAGES:
        try:
            return tree_sitter_languages.get_language(name)
        except Exception:
            return None

    return None


@lru_cache(maxsize=32)
def get_parser(name: str) -> Optional[Parser]:
    """Get a tree-sitter Parser for a language."""
    lang = get_language(name)
    if lang is None:
        return None
    return Parser(lang)


def get_language_for_file(path: str) -> Optional[str]:
    """Determine language from file extension."""
    from pathlib import Path
    ext = Path(path).suffix.lower()
    return EXTENSION_MAP.get(ext)


def is_supported(path: str) -> bool:
    """Check if a file's language is supported."""
    return get_language_for_file(path) is not None