"""
Edge resolver for turning raw edges into resolved edges.

Resolution strategies (in order):
(a) Same-file definitions
(b) Import-table lookup (aliases, from-imports, relative imports)
(c) Self/cls method resolution within enclosing class and its base classes
(d) Unique global name match

Confidence levels:
- 1.0: Exact match (same file, import-based with alias)
- 0.7: Import-based resolution
- 0.4: Name-only match (unique global)

Leaves dst_symbol_id NULL if ambiguous.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from codecompass.db import get_db
from codecompass.db.repository import (
    get_edges_from,
    get_symbol,
    get_symbols_by_name,
)
from codecompass.parsing import EdgeKind, RawEdge


@dataclass
class ImportInfo:
    """Information about an import statement."""
    module: str           # Module being imported from
    name: str             # Name being imported
    alias: Optional[str]  # Alias (if any)
    is_from_import: bool  # True for "from x import y"
    import_level: int     # 0 for absolute, >0 for relative
    line: int
    col: int


@dataclass
class ResolvedEdge:
    """An edge with resolved target symbol."""
    src_symbol_id: int
    dst_symbol_id: Optional[int]
    dst_name: str
    edge_type: str
    confidence: float
    resolution_method: str  # 'same_file', 'import', 'self_cls', 'global_unique', 'unresolved'
    src_file_id: int
    dst_file_id: Optional[int]
    line: int
    col: int


class ImportTable:
    """Builds and manages import resolution table for a file."""

    def __init__(self):
        # Maps local name -> (module, imported_name, alias)
        self.imports: Dict[str, Tuple[str, str, Optional[str]]] = {}
        # Maps alias -> (module, imported_name)
        self.aliases: Dict[str, Tuple[str, str]] = {}

    def add_import(self, module: str, name: str, alias: Optional[str] = None) -> None:
        """Add an import statement."""
        local_name = alias or name
        self.imports[local_name] = (module, name, alias)
        if alias:
            self.aliases[alias] = (module, name)

    def add_from_import(self, module: str, name: str, alias: Optional[str] = None, level: int = 0) -> None:
        """Add a from-import statement."""
        local_name = alias or name
        full_module = "." * level + module if level > 0 else module
        self.imports[local_name] = (full_module, name, alias)
        if alias:
            self.aliases[alias] = (full_module, name)

    def resolve(self, name: str) -> Optional[Tuple[str, str]]:
        """
        Resolve a local name to (module, imported_name).

        Returns (module, imported_name) if found, None otherwise.
        """
        if name in self.imports:
            module, imported_name, _ = self.imports[name]
            return (module, imported_name)
        return None


def build_import_table(parsed_file) -> ImportTable:
    """Build import table from parsed file edges."""
    table = ImportTable()

    for edge in parsed_file.edges:
        if edge.kind == EdgeKind.IMPORTS:
            # Get import details from edge attributes
            module = getattr(edge, 'import_module', '')
            alias = getattr(edge, 'import_alias', None)
            is_from = getattr(edge, 'is_from_import', False)
            level = getattr(edge, 'import_level', 0)

            if is_from:
                table.add_from_import(module, edge.dst_name, alias, level)
            else:
                table.add_import(module, edge.dst_name, alias)

    return table


class EdgeResolver:
    """Resolves raw edges to database symbol IDs."""

    def __init__(self, repository_id: int):
        self.repository_id = repository_id
        self.db = get_db()
        # Cache for symbol lookups
        self._symbol_cache: Dict[str, List[dict]] = {}
        self._qualified_name_cache: Dict[str, dict] = {}

    def _get_symbols_by_name(self, name: str) -> List[dict]:
        """Get symbols by name with caching."""
        if name not in self._symbol_cache:
            self._symbol_cache[name] = get_symbols_by_name(name, self.repository_id)
        return self._symbol_cache[name]

    def _get_symbol_by_qualified_name(self, qualified_name: str) -> Optional[dict]:
        """Get symbol by qualified name with caching."""
        if qualified_name not in self._qualified_name_cache:
            # Search by qualified_name in v_symbols_with_file
            rows = self.db.execute(
                "SELECT * FROM v_symbols_with_file WHERE qualified_name = %s AND repo_id = %s",
                (qualified_name, self.repository_id)
            )
            self._qualified_name_cache[qualified_name] = rows[0] if rows else None
        return self._qualified_name_cache[qualified_name]

    def resolve_edges(
        self,
        file_id: int,
        symbols: List[dict],
        raw_edges: List[RawEdge],
        import_table: ImportTable,
    ) -> List[ResolvedEdge]:
        """
        Resolve all raw edges for a file.

        Args:
            file_id: Database file ID
            symbols: List of symbol dicts in this file (with id, name, qualified_name, kind, etc.)
            raw_edges: Raw edges from parser
            import_table: Import table for this file

        Returns:
            List of resolved edges
        """
        # Build lookup maps
        symbols_by_idx = dict(enumerate(symbols))
        symbols_by_name = defaultdict(list)
        for s in symbols:
            symbols_by_name[s["name"]].append(s)
            symbols_by_name[s["qualified_name"]].append(s)

        # Build class hierarchy for self/cls resolution
        class_hierarchy = self._build_class_hierarchy(symbols)

        resolved = []

        for edge in raw_edges:
            resolved_edge = self._resolve_single_edge(
                edge, file_id, symbols_by_idx, symbols_by_name,
                import_table, class_hierarchy, symbols
            )
            resolved.append(resolved_edge)

        return resolved

    def _build_class_hierarchy(self, symbols: List[dict]) -> Dict[int, List[int]]:
        """Build class inheritance hierarchy for method resolution."""
        hierarchy = defaultdict(list)

        # Find all classes
        classes = [s for s in symbols if s["kind"] == "class"]
        class_by_name = {c["name"]: c for c in classes}
        class_by_qname = {c["qualified_name"]: c for c in classes}

        # Get inheritance edges
        for cls in classes:
            cls_id = cls["id"]
            # Get inherits edges
            edges = get_edges_from(cls_id, edge_type="inherits")
            for edge in edges:
                if edge["dst_symbol_id"]:
                    hierarchy[cls_id].append(edge["dst_symbol_id"])
                elif edge["dst_name"] in class_by_name:
                    hierarchy[cls_id].append(class_by_name[edge["dst_name"]]["id"])
                elif edge["dst_name"] in class_by_qname:
                    hierarchy[cls_id].append(class_by_qname[edge["dst_name"]]["id"])

        return hierarchy

    def _resolve_single_edge(
        self,
        edge: RawEdge,
        file_id: int,
        symbols_by_idx: Dict[int, dict],
        symbols_by_name: Dict[str, List[dict]],
        import_table: ImportTable,
        class_hierarchy: Dict[int, List[int]],
        all_symbols: List[dict],
    ) -> ResolvedEdge:
        """Resolve a single raw edge."""
        src_idx = edge.src_symbol_idx
        if src_idx is None or src_idx not in symbols_by_idx:
            return ResolvedEdge(
                src_symbol_id=0,
                dst_symbol_id=None,
                dst_name=edge.dst_name,
                edge_type=edge.kind.value,
                confidence=0.0,
                resolution_method="unresolved",
                src_file_id=file_id,
                dst_file_id=None,
                line=edge.line,
                col=edge.col
            )

        src_symbol = symbols_by_idx[src_idx]
        src_sym_id = src_symbol["id"]

        # Strategy (a): Same-file definitions
        if edge.dst_symbol_idx is not None and edge.dst_symbol_idx in symbols_by_idx:
            dst_symbol = symbols_by_idx[edge.dst_symbol_idx]
            return ResolvedEdge(
                src_symbol_id=src_sym_id,
                dst_symbol_id=dst_symbol["id"],
                dst_name=edge.dst_name,
                edge_type=edge.kind.value,
                confidence=1.0,
                resolution_method="same_file",
                src_file_id=file_id,
                dst_file_id=file_id,
                line=edge.line,
                col=edge.col
            )

        # Strategy (b): Import-table lookup
        if edge.kind == EdgeKind.CALLS:
            import_resolution = import_table.resolve(edge.dst_name)
            if import_resolution:
                module, imported_name = import_resolution
                # Try to find the imported symbol
                dst_sym = self._find_imported_symbol(module, imported_name)
                if dst_sym:
                    return ResolvedEdge(
                        src_symbol_id=src_sym_id,
                        dst_symbol_id=dst_sym["id"],
                        dst_name=edge.dst_name,
                        edge_type=edge.kind.value,
                        confidence=0.7,
                        resolution_method="import",
                        src_file_id=file_id,
                        dst_file_id=dst_sym["file_id"],
                        line=edge.line,
                        col=edge.col
                    )

        # Strategy (c): Self/cls method resolution
        if src_symbol["kind"] == "method" and edge.dst_name in ("self", "cls"):
            resolved = self._resolve_self_cls(
                edge, src_symbol, class_hierarchy, symbols_by_name
            )
            if resolved:
                return resolved

        # Strategy (d): Unique global name match
        candidates = self._get_symbols_by_name(edge.dst_name)
        if len(candidates) == 1:
            return ResolvedEdge(
                src_symbol_id=src_sym_id,
                dst_symbol_id=candidates[0]["id"],
                dst_name=edge.dst_name,
                edge_type=edge.kind.value,
                confidence=0.4,
                resolution_method="global_unique",
                src_file_id=file_id,
                dst_file_id=candidates[0]["file_id"],
                line=edge.line,
                col=edge.col
            )
        elif len(candidates) > 1:
            # Ambiguous - try to disambiguate by module
            disambiguated = self._disambiguate_by_module(
                edge.dst_name, candidates, src_symbol
            )
            if disambiguated:
                return ResolvedEdge(
                    src_symbol_id=src_sym_id,
                    dst_symbol_id=disambiguated["id"],
                    dst_name=edge.dst_name,
                    edge_type=edge.kind.value,
                    confidence=0.5,
                    resolution_method="global_unique",
                    src_file_id=file_id,
                    dst_file_id=disambiguated["file_id"],
                    line=edge.line,
                    col=edge.col
                )

        # Unresolved
        return ResolvedEdge(
            src_symbol_id=src_sym_id,
            dst_symbol_id=None,
            dst_name=edge.dst_name,
            edge_type=edge.kind.value,
            confidence=0.0,
            resolution_method="unresolved",
            src_file_id=file_id,
            dst_file_id=None,
            line=edge.line,
            col=edge.col
        )

    def _find_imported_symbol(self, module: str, name: str) -> Optional[dict]:
        """Find a symbol that was imported from a module."""
        # This is a simplified version - in practice we'd need to track
        # which symbols come from which modules
        # For now, try qualified name match
        qualified = f"{module}.{name}" if module else name
        return self._get_symbol_by_qualified_name(qualified)

    def _resolve_self_cls(
        self,
        edge: RawEdge,
        src_symbol: dict,
        class_hierarchy: Dict[int, List[int]],
        symbols_by_name: Dict[str, List[dict]],
    ) -> Optional[ResolvedEdge]:
        """Resolve self/cls method calls within class hierarchy."""
        scope_id = src_symbol.get("scope_symbol_id")
        if not scope_id:
            return None

        # Get the class this method belongs to
        cls_symbol = get_symbol(scope_id)
        if not cls_symbol or cls_symbol["kind"] != "class":
            return None

        # Look for method in this class or its parents
        method_name = edge.dst_name  # This would be the actual method name
        # Actually, for self.method(), the dst_name is the method name
        # The parser should have created an edge with the method name

        # Search in class hierarchy
        visited = set()
        to_check = [cls_symbol["id"]]

        while to_check:
            current_id = to_check.pop()
            if current_id in visited:
                continue
            visited.add(current_id)

            # Get methods in this class
            methods = [s for s in symbols_by_name.get(method_name, [])
                      if s.get("scope_symbol_id") == current_id and s["kind"] == "method"]
            if methods:
                return ResolvedEdge(
                    src_symbol_id=src_symbol["id"],
                    dst_symbol_id=methods[0]["id"],
                    dst_name=edge.dst_name,
                    edge_type=edge.kind.value,
                    confidence=0.7,
                    resolution_method="self_cls",
                    src_file_id=src_symbol["file_id"],
                    dst_file_id=methods[0]["file_id"],
                    line=edge.line,
                    col=edge.col
                )

            # Check parent classes
            to_check.extend(class_hierarchy.get(current_id, []))

        return None

    def _disambiguate_by_module(
        self,
        name: str,
        candidates: List[dict],
        src_symbol: dict,
    ) -> Optional[dict]:
        """Try to disambiguate by module path."""
        src_file_path = src_symbol.get("file_path", "")
        if not src_file_path:
            return None

        # Get module prefix (e.g., "mypackage.module")
        src_parts = src_file_path.replace("/", ".").replace(".py", "").split(".")
        if len(src_parts) < 2:
            return None

        # Try to find candidate in same package
        for candidate in candidates:
            cand_path = candidate.get("file_path", "")
            cand_parts = cand_path.replace("/", ".").replace(".py", "").split(".")
            if len(cand_parts) >= 2 and cand_parts[0] == src_parts[0]:
                return candidate

        return None


def resolve_edges_for_file(
    repository_id: int,
    file_id: int,
    symbols: List[dict],
    raw_edges: List[RawEdge],
    import_table: ImportTable,
) -> List[ResolvedEdge]:
    """
    Convenience function to resolve edges for a single file.

    Args:
        repository_id: Database repository ID
        file_id: Database file ID
        symbols: Symbols in this file
        raw_edges: Raw edges from parser
        import_table: Import table for this file

    Returns:
        List of resolved edges
    """
    resolver = EdgeResolver(repository_id)
    return resolver.resolve_edges(file_id, symbols, raw_edges, import_table)