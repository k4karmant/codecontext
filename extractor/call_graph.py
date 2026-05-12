"""
codecontext/extractor/call_graph.py

Post-processes raw parse results to resolve unresolved call/inherits edges.

Phase 2 parsers emit edges like:
    (caller_id, "unresolved::hash_password", "calls")

This module walks the node index and replaces ``unresolved::name`` targets
with real node IDs where a match is found.  Unresolvable edges are dropped.
"""
from __future__ import annotations

from codecontext.storage.db import Database


def resolve_edges(db: Database) -> None:
    """Resolve all unresolved edges stored in *db* in-place.

    For each edge whose ``to_node`` starts with ``"unresolved::"``, attempt to
    find a node whose ``name`` matches the symbol name.  If exactly one match
    is found, replace the edge with the real node ID.  Ambiguous or missing
    symbols are dropped.
    """
    import sqlite3

    conn = db._conn  # access raw connection for batch efficiency
    cur = conn.cursor()

    # Fetch all unresolved edges
    cur.execute(
        "SELECT id, from_node, to_node, kind FROM edges WHERE to_node LIKE 'unresolved::%'"
    )
    unresolved = cur.fetchall()

    for row in unresolved:
        edge_id, from_node, to_node, kind = row["id"], row["from_node"], row["to_node"], row["kind"]
        symbol_name = to_node[len("unresolved::"):]

        candidates = db.find_nodes_by_name(symbol_name)
        if len(candidates) == 1:
            real_id = candidates[0]["id"]
            # Replace the edge — use INSERT OR IGNORE to skip if already exists
            cur.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
            cur.execute(
                "INSERT OR IGNORE INTO edges (from_node, to_node, kind) VALUES (?, ?, ?)",
                (from_node, real_id, kind),
            )
        else:
            # Ambiguous or not found — drop the unresolved edge
            cur.execute("DELETE FROM edges WHERE id = ?", (edge_id,))

    conn.commit()


def drop_external_edges(db: Database) -> None:
    """Remove all edges whose ``to_node`` starts with ``'external::'``.

    External imports (e.g. stdlib, third-party) are kept during parsing for
    context but are pruned before serving so the graph stays self-contained.
    """
    db._conn.execute("DELETE FROM edges WHERE to_node LIKE 'external::%'")
    db._conn.commit()
