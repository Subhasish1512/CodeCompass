"""
Language parser protocol.

Defines the interface that all language parsers must implement.
This allows adding new languages without changing the indexing pipeline.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Tuple

from .dataclasses import ParsedFile


class LanguageParser(ABC):
    """Abstract base class for language-specific parsers."""

    @property
    @abstractmethod
    def language_name(self) -> str:
        """Tree-sitter language name (e.g., 'python', 'javascript')."""
        pass

    @property
    @abstractmethod
    def file_extensions(self) -> Tuple[str, ...]:
        """File extensions this parser handles."""
        pass

    @abstractmethod
    def parse(self, content: str, file_path: Path) -> ParsedFile:
        """
        Parse a file and return symbols and edges.

        Args:
            content: Source code as string
            file_path: Path to the file (for relative imports, module name)

        Returns:
            ParsedFile with symbols, edges, and any syntax errors
        """
        pass

    def can_parse(self, file_path: Path) -> bool:
        """Check if this parser can handle the given file."""
        return file_path.suffix.lower() in self.file_extensions

    def is_binary(self, content: bytes) -> bool:
        """Quick check for binary files."""
        if not content:
            return False
        # Check for null bytes in first 8KB
        return b"\x00" in content[:8192]

    def is_too_large(self, content: bytes, max_size_mb: int = 1) -> bool:
        """Check if file exceeds size limit."""
        return len(content) > max_size_mb * 1024 * 1024