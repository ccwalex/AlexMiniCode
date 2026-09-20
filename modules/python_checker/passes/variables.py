"""Variable binding and structural nature rule checks."""

from __future__ import annotations

import ast

from python_checker.facts import FactsStore, Nature
from python_checker.nature_match import check_for_iter_slot
from python_checker.parse import ParsedSource
from python_checker.report import Finding
from python_checker.rules_loader import load_rule_packs


def check_variables(
    parsed: ParsedSource,
    facts: FactsStore,
    packs: list[str] | None = None,
) -> list[Finding]:
    contracts = load_rule_packs(packs)
    for_iter_rules = [c for c in contracts if c.kind == "for_iter" and c.api == "for_iter"]

    findings: list[Finding] = []
    for node in ast.walk(parsed.tree):
        if not isinstance(node, ast.For):
            continue
        _bind_for_targets(node, facts)
        for rule in for_iter_rules:
            findings.extend(check_for_iter_slot(parsed.path, node, rule, facts, facts.nature_index))
    return findings


def _bind_for_targets(node: ast.For, facts: FactsStore) -> None:
    for name in _names_in_target(node.target):
        fact = facts.get_or_create(name)
        fact.defined = True
        fact.assigned = True
        fact.nature = Nature.SCALAR


def _names_in_target(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Tuple):
        names: list[str] = []
        for elt in target.elts:
            names.extend(_names_in_target(elt))
        return names
    return []
