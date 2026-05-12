"""
codecontext/parser/base.py

Abstract interface that every language parser must implement.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParseResult:
    """Container returned by every language parser.

    nodes: list of raw node dicts ready for ``db.upsert_node()``.
    edges: list of ``(from_node_id, to_node_id, kind)`` tuples ready for
           ``db.insert_edge()``.  kind ∈ {'calls', 'imports', 'inherits'}.
    """
    nodes: list[dict] = field(default_factory=list)
    edges: list[tuple[str, str, str]] = field(default_factory=list)


class BaseParser(ABC):
    """Abstract base that all language parsers implement."""

    @abstractmethod
    def parse_file(self, file_path: Path, rel_path: str) -> ParseResult:
        """Parse *file_path* and return extracted nodes and edges.

        Args:
            file_path: Absolute path to the source file (used for reading).
            rel_path:  Path relative to the project root — used in node IDs
                       and stored in ``file_path`` column of the DB.
        """
        ...
