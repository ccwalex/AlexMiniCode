"""Static PyTorch tensor shape tracking pass."""

from __future__ import annotations

import ast
from typing import Any

from python_checker.facts import FactsStore, Nature, TensorFact
from python_checker.parse import ParsedSource, node_location
from python_checker.report import Finding, Severity
from python_checker.shapes.dims import Dim, Shape, join_shapes
from python_checker.shapes.transfers import apply_transfer


SHAPE_CONSTRUCTORS = {
    "torch.randn",
    "torch.zeros",
    "torch.ones",
    "torch.tensor",
}


class ShapeTracker:
    def __init__(self, parsed: ParsedSource, facts: FactsStore) -> None:
        self.parsed = parsed
        self.facts = facts
        self.shapes: dict[str, Shape | None] = {}
        self.findings: list[Finding] = []

    def run(self) -> list[Finding]:
        self._seed_from_assignments()
        for site in self.facts.call_sites:
            self._process_call(site)
        return self.findings

    def _seed_from_assignments(self) -> None:
        for node in ast.walk(self.parsed.tree):
            if isinstance(node, ast.Assign):
                shape = self._infer_shape(node.value)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.shapes[target.id] = shape
                        fact = self.facts.get_or_create(target.id)
                        fact.nature = Nature.TENSOR if shape is not None else fact.nature
                        fact.tensor = TensorFact(shape=shape)

    def _process_call(self, site: dict[str, Any]) -> None:
        func = site["func"]
        line, col = site["line"], site["col"]
        transfer_id = self._transfer_id(func, site["node"])
        if transfer_id is None:
            if func.startswith("torch.") and func not in SHAPE_CONSTRUCTORS:
                self.findings.append(
                    Finding(
                        code="SHAPE_UNCOVERED",
                        severity=Severity.UNCOVERED,
                        message=f"No shape rule for {func}",
                        path=self.parsed.path,
                        line=line,
                        col=col,
                        rule_id="pytorch.shape.uncovered",
                        evidence={"api": func},
                    )
                )
            return

        operand_shapes = [self._shape_from_expr(arg) for arg in site["node"].args]
        kwargs = self._shape_kwargs(site)
        result = apply_transfer(transfer_id, operand_shapes, kwargs)
        if not result.ok and not result.uncovered:
            self.findings.append(
                Finding(
                    code="SHAPE_MISMATCH",
                    severity=Severity.ERROR,
                    message=result.reason or "shape mismatch",
                    path=self.parsed.path,
                    line=line,
                    col=col,
                    rule_id=f"pytorch.{transfer_id}",
                    evidence={"api": func},
                )
            )
        elif result.uncovered:
            self.findings.append(
                Finding(
                    code="SHAPE_UNCOVERED",
                    severity=Severity.UNCOVERED,
                    message=result.reason or "shape unknown",
                    path=self.parsed.path,
                    line=line,
                    col=col,
                    rule_id=f"pytorch.{transfer_id}",
                    evidence={"api": func},
                )
            )

    def _transfer_id(self, func: str, node: ast.Call) -> str | None:
        if func in {"torch.matmul", "torch.mm"}:
            return "torch.matmul"
        if func.endswith(".matmul"):
            return "torch.matmul"
        if func.endswith(".view") or func.endswith(".reshape"):
            return "view"
        if func in {"torch.cat"}:
            return "torch.cat"
        if func.endswith(".Linear") or func.endswith("nn.Linear"):
            return "nn.Linear.forward"
        if func.endswith("Conv2d"):
            return "nn.Conv2d.forward"
        if func == "torch.ones_like":
            return "torch.ones_like"
        return None

    def _shape_kwargs(self, site: dict[str, Any]) -> dict[str, Any]:
        kwargs = dict(site.get("kwargs", {}))
        node = site["node"]
        if not isinstance(node, ast.Call):
            return kwargs
        func = site["func"]
        if "Linear" in func and len(node.args) >= 1:
            module_name = self._module_name(node.func)
            if module_name and module_name in self.facts.symbols:
                pass
        if func.endswith("Linear") or "Linear" in func:
            for kw in node.keywords:
                if kw.arg == "in_features" and isinstance(kw.value, ast.Constant):
                    kwargs.setdefault("in_features", kw.value.value)
                if kw.arg == "out_features" and isinstance(kw.value, ast.Constant):
                    kwargs.setdefault("out_features", kw.value.value)
        if "Conv2d" in func:
            for kw in node.keywords:
                if kw.arg == "out_channels" and isinstance(kw.value, ast.Constant):
                    kwargs.setdefault("out_channels", kw.value.value)
        if site.get("func", "").endswith(("view", "reshape")):
            if node.args:
                shape = self._literal_shape(node.args[0])
                if shape is not None:
                    kwargs["shape"] = shape
        return kwargs

    def _module_name(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return node.value.id
        return None

    def _infer_shape(self, node: ast.expr) -> Shape | None:
        if isinstance(node, ast.Call):
            func = self._expr_name(node.func)
            if func in {"torch.randn", "torch.zeros", "torch.ones", "torch.tensor"}:
                dims: list[Dim] = []
                if node.args and all(
                    isinstance(arg, ast.Constant) and isinstance(arg.value, int)
                    for arg in node.args
                ):
                    dims = [Dim.concrete(arg.value) for arg in node.args]
                elif node.args and isinstance(node.args[0], ast.Tuple):
                    for elt in node.args[0].elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, int):
                            dims.append(Dim.concrete(elt.value))
                        elif isinstance(elt, ast.Name):
                            dims.append(Dim.symbol(elt.id))
                        else:
                            return Shape.any_rank(len(node.args[0].elts))
                if dims:
                    return Shape(tuple(dims))
            if func == "torch.ones_like" and node.args:
                return self._shape_from_expr(node.args[0])
        if isinstance(node, ast.Name):
            return self.shapes.get(node.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            func = self._expr_name(node.func)
            if func.endswith(".view") or func.endswith(".reshape"):
                base = self._shape_from_expr(node.func.value)
                if base and node.args:
                    new_shape = self._literal_shape(node.args[0])
                    if new_shape is not None:
                        return new_shape
            if func.endswith(".matmul") or func.endswith("matmul"):
                left = self._shape_from_expr(node.args[0]) if node.args else None
                right = self._shape_from_expr(node.args[1]) if len(node.args) > 1 else None
                result = apply_transfer("matmul", [left, right], {})
                return result.shape if result.ok else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.MatMult):
            left = self._shape_from_expr(node.left)
            right = self._shape_from_expr(node.right)
            result = apply_transfer("@", [left, right], {})
            return result.shape if result.ok else None
        return None

    def _shape_from_expr(self, node: ast.expr) -> Shape | None:
        if isinstance(node, ast.Name):
            return self.shapes.get(node.id)
        return self._infer_shape(node)

    def _literal_shape(self, node: ast.expr) -> Shape | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return Shape((Dim.concrete(node.value),))
        if isinstance(node, ast.Tuple):
            dims = []
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, int):
                    dims.append(Dim.concrete(elt.value))
                elif isinstance(elt, ast.Name):
                    dims.append(Dim.symbol(elt.id))
                else:
                    return None
            return Shape(tuple(dims))
        return None

    def _expr_name(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return self.facts.import_map.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self._expr_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return ""


def check_shapes(parsed: ParsedSource, facts: FactsStore, packs: list[str] | None = None) -> list[Finding]:
    if packs is not None and "pytorch" not in packs:
        return []
    return ShapeTracker(parsed, facts).run()
