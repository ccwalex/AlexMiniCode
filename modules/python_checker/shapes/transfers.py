"""Shape transfer functions for PyTorch operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from python_checker.shapes.dims import Dim, DimKind, Shape, UnificationResult, unify_shapes


@dataclass
class TransferResult:
    ok: bool
    shape: Shape | None = None
    uncovered: bool = False
    reason: str | None = None


TransferFn = Callable[[list[Shape | None], dict], TransferResult]


def _require_shapes(shapes: list[Shape | None], count: int) -> tuple[list[Shape], TransferResult | None]:
    if len(shapes) < count:
        return [], TransferResult(False, reason="not enough operands")
    resolved: list[Shape] = []
    for shape in shapes[:count]:
        if shape is None:
            return [], TransferResult(False, uncovered=True, reason="unknown operand shape")
        resolved.append(shape)
    return resolved, None


def transfer_matmul(shapes: list[Shape | None], _: dict) -> TransferResult:
    operands, err = _require_shapes(shapes, 2)
    if err:
        return err
    left, right = operands
    if left.rank() < 1 or right.rank() < 1:
        return TransferResult(False, reason="matmul requires rank >= 1")

    if right.rank() == 1:
        inner_left = left.dims[-1]
        inner_right = right.dims[0]
        out_prefix = left.dims[:-1]
        out_suffix: tuple[Dim, ...] = ()
    else:
        inner_left = left.dims[-1]
        inner_right = right.dims[-2]
        out_prefix = left.dims[:-1]
        out_suffix = right.dims[-1:]

    unified = unify_shapes(Shape((inner_left,)), Shape((inner_right,)))
    if not unified.ok:
        return TransferResult(False, reason="inner dim mismatch")
    return TransferResult(True, Shape(out_prefix + out_suffix))


def transfer_view(shapes: list[Shape | None], kwargs: dict) -> TransferResult:
    operands, err = _require_shapes(shapes, 1)
    if err:
        return err
    target = kwargs.get("shape")
    if target is None:
        return TransferResult(False, uncovered=True, reason="dynamic view shape")
    if isinstance(target, Shape):
        return TransferResult(True, target)
    if isinstance(target, tuple):
        dims = []
        for item in target:
            if isinstance(item, int):
                dims.append(Dim.concrete(item))
            elif isinstance(item, str):
                dims.append(Dim.symbol(item))
            else:
                return TransferResult(False, uncovered=True, reason="unsupported view dim")
        return TransferResult(True, Shape(tuple(dims)))
    return TransferResult(False, uncovered=True, reason="unsupported view shape")


def transfer_cat(shapes: list[Shape | None], kwargs: dict) -> TransferResult:
    if not shapes:
        return TransferResult(False, reason="cat requires tensors")
    resolved = [s for s in shapes if s is not None]
    if not resolved:
        return TransferResult(False, uncovered=True, reason="unknown cat operands")
    base = resolved[0]
    for other in resolved[1:]:
        if other.rank() != base.rank():
            return TransferResult(False, reason="cat rank mismatch")
        for i, (a, b) in enumerate(zip(base.dims, other.dims)):
            axis = kwargs.get("dim", 0)
            if i == axis:
                continue
            if a != b:
                return TransferResult(False, reason="cat dim mismatch")
    axis = kwargs.get("dim", 0)
    if axis < 0 or axis >= base.rank():
        return TransferResult(False, reason="invalid cat axis")
    new_dims = list(base.dims)
    total = 0
    has_any = False
    for shape in resolved:
        dim = shape.dims[axis]
        if dim.kind == DimKind.CONCRETE:
            total += int(dim.value)
        else:
            has_any = True
    if has_any:
        new_dims[axis] = Dim.any_dim()
    else:
        new_dims[axis] = Dim.concrete(total)
    return TransferResult(True, Shape(tuple(new_dims)))


def transfer_linear(shapes: list[Shape | None], kwargs: dict) -> TransferResult:
    operands, err = _require_shapes(shapes, 1)
    if err:
        return err
    inp = operands[0]
    in_features = kwargs.get("in_features")
    out_features = kwargs.get("out_features")
    if in_features is None or out_features is None:
        return TransferResult(False, uncovered=True, reason="unknown linear features")
    if inp.rank() == 0:
        return TransferResult(False, reason="linear expects tensor input")
    if inp.dims[-1].kind == DimKind.CONCRETE and int(inp.dims[-1].value) != int(in_features):
        return TransferResult(False, reason="linear in_features mismatch")
    if inp.dims[-1].kind not in (DimKind.CONCRETE, DimKind.SYMBOL, DimKind.ANY):
        return TransferResult(False, uncovered=True, reason="unknown input feature dim")
    if inp.dims[-1].kind == DimKind.SYMBOL and inp.dims[-1].value != "in_features":
        unified = unify_shapes(
            Shape((inp.dims[-1],)),
            Shape((Dim.concrete(int(in_features)),)),
        )
        if not unified.ok:
            return TransferResult(False, reason="linear in_features mismatch")
    out_dims = inp.dims[:-1] + (Dim.concrete(int(out_features)),)
    return TransferResult(True, Shape(out_dims))


def transfer_conv2d(shapes: list[Shape | None], kwargs: dict) -> TransferResult:
    operands, err = _require_shapes(shapes, 1)
    if err:
        return err
    inp = operands[0]
    if inp.rank() != 4:
        return TransferResult(False, reason="conv2d expects NCHW input")
    out_channels = kwargs.get("out_channels")
    if out_channels is None:
        return TransferResult(False, uncovered=True, reason="unknown out_channels")
    n, _, h, w = inp.dims
    return TransferResult(True, Shape((n, Dim.concrete(int(out_channels)), h, w)))


def transfer_ones_like(shapes: list[Shape | None], _: dict) -> TransferResult:
    operands, err = _require_shapes(shapes, 1)
    if err:
        return err
    return TransferResult(True, operands[0])


TRANSFERS: dict[str, TransferFn] = {
    "matmul": transfer_matmul,
    "torch.matmul": transfer_matmul,
    "torch.mm": transfer_matmul,
    "@": transfer_matmul,
    "view": transfer_view,
    "reshape": transfer_view,
    "cat": transfer_cat,
    "torch.cat": transfer_cat,
    "nn.Linear.forward": transfer_linear,
    "linear": transfer_linear,
    "nn.Conv2d.forward": transfer_conv2d,
    "conv2d": transfer_conv2d,
    "torch.ones_like": transfer_ones_like,
}


def apply_transfer(transfer_id: str, shapes: list[Shape | None], kwargs: dict | None = None) -> TransferResult:
    fn = TRANSFERS.get(transfer_id)
    if fn is None:
        return TransferResult(False, uncovered=True, reason=f"unsupported transfer {transfer_id}")
    return fn(shapes, kwargs or {})
