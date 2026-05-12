"""
codecontext/storage/hasher.py

Three hashing utilities used throughout the project.

All functions return a 16-character lowercase hex string (first 8 bytes of
SHA-256).  They NEVER raise — on any error they return an empty string.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def _hex16(data: bytes) -> str:
    """Return the first 16 hex characters of the SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()[:16]


def hash_file(path: str | Path) -> str:
    """Return a 16-char hex SHA-256 of the entire file at *path*.

    Returns ``""`` if the file cannot be read for any reason.
    """
    try:
        return _hex16(Path(path).read_bytes())
    except OSError:
        return ""


def hash_lines(path: str | Path, line_start: int, line_end: int) -> str:
    """Return a 16-char hex SHA-256 of lines *line_start*–*line_end* (1-indexed, inclusive).

    The range is clamped to the actual file length — out-of-bounds values are
    silently adjusted, never raised.  Returns ``""`` if the file cannot be read.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError:
        return ""

    if not lines:
        return _hex16(b"")

    # Clamp to actual line range (convert to 0-indexed for slicing).
    start = max(0, line_start - 1)
    end = min(len(lines), line_end)          # exclusive upper bound for slice

    if start >= end:
        # Fully out-of-range — return hash of empty string.
        return _hex16(b"")

    chunk = "".join(lines[start:end])
    return _hex16(chunk.encode("utf-8"))


def hash_string(text: str) -> str:
    """Return a 16-char hex SHA-256 of the given string (UTF-8 encoded).

    Returns ``""`` if *text* is not a string (defensive — should never happen
    in normal usage).
    """
    try:
        return _hex16(text.encode("utf-8"))
    except (AttributeError, TypeError):
        return ""
