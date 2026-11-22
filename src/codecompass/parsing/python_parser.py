"""
Python parser implementation using tree-sitter.

Extracts symbols (functions, classes, methods, variables) and raw edges
(calls, imports, inheritance) from Python source code.
"""

import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import tree_sitter_python
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from .dataclasses import EdgeKind, ParsedFile, ParsedSymbol, RawEdge, SymbolKind
from .protocol import LanguageParser


class PythonParser(LanguageParser):
    """Python-specific parser using tree-sitter."""

    language_name = "python"
    file_extensions = (".py",)

    # Tree-sitter queries for symbol extraction
    SYMBOL_QUERY = """
    ; Module-level functions (including decorated and async)
    ; Must be direct child of module (not nested in class)
    (module
      (function_definition
        name: (identifier) @name
        parameters: (parameters) @params
        body: (block) @body) @function)

    ; Module-level decorated functions
    (module
      (decorated_definition
        definition: (function_definition
          name: (identifier) @name
          parameters: (parameters) @params
          body: (block) @body)
        (decorator)* @decorator) @function)

    ; Methods inside classes
    (class_definition
      body: (block
        (function_definition
          name: (identifier) @name
          parameters: (parameters) @params
          body: (block) @body) @method))

    ; Decorated methods inside classes
    (class_definition
      body: (block
        (decorated_definition
          definition: (function_definition
            name: (identifier) @name
            parameters: (parameters) @params
            body: (block) @body)
          (decorator)* @decorator) @method))

    ; Classes (including decorated) - module level
    (module
      (class_definition
        name: (identifier) @name
        body: (block) @body
        superclasses: (argument_list)? @superclasses) @class)

    ; Decorated classes - module level
    (module
      (decorated_definition
        definition: (class_definition
          name: (identifier) @name
          body: (block) @body
          superclasses: (argument_list)? @superclasses)
        (decorator)* @decorator) @class)

    ; Nested classes inside classes
    (class_definition
      body: (block
        (class_definition
          name: (identifier) @name
          body: (block) @body
          superclasses: (argument_list)? @superclasses) @class))

    ; Module-level variables (assignments, including typed)
    (module
      (expression_statement
        (assignment
          left: (identifier) @name
          right: (_) @value)) @variable)
    """

    # Tree-sitter queries for edge extraction
    EDGE_QUERY = """
    ; Function calls: foo()
    (call
      function: (identifier) @callee_name) @call

    ; Method calls: obj.method() and chained calls: a.b.c()
    (call
      function: (attribute
        attribute: (identifier) @callee_name)) @call

    ; Imports: import x
    (import_statement
      name: (dotted_name) @import_name) @import

    ; Imports with alias: import x as y
    (import_statement
      name: (aliased_import
        name: (dotted_name) @import_name
        alias: (identifier) @import_alias)) @import

    ; From imports: from x import y (non-relative)
    (import_from_statement
      module_name: (dotted_name) @import_module
      name: (dotted_name) @import_name) @from_import

    ; From imports with alias: from x import y as z (non-relative)
    (import_from_statement
      module_name: (dotted_name) @import_module
      name: (aliased_import
        name: (dotted_name) @import_name
        alias: (identifier) @import_alias)) @from_import

    ; Relative imports: from . import x
    (import_from_statement
      module_name: (relative_import) @relative_module
      name: (dotted_name) @import_name) @from_import

    ; Relative imports with alias: from . import x as y
    (import_from_statement
      module_name: (relative_import) @relative_module
      name: (aliased_import
        name: (dotted_name) @import_name
        alias: (identifier) @import_alias)) @from_import

    ; Class inheritance: class Foo(Bar, Baz)
    (class_definition
      name: (identifier) @class_name
      superclasses: (argument_list
        (identifier) @superclass_name)) @inheritance
    """

    def __init__(self):
        self._language: Optional[Language] = None
        self._parser: Optional[Parser] = None
        self._symbol_query = None
        self._edge_query = None

    def _get_language(self) -> Language:
        """Lazy-load the tree-sitter Python language."""
        if self._language is None:
            try:
                self._language = Language(tree_sitter_python.language())
            except Exception as e:
                raise RuntimeError(
                    "tree-sitter-python not installed. "
                    "Install with: pip install tree-sitter-python"
                ) from e
        return self._language

    def _get_parser(self) -> Parser:
        """Get or create the parser instance."""
        if self._parser is None:
            self._parser = Parser(self._get_language())
        return self._parser

    def _get_symbol_query(self):
        if self._symbol_query is None:
            self._symbol_query = Query(self._get_language(), self.SYMBOL_QUERY)
        return self._symbol_query

    def _get_edge_query(self):
        if self._edge_query is None:
            self._edge_query = Query(self._get_language(), self.EDGE_QUERY)
        return self._edge_query

    def parse(self, content: str, file_path: Path) -> ParsedFile:
        """
        Parse a Python file and extract symbols and edges.

        Args:
            content: Source code as string
            file_path: Path to the file (used for module name)

        Returns:
            ParsedFile with extracted symbols and edges
        """
        # Check for binary/too large
        content_bytes = content.encode("utf-8")
        if self.is_binary(content_bytes):
            return ParsedFile(
                path=str(file_path),
                language="python",
                syntax_errors=("File appears to be binary",)
            )
        if self.is_too_large(content_bytes):
            return ParsedFile(
                path=str(file_path),
                language="python",
                syntax_errors=(f"File exceeds size limit ({len(content_bytes)} bytes)",)
            )

        # Parse with tree-sitter
        parser = self._get_parser()
        tree = parser.parse(content_bytes)

        # Check for syntax errors
        syntax_errors = self._collect_syntax_errors(tree.root_node)
        if syntax_errors and tree.root_node.has_error:
            # Continue parsing anyway - tree-sitter provides partial trees
            pass

        # Compute content hash
        content_hash = hashlib.sha256(content_bytes).digest()

        # Extract symbols
        symbols = self._extract_symbols(tree.root_node, content, file_path)

        # Extract edges
        edges = self._extract_edges(tree.root_node, content, symbols, file_path)

        return ParsedFile(
            path=str(file_path),
            language="python",
            symbols=tuple(symbols),
            edges=tuple(edges),
            content_hash=content_hash,
            syntax_errors=tuple(syntax_errors)
        )

    def _collect_syntax_errors(self, node: Node) -> List[str]:
        """Collect syntax error messages from the tree."""
        errors = []
        if node.has_error:
            if node.type == "ERROR":
                errors.append(f"Syntax error at line {node.start_point[0] + 1}")
            for child in node.children:
                errors.extend(self._collect_syntax_errors(child))
        return errors

    def _extract_symbols(
        self,
        root: Node,
        content: str,
        file_path: Path
    ) -> List[ParsedSymbol]:
        """Extract all symbols from the AST."""
        query = self._get_symbol_query()
        cursor = QueryCursor(query)
        matches = cursor.matches(root)

        # First, collect all symbol info and sort by start line
        symbol_infos = []
        for match in matches:
            _, captures = match

            # Determine symbol kind
            kind = None
            main_node = None
            if "function" in captures:
                kind = SymbolKind.FUNCTION
                main_node = captures["function"][0]
            elif "method" in captures:
                kind = SymbolKind.METHOD
                main_node = captures["method"][0]
            elif "class" in captures:
                kind = SymbolKind.CLASS
                main_node = captures["class"][0]
            elif "variable" in captures:
                kind = SymbolKind.VARIABLE
                main_node = captures["variable"][0]
            else:
                continue

            name_nodes = captures.get("name")
            if not name_nodes:
                continue
            name_node = name_nodes[0]

            start_line = main_node.start_point[0] + 1
            end_line = main_node.end_point[0] + 1

            symbol_infos.append({
                'captures': captures,
                'kind': kind,
                'main_node': main_node,
                'name_node': name_node,
                'start_line': start_line,
                'end_line': end_line,
            })

        # Sort by start line to ensure parents come before children
        symbol_infos.sort(key=lambda x: x['start_line'])

        symbols = []
        source_lines = content.splitlines()
        module_name = self._get_module_name(file_path)

        # Track parent context for qualified names
        parent_stack: List[Tuple[int, str]] = []  # (symbol_idx, qualified_name)

        for info in symbol_infos:
            captures = info['captures']
            kind = info['kind']
            main_node = info['main_node']
            name_node = info['name_node']
            start_line = info['start_line']
            end_line = info['end_line']

            # Pop stack for any parents that have ended before this symbol starts
            # (i.e., we've exited their scope) - DO THIS FIRST
            parent_stack = [(idx, qn, el) for (idx, qn, el) in parent_stack if el >= start_line]

            name = self._node_text(name_node, content)

            # Build qualified name
            if parent_stack:
                qualified_name = f"{parent_stack[-1][1]}.{name}"
            else:
                qualified_name = f"{module_name}.{name}" if module_name else name

            start_col = main_node.start_point[1]
            end_col = main_node.end_point[1]

            # Get signature and docstring
            signature = self._extract_signature(captures, content, kind)
            docstring = self._extract_docstring(captures, content, source_lines)

            # Find parent symbol index based on line containment
            parent_idx = None
            if kind in (SymbolKind.METHOD, SymbolKind.FUNCTION):
                # Find the innermost parent that contains this symbol
                for j in range(len(symbols) - 1, -1, -1):
                    p = symbols[j]
                    if p.start_line <= start_line <= p.end_line and p.kind in (SymbolKind.CLASS, SymbolKind.FUNCTION):
                        parent_idx = j
                        break

            symbol = ParsedSymbol(
                name=name,
                qualified_name=qualified_name,
                kind=kind,
                start_line=start_line,
                end_line=end_line,
                start_col=start_col,
                end_col=end_col,
                signature=signature,
                docstring=docstring,
                parent_symbol_id=parent_idx
            )
            symbols.append(symbol)

            # Update parent stack for nested symbols
            if kind in (SymbolKind.CLASS, SymbolKind.FUNCTION):
                symbol_idx = len(symbols) - 1
                parent_stack.append((symbol_idx, qualified_name, end_line))

            # Pop stack when we exit the scope
            # (This is a simplification - proper scope tracking needs more work)
            # We'll handle this by checking line numbers after

        # Fix parent stack based on line ranges (simple approach)
        # For now, we'll do a post-process to set correct parents
        self._fix_parent_references(symbols)

        return symbols

    def _fix_parent_references(self, symbols: List[ParsedSymbol]) -> None:
        """Fix parent_symbol_id based on line ranges."""
        # Convert to mutable list for modification
        for i, sym in enumerate(symbols):
            if sym.kind in (SymbolKind.METHOD, SymbolKind.FUNCTION) and sym.parent_symbol_id is not None:
                # Find the actual parent by line containment
                parent_idx = sym.parent_symbol_id
                if 0 <= parent_idx < len(symbols):
                    parent = symbols[parent_idx]
                    if parent.start_line <= sym.start_line <= parent.end_line:
                        # Parent is correct
                        continue
                # Find correct parent
                for j in range(i - 1, -1, -1):
                    p = symbols[j]
                    if p.start_line <= sym.start_line <= p.end_line and p.kind in (SymbolKind.CLASS, SymbolKind.FUNCTION):
                        # Create new symbol with corrected parent
                        symbols[i] = ParsedSymbol(
                            name=sym.name,
                            qualified_name=sym.qualified_name,
                            kind=sym.kind,
                            start_line=sym.start_line,
                            end_line=sym.end_line,
                            start_col=sym.start_col,
                            end_col=sym.end_col,
                            signature=sym.signature,
                            docstring=sym.docstring,
                            parent_symbol_id=j
                        )
                        break

    def _extract_edges(
        self,
        root: Node,
        content: str,
        symbols: List[ParsedSymbol],
        file_path: Path
    ) -> List[RawEdge]:
        """Extract raw edges (calls, imports, inheritance) from the AST."""
        query = self._get_edge_query()
        cursor = QueryCursor(query)
        matches = cursor.matches(root)

        edges = []
        source_lines = content.splitlines()

        # Build lookup maps for resolving references within the file
        symbol_by_name = {}
        for idx, sym in enumerate(symbols):
            symbol_by_name.setdefault(sym.name, []).append(idx)
            symbol_by_name.setdefault(sym.qualified_name, []).append(idx)

        for match in matches:
            _, captures = match

            # Determine edge type
            if "call" in captures:
                edge = self._extract_call_edge(captures, content, symbols, symbol_by_name)
                if edge:
                    edges.append(edge)

            elif "import" in captures:
                edge = self._extract_import_edge(captures, content, symbols, source_lines)
                if edge:
                    edges.append(edge)

            elif "from_import" in captures:
                edge = self._extract_from_import_edge(captures, content, symbols, source_lines)
                if edge:
                    edges.append(edge)

            elif "inheritance" in captures:
                edge = self._extract_inheritance_edge(captures, content, symbols, symbol_by_name)
                if edge:
                    edges.append(edge)

        return edges

    def _extract_call_edge(
        self,
        captures: Dict[str, List[Node]],
        content: str,
        symbols: List[ParsedSymbol],
        symbol_by_name: Dict[str, List[int]]
    ) -> Optional[RawEdge]:
        """Extract a function/method call edge."""
        callee_nodes = captures.get("callee_name")
        if not callee_nodes:
            return None
        callee_node = callee_nodes[0]

        callee_name = self._node_text(callee_node, content)
        call_nodes = captures["call"]
        call_node = call_nodes[0]
        line = call_node.start_point[0] + 1
        col = call_node.start_point[1]

        # Find enclosing symbol (the caller)
        src_idx = self._find_enclosing_symbol(line, symbols)
        if src_idx is None:
            return None

        # Try to resolve callee in same file
        dst_idx = None
        candidates = symbol_by_name.get(callee_name, [])
        if candidates:
            # Prefer the first match (could be improved with scope awareness)
            dst_idx = candidates[0]

        return RawEdge(
            kind=EdgeKind.CALLS,
            src_symbol_idx=src_idx,
            dst_name=callee_name,
            dst_symbol_idx=dst_idx,
            line=line,
            col=col
        )

    def _extract_import_edge(
        self,
        captures: Dict[str, List[Node]],
        content: str,
        symbols: List[ParsedSymbol],
        source_lines: List[str]
    ) -> Optional[RawEdge]:
        """Extract an import statement edge."""
        import_nodes = captures["import"]
        import_node = import_nodes[0]
        name_nodes = captures.get("import_name")
        alias_nodes = captures.get("import_alias")

        if not name_nodes:
            return None
        name_node = name_nodes[0]
        alias_node = alias_nodes[0] if alias_nodes else None

        module_name = self._node_text(name_node, content)
        alias = self._node_text(alias_node, content) if alias_node else None
        line = import_node.start_point[0] + 1
        col = import_node.start_point[1]

        # Module-level imports have no enclosing symbol - use -1
        src_idx = -1

        return RawEdge(
            kind=EdgeKind.IMPORTS,
            src_symbol_idx=src_idx,
            dst_name=module_name,
            dst_symbol_idx=None,  # External module
            line=line,
            col=col,
            import_module=module_name,
            import_alias=alias,
            is_from_import=False,
            import_level=0
        )

    def _extract_from_import_edge(
        self,
        captures: Dict[str, List[Node]],
        content: str,
        symbols: List[ParsedSymbol],
        source_lines: List[str]
    ) -> Optional[RawEdge]:
        """Extract a from-import statement edge."""
        import_nodes = captures["from_import"]
        import_node = import_nodes[0]
        name_nodes = captures.get("import_name")
        alias_nodes = captures.get("import_alias")
        module_nodes = captures.get("import_module")
        relative_nodes = captures.get("relative_module")

        if not name_nodes:
            return None
        name_node = name_nodes[0]
        alias_node = alias_nodes[0] if alias_nodes else None
        module_node = module_nodes[0] if module_nodes else None
        relative_node = relative_nodes[0] if relative_nodes else None

        imported_name = self._node_text(name_node, content)
        alias = self._node_text(alias_node, content) if alias_node else None

        # Determine module being imported from
        if relative_node:
            # Relative import: from . import x
            level = self._count_relative_level(relative_node, content)
            import_module = "." * level
            import_level = level
        elif module_node:
            import_module = self._node_text(module_node, content)
            import_level = 0
        else:
            import_module = ""
            import_level = 0

        line = import_node.start_point[0] + 1
        col = import_node.start_point[1]
        src_idx = -1  # Module-level

        return RawEdge(
            kind=EdgeKind.IMPORTS,
            src_symbol_idx=src_idx,
            dst_name=imported_name,
            dst_symbol_idx=None,  # External
            line=line,
            col=col,
            import_module=import_module,
            import_alias=alias,
            is_from_import=True,
            import_level=import_level
        )

    def _extract_inheritance_edge(
        self,
        captures: Dict[str, List[Node]],
        content: str,
        symbols: List[ParsedSymbol],
        symbol_by_name: Dict[str, List[int]]
    ) -> Optional[RawEdge]:
        """Extract a class inheritance edge."""
        class_nodes = captures["inheritance"]
        class_node = class_nodes[0]
        class_name_nodes = captures.get("class_name")
        superclass_nodes = captures.get("superclass_name")

        if not class_name_nodes or not superclass_nodes:
            return None
        class_name_node = class_name_nodes[0]
        superclass_node = superclass_nodes[0]

        class_name = self._node_text(class_name_node, content)
        superclass_name = self._node_text(superclass_node, content)
        line = class_node.start_point[0] + 1
        col = class_node.start_point[1]

        # Find the class symbol
        src_idx = None
        for idx, sym in enumerate(symbols):
            if sym.name == class_name and sym.kind == SymbolKind.CLASS:
                src_idx = idx
                break

        if src_idx is None:
            return None

        # Try to resolve superclass in same file
        dst_idx = None
        candidates = symbol_by_name.get(superclass_name, [])
        if candidates:
            dst_idx = candidates[0]

        return RawEdge(
            kind=EdgeKind.INHERITS,
            src_symbol_idx=src_idx,
            dst_name=superclass_name,
            dst_symbol_idx=dst_idx,
            line=line,
            col=col
        )

    def _find_enclosing_symbol(self, line: int, symbols: List[ParsedSymbol]) -> Optional[int]:
        """Find the symbol that encloses a given line number."""
        # Find the innermost symbol that contains this line
        enclosing = None
        for idx, sym in enumerate(symbols):
            if sym.start_line <= line <= sym.end_line:
                enclosing = idx
        return enclosing if enclosing is not None else 0

    def _get_module_name(self, file_path: Path) -> str:
        """Extract module name from file path."""
        # Remove .py extension and convert path to module notation
        name = file_path.stem
        if name == "__init__":
            # Package init file - use parent directory
            return file_path.parent.name
        return name

    def _extract_signature(
        self,
        captures: Dict[str, List[Node]],
        content: str,
        kind: SymbolKind
    ) -> Optional[str]:
        """Extract function/class signature."""
        if kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
            return None

        params_nodes = captures.get("params")
        if not params_nodes:
            return None
        params_node = params_nodes[0]

        params_text = self._node_text(params_node, content)
        return f"({params_text})"

    def _extract_docstring(
        self,
        captures: Dict[str, List[Node]],
        content: str,
        source_lines: List[str]
    ) -> Optional[str]:
        """Extract docstring from function/class definition."""
        body_nodes = captures.get("body")
        if not body_nodes:
            return None
        body_node = body_nodes[0]
        if not body_node.children:
            return None

        # First statement in body should be a string literal
        first_stmt = body_node.children[0]
        if first_stmt.type == "expression_statement":
            expr = first_stmt.children[0] if first_stmt.children else None
            if expr and expr.type == "string":
                text = self._node_text(expr, content)
                # Strip quotes
                return text.strip('"\'')

    def _count_relative_level(self, node: Node, content: str) -> int:
        """Count the number of dots in a relative import."""
        text = self._node_text(node, content)
        # text looks like "...module" or just "."
        if text.startswith("."):
            # Count leading dots
            count = 0
            for c in text:
                if c == ".":
                    count += 1
                else:
                    break
            return count
        return 0

    def _node_text(self, node: Node, content: str) -> str:
        """Get text content of a node."""
        return content[node.start_byte:node.end_byte]