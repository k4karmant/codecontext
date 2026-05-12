"""
codecontext/extractor/ast_walker.py

Walks a project directory and dispatches each source file to the correct parser.
Returns a list of ParseResults, one per file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Generator

from codecontext.config import EXTENSION_LANGUAGE_MAP, SKIP_DIRS
from codecontext.parser.base import BaseParser, ParseResult
from codecontext.parser.python_parser import PythonParser
from codecontext.parser.java_parser import JavaParser
from codecontext.parser.js_parser import JsParser


_PARSERS: dict[str, BaseParser] = {
    "python": PythonParser(),
    "java": JavaParser(),
    "javascript": JsParser(),
}


def _iter_source_files(project_root: Path) -> Generator[tuple[Path, str, str], None, None]:
    """Yield ``(abs_path, rel_path_str, language)`` for every indexable file."""
    skip_files = {
        "__init__.py",
        "setup.py",
        "conftest.py",
    }
    
    for path in sorted(project_root.rglob("*")):
        # Skip directories in the skip list
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.name in skip_files:
            continue
        lang = EXTENSION_LANGUAGE_MAP.get(path.suffix.lower())
        if lang is None:
            continue
        rel = path.relative_to(project_root).as_posix()
        yield path, rel, lang


def walk_project(
    project_root: Path,
    changed_files: set[str] | None = None,
) -> list[tuple[str, str, ParseResult]]:
    """Walk *project_root* and parse every source file.

    Args:
        project_root:  Absolute path to the project directory.
        changed_files: If provided, only files whose rel_path is in this set
                       are parsed (incremental mode).  None → parse all.

    Returns:
        List of ``(rel_path, language, ParseResult)`` tuples.
    """
    results: list[tuple[str, str, ParseResult]] = []

    for abs_path, rel_path, language in _iter_source_files(project_root):
        if changed_files is not None and rel_path not in changed_files:
            continue
        parser = _PARSERS.get(language)
        if parser is None:
            continue
        parse_result = parser.parse_file(abs_path, rel_path)
        results.append((rel_path, language, parse_result))

    return results


def detect_changed_files(
    project_root: Path,
    db,  # Database — avoid circular import by using duck typing
) -> set[str]:
    """Compare current file hashes against the DB and return changed rel_paths."""
    from codecontext.storage.hasher import hash_file

    changed: set[str] = set()
    for abs_path, rel_path, _lang in _iter_source_files(project_root):
        current_hash = hash_file(abs_path)
        row = db.get_file(rel_path)
        if row is None or row["file_hash"] != current_hash:
            changed.add(rel_path)
    return changed
