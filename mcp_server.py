"""
codecontext/mcp_server.py

Model Context Protocol (MCP) server for CodeContext.
Exposes the semantic index to AI coding assistants (like Claude, Cursor, Nexus).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from codecontext.config import CODECONTEXT_DIR, DB_FILENAME

# Global state to track the active project root
_project_root: Path | None = None

mcp = FastMCP("CodeContext")


def _get_db():
    if not _project_root:
        raise ValueError("Project root not set. Cannot access database.")
    db_path = _project_root / CODECONTEXT_DIR / DB_FILENAME
    if not db_path.exists():
        raise FileNotFoundError(f"No index found at {db_path}. Run 'codecontext index' first.")
    
    from codecontext.storage.db import Database
    return Database(db_path)


@mcp.tool()
def codecontext_lookup(symbol_name: str, file_hint: Optional[str] = None) -> str:
    """Look up a symbol (function, class, file, or package) in the CodeContext index.
    
    Args:
        symbol_name: The name of the symbol to search for (e.g., 'login', 'AuthManager').
        file_hint: Optional file path to disambiguate if multiple symbols have the same name.
    
    Returns:
        A human-readable summary of the symbol including its semantic description, 
        what it calls, what calls it, and its current staleness status.
    """
    try:
        from codecontext.query.lookup import lookup_symbol
        with _get_db() as db:
            results = lookup_symbol(db, symbol_name, _project_root, file_hint=file_hint)
            
        if not results:
            return f"No symbol named '{symbol_name}' found in the index."

        output = []
        for node in results:
            status = node.get("live_status", node.get("status", "missing"))
            
            summary = (
                f"[{node['id']}]\n"
                f"Name:     {node['name']}\n"
                f"Kind:     {node['kind']}\n"
                f"File:     {node.get('file_path', 'N/A')}\n"
                f"Lines:    {node.get('line_start')}–{node.get('line_end')}\n"
                f"Language: {node.get('language', 'N/A')}\n"
                f"Status:   {status}\n"
            )
            
            if status == "stale":
                summary += "WARNING: Source has changed since this description was generated.\n"
                
            desc = node.get("annotation") or node.get("description")
            if desc:
                summary += f"\nDescription:\n{desc}\n"
            else:
                summary += "\nDescription: None\n"
                
            called_by = node.get("called_by", [])
            if called_by:
                summary += "\nCalled by:\n" + "\n".join(
                    f"  - {e['from_node']}" for e in called_by
                ) + "\n"
                
            calls = node.get("calls", [])
            if calls:
                summary += "\nCalls:\n" + "\n".join(
                    f"  - {e['to_node']}" for e in calls
                ) + "\n"
                
            output.append(summary)
            
        return "\n---\n\n".join(output)
        
    except Exception as e:
        return f"Error querying index: {e}"


@mcp.tool()
def codecontext_status() -> str:
    """Get the project-wide staleness summary from the CodeContext index.
    
    Returns:
        JSON string containing the counts of fresh, stale, and missing nodes, 
        and total sizes of the graph.
    """
    try:
        from codecontext.query.lookup import check_project_staleness
        with _get_db() as db:
            summary = check_project_staleness(db, _project_root)
            db_stats = db.get_stats()
            
        return json.dumps({
            "fresh_nodes": summary["fresh"],
            "stale_nodes_count": summary["stale"],
            "missing_nodes_count": summary["missing"],
            "total_edges": db_stats["total_edges"],
            "total_files": db_stats["total_files"]
        }, indent=2)
    except Exception as e:
        return f"Error getting status: {e}"


def start_mcp_server(project_root: Path) -> None:
    """Start the stdio MCP server for the given project root."""
    global _project_root
    _project_root = project_root
    
    # FastMCP uses stdio transport by default when run like this
    mcp.run()
