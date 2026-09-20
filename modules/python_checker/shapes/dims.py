"""Symbolic dimension and shape types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator


class DimKind(str, Enum):
    CONCRETE = "concrete"
    SYMBOL = "symbol"
    ANY = "any"


@dataclass(frozen=True)
class Dim:
    kind: DimKind
    value: int | str | None = None

    @staticmethod
    def concrete(value: int) -> "Dim":
        return Dim(DimKind.CONCRETE, value)

    @staticmethod
    def symbol(name: str) -> "Dim":
        return Dim(DimKind.SYMBOL, name)

    @staticmethod
    def any_dim() -> "Dim":
        return Dim(DimKind.ANY, None)

    def __str__(self) -> str:
        if self.kind == DimKind.CONCRETE:
            return str(self.value)
        if self.kind == DimKind.SYMBOL:
            return str(self.value)
        return "*"


@dataclass(frozen=True)
class Shape:
    dims: tuple[Dim, ...]

    @staticmethod
    def scalar() -> "Shape":
        return Shape(())

    @staticmethod
    def from_ints(values: tuple[int, ...]) -> "Shape":
        return Shape(tuple(Dim.concrete(v) for v in values))

    @staticmethod
    def from_symbols(values: tuple[str, ...]) -> "Shape":
        return Shape(tuple(Dim.symbol(v) for v in values))

    @staticmethod
    def any_rank(rank: int) -> "Shape":
        return Shape(tuple(Dim.any_dim() for _ in range(rank)))

    def rank(self) -> int:
        return len(self.dims)

    def __str__(self) -> str:
        if not self.dims:
            return "()"
        return "(" + ", ".join(str(d) for d in self.dims) + ")"


@dataclass
class UnificationResult:
    ok: bool
    shape: Shape | None = None
    bindings: dict[str, int] | None = None
    reason: str | None = None


def unify_dim(a: Dim, b: Dim, bindings: dict[str, int]) -> bool:
    if a.kind == DimKind.ANY or b.kind == DimKind.ANY:
        return True
    if a.kind == DimKind.CONCRETE and b.kind == DimKind.CONCRETE:
        return a.value == b.value
    if a.kind == DimKind.SYMBOL and b.kind == DimKind.CONCRETE:
        name = str(a.value)
        if name in bindings and bindings[name] != b.value:
            return False
        bindings[name] = int(b.value)
        return True
    if b.kind == DimKind.SYMBOL and a.kind == DimKind.CONCRETE:
        name = str(b.value)
        if name in bindings and bindings[name] != a.value:
            return False
        bindings[name] = int(a.value)
        return True
    if a.kind == DimKind.SYMBOL and b.kind == DimKind.SYMBOL:
        return str(a.value) == str(b.value)
    return False


def unify_shapes(left: Shape, right: Shape) -> UnificationResult:
    if left.rank() != right.rank():
        return UnificationResult(False, reason="rank mismatch")
    bindings: dict[str, int] = {}
    merged: list[Dim] = []
    for a, b in zip(left.dims, right.dims):
        if not unify_dim(a, b, bindings):
            return UnificationResult(False, reason=f"dim mismatch: {a} vs {b}")
        if a.kind == DimKind.CONCRETE:
            merged.append(a)
        elif b.kind == DimKind.CONCRETE:
            merged.append(b)
        elif a.kind == DimKind.SYMBOL:
            merged.append(a)
        elif b.kind == DimKind.SYMBOL:
            merged.append(b)
        else:
            merged.append(Dim.any_dim())
    return UnificationResult(True, Shape(tuple(merged)), bindings)


def join_shapes(a: Shape | None, b: Shape | None) -> Shape | None:
    if a is None:
        return b
    if b is None:
        return a
    result = unify_shapes(a, b)
    if result.ok:
        return result.shape
    return Shape.any_rank(max(a.rank(), b.rank()))
