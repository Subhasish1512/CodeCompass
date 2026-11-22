"""
MCP Server for CodeCompass - exposes code intelligence tools over stdio.

Tools:
- find_definition: Find symbol definitions by name
- find_callers: Find functions that call a given symbol
- find_callees: Find functions called by a given symbol
- impact_analysis: Transitive impact analysis (callers + callees)
- search_code: Hybrid search (BM25 + Vector + Graph expansion)

Returns compact JSON optimized for token efficiency.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ListToolsRequest,
    ListToolsResult,
    TextContent,
    Tool,
)

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from codecompass.db import close_db, init_db
from codecompass.db.repository import get_repository_by_name, list_repositories
from codecompass.db.search import (
    find_definition as db_find_definition,
)
from codecompass.db.search import (
    transitive_callees,
    transitive_callers,
    transitive_impact,
)
from codecompass.search.hybrid import search as hybrid_search

server = Server("codecompass", version="0.1.0")


def _get_default_repo_id() -> int:
    """Get the first available repository ID, or raise if none."""
    repos = list_repositories()
    if not repos:
        raise RuntimeError("No repositories indexed. Run 'codecompass index <path>' first.")
    return repos[0]["id"]


def _compact_result(r: Dict, max_snippet: int = 120) -> Dict:
    """Convert a database result to compact format."""
    return {
        "file": r.get("file_path", r.get("src_file_path", r.get("dst_file_path", ""))),
        "lines": f"{r.get('start_line', r.get('line', 0))}-{r.get('end_line', r.get('line', 0))}",
        "name": r.get("name", r.get("src_name", r.get("dst_name", ""))),
        "kind": r.get("kind", ""),
        "confidence": round(r.get("confidence", r.get("score", r.get("fused_score", 1.0))), 2),
        "snippet": (r.get("signature") or r.get("docstring") or r.get("text", "") or "")[:max_snippet],
    }


async def _find_definition_handler(arguments: Dict[str, Any]) -> CallToolResult:
    """Find symbol definitions by exact name match with ranked disambiguation."""
    init_db()
    try:
        name = arguments.get("name", "")
        repository = arguments.get("repository")
        kind = arguments.get("kind")

        repo_id = _get_default_repo_id()
        if repository:
            repo = get_repository_by_name(repository)
            if repo:
                repo_id = repo["id"]

        results = db_find_definition(name, repo_id, kind, use_ranking=True)
        compact = [_compact_result(r) for r in results[:10]]
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(compact, indent=2))])
    finally:
        close_db()


async def _find_callers_handler(arguments: Dict[str, Any]) -> CallToolResult:
    """Find all functions that call a given symbol (transitive callers)."""
    init_db()
    try:
        symbol_name = arguments.get("symbol_name", "")
        repository = arguments.get("repository")
        depth = arguments.get("depth", 3)
        min_confidence = arguments.get("min_confidence", 0.0)

        repo_id = _get_default_repo_id()
        if repository:
            repo = get_repository_by_name(repository)
            if repo:
                repo_id = repo["id"]

        defs = db_find_definition(symbol_name, repo_id, use_ranking=True)
        if not defs:
            return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": f"Symbol not found: {symbol_name}"}, indent=2))])

        symbol_id = defs[0].get("id") or defs[0].get("symbol_id")
        if not symbol_id:
            return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": f"Could not get symbol ID for: {symbol_name}"}, indent=2))])

        results = transitive_callers(symbol_id, max_depth=depth, min_confidence=min_confidence)
        compact = [_compact_result(r) for r in results[:20]]
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(compact, indent=2))])
    finally:
        close_db()


async def _find_callees_handler(arguments: Dict[str, Any]) -> CallToolResult:
    """Find all functions called by a given symbol (transitive callees)."""
    init_db()
    try:
        symbol_name = arguments.get("symbol_name", "")
        repository = arguments.get("repository")
        depth = arguments.get("depth", 3)
        min_confidence = arguments.get("min_confidence", 0.0)

        repo_id = _get_default_repo_id()
        if repository:
            repo = get_repository_by_name(repository)
            if repo:
                repo_id = repo["id"]

        defs = db_find_definition(symbol_name, repo_id, use_ranking=True)
        if not defs:
            return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": f"Symbol not found: {symbol_name}"}, indent=2))])

        symbol_id = defs[0].get("id") or defs[0].get("symbol_id")
        if not symbol_id:
            return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": f"Could not get symbol ID for: {symbol_name}"}, indent=2))])

        results = transitive_callees(symbol_id, max_depth=depth, min_confidence=min_confidence)
        compact = [_compact_result(r) for r in results[:20]]
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(compact, indent=2))])
    finally:
        close_db()


async def _impact_analysis_handler(arguments: Dict[str, Any]) -> CallToolResult:
    """Transitive impact analysis: all code affected by changing a symbol (callers + callees)."""
    init_db()
    try:
        symbol_name = arguments.get("symbol_name", "")
        repository = arguments.get("repository")
        depth = arguments.get("depth", 5)
        min_confidence = arguments.get("min_confidence", 0.0)

        repo_id = _get_default_repo_id()
        if repository:
            repo = get_repository_by_name(repository)
            if repo:
                repo_id = repo["id"]

        defs = db_find_definition(symbol_name, repo_id, use_ranking=True)
        if not defs:
            return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": f"Symbol not found: {symbol_name}"}, indent=2))])

        symbol_id = defs[0].get("id") or defs[0].get("symbol_id")
        if not symbol_id:
            return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": f"Could not get symbol ID for: {symbol_name}"}, indent=2))])

        results = transitive_impact(symbol_id, max_depth=depth, min_confidence=min_confidence)
        compact = [_compact_result(r) for r in results[:30]]
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(compact, indent=2))])
    finally:
        close_db()


async def _search_code_handler(arguments: Dict[str, Any]) -> CallToolResult:
    """Hybrid code search: BM25 + Vector + RRF + optional graph expansion."""
    init_db()
    try:
        query = arguments.get("query", "")
        repository = arguments.get("repository")
        limit = arguments.get("limit", 10)
        use_graph = arguments.get("use_graph", True)

        repo_id = _get_default_repo_id()
        if repository:
            repo = get_repository_by_name(repository)
            if repo:
                repo_id = repo["id"]

        results = hybrid_search(query, repo_id, limit=limit, use_graph_expansion=use_graph, graph_depth=2)
        compact = [_compact_result(r) for r in results[:limit]]
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(compact, indent=2))])
    finally:
        close_db()


TOOL_HANDLERS = {
    "find_definition": _find_definition_handler,
    "find_callers": _find_callers_handler,
    "find_callees": _find_callees_handler,
    "impact_analysis": _impact_analysis_handler,
    "search_code": _search_code_handler,
}

TOOL_DEFINITIONS = [
    Tool(
        name="find_definition",
        description="Find symbol definitions by exact name match with ranked disambiguation",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Symbol name to search for (e.g., 'Flask.__init__', 'RequestContext')"},
                "repository": {"type": "string", "description": "Optional repository name"},
                "kind": {"type": "string", "description": "Optional symbol kind filter (function, class, method, etc.)"},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="find_callers",
        description="Find all functions that call a given symbol (transitive callers)",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol_name": {"type": "string", "description": "Name of the target symbol (e.g., 'auth_middleware')"},
                "repository": {"type": "string", "description": "Optional repository name"},
                "depth": {"type": "integer", "description": "Max traversal depth (default 3)", "default": 3},
                "min_confidence": {"type": "number", "description": "Minimum edge confidence 0-1 (default 0)", "default": 0.0},
            },
            "required": ["symbol_name"],
        },
    ),
    Tool(
        name="find_callees",
        description="Find all functions called by a given symbol (transitive callees)",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol_name": {"type": "string", "description": "Name of the target symbol (e.g., 'process_request')"},
                "repository": {"type": "string", "description": "Optional repository name"},
                "depth": {"type": "integer", "description": "Max traversal depth (default 3)", "default": 3},
                "min_confidence": {"type": "number", "description": "Minimum edge confidence 0-1 (default 0)", "default": 0.0},
            },
            "required": ["symbol_name"],
        },
    ),
    Tool(
        name="impact_analysis",
        description="Transitive impact analysis: all code affected by changing a symbol (callers + callees)",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol_name": {"type": "string", "description": "Name of the target symbol (e.g., 'User', 'SessionInterface')"},
                "repository": {"type": "string", "description": "Optional repository name"},
                "depth": {"type": "integer", "description": "Max traversal depth (default 5)", "default": 5},
                "min_confidence": {"type": "number", "description": "Minimum edge confidence 0-1 (default 0)", "default": 0.0},
            },
            "required": ["symbol_name"],
        },
    ),
    Tool(
        name="search_code",
        description="Hybrid code search: BM25 + Vector + RRF + optional graph expansion",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language or keyword query (e.g., 'how does Flask handle CORS')"},
                "repository": {"type": "string", "description": "Optional repository name"},
                "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
                "use_graph": {"type": "boolean", "description": "Enable graph expansion (default true)", "default": True},
            },
            "required": ["query"],
        },
    ),
]


async def _list_tools_handler(request: ListToolsRequest) -> ListToolsResult:
    """Handle tools/list request."""
    return ListToolsResult(tools=TOOL_DEFINITIONS)


async def _call_tool_handler(request: CallToolRequest) -> CallToolResult:
    """Handle tools/call request."""
    handler = TOOL_HANDLERS.get(request.params.name)
    if not handler:
        return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {request.params.name}"}, indent=2))])
    return await handler(request.params.arguments or {})


# Register handlers
server.add_request_handler("tools/list", ListToolsRequest, _list_tools_handler)
server.add_request_handler("tools/call", CallToolRequest, _call_tool_handler)


async def main():
    """Run the MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())