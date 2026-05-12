"""
codecontext/parser/python_parser.py

Tree-sitter based Python source parser.
Extracts functions, classes, calls, imports, and inheritance edges.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tree_sitter import Language, Node, Parser
import tree_sitter_python as tspython

from codecontext.parser.base import BaseParser, ParseResult

PY_LANGUAGE = Language(tspython.language())
_PARSER = Parser(PY_LANGUAGE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hex16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _hash_range(lines: list[str], start_1: int, end_1: int) -> str:
    """Hash lines[start_1-1 : end_1] (1-indexed inclusive)."""
    chunk = "".join(lines[max(0, start_1 - 1): min(len(lines), end_1)])
    return _hex16(chunk.encode("utf-8"))


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte: node.end_byte].decode("utf-8", errors="replace")


def _extract_docstring(body: Node, src: bytes) -> Optional[str]:
    """Return the first string literal in *body* if it is a docstring."""
    for child in body.children:
        if child.type == "expression_statement":
            for gc in child.children:
                if gc.type == "string":
                    raw = _text(gc, src).strip()
                    # strip surrounding quotes
                    for q in ('"""', "'''", '"', "'"):
                        if raw.startswith(q) and raw.endswith(q) and len(raw) > 2 * len(q):
                            return raw[len(q): -len(q)].strip()
                    return raw
        break  # docstring must be the very first statement
    return None


def _get_call_names(node: Node, src: bytes) -> list[str]:
    """Recursively collect called function/method names within *node*."""
    names: list[str] = []

    def walk(n: Node) -> None:
        if n.type == "call":
            fn = n.child_by_field_name("function")
            if fn:
                if fn.type == "identifier":
                    names.append(_text(fn, src))
                elif fn.type == "attribute":
                    attr = fn.child_by_field_name("attribute")
                    if attr:
                        names.append(_text(attr, src))
        for c in n.children:
            walk(c)

    walk(node)
    return names


def _get_import_names(root: Node, src: bytes) -> list[str]:
    """Return a flat list of imported module/symbol names from the module root."""
    imported: list[str] = []
    for node in root.children:
        if node.type == "import_statement":
            for child in node.named_children:
                imported.append(_text(child, src).split(".")[0])
        elif node.type == "import_from_statement":
            mod = node.child_by_field_name("module_name")
            if mod:
                imported.append(_text(mod, src).split(".")[0])
    return imported


# ---------------------------------------------------------------------------
# Main walker
# ---------------------------------------------------------------------------

def _unwrap_decorated(node: Node) -> Node:
    """If *node* is a decorated_definition, return the inner definition."""
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return child
    return node


class PythonParser(BaseParser):
    """Tree-sitter Python parser."""

    def parse_file(self, file_path: Path, rel_path: str) -> ParseResult:
        result = ParseResult()
        try:
            source_bytes = file_path.read_bytes()
        except OSError:
            return result

        source_text = source_bytes.decode("utf-8", errors="replace")
        lines = source_text.splitlines(keepends=True)
        tree = _PARSER.parse(source_bytes)
        root = tree.root_node

        file_id = f"file::{rel_path}"

        # Collect import names for import edges (file → module, symbolic only)
        import_names = _get_import_names(root, source_bytes)
        for mod_name in import_names:
            # Edges to external modules are stored symbolically (to_node is not a real ID)
            result.edges.append((file_id, f"external::{mod_name}", "imports"))

        # Walk top-level children
        for child in root.children:
            node = _unwrap_decorated(child)

            if node.type == "function_definition":
                fn_nodes, fn_edges = self._handle_function(
                    node, source_bytes, lines, rel_path, file_id, class_name=None
                )
                result.nodes.extend(fn_nodes)
                result.edges.extend(fn_edges)

            elif node.type == "class_definition":
                cls_nodes, cls_edges = self._handle_class(
                    node, source_bytes, lines, rel_path, file_id
                )
                result.nodes.extend(cls_nodes)
                result.edges.extend(cls_edges)

        return result

    def _handle_function(
        self,
        node: Node,
        src: bytes,
        lines: list[str],
        rel_path: str,
        parent_id: str,
        class_name: Optional[str],
    ) -> tuple[list[dict], list[tuple]]:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return [], []

        fn_name = _text(name_node, src)
        line_start = node.start_point[0] + 1  # 1-indexed
        line_end = node.end_point[0] + 1

        if class_name:
            node_id = f"function::{rel_path}::{class_name}::{fn_name}"
        else:
            node_id = f"function::{rel_path}::{fn_name}"

        body = node.child_by_field_name("body")
        docstring = _extract_docstring(body, src) if body else None
        call_names = _get_call_names(node, src) if body else []
        source_hash = _hash_range(lines, line_start, line_end)

        fn_node: dict = {
            "id": node_id,
            "name": fn_name,
            "kind": "function",
            "language": "python",
            "file_path": rel_path,
            "line_start": line_start,
            "line_end": line_end,
            "source_hash": source_hash,
            "description": docstring,  # use docstring as initial description if present
            "annotation": None,
            "parent_id": parent_id,
            "status": "missing",
            "updated_at": _now(),
        }

        edges: list[tuple] = []
        for called in call_names:
            edges.append((node_id, f"unresolved::{called}", "calls"))

        return [fn_node], edges

    def _handle_class(
        self,
        node: Node,
        src: bytes,
        lines: list[str],
        rel_path: str,
        file_id: str,
    ) -> tuple[list[dict], list[tuple]]:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return [], []

        class_name = _text(name_node, src)
        line_start = node.start_point[0] + 1
        line_end = node.end_point[0] + 1
        class_id = f"class::{rel_path}::{class_name}"
        source_hash = _hash_range(lines, line_start, line_end)

        body = node.child_by_field_name("body")
        docstring = _extract_docstring(body, src) if body else None

        cls_node: dict = {
            "id": class_id,
            "name": class_name,
            "kind": "class",
            "language": "python",
            "file_path": rel_path,
            "line_start": line_start,
            "line_end": line_end,
            "source_hash": source_hash,
            "description": docstring,
            "annotation": None,
            "parent_id": file_id,
            "status": "missing",
            "updated_at": _now(),
        }

        nodes: list[dict] = [cls_node]
        edges: list[tuple] = []

        # Extract base classes → inherits edges
        superclasses = node.child_by_field_name("superclasses")
        if superclasses:
            for arg in superclasses.named_children:
                base_name = _text(arg, src).strip()
                if base_name:
                    edges.append((class_id, f"unresolved::{base_name}", "inherits"))

        # Extract methods
        if body:
            for child in body.children:
                mnode = _unwrap_decorated(child)
                if mnode.type == "function_definition":
                    fn_nodes, fn_edges = self._handle_function(
                        mnode, src, lines, rel_path, class_id, class_name=class_name
                    )
                    nodes.extend(fn_nodes)
                    edges.extend(fn_edges)

        return nodes, edges
