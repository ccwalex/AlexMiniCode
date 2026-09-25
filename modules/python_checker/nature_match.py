"""Shared nature resolution and slot matching for rule contracts."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from python_checker.facts import FactsStore, Nature
from python_checker.parse import node_location
from python_checker.report import Finding, Severity
from python_checker.rules_loader import RuleContract

NATURE_BY_NAME: dict[str, Nature] = {
    "Unknown": Nature.UNKNOWN,
    "Scalar": Nature.SCALAR,
    "List": Nature.LIST,
    "NdArray": Nature.NDARRAY,
    "DataFrame": Nature.DATAFRAME,
    "Series": Nature.SERIES,
    "Tensor": Nature.TENSOR,
    "Module": Nature.MODULE,
    "FastAPIApp": Nature.FASTAPI_APP,
    "OpenAIClient": Nature.OPENAI_CLIENT,
    "SklearnEstimator": Nature.SKLEARN_ESTIMATOR,
}

NATURE_BY_API: dict[str, Nature] = {
    "torch.randn": Nature.TENSOR,
    "torch.zeros": Nature.TENSOR,
    "torch.ones": Nature.TENSOR,
    "torch.tensor": Nature.TENSOR,
    "torch.from_numpy": Nature.TENSOR,
    "torch.as_tensor": Nature.TENSOR,
    "numpy.array": Nature.NDARRAY,
    "numpy.asarray": Nature.NDARRAY,
    "numpy.zeros": Nature.NDARRAY,
    "numpy.ones": Nature.NDARRAY,
    "pandas.read_csv": Nature.DATAFRAME,
    "pandas.DataFrame": Nature.DATAFRAME,
    "pandas.Series": Nature.SERIES,
    "fastapi.FastAPI": Nature.FASTAPI_APP,
    "openai.OpenAI": Nature.OPENAI_CLIENT,
}


@dataclass
class NatureIndex:
    call_contracts: list[RuleContract] = field(default_factory=list)
    attr_contracts: list[RuleContract] = field(default_factory=list)
    contracts_by_api: dict[str, RuleContract] = field(default_factory=dict)
    contracts_by_attr: dict[str, list[RuleContract]] = field(default_factory=dict)


def build_nature_index(contracts: list[RuleContract]) -> NatureIndex:
    index = NatureIndex()
    for contract in contracts:
        if contract.kind == "call":
            index.call_contracts.append(contract)
            index.contracts_by_api[contract.api] = contract
            attr = contract.api.split(".")[-1]
            index.contracts_by_attr.setdefault(attr, []).append(contract)
        elif contract.kind == "attr":
            index.attr_contracts.append(contract)
    return index


def nature_from_name(name: str) -> Nature | None:
    return NATURE_BY_NAME.get(name)


def effect_nature_from_contract(contract: RuleContract) -> Nature | None:
    value = contract.effects.get("nature")
    if value is None:
        return None
    if isinstance(value, Nature):
        return value
    return nature_from_name(str(value))


def expr_name(node: ast.expr, facts: FactsStore) -> str:
    if isinstance(node, ast.Name):
        return facts.import_map.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = expr_name(node.value, facts)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def call_name(node: ast.Call, facts: FactsStore) -> str:
    return expr_name(node.func, facts)


def infer_literal_nature(node: ast.expr) -> Nature | None:
    if isinstance(node, (ast.List, ast.Tuple)):
        return Nature.LIST
    if isinstance(node, ast.Constant) and node.value is not None:
        return Nature.SCALAR
    return None


def effect_nature_for_expr(
    node: ast.expr,
    facts: FactsStore,
    index: NatureIndex,
) -> Nature | None:
    literal = infer_literal_nature(node)
    if literal is not None:
        return literal
    if isinstance(node, ast.Call):
        return effect_nature_for_call(node, facts, index)
    if isinstance(node, ast.Attribute):
        return effect_nature_for_attr(node, facts, index)
    return None


def resolve_nature(
    node: ast.expr,
    facts: FactsStore,
    index: NatureIndex | None = None,
) -> Nature | None:
    literal = infer_literal_nature(node)
    if literal is not None:
        return literal

    if isinstance(node, ast.Name):
        fact = facts.symbols.get(node.id)
        if fact and fact.nature != Nature.UNKNOWN:
            return fact.nature
        return None

    if isinstance(node, ast.Call):
        if index is not None:
            effect = effect_nature_for_call(node, facts, index)
            if effect is not None:
                return effect
        func = call_name(node, facts)
        return NATURE_BY_API.get(func)

    if isinstance(node, ast.Attribute) and index is not None:
        receiver = resolve_nature(node.value, facts, index)
        if receiver is None:
            return None
        for contract in index.attr_contracts:
            if not contract.api.endswith(f".{node.attr}"):
                continue
            accepts = contract.normalized_accepts_nature()
            if not accepts:
                continue
            allowed = {nature_from_name(name) for name in accepts[0]}
            allowed.discard(None)
            if receiver in allowed:
                return effect_nature_from_contract(contract)
        return None

    return None


def is_module_receiver(node: ast.Call, facts: FactsStore) -> bool:
    if not isinstance(node.func, ast.Attribute):
        return False
    receiver = node.func.value
    if isinstance(receiver, ast.Name):
        fact = facts.symbols.get(receiver.id)
        if fact and fact.nature == Nature.MODULE:
            return True
        if receiver.id in facts.import_map:
            return True
    return False


def slot_exprs(node: ast.Call, facts: FactsStore) -> list[ast.expr]:
    if isinstance(node.func, ast.Attribute) and not is_module_receiver(node, facts):
        return [node.func.value, *node.args]
    return list(node.args)


def _slot_label(contract: RuleContract, index: int, is_method: bool) -> str:
    if is_method and index == 0:
        return "receiver"
    arg_index = index - 1 if is_method else index
    return f"arg{arg_index}"


def check_call_slots(
    path: str,
    line: int,
    col: int,
    node: ast.Call,
    contract: RuleContract,
    facts: FactsStore,
    index: NatureIndex | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    accepts = contract.normalized_accepts_nature()
    if not accepts:
        return findings

    is_method = isinstance(node.func, ast.Attribute) and not is_module_receiver(node, facts)
    slots = slot_exprs(node, facts)

    for slot_index, allowed in enumerate(accepts):
        if slot_index >= len(slots):
            break
        actual = resolve_nature(slots[slot_index], facts, index)
        slot = _slot_label(contract, slot_index, is_method)
        if actual is None:
            findings.append(
                Finding(
                    code="UNKNOWN_NATURE",
                    severity=Severity.UNCOVERED,
                    message=(
                        f"Cannot verify nature for {slot} of {contract.api} "
                        f"(expected one of {allowed})"
                    ),
                    path=path,
                    line=line,
                    col=col,
                    rule_id=f"{contract.pack}.{contract.api}",
                    evidence={"slot": slot, "accepts_nature": allowed},
                )
            )
            continue
        allowed_natures = {nature_from_name(name) for name in allowed}
        allowed_natures.discard(None)
        if actual not in allowed_natures:
            expected = ", ".join(allowed)
            findings.append(
                Finding(
                    code="NATURE_MISMATCH",
                    severity=Severity.ERROR,
                    message=f"{contract.api} {slot} expected {expected}, got {actual.value}",
                    path=path,
                    line=line,
                    col=col,
                    rule_id=f"{contract.pack}.{contract.api}",
                    symbol=_symbol_name(slots[slot_index]),
                    evidence={"slot": slot, "expected": allowed, "actual": actual.value},
                )
            )
    return findings


def check_for_iter_slot(
    path: str,
    node: ast.For,
    contract: RuleContract,
    facts: FactsStore,
    index: NatureIndex | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    if not contract.forbids_nature:
        return findings

    line, col = node_location(node)
    actual = resolve_nature(node.iter, facts, index)
    if actual is None:
        return findings

    forbidden = {nature_from_name(name) for name in contract.forbids_nature}
    forbidden.discard(None)
    if actual in forbidden:
        findings.append(
            Finding(
                code="NATURE_MISMATCH",
                severity=Severity.ERROR,
                message=(
                    f"for-loop iterable must not be {actual.value}; "
                    f"for k in <{actual.value}> is forbidden"
                ),
                path=path,
                line=line,
                col=col,
                rule_id=f"{contract.pack}.{contract.api}",
                symbol=_symbol_name(node.iter),
                evidence={"forbids_nature": contract.forbids_nature, "actual": actual.value},
            )
        )
    return findings


def _symbol_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    return None


def receiver_matches_contract(
    node: ast.Call,
    contract: RuleContract,
    facts: FactsStore,
    index: NatureIndex | None = None,
) -> bool:
    accepts = contract.normalized_accepts_nature()
    if not accepts or not isinstance(node.func, ast.Attribute):
        return True
    if is_module_receiver(node, facts):
        return True
    receiver_nature = resolve_nature(node.func.value, facts, index)
    if receiver_nature is None:
        return True
    allowed = {nature_from_name(name) for name in accepts[0]}
    allowed.discard(None)
    return receiver_nature in allowed


def match_call_contract(
    func: str,
    node: ast.Call,
    facts: FactsStore,
    index: NatureIndex,
) -> RuleContract | None:
    if func in index.contracts_by_api:
        return index.contracts_by_api[func]

    if isinstance(node.func, ast.Attribute):
        attr = node.func.attr
        receiver_nature = resolve_nature(node.func.value, facts, index)
        for contract in index.contracts_by_attr.get(attr, []):
            if not (contract.api.endswith(f".{attr}") or contract.api.split(".")[-1] == attr):
                continue
            if contract.normalized_accepts_nature():
                if receiver_matches_contract(node, contract, facts, index):
                    return contract
            elif receiver_nature is None or _receiver_in_first_slot(contract, receiver_nature):
                return contract
        return None

    for api, contract in index.contracts_by_api.items():
        if func.endswith(api.split(".", 1)[-1]) or func.endswith(api):
            if receiver_matches_contract(node, contract, facts, index):
                return contract
    return None


def _receiver_in_first_slot(contract: RuleContract, receiver_nature: Nature) -> bool:
    accepts = contract.normalized_accepts_nature()
    if not accepts:
        return True
    allowed = {nature_from_name(name) for name in accepts[0]}
    allowed.discard(None)
    return receiver_nature in allowed


def effect_nature_for_call(
    node: ast.Call,
    facts: FactsStore,
    index: NatureIndex,
) -> Nature | None:
    func = call_name(node, facts)
    contract = match_call_contract(func, node, facts, index)
    if contract is not None:
        effect = effect_nature_from_contract(contract)
        if effect is not None:
            return effect
    return NATURE_BY_API.get(func)


def effect_nature_for_attr(
    node: ast.Attribute,
    facts: FactsStore,
    index: NatureIndex,
) -> Nature | None:
    receiver = resolve_nature(node.value, facts, index)
    if receiver is None:
        return None
    for contract in index.attr_contracts:
        if not contract.api.endswith(f".{node.attr}"):
            continue
        accepts = contract.normalized_accepts_nature()
        if not accepts:
            continue
        allowed = {nature_from_name(name) for name in accepts[0]}
        allowed.discard(None)
        if receiver in allowed:
            return effect_nature_from_contract(contract)
    return None
