"""Checker-specific structural patterns not covered by Ruff."""

from __future__ import annotations

import ast

from python_checker.parse import ParsedSource, node_location
from python_checker.report import Finding, Severity


FORBIDDEN_CALLS = {"eval", "exec"}


def check_structure(parsed: ParsedSource) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(parsed.tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                line, col = node_location(node)
                findings.append(
                    Finding(
                        code="FORBIDDEN_CALL",
                        severity=Severity.ERROR,
                        message=f"Use of {node.func.id}() is forbidden",
                        path=parsed.path,
                        line=line,
                        col=col,
                        rule_id=f"structure.{node.func.id}",
                    )
                )
    return findings
