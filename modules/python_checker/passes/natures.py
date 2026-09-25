"""Nature propagation pass."""

from __future__ import annotations

import ast

from python_checker.facts import FactsStore
from python_checker.nature_match import (
    build_nature_index,
    effect_nature_for_expr,
    infer_literal_nature,
)
from python_checker.parse import ParsedSource
from python_checker.report import Finding
from python_checker.rules_loader import load_rule_packs


def propagate_natures(
    parsed: ParsedSource,
    facts: FactsStore,
    packs: list[str] | None = None,
) -> list[Finding]:
    contracts = load_rule_packs(packs)
    index = build_nature_index(contracts)
    facts.nature_index = index

    for node in ast.walk(parsed.tree):
        if not isinstance(node, ast.Assign):
            continue
        nature = effect_nature_for_expr(node.value, facts, index)
        if nature is None:
            nature = infer_literal_nature(node.value)
        if nature is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                facts.merge_nature(target.id, nature)
    return []
