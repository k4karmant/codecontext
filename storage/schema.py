"""
codecontext/storage/schema.py

Idempotent SQLite schema creation.  Call ``apply_schema(conn)`` on every
startup — it is safe to call multiple times and will never drop data.
"""

from __future__ import annotations

import sqlite3

# Bump this integer whenever the schema changes.
SCHEMA_VERSION: int = 1

_DDL_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK(kind IN ('function','class','file','package','project')),
    language     TEXT,
    file_path    TEXT,
    line_start   INTEGER,
    line_end     INTEGER,
    source_hash  TEXT NOT NULL DEFAULT '',
    description  TEXT,
    annotation   TEXT,
    parent_id    TEXT,          -- intentionally NO FK constraint (bottom-up insertion)
    status       TEXT NOT NULL DEFAULT 'missing'
                     CHECK(status IN ('fresh','stale','missing')),
    updated_at   TEXT NOT NULL  -- ISO-8601 UTC
);
"""

_DDL_NODES_IDX_NAME = """
CREATE INDEX IF NOT EXISTS idx_nodes_name      ON nodes(name COLLATE NOCASE);
"""
_DDL_NODES_IDX_PARENT = """
CREATE INDEX IF NOT EXISTS idx_nodes_parent_id ON nodes(parent_id);
"""
_DDL_NODES_IDX_FILE = """
CREATE INDEX IF NOT EXISTS idx_nodes_file_path ON nodes(file_path);
"""
_DDL_NODES_IDX_KIND = """
CREATE INDEX IF NOT EXISTS idx_nodes_kind      ON nodes(kind);
"""
_DDL_NODES_IDX_STATUS = """
CREATE INDEX IF NOT EXISTS idx_nodes_status    ON nodes(status);
"""

_DDL_EDGES = """
CREATE TABLE IF NOT EXISTS edges (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node TEXT NOT NULL,
    to_node   TEXT NOT NULL,
    kind      TEXT NOT NULL CHECK(kind IN ('calls','imports','inherits')),
    UNIQUE(from_node, to_node, kind)
);
"""

_DDL_EDGES_IDX_FROM = """
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_node);
"""
_DDL_EDGES_IDX_TO = """
CREATE INDEX IF NOT EXISTS idx_edges_to   ON edges(to_node);
"""

_DDL_FILES = """
CREATE TABLE IF NOT EXISTS files (
    path          TEXT PRIMARY KEY,
    language      TEXT,
    file_hash     TEXT NOT NULL DEFAULT '',
    last_indexed  TEXT NOT NULL  -- ISO-8601 UTC
);
"""

_DDL_META = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_DDL_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not yet exist.

    Safe to call on every startup.  Never drops or alters existing data.
    Writes/updates the schema_version row after all DDL succeeds.
    """
    statements = [
        _DDL_NODES,
        _DDL_NODES_IDX_NAME,
        _DDL_NODES_IDX_PARENT,
        _DDL_NODES_IDX_FILE,
        _DDL_NODES_IDX_KIND,
        _DDL_NODES_IDX_STATUS,
        _DDL_EDGES,
        _DDL_EDGES_IDX_FROM,
        _DDL_EDGES_IDX_TO,
        _DDL_FILES,
        _DDL_META,
        _DDL_SCHEMA_VERSION,
    ]

    cur = conn.cursor()
    for stmt in statements:
        cur.execute(stmt)

    # Upsert the schema version row (there should be exactly one).
    cur.execute("SELECT COUNT(*) FROM schema_version")
    (count,) = cur.fetchone()
    if count == 0:
        cur.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    else:
        cur.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))

    conn.commit()
