"""
codecontext/query/lookup.py

Symbol lookup with real-time staleness detection.
"""
from __future__ import annotations

from pathlib import Path

from codecontext.storage.db import Database
from codecontext.storage.hasher import hash_lines, hash_file


def lookup_symbol(
    db: Database,
    name: str,
    project_root: Path,
    file_hint: str | None = None,
) -> list[dict]:
    """Find nodes named *name* and annotate each with live staleness status.

    Args:
        db:           Open Database instance.
        name:         Symbol name to search (case-insensitive).
        project_root: Project root directory (for resolving relative file paths).
        file_hint:    Optional relative file path to narrow results.

    Returns:
        List of enriched node dicts with added keys:
            ``live_status``   — ``'fresh'`` | ``'stale'`` | ``'missing'``
            ``calls``         — list of edge dicts (outgoing)
            ``called_by``     — list of edge dicts (incoming)
    """
    matches = db.find_nodes_by_name(name)

    if file_hint:
        matches = [m for m in matches if file_hint in m.get("file_path", "")]

    enriched: list[dict] = []
    for node in matches:
        result = dict(node)

        # Real-time staleness check
        if node["kind"] in ("project", "package"):
            # Upper-level nodes don't have source code to hash.
            # Just trust the DB status.
            result["live_status"] = node.get("status", "missing")
        else:
            abs_path = project_root / node["file_path"] if node.get("file_path") else None
            if not abs_path or not abs_path.exists():
                result["live_status"] = "missing"
            elif node["kind"] == "file":
                # Files don't have line ranges; hash the whole file
                current_hash = hash_file(abs_path)
                # But wait, node["source_hash"] for files was set to "" by the summarizer.
                # Actually, in cli.py, the node gets created with the file_hash! 
                # And the summarizer overwrites it with "".
                # Let's just pull the file hash from the `files` table to be safe,
                # or just use the node's status if it has a description.
                file_row = db.get_file(node["file_path"])
                if file_row and current_hash == file_row.get("file_hash"):
                    result["live_status"] = "fresh" if node.get("description") else "missing"
                else:
                    result["live_status"] = "stale"
            else:
                # Functions and classes have line ranges
                if node.get("line_start") and node.get("line_end"):
                    current_hash = hash_lines(abs_path, node["line_start"], node["line_end"])
                    if current_hash == node.get("source_hash", ""):
                        result["live_status"] = "fresh"
                    else:
                        result["live_status"] = "stale"
                        db.update_node_status(node["id"], "stale")
                else:
                    result["live_status"] = "missing"

        result["calls"] = db.get_edges_from(node["id"])
        result["called_by"] = db.get_edges_to(node["id"])
        enriched.append(result)

    return enriched


def check_project_staleness(db: Database, project_root: Path) -> dict:
    """Check every node's hash against current source and return a summary.

    Returns a dict with:
        ``fresh``, ``stale``, ``missing`` — counts
        ``stale_nodes`` — list of node dicts that are now stale
    """
    summary = {"fresh": 0, "stale": 0, "missing": 0, "stale_nodes": []}

    for node in db.get_all_nodes():
        if node["kind"] in ("project", "package"):
            # Upper-level nodes don't have a source range to hash
            summary[node.get("status", "missing")] += 1
            continue

        fp = node.get("file_path")
        if not fp:
            summary["missing"] += 1
            continue

        abs_path = project_root / fp
        if not abs_path.exists():
            summary["missing"] += 1
            db.update_node_status(node["id"], "missing")
            continue

        if node["kind"] == "file":
            current_hash = hash_file(abs_path)
            file_row = db.get_file(fp)
            if file_row and current_hash == file_row.get("file_hash"):
                status = "fresh" if node.get("description") else "missing"
                summary[status] += 1
                db.update_node_status(node["id"], status)
            else:
                summary["stale"] += 1
                db.update_node_status(node["id"], "stale")
                summary["stale_nodes"].append(node)
        else:
            ls = node.get("line_start")
            le = node.get("line_end")
            if not (ls and le):
                summary["missing"] += 1
                continue

            current_hash = hash_lines(abs_path, ls, le)
            if current_hash == node.get("source_hash", ""):
                summary["fresh"] += 1
                db.update_node_status(node["id"], "fresh")
            else:
                summary["stale"] += 1
                db.update_node_status(node["id"], "stale")
                summary["stale_nodes"].append(node)

    return summary
