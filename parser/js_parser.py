"""
codecontext/parser/js_parser.py

Tree-sitter based JavaScript/JSX source parser.
Extracts functions, arrow functions, classes, calls, and imports.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tree_sitter import Language, Node, Parser
import tree_sitter_javascript as tsjs

from codecontext.parser.base import BaseParser, ParseResult

JS_LANGUAGE = Language(tsjs.language())
_PARSER = Parser(JS_LANGUAGE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hex16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _hash_range(lines: list[str], s: int, e: int) -> str:
    chunk = "".join(lines[max(0, s - 1): min(len(lines), e)])
    return _hex16(chunk.encode("utf-8"))


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte: node.end_byte].decode("utf-8", errors="replace")


def _get_call_names(node: Node, src: bytes) -> list[str]:
    names: list[str] = []

    def walk(n: Node) -> None:
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn:
                if fn.type == "identifier":
                    names.append(_text(fn, src))
                elif fn.type == "member_expression":
                    prop = fn.child_by_field_name("property")
                    if prop:
                        names.append(_text(prop, src))
        for c in n.children:
            walk(c)

    walk(node)
    return names


def _make_fn_node(
    node_id: str,
    name: str,
    rel_path: str,
    parent_id: str,
    line_start: int,
    line_end: int,
    lines: list[str],
) -> dict:
    return {
        "id": node_id,
        "name": name,
        "kind": "function",
        "language": "javascript",
        "file_path": rel_path,
        "line_start": line_start,
        "line_end": line_end,
        "source_hash": _hash_range(lines, line_start, line_end),
        "description": None,
        "annotation": None,
        "parent_id": parent_id,
        "status": "missing",
        "updated_at": _now(),
    }


class JsParser(BaseParser):
    """Tree-sitter JavaScript parser."""

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

        for child in root.children:
            self._visit(child, source_bytes, lines, rel_path, file_id, result, class_name=None)

        return result

    def _visit(
        self,
        node: Node,
        src: bytes,
        lines: list[str],
        rel_path: str,
        parent_id: str,
        result: ParseResult,
        class_name: Optional[str],
    ) -> None:
        t = node.type

        # import_statement → imports edge
        if t == "import_statement":
            source_node = node.child_by_field_name("source")
            if source_node:
                mod = _text(source_node, src).strip("'\"")
                result.edges.append((parent_id if parent_id.startswith("file::") else f"file::{rel_path}", f"external::{mod}", "imports"))
            return

        # function_declaration
        if t == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                fn_name = _text(name_node, src)
                ls = node.start_point[0] + 1
                le = node.end_point[0] + 1
                nid = f"function::{rel_path}::{class_name}::{fn_name}" if class_name else f"function::{rel_path}::{fn_name}"
                result.nodes.append(_make_fn_node(nid, fn_name, rel_path, parent_id, ls, le, lines))
                for called in _get_call_names(node, src):
                    result.edges.append((nid, f"unresolved::{called}", "calls"))
            return

        # class_declaration
        if t == "class_declaration":
            name_node = node.child_by_field_name("name")
            if not name_node:
                return
            cname = _text(name_node, src)
            ls = node.start_point[0] + 1
            le = node.end_point[0] + 1
            class_id = f"class::{rel_path}::{cname}"

            # heritage
            heritage = node.child_by_field_name("heritage")
            if heritage:
                for h in heritage.named_children:
                    result.edges.append((class_id, f"unresolved::{_text(h, src)}", "inherits"))

            result.nodes.append({
                "id": class_id,
                "name": cname,
                "kind": "class",
                "language": "javascript",
                "file_path": rel_path,
                "line_start": ls,
                "line_end": le,
                "source_hash": _hash_range(lines, ls, le),
                "description": None,
                "annotation": None,
                "parent_id": parent_id,
                "status": "missing",
                "updated_at": _now(),
            })

            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    self._visit(child, src, lines, rel_path, class_id, result, class_name=cname)
            return

        # method_definition (inside class body)
        if t == "method_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                m_name = _text(name_node, src)
                ls = node.start_point[0] + 1
                le = node.end_point[0] + 1
                nid = f"function::{rel_path}::{class_name}::{m_name}" if class_name else f"function::{rel_path}::{m_name}"
                result.nodes.append(_make_fn_node(nid, m_name, rel_path, parent_id, ls, le, lines))
                for called in _get_call_names(node, src):
                    result.edges.append((nid, f"unresolved::{called}", "calls"))
            return

        # variable declarator with arrow_function or function_expression
        if t in ("lexical_declaration", "variable_declaration"):
            for child in node.named_children:
                if child.type == "variable_declarator":
                    name_node = child.child_by_field_name("name")
                    value_node = child.child_by_field_name("value")
                    if name_node and value_node and value_node.type in ("arrow_function", "function_expression", "function"):
                        fn_name = _text(name_node, src)
                        ls = node.start_point[0] + 1
                        le = node.end_point[0] + 1
                        nid = f"function::{rel_path}::{class_name}::{fn_name}" if class_name else f"function::{rel_path}::{fn_name}"
                        result.nodes.append(_make_fn_node(nid, fn_name, rel_path, parent_id, ls, le, lines))
                        for called in _get_call_names(value_node, src):
                            result.edges.append((nid, f"unresolved::{called}", "calls"))
            return

        # Export statements — unwrap and recurse
        if t in ("export_statement", "export_default_declaration"):
            for child in node.children:
                self._visit(child, src, lines, rel_path, parent_id, result, class_name)
