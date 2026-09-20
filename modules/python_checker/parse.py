"""AST parsing helpers."""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass
class ParsedSource:
    path: str
    source: str
    tree: ast.AST


def parse_source(source: str, path: str = "<string>") -> ParsedSource:
    tree = ast.parse(source, filename=path)
    return ParsedSource(path=path, source=source, tree=tree)


def node_location(node: ast.AST) -> tuple[int, int]:
    return getattr(node, "lineno", 1), getattr(node, "col_offset", 0) + 1
