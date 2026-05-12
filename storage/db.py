"""
codecontext/storage/db.py

``Database`` — the single point of access for all SQLite read/write operations.

Usage::

    from codecontext.storage.db import Database

    with Database("/path/to/.codecontext/index.db") as db:
        db.set_meta("project_root", "/home/user/myproject")
        db.upsert_node({...})

All public methods are synchronous.  Thread safety is NOT guaranteed — use one
``Database`` instance per thread/process.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codecontext.storage.schema import apply_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------

class Database:
    """Wraps a SQLite connection and exposes typed read/write methods.

    Can be used as a context manager::

        with Database(path) as db:
            ...

    or created manually and closed with :meth:`close`.
    """

    def __init__(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        # Enable WAL for better concurrent read performance.
        self._conn.execute("PRAGMA journal_mode=WAL")
        apply_schema(self._conn)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        """Commit any pending work and close the underlying connection."""
        self._conn.commit()
        self._conn.close()

    # ------------------------------------------------------------------
    # Meta table
    # ------------------------------------------------------------------

    def set_meta(self, key: str, value: str) -> None:
        """Insert or replace a key-value pair in the ``meta`` table."""
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        """Return the value for *key*, or ``None`` if the key doesn't exist."""
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    # ------------------------------------------------------------------
    # Files table
    # ------------------------------------------------------------------

    def upsert_file(self, path: str, language: str, file_hash: str) -> None:
        """Insert or update a file record, refreshing ``last_indexed``."""
        self._conn.execute(
            """
            INSERT INTO files (path, language, file_hash, last_indexed)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                language     = excluded.language,
                file_hash    = excluded.file_hash,
                last_indexed = excluded.last_indexed
            """,
            (path, language, file_hash, _now_iso()),
        )
        self._conn.commit()

    def get_file(self, path: str) -> dict | None:
        """Return the file record for *path*, or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM files WHERE path = ?", (path,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_files(self) -> list[dict]:
        """Return all file records."""
        rows = self._conn.execute("SELECT * FROM files").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Nodes table
    # ------------------------------------------------------------------

    def upsert_node(self, node: dict) -> None:
        """Insert or replace a node record.

        *node* must contain at minimum: ``id``, ``name``, ``kind``,
        ``source_hash``, ``status``.  Missing optional fields default to
        ``None`` / empty string.
        """
        self._conn.execute(
            """
            INSERT INTO nodes
                (id, name, kind, language, file_path, line_start, line_end,
                 source_hash, description, annotation, parent_id, status, updated_at)
            VALUES
                (:id, :name, :kind, :language, :file_path, :line_start, :line_end,
                 :source_hash, :description, :annotation, :parent_id, :status, :updated_at)
            ON CONFLICT(id) DO UPDATE SET
                name        = excluded.name,
                kind        = excluded.kind,
                language    = excluded.language,
                file_path   = excluded.file_path,
                line_start  = excluded.line_start,
                line_end    = excluded.line_end,
                source_hash = excluded.source_hash,
                description = excluded.description,
                annotation  = excluded.annotation,
                parent_id   = excluded.parent_id,
                status      = excluded.status,
                updated_at  = excluded.updated_at
            """,
            {
                "id":          node.get("id"),
                "name":        node.get("name"),
                "kind":        node.get("kind"),
                "language":    node.get("language"),
                "file_path":   node.get("file_path"),
                "line_start":  node.get("line_start"),
                "line_end":    node.get("line_end"),
                "source_hash": node.get("source_hash", ""),
                "description": node.get("description"),
                "annotation":  node.get("annotation"),
                "parent_id":   node.get("parent_id"),
                "status":      node.get("status", "missing"),
                "updated_at":  node.get("updated_at", _now_iso()),
            },
        )
        self._conn.commit()

    def get_node(self, node_id: str) -> dict | None:
        """Return the node with *node_id*, or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        return dict(row) if row else None

    def find_nodes_by_name(self, name: str) -> list[dict]:
        """Case-insensitive search for nodes whose ``name`` matches *name*."""
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE LOWER(name) = LOWER(?)", (name,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_children(self, parent_id: str) -> list[dict]:
        """Return all immediate children of *parent_id*, ordered by ``line_start``."""
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE parent_id = ? ORDER BY line_start ASC NULLS LAST",
            (parent_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_nodes_by_file(self, file_path: str) -> list[dict]:
        """Return all nodes whose ``file_path`` matches *file_path*, ordered by ``line_start``."""
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE file_path = ? ORDER BY line_start ASC NULLS LAST",
            (file_path,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_nodes_by_kind(self, kind: str) -> list[dict]:
        """Return all nodes of a given *kind*."""
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE kind = ?", (kind,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stale_nodes(self) -> list[dict]:
        """Return all nodes whose status is ``'stale'``."""
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE status = 'stale'"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_nodes(self) -> list[dict]:
        """Return every node in the database."""
        rows = self._conn.execute("SELECT * FROM nodes").fetchall()
        return [dict(r) for r in rows]

    def update_node_status(self, node_id: str, status: str) -> None:
        """Update the ``status`` column of a single node."""
        self._conn.execute(
            "UPDATE nodes SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now_iso(), node_id),
        )
        self._conn.commit()

    def update_node_description(
        self,
        node_id: str,
        description: str,
        source_hash: str,
    ) -> None:
        """Update the ``description`` and ``source_hash`` of a node and mark it ``fresh``."""
        self._conn.execute(
            """
            UPDATE nodes
               SET description = ?,
                   source_hash = ?,
                   status      = 'fresh',
                   updated_at  = ?
             WHERE id = ?
            """,
            (description, source_hash, _now_iso(), node_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Edges table
    # ------------------------------------------------------------------

    def insert_edge(self, from_node: str, to_node: str, kind: str) -> None:
        """Insert an edge if it doesn't already exist (deduplication via UNIQUE constraint)."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO edges (from_node, to_node, kind)
            VALUES (?, ?, ?)
            """,
            (from_node, to_node, kind),
        )
        self._conn.commit()

    def get_edges_from(self, node_id: str) -> list[dict]:
        """Return all edges whose ``from_node`` is *node_id*."""
        rows = self._conn.execute(
            "SELECT * FROM edges WHERE from_node = ?", (node_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_edges_to(self, node_id: str) -> list[dict]:
        """Return all edges whose ``to_node`` is *node_id*."""
        rows = self._conn.execute(
            "SELECT * FROM edges WHERE to_node = ?", (node_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_edges_for_file(self, file_path: str) -> None:
        """Delete all edges that originate from nodes in *file_path*.

        Used during incremental re-index to clear stale call/import edges
        before writing fresh ones.
        """
        # Collect node IDs belonging to this file, then delete their edges.
        rows = self._conn.execute(
            "SELECT id FROM nodes WHERE file_path = ?", (file_path,)
        ).fetchall()
        for row in rows:
            self._conn.execute(
                "DELETE FROM edges WHERE from_node = ?", (row["id"],)
            )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return a summary dict with counts broken down by status and kind."""
        stats: dict = {
            "total_nodes": 0,
            "by_status": {"fresh": 0, "stale": 0, "missing": 0},
            "by_kind": {},
            "total_edges": 0,
            "total_files": 0,
        }

        # Node totals by status.
        for row in self._conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM nodes GROUP BY status"
        ):
            stats["by_status"][row["status"]] = row["cnt"]
            stats["total_nodes"] += row["cnt"]

        # Node totals by kind.
        for row in self._conn.execute(
            "SELECT kind, COUNT(*) AS cnt FROM nodes GROUP BY kind"
        ):
            stats["by_kind"][row["kind"]] = row["cnt"]

        # Edge count.
        (stats["total_edges"],) = self._conn.execute(
            "SELECT COUNT(*) FROM edges"
        ).fetchone()

        # File count.
        (stats["total_files"],) = self._conn.execute(
            "SELECT COUNT(*) FROM files"
        ).fetchone()

        return stats
