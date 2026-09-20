"""Shared facts store for analysis passes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from python_checker.shapes.dims import Shape

if TYPE_CHECKING:
    from python_checker.nature_match import NatureIndex


class Nature(str, Enum):
    UNKNOWN = "Unknown"
    SCALAR = "Scalar"
    LIST = "List"
    NDARRAY = "NdArray"
    DATAFRAME = "DataFrame"
    SERIES = "Series"
    TENSOR = "Tensor"
    MODULE = "Module"
    FASTAPI_APP = "FastAPIApp"
    OPENAI_CLIENT = "OpenAIClient"
    SKLEARN_ESTIMATOR = "SklearnEstimator"


@dataclass
class TensorFact:
    shape: Shape | None = None
    dtype: str | None = None
    device: str | None = None


@dataclass
class SymbolFact:
    name: str
    nature: Nature = Nature.UNKNOWN
    tensor: TensorFact | None = None
    defined: bool = False
    assigned: bool = False
    read: bool = False
    attrs: dict[str, "SymbolFact"] = field(default_factory=dict)


@dataclass
class ImportAlias:
    module: str
    name: str | None
    asname: str


@dataclass
class FactsStore:
    imports: list[ImportAlias] = field(default_factory=list)
    import_map: dict[str, str] = field(default_factory=dict)
    symbols: dict[str, SymbolFact] = field(default_factory=dict)
    call_sites: list[dict[str, Any]] = field(default_factory=list)
    nature_index: NatureIndex | None = None

    def get_or_create(self, name: str) -> SymbolFact:
        if name not in self.symbols:
            self.symbols[name] = SymbolFact(name=name)
        return self.symbols[name]

    def resolve_module(self, alias: str) -> str | None:
        return self.import_map.get(alias, alias if alias in self.import_map.values() else None)

    def merge_nature(self, name: str, nature: Nature) -> None:
        fact = self.get_or_create(name)
        if fact.nature == Nature.UNKNOWN:
            fact.nature = nature
        elif fact.nature != nature and nature != Nature.UNKNOWN:
            fact.nature = Nature.UNKNOWN
