"""Rule pack loading and library contract checking."""

from __future__ import annotations

import ast
from typing import Any

from python_checker.facts import FactsStore
from python_checker.nature_match import check_call_slots, match_call_contract
from python_checker.parse import ParsedSource
from python_checker.report import Finding, Severity
from python_checker.rules_loader import RuleContract, load_rule_packs


class LibraryRulesPass:
    def __init__(self, contracts: list[RuleContract], facts: FactsStore) -> None:
        self.index = facts.nature_index or build_nature_index(contracts)
        self.call_contracts = [c for c in contracts if c.kind == "call"]

    def run(self, parsed: ParsedSource, facts: FactsStore) -> list[Finding]:
        findings: list[Finding] = []
        for site in facts.call_sites:
            func = site["func"]
            contract = match_call_contract(func, site["node"], facts, self.index)
            if contract is None:
                if self._is_tracked_lib_call(func):
                    findings.append(
                        Finding(
                            code="CATALOG_MISS",
                            severity=Severity.UNCOVERED,
                            message=f"No rule covers call to {func}",
                            path=parsed.path,
                            line=site["line"],
                            col=site["col"],
                            rule_id="catalog.miss",
                            evidence={"api": func},
                        )
                    )
                continue
            findings.extend(self._check_contract(parsed.path, site, contract, facts))
        return findings

    def _is_tracked_lib_call(self, func: str) -> bool:
        prefixes = (
            "torch.",
            "numpy.",
            "np.",
            "pandas.",
            "pd.",
            "sklearn.",
            "fastapi.",
            "openai.",
        )
        return any(func.startswith(prefix) for prefix in prefixes)

    def _check_contract(
        self,
        path: str,
        site: dict[str, Any],
        contract: RuleContract,
        facts: FactsStore,
    ) -> list[Finding]:
        findings: list[Finding] = []
        line, col = site["line"], site["col"]
        kwargs = site.get("kwargs", {})
        node = site["node"]

        for required in contract.required_kwargs:
            if required not in kwargs:
                findings.append(
                    Finding(
                        code="MISSING_KWARG",
                        severity=Severity.ERROR,
                        message=f"{contract.api} missing required kwarg '{required}'",
                        path=path,
                        line=line,
                        col=col,
                        rule_id=f"{contract.pack}.{contract.api}",
                        evidence={"kwarg": required},
                    )
                )

        if isinstance(node, ast.Call):
            findings.extend(
                check_call_slots(path, line, col, node, contract, facts, self.index)
            )
        return findings


def check_library_rules(
    parsed: ParsedSource,
    facts: FactsStore,
    packs: list[str] | None = None,
) -> list[Finding]:
    contracts = load_rule_packs(packs)
    return LibraryRulesPass(contracts, facts).run(parsed, facts)
