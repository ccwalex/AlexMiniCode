"""Collect import aliases and call sites into the facts store."""

from __future__ import annotations

import ast
from typing import Any

from python_checker.facts import FactsStore, ImportAlias, Nature
from python_checker.parse import ParsedSource, node_location


class ImportFactsVisitor(ast.NodeVisitor):
    def __init__(self, facts: FactsStore, parsed: ParsedSource) -> None:
        self.facts = facts
        self.parsed = parsed

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            asname = alias.asname or alias.name.split(".")[0]
            self.facts.imports.append(ImportAlias(alias.name, None, asname))
            self.facts.import_map[asname] = alias.name
            self.facts.get_or_create(asname).defined = True
            self.facts.get_or_create(asname).nature = Nature.MODULE
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                continue
            asname = alias.asname or alias.name
            full = f"{module}.{alias.name}" if module else alias.name
            self.facts.imports.append(ImportAlias(module, alias.name, asname))
            self.facts.import_map[asname] = full
            self.facts.get_or_create(asname).defined = True
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        value_info = self._expr_info(node.value)
        for target in node.targets:
            for name in self._names_in_target(target):
                fact = self.facts.get_or_create(name)
                fact.defined = True
                fact.assigned = True
                if value_info.get("nature"):
                    fact.nature = value_info["nature"]
                if value_info.get("shape") is not None:
                    from python_checker.facts import TensorFact

                    fact.tensor = TensorFact(shape=value_info["shape"])
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            fact = self.facts.get_or_create(node.target.id)
            fact.defined = True
            fact.assigned = node.value is not None
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.facts.get_or_create(node.id).read = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        info = self._call_info(node)
        self.facts.call_sites.append(info)
        self.generic_visit(node)

    def _names_in_target(self, target: ast.expr) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, ast.Tuple):
            names: list[str] = []
            for elt in target.elts:
                names.extend(self._names_in_target(elt))
            return names
        return []

    def _expr_info(self, node: ast.expr) -> dict[str, Any]:
        from python_checker.nature_match import infer_literal_nature

        literal = infer_literal_nature(node)
        if literal is not None:
            return {"nature": literal}
        if isinstance(node, ast.Call):
            return self._call_info(node)
        if isinstance(node, ast.Name):
            fact = self.facts.symbols.get(node.id)
            info: dict[str, Any] = {}
            if fact and fact.nature != Nature.UNKNOWN:
                info["nature"] = fact.nature
            if fact and fact.tensor and fact.tensor.shape is not None:
                info["shape"] = fact.tensor.shape
            return info
        if isinstance(node, ast.Attribute):
            base = self._expr_to_dotted(node.value)
            return self._lookup_constructor(base, node.attr)
        return {}

    def _call_info(self, node: ast.Call) -> dict[str, Any]:
        line, col = node_location(node)
        func_name = self._expr_to_dotted(node.func)
        args = [self._expr_info(arg) for arg in node.args]
        kwargs = {
            kw.arg: self._literal_value(kw.value)
            for kw in node.keywords
            if kw.arg is not None
        }
        info: dict[str, Any] = {
            "path": self.parsed.path,
            "line": line,
            "col": col,
            "func": func_name,
            "args": args,
            "kwargs": kwargs,
            "node": node,
        }
        ctor = self._lookup_constructor(func_name, "")
        if ctor:
            info.update({k: v for k, v in ctor.items() if k not in info})
        return info

    def _expr_to_dotted(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            resolved = self.facts.import_map.get(node.id, node.id)
            return resolved
        if isinstance(node, ast.Attribute):
            base = self._expr_to_dotted(node.value)
            if base:
                return f"{base}.{node.attr}"
            return node.attr
        return ""

    def _literal_value(self, node: ast.expr) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Tuple):
            return tuple(self._literal_value(elt) for elt in node.elts)
        if isinstance(node, ast.List):
            return [self._literal_value(elt) for elt in node.elts]
        if isinstance(node, ast.Name):
            return node.id
        return None

    def _lookup_constructor(self, base: str, attr: str) -> dict[str, Any]:
        from python_checker.shapes.dims import Dim, Shape

        api = f"{base}.{attr}" if attr else base
        constructors: dict[str, dict[str, Any]] = {
            "torch.randn": {
                "nature": Nature.TENSOR,
                "shape": self._shape_from_args(base, attr),
            },
            "torch.zeros": {"nature": Nature.TENSOR},
            "torch.ones": {"nature": Nature.TENSOR},
            "torch.tensor": {"nature": Nature.TENSOR},
            "torch.nn.Linear": {"nature": Nature.MODULE},
            "torch.nn.Conv2d": {"nature": Nature.MODULE},
            "pandas.read_csv": {"nature": Nature.DATAFRAME},
            "pandas.DataFrame": {"nature": Nature.DATAFRAME},
            "numpy.array": {"nature": Nature.NDARRAY},
            "numpy.zeros": {"nature": Nature.NDARRAY},
            "numpy.ones": {"nature": Nature.NDARRAY},
            "fastapi.FastAPI": {"nature": Nature.FASTAPI_APP},
            "openai.OpenAI": {"nature": Nature.OPENAI_CLIENT},
            "sklearn.base.BaseEstimator": {"nature": Nature.SKLEARN_ESTIMATOR},
        }
        if api in constructors:
            return constructors[api]
        if base.endswith(".Linear") and attr == "":
            return {"nature": Nature.MODULE}
        return {}

    def _shape_from_args(self, base: str, attr: str) -> Shape | None:
        return None


def collect_import_facts(parsed: ParsedSource, facts: FactsStore) -> None:
    ImportFactsVisitor(facts, parsed).visit(parsed.tree)
