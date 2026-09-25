"""Path sandboxing under a configured project root."""

from __future__ import annotations

from pathlib import Path


class PathEscapeError(ValueError):
    """Raised when a requested path escapes the project root."""


def resolve_under_root(root: Path, path: str | Path) -> Path:
    """Resolve ``path`` under ``root`` and reject escapes.

    Relative paths are joined to ``root``. Absolute paths are allowed only if
    they still resolve inside ``root``.
    """
    root = root.resolve()
    raw = Path(path)
    candidate = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if candidate != root and root not in candidate.parents:
        raise PathEscapeError(f"path escapes project root: {path}")
    return candidate


def relpath_under_root(root: Path, path: Path) -> str:
    """Return a POSIX relative path from ``root`` to ``path``."""
    root = root.resolve()
    path = path.resolve()
    if path == root:
        return "."
    if root not in path.parents:
        raise PathEscapeError("outside project root")
    return path.relative_to(root).as_posix()
