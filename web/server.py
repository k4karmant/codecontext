"""
codecontext/web/server.py

Minimal built-in HTTP server for the CodeContext web visualizer.
Serves the single-page UI and a JSON API for the DB.

API:
    GET /               → index.html
    GET /api/tree       → full nested node tree
    GET /api/node/<id>  → single node + edges
    GET /api/status     → project-wide staleness stats
"""
from __future__ import annotations

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_db_path: Path | None = None


def _get_db():
    from codecontext.storage.db import Database
    return Database(_db_path)


def _build_tree(db) -> dict:
    """Build a nested dict tree from the flat DB nodes."""
    all_nodes = db.get_all_nodes()
    nodes_by_id: dict[str, dict] = {n["id"]: dict(n) for n in all_nodes}

    # Add children list to each node
    for node in nodes_by_id.values():
        node["children"] = []

    roots: list[dict] = []
    for node in nodes_by_id.values():
        parent_id = node.get("parent_id")
        if parent_id and parent_id in nodes_by_id:
            nodes_by_id[parent_id]["children"].append(node)
        else:
            roots.append(node)

    # Sort children by line_start
    def sort_children(node: dict) -> None:
        node["children"].sort(key=lambda n: (n.get("line_start") or 0))
        for child in node["children"]:
            sort_children(child)

    for root in roots:
        sort_children(root)

    return {"roots": roots}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # silence default logs
        pass

    def _send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            ui_file = Path(__file__).parent / "ui" / "index.html"
            self._send_html(ui_file.read_bytes())
            return

        if path == "/api/tree":
            with _get_db() as db:
                self._send_json(_build_tree(db))
            return

        if path == "/api/status":
            with _get_db() as db:
                self._send_json(db.get_stats())
            return

        if path.startswith("/api/node/"):
            raw_id = path[len("/api/node/"):]
            node_id = urllib.parse.unquote(raw_id)
            with _get_db() as db:
                node = db.get_node(node_id)
                if node is None:
                    self._send_json({"error": "not found"}, 404)
                    return
                node["calls"] = db.get_edges_from(node_id)
                node["called_by"] = db.get_edges_to(node_id)
                self._send_json(node)
            return

        self.send_error(404, "Not found")


def start_server(db_path: Path, host: str = "localhost", port: int = 7842) -> None:
    """Start the HTTP server (blocks until Ctrl+C)."""
    global _db_path
    _db_path = db_path

    server = HTTPServer((host, port), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
