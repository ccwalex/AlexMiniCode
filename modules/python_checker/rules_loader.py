"""Rule pack loading and contract schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RULES_ROOT = Path(__file__).resolve().parent / "rules"
ALWAYS_LOAD_PACKS = {"ops", "conversions"}


@dataclass
class RuleContract:
    api: str
    pack: str
    kind: str = "call"
    construct: dict[str, Any] = field(default_factory=dict)
    call: dict[str, Any] = field(default_factory=dict)
    requires_nature: list[str] = field(default_factory=list)
    accepts_nature: list[list[str]] = field(default_factory=list)
    forbids_nature: list[str] = field(default_factory=list)
    effects: dict[str, Any] = field(default_factory=dict)
    shape: str | None = None
    required_kwargs: list[str] = field(default_factory=list)

    def normalized_accepts_nature(self) -> list[list[str]]:
        if self.accepts_nature:
            return self.accepts_nature
        if self.requires_nature:
            return [[name] for name in self.requires_nature]
        return []


def load_rule_packs(packs: list[str] | None = None) -> list[RuleContract]:
    if packs is None:
        selected: set[str] | None = None
    else:
        selected = set(packs) | ALWAYS_LOAD_PACKS

    contracts: list[RuleContract] = []
    if not RULES_ROOT.exists():
        return contracts

    for pack_dir in sorted(RULES_ROOT.iterdir()):
        if not pack_dir.is_dir():
            continue
        if selected is not None and pack_dir.name not in selected:
            continue
        for rule_file in sorted(pack_dir.glob("*.yaml")):
            data = yaml.safe_load(rule_file.read_text(encoding="utf-8")) or {}
            contracts.append(
                RuleContract(
                    api=data["api"],
                    pack=pack_dir.name,
                    kind=data.get("kind", "call"),
                    construct=data.get("construct", {}),
                    call=data.get("call", {}),
                    requires_nature=data.get("requires_nature", []),
                    accepts_nature=data.get("accepts_nature", []),
                    forbids_nature=data.get("forbids_nature", []),
                    effects=data.get("effects", {}),
                    shape=data.get("shape"),
                    required_kwargs=data.get("required_kwargs", []),
                )
            )
    return contracts
