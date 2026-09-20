"""Shape analysis engine."""

from __future__ import annotations

from python_checker.shapes.dims import Dim, Shape
from python_checker.shapes.transfers import TransferResult, apply_transfer

__all__ = [
    "Dim",
    "Shape",
    "TransferResult",
    "apply_transfer",
]
