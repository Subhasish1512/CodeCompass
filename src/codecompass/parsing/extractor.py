"""
Symbol and edge extraction using tree-sitter queries.

Each language has a set of queries to find definitions (symbols) and
references/calls (edges). The extractor runs these queries and normalizes
results into a common format.
"""

from dataclasses import dataclass
from typing import Optional

import tree_sitter
from tree_sitter import Node, Query, QueryCursor

from codecompass.parsing.languages import get_language, get_parser


@dataclass
class Symbol:
    """A symbol definition extracted from source code."""
    name: str
    kind: str                    # function, class, method, variable, constant, interface, etc.
    signature: Optional[str]     # Full signature for functions/methods
    docstring: Optional[str]     # Documentation comment
    start_line: int              # 1-indexed
    end_line: int
    start_col: int               # 0-indexed
    end_col: int
    scope_symbol_id: Optional[int] = None  # Parent symbol ID (set during indexing)


@dataclass
class Edge:
    """A reference or call edge between symbols."""
    src_symbol_id: int           # Symbol making the reference/call
    dst_symbol_id: int           # Symbol being referenced/called
    edge_type: str               # 'calls', 'references', 'inherits', 'imports'
    src_file_id: int             # File where edge originates
    dst_file_id: Optional[int]   # File where target is defined (None for external)
    line: int                    # Line in source file (1-indexed)
    col: int                     # Column in source file (0-indexed)


# ============================================================================
# TREE-SITTER QUERIES
# ============================================================================

# Common query patterns - each language extends these
BASE_SYMBOL_QUERIES = {
    "function": """
        (function_definition
          name: (identifier) @name
          parameters: (parameters) @params
          body: (_) @body) @function
    """,
    "class": """
        (class_definition
          name: (identifier) @name
          body: (_) @body) @class
    """,
    "method": """
        (function_definition
          name: (identifier) @name
          parameters: (parameters) @params
          body: (_) @body) @method
    """,
}

# Language-specific queries for symbol extraction
SYMBOL_QUERIES = {
    "python": """
        ; Functions
        (function_definition
          name: (identifier) @name
          parameters: (parameters) @params
          ["async"]? @async
          body: (block) @body) @function

        ; Classes
        (class_definition
          name: (identifier) @name
          body: (block) @body) @class

        ; Async functions
        (async_function_definition
          name: (identifier) @name
          parameters: (parameters) @params
          body: (block) @body) @function

        ; Decorated functions
        (decorated_definition
          (function_definition
            name: (identifier) @name
            parameters: (parameters) @params
            body: (block) @body) @function)

        ; Lambda (anonymous) - skip for now
        ; Assignments with function values
        (assignment
          left: (identifier) @name
          right: (lambda) @lambda) @variable

        ; Type aliases
        (type_alias
          name: (identifier) @name
          value: (_) @value) @type

        ; Module-level variables (simple assignments)
        (expression_statement
          (assignment
            left: (identifier) @name
            right: (_) @value)) @variable
    """,
    "javascript": """
        ; Functions
        (function_declaration
          name: (identifier) @name
          parameters: (formal_parameters) @params
          body: (statement_block) @body) @function

        (function_expression
          name: (identifier)? @name
          parameters: (formal_parameters) @params
          body: (statement_block) @body) @function

        (arrow_function
          parameters: (formal_parameters) @params
          body: (_) @body) @function

        ; Classes
        (class_declaration
          name: (identifier) @name
          body: (class_body) @body) @class

        ; Methods
        (method_definition
          name: (property_identifier) @name
          parameters: (formal_parameters) @params
          body: (statement_block) @body) @method

        ; Variables
        (variable_declarator
          name: (identifier) @name
          value: (_) @value) @variable

        (lexical_declaration
          (variable_declarator
            name: (identifier) @name
            value: (_) @value)) @variable
    """,
    "typescript": """
        ; Functions (extends javascript)
        (function_declaration
          name: (identifier) @name
          type_parameters: (type_parameters)? @type_params
          parameters: (formal_parameters) @params
          return_type: (type_annotation)? @return_type
          body: (statement_block) @body) @function

        (function_expression
          name: (identifier)? @name
          type_parameters: (type_parameters)? @type_params
          parameters: (formal_parameters) @params
          return_type: (type_annotation)? @return_type
          body: (statement_block) @body) @function

        (arrow_function
          type_parameters: (type_parameters)? @type_params
          parameters: (formal_parameters) @params
          return_type: (type_annotation)? @return_type
          body: (_) @body) @function

        ; Classes
        (class_declaration
          name: (type_identifier) @name
          type_parameters: (type_parameters)? @type_params
          heritage: (class_heritage)? @heritage
          body: (class_body) @body) @class

        ; Interfaces
        (interface_declaration
          name: (type_identifier) @name
          type_parameters: (type_parameters)? @type_params
          body: (interface_body) @body) @interface

        ; Type aliases
        (type_alias_declaration
          name: (type_identifier) @name
          type_parameters: (type_parameters)? @type_params
          value: (type) @value) @type

        ; Methods
        (method_definition
          name: (property_identifier) @name
          type_parameters: (type_parameters)? @type_params
          parameters: (formal_parameters) @params
          return_type: (type_annotation)? @return_type
          body: (statement_block) @body) @method

        ; Variables
        (variable_declarator
          name: (identifier) @name
          value: (_) @value) @variable
    """,
    "go": """
        ; Functions
        (function_declaration
          name: (identifier) @name
          parameters: (parameter_list) @params
          result: (parameter_list)? @results
          body: (block) @body) @function

        ; Methods
        (method_declaration
          receiver: (parameter_list) @receiver
          name: (identifier) @name
          parameters: (parameter_list) @params
          result: (parameter_list)? @results
          body: (block) @body) @method

        ; Types (structs, interfaces)
        (type_declaration
          (type_spec
            name: (type_identifier) @name
            type: (_) @type)) @type

        ; Variables
        (var_declaration
          (var_spec
            name: (identifier) @name
            value: (_)? @value)) @variable

        (short_var_declaration
          left: (identifier) @name
          right: (_) @value) @variable

        ; Constants
        (const_declaration
          (const_spec
            name: (identifier) @name
            value: (_)? @value)) @constant
    """,
    "rust": """
        ; Functions
        (function_item
          name: (identifier) @name
          parameters: (parameters) @params
          return_type: (type)? @return_type
          body: (block) @body) @function

        ; Methods (in impl blocks)
        (impl_item
          (function_item
            name: (identifier) @name
            parameters: (parameters) @params
            return_type: (type)? @return_type
            body: (block) @body)) @method

        ; Structs
        (struct_item
          name: (type_identifier) @name
          body: (_) @body) @class

        ; Enums
        (enum_item
          name: (type_identifier) @name
          body: (enum_variants) @body) @class

        ; Traits (interfaces)
        (trait_item
          name: (type_identifier) @name
          body: (declaration_list) @body) @interface

        ; Impl blocks (for type)
        (impl_item
          type: (type_identifier) @type) @impl

        ; Constants
        (const_item
          name: (identifier) @name
          type: (type) @type
          value: (expression) @value) @constant

        ; Static items
        (static_item
          name: (identifier) @name
          type: (type) @type
          value: (expression) @value) @variable

        ; Modules
        (mod_item
          name: (identifier) @name
          body: (declaration_list)? @body) @module

        ; Type aliases
        (type_item
          name: (type_identifier) @name
          type: (type) @type) @type

        ; Macros (skip)
    """,
    "java": """
        ; Methods
        (method_declaration
          name: (identifier) @name
          type_parameters: (type_parameters)? @type_params
          parameters: (formal_parameters) @params
          return_type: (type)? @return_type
          body: (block) @body) @method

        ; Constructors
        (constructor_declaration
          name: (identifier) @name
          parameters: (formal_parameters) @params
          body: (constructor_body) @body) @method

        ; Classes
        (class_declaration
          name: (identifier) @name
          type_parameters: (type_parameters)? @type_params
          superclass: (superclass)? @super
          interfaces: (super_interfaces)? @interfaces
          body: (class_body) @body) @class

        ; Interfaces
        (interface_declaration
          name: (identifier) @name
          type_parameters: (type_parameters)? @type_params
          extends: (extends_interfaces)? @extends
          body: (interface_body) @body) @interface

        ; Enums
        (enum_declaration
          name: (identifier) @name
          body: (enum_body) @body) @class

        ; Fields
        (field_declaration
          (variable_declarator
            name: (identifier) @name
            initializer: (_)? @value)) @variable

        ; Constants (static final)
        (field_declaration
          (modifiers) @mods
          (variable_declarator
            name: (identifier) @name
            initializer: (_)? @value)) @variable
    """,
    "cpp": """
        ; Functions
        (function_definition
          declarator: (function_declarator
            declarator: (identifier) @name
            parameters: (parameter_list) @params)
          body: (compound_statement) @body) @function

        ; Methods (in class)
        (field_declaration
          declarator: (function_declarator
            declarator: (field_identifier) @name
            parameters: (parameter_list) @params)
          body: (compound_statement) @body) @method

        ; Classes/Structs
        (class_specifier
          name: (type_identifier) @name
          body: (field_declaration_list) @body) @class

        (struct_specifier
          name: (type_identifier) @name
          body: (field_declaration_list) @body) @class

        ; Variables
        (declaration
          declarator: (init_declarator
            declarator: (identifier) @name
            value: (_)? @value)) @variable
    """,
}


# Reference/Call queries - find usages of symbols
REFERENCE_QUERIES = {
    "python": """
        ; Function calls
        (call
          function: (identifier) @name) @call

        (call
          function: (attribute
            attribute: (identifier) @name)) @call

        ; Attribute access (method calls, property access)
        (attribute
          attribute: (identifier) @name) @reference

        ; Name references
        (identifier) @reference
    """,
    "javascript": """
        ; Function calls
        (call_expression
          function: (identifier) @name) @call

        (call_expression
          function: (member_expression
            property: (property_identifier) @name)) @call

        ; Member access
        (member_expression
          property: (property_identifier) @name) @reference

        ; Identifier references
        (identifier) @reference
    """,
    "typescript": """
        ; Function calls
        (call_expression
          function: (identifier) @name) @call

        (call_expression
          function: (member_expression
            property: (property_identifier) @name)) @call

        ; Member access
        (member_expression
          property: (property_identifier) @name) @reference

        ; Type references
        (type_identifier) @reference

        ; Identifier references
        (identifier) @reference
    """,
    "go": """
        ; Function calls
        (call_expression
          function: (identifier) @name) @call

        (call_expression
          function: (selector_expression
            field: (field_identifier) @name)) @call

        ; Selector expressions (method calls, field access)
        (selector_expression
          field: (field_identifier) @name) @reference

        ; Identifier references
        (identifier) @reference
    """,
    "rust": """
        ; Function/method calls
        (call_expression
          function: (scoped_identifier
            name: (identifier) @name)) @call

        (call_expression
          function: (field_expression
            field: (field_identifier) @name)) @call

        ; Field access
        (field_expression
          field: (field_identifier) @name) @reference

        ; Path references (use, types, etc.)
        (scoped_identifier
          name: (identifier) @name) @reference

        ; Type identifiers
        (type_identifier) @reference
    """,
    "java": """
        ; Method calls
        (method_invocation
          name: (identifier) @name) @call

        ; Field access
        (field_access
          field: (identifier) @name) @reference

        ; Type references
        (type_identifier) @reference

        ; Identifier references
        (identifier) @reference
    """,
    "cpp": """
        ; Function calls
        (call_expression
          function: (field_expression
            field: (field_identifier) @name)) @call

        (call_expression
          function: (identifier) @name) @call

        ; Member access
        (field_expression
          field: (field_identifier) @name) @reference

        ; Qualified identifiers
        (qualified_identifier
          name: (identifier) @name) @reference

        ; Type identifiers
        (type_identifier) @reference

        ; Identifiers
        (identifier) @reference
    """,
}


# ============================================================================
# EXTRACTION LOGIC
# ============================================================================

def extract_symbols_and_edges(
    source_code: str,
    language: str,
    file_id: int
) -> tuple[list[Symbol], list[Edge]]:
    """
    Extract symbols and edges from source code.

    Returns:
        Tuple of (symbols, edges)
    """
    parser = get_parser(language)
    if parser is None:
        return [], []

    tree = parser.parse(source_code.encode("utf-8"))
    root_node = tree.root_node

    # Extract symbols
    symbols = _extract_symbols(root_node, source_code, language)

    # Extract edges (references/calls)
    edges = _extract_edges(root_node, source_code, language, file_id, symbols)

    return symbols, edges


def _extract_symbols(
    root_node: Node,
    source_code: str,
    language: str
) -> list[Symbol]:
    """Extract symbol definitions from the AST."""
    query_str = SYMBOL_QUERIES.get(language, "")
    if not query_str:
        return []

    lang = get_language(language)
    if lang is None:
        return []

    query = Query(lang, query_str)
    cursor = QueryCursor(query)
    matches = cursor.matches(root_node)

    symbols = []
    source_lines = source_code.splitlines()

    for match in matches:
        symbol = _match_to_symbol(match, source_code, source_lines, language)
        if symbol:
            symbols.append(symbol)

    # Sort by position
    symbols.sort(key=lambda s: (s.start_line, s.start_col))
    return symbols


def _match_to_symbol(
    match: tree_sitter.QueryMatch,
    source_code: str,
    source_lines: list[str],
    language: str
) -> Optional[Symbol]:
    """Convert a query match to a Symbol."""
    captures = {capture.name: node for node, capture in match.captures}

    # Determine symbol kind from match pattern
    kind = "variable"
    if "function" in captures:
        kind = "function"
    elif "class" in captures:
        kind = "class"
    elif "method" in captures:
        kind = "method"
    elif "interface" in captures:
        kind = "interface"
    elif "type" in captures:
        kind = "type"
    elif "constant" in captures:
        kind = "constant"
    elif "module" in captures:
        kind = "module"
    elif "impl" in captures:
        kind = "impl"

    # Get name
    name_node = captures.get("name")
    if name_node is None:
        return None
    name = _node_text(name_node, source_code)

    # Get signature for functions/methods
    signature = None
    if kind in ("function", "method"):
        params_node = captures.get("params")
        if params_node:
            signature = _node_text(params_node, source_code)
            # Add return type if available
            return_node = captures.get("return_type") or captures.get("results")
            if return_node:
                signature += f" -> {_node_text(return_node, source_code)}"
        signature = f"def {name}{signature}" if kind == "function" else f"fn {name}{signature}"

    # Get docstring (language-specific)
    docstring = _extract_docstring(captures, source_code, language)

    # Get position
    # The main node for the symbol (function, class, etc.)
    main_node = captures.get("function") or captures.get("class") or captures.get("method") \
                or captures.get("interface") or captures.get("type") or captures.get("variable") \
                or captures.get("constant") or captures.get("module") or captures.get("impl") \
                or name_node

    start_line = main_node.start_point[0] + 1
    end_line = main_node.end_point[0] + 1
    start_col = main_node.start_point[1]
    end_col = main_node.end_point[1]

    return Symbol(
        name=name,
        kind=kind,
        signature=signature,
        docstring=docstring,
        start_line=start_line,
        end_line=end_line,
        start_col=start_col,
        end_col=end_col,
    )


def _extract_docstring(
    captures: dict[str, Node],
    source_code: str,
    language: str
) -> Optional[str]:
    """Extract documentation comment for a symbol."""
    # Python: string literal as first statement in function/class body
    if language == "python":
        body = captures.get("body")
        if body and body.children:
            first_child = body.children[0]
            if first_child.type == "expression_statement":
                expr = first_child.children[0] if first_child.children else None
                if expr and expr.type == "string":
                    return _node_text(expr, source_code).strip('"\'')

    # JavaScript/TypeScript: JSDoc comment before declaration
    # (would need to look at preceding sibling)

    # Go: comment before declaration
    # Rust: doc comments /// or //! before item

    return None


def _extract_edges(
    root_node: Node,
    source_code: str,
    language: str,
    file_id: int,
    symbols: list[Symbol]
) -> list[Edge]:
    """Extract reference/call edges from the AST."""
    query_str = REFERENCE_QUERIES.get(language, "")
    if not query_str:
        return []

    lang = get_language(language)
    if lang is None:
        return []

    query = Query(lang, query_str)
    cursor = QueryCursor(query)
    matches = cursor.matches(root_node)

    # Build a name -> symbol map for quick lookup
    # In practice, we'd need scope-aware resolution
    symbol_by_name: dict[str, list[Symbol]] = {}
    for sym in symbols:
        symbol_by_name.setdefault(sym.name, []).append(sym)

    edges = []
    for match in matches:
        edge = _match_to_edge(match, source_code, file_id, symbol_by_name, language)
        if edge:
            edges.append(edge)

    return edges


def _match_to_edge(
    match: tree_sitter.QueryMatch,
    source_code: str,
    file_id: int,
    symbol_by_name: dict[str, list[Symbol]],
    language: str
) -> Optional[Edge]:
    """Convert a query match to an Edge."""
    captures = {capture.name: node for node, capture in match.captures}

    name_node = captures.get("name")
    if name_node is None:
        return None

    name = _node_text(name_node, source_code)
    if not name:
        return None

    # Determine edge type
    edge_type = "references"
    if "call" in captures:
        edge_type = "calls"

    # Find target symbol (simplified: first match by name)
    # Real implementation would use scope resolution
    target_symbols = symbol_by_name.get(name, [])
    if not target_symbols:
        return None  # External reference or not yet indexed

    dst_symbol = target_symbols[0]  # Simplified

    # For source symbol, we'd need to find the enclosing function/method
    # This is a placeholder - real implementation tracks scope during symbol extraction
    src_symbol_id = 0  # Would be set during indexing

    line = name_node.start_point[0] + 1
    col = name_node.start_point[1]

    return Edge(
        src_symbol_id=src_symbol_id,
        dst_symbol_id=dst_symbol.id if hasattr(dst_symbol, 'id') else 0,
        edge_type=edge_type,
        src_file_id=file_id,
        dst_file_id=None,  # Would be set if target is in our index
        line=line,
        col=col,
    )


def _node_text(node: Node, source_code: str) -> str:
    """Get text content of a node."""
    return source_code[node.start_byte:node.end_byte]