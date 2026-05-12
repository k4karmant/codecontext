"""
codecontext/parser/java_parser.py

Tree-sitter based Java source parser.
Extracts classes, methods, constructors, calls, imports, and inheritance.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tree_sitter import Language, Node, Parser
import tree_sitter_java as tsjava

from codecontext.parser.base import BaseParser, ParseResult

JAVA_LANGUAGE = Language(tsjava.language())
_PARSER = Parser(JAVA_LANGUAGE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hex16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _hash_range(lines: list[str], s: int, e: int) -> str:
    chunk = "".join(lines[max(0, s - 1): min(len(lines), e)])
    return _hex16(chunk.encode("utf-8"))


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte: node.end_byte].decode("utf-8", errors="replace")


def _extract_javadoc(node: Node, src: bytes) -> Optional[str]:
    """Return the block_comment immediately preceding *node* if it is a Javadoc."""
    # Siblings before this node in the parent might contain block_comment
    # We look at the first comment child of the parent that precedes this node.
    parent = node.parent
    if not parent:
        return None
    prev = None
    for child in parent.children:
        if child.id == node.id:
            break
        if child.type in ("block_comment", "line_comment"):
            prev = child
    if prev and prev.type == "block_comment":
        raw = _text(prev, src).strip()
        if raw.startswith("/**"):
            return raw[3:].rstrip("*/").strip()
    return None


def _get_call_names(node: Node, src: bytes) -> list[str]:
    names: list[str] = []

    def walk(n: Node) -> None:
        if n.type == "method_invocation":
            name_node = n.child_by_field_name("name")
            if name_node:
                names.append(_text(name_node, src))
        for c in n.children:
            walk(c)

    walk(node)
    return names


class JavaParser(BaseParser):
    """Tree-sitter Java parser."""

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

        # Import edges
        for node in root.children:
            if node.type == "import_declaration":
                raw = _text(node, source_bytes).strip().removeprefix("import").removesuffix(";").strip()
                mod = raw.split(".")[0]
                result.edges.append((file_id, f"external::{mod}", "imports"))

        # Walk for class declarations (top-level and nested via program)
        self._walk_root(root, source_bytes, lines, rel_path, file_id, result)
        return result

    def _walk_root(
        self,
        root: Node,
        src: bytes,
        lines: list[str],
        rel_path: str,
        file_id: str,
        result: ParseResult,
    ) -> None:
        for child in root.children:
            if child.type == "class_declaration":
                self._handle_class(child, src, lines, rel_path, file_id, result)

    def _handle_class(
        self,
        node: Node,
        src: bytes,
        lines: list[str],
        rel_path: str,
        parent_id: str,
        result: ParseResult,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return

        class_name = _text(name_node, src)
        line_start = node.start_point[0] + 1
        line_end = node.end_point[0] + 1
        class_id = f"class::{rel_path}::{class_name}"

        # Inheritance
        supers = node.child_by_field_name("superclass")
        if supers:
            for sc in supers.named_children:
                result.edges.append((class_id, f"unresolved::{_text(sc, src)}", "inherits"))

        interfaces = node.child_by_field_name("interfaces")
        if interfaces:
            for iface in interfaces.named_children:
                result.edges.append((class_id, f"unresolved::{_text(iface, src)}", "inherits"))

        result.nodes.append({
            "id": class_id,
            "name": class_name,
            "kind": "class",
            "language": "java",
            "file_path": rel_path,
            "line_start": line_start,
            "line_end": line_end,
            "source_hash": _hash_range(lines, line_start, line_end),
            "description": _extract_javadoc(node, src),
            "annotation": None,
            "parent_id": parent_id,
            "status": "missing",
            "updated_at": _now(),
        })

        body = node.child_by_field_name("body")
        if not body:
            return

        for child in body.children:
            if child.type in ("method_declaration", "constructor_declaration"):
                self._handle_method(child, src, lines, rel_path, class_id, class_name, result)
            elif child.type == "class_declaration":
                self._handle_class(child, src, lines, rel_path, class_id, result)

    def _handle_method(
        self,
        node: Node,
        src: bytes,
        lines: list[str],
        rel_path: str,
        class_id: str,
        class_name: str,
        result: ParseResult,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return

        method_name = _text(name_node, src)
        line_start = node.start_point[0] + 1
        line_end = node.end_point[0] + 1
        node_id = f"function::{result.nodes[0]['file_path'] if result.nodes else ''}::{class_name}::{method_name}"
        # Rebuild properly from rel_path (passed through class_id)
        file_rel = class_id.split("::")[1]  # class::rel_path::ClassName → rel_path
        node_id = f"function::{file_rel}::{class_name}::{method_name}"

        call_names = _get_call_names(node, src)
        result.nodes.append({
            "id": node_id,
            "name": method_name,
            "kind": "function",
            "language": "java",
            "file_path": file_rel,
            "line_start": line_start,
            "line_end": line_end,
            "source_hash": _hash_range(lines, line_start, line_end),
            "description": _extract_javadoc(node, src),
            "annotation": None,
            "parent_id": class_id,
            "status": "missing",
            "updated_at": _now(),
        })
        for called in call_names:
            result.edges.append((node_id, f"unresolved::{called}", "calls"))
