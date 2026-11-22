"""
Data classes for parsed output.

These are plain data containers with no database dependencies.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple


class SymbolKind(str, Enum):
    """Types of symbols that can be extracted."""
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    VARIABLE = "variable"
    MODULE = "module"


class EdgeKind(str, Enum):
    """Types of edges/relationships between symbols."""
    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"


@dataclass(frozen=True)
class ParsedSymbol:
    """A symbol definition extracted from source code."""
    name: str
    qualified_name: str
    kind: SymbolKind
    start_line: int
    end_line: int
    start_col: int
    end_col: int
    signature: Optional[str] = None
    docstring: Optional[str] = None
    parent_symbol_id: Optional[int] = None  # Index in the same file's symbol list


@dataclass(frozen=True)
class RawEdge:
    """A raw relationship between symbols (unresolved)."""
    kind: EdgeKind
    src_symbol_idx: int  # Index into the file's symbol list
    dst_name: str  # Target name as it appears in source (may be unresolved)
    dst_symbol_idx: Optional[int] = None  # Resolved index if found in same file
    line: int = 0
    col: int = 0
    confidence: float = 1.0
    # For imports: the module/path being imported
    import_module: Optional[str] = None
    import_alias: Optional[str] = None
    is_from_import: bool = False
    import_level: int = 0  # For relative imports (0=absolute, 1=., 2=.., etc.)


@dataclass(frozen=True)
class ParsedFile:
    """Complete parse result for a single file."""
    path: str
    language: str
    symbols: Tuple[ParsedSymbol, ...] = field(default_factory=tuple)
    edges: Tuple[RawEdge, ...] = field(default_factory=tuple)
    content_hash: Optional[bytes] = None
    syntax_errors: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_errors(self) -> bool:
        return len(self.syntax_errors) > 0

    def symbol_by_name(self, name: str) -> Optional[ParsedSymbol]:
        """Find a symbol by simple name (first match)."""
        for s in self.symbols:
            if s.name == name:
                return s
        return None

    def symbol_by_qualified_name(self, qname: str) -> Optional[ParsedSymbol]:
        """Find a symbol by qualified name."""
        for s in self.symbols:
            if s.qualified_name == qname:
                return s
        return None