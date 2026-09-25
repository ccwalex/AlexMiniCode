MODULE_METADATA = {
    "name": "extract_public_contract",
    "type": "function",
    "description": "Extract a deterministic public I/O contract from Python or TS/React source using AST heuristics.",
    "functions": [
        {
            "name": "extract_public_contract",
            "inputs": {
                "path": "str file path",
                "content": "str source text",
                "code_type": "str or None py, ts, or react",
            },
            "outputs": "dict with plausible bool, outputs dict, and error",
        },
        {
            "name": "output_format_changed",
            "inputs": {
                "before": "dict contract",
                "after": "dict contract",
            },
            "outputs": "tuple (changed bool, reason str, used_llm_fallback_needed bool)",
        },
        {
            "name": "classify_io_delta",
            "inputs": {
                "before": "dict contract",
                "after": "dict contract",
            },
            "outputs": "dict with action skip or update_callers, reason, and hints",
        },
    ],
}

import ast
import json
import re

from infer_code_type import infer_code_type


def _ann(node):
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return ast.dump(node, annotate_fields=False)


def _kwonly_param_default(kw_defaults, kw_index):
    default_node = kw_defaults[kw_index] if kw_index < len(kw_defaults) else None
    if default_node is None:
        return None, True
    return _ann(default_node), False


def _return_arity_from_annotation(returns_ann):
    if not returns_ann:
        return None
    text = str(returns_ann).strip()
    if not text or text in {"None", "typing.None", "NoneType"}:
        return 0
    lowered = text.lower()
    if lowered.startswith("tuple[") or lowered.startswith("typing.tuple["):
        inner = text[text.index("[") + 1 : text.rindex("]")]
        parts = [part.strip() for part in inner.split(",") if part.strip()]
        return len(parts) if parts else None
    if lowered.startswith("tuple("):
        inner = text[text.index("(") + 1 : text.rindex(")")]
        parts = [part.strip() for part in inner.split(",") if part.strip()]
        return len(parts) if parts else None
    return 1


def _return_arity_from_body(node):
    arities = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Return) or child.value is None:
            continue
        value = child.value
        if isinstance(value, ast.Tuple):
            arities.add(len(value.elts))
        elif isinstance(value, ast.Name) and value.id == "None":
            arities.add(0)
        else:
            arities.add(1)
    if len(arities) == 1:
        return next(iter(arities))
    return None


def _py_func_sig(node, skip_self=False):
    params = []
    positional_args = list(node.args.args)
    start_index = 1 if skip_self and positional_args else 0
    visible_args = positional_args[start_index:]
    defaults = list(node.args.defaults)
    num_no_default = len(positional_args) - len(defaults)
    for offset, arg in enumerate(visible_args):
        pos_index = start_index + offset
        if pos_index < num_no_default:
            default_val, required = None, True
        else:
            default_val = _ann(defaults[pos_index - num_no_default])
            required = False
        params.append(
            {
                "name": arg.arg,
                "annotation": _ann(arg.annotation),
                "default": default_val,
                "required": required,
            }
        )
    for index, arg in enumerate(node.args.kwonlyargs):
        default_val, required = _kwonly_param_default(node.args.kw_defaults, index)
        params.append(
            {
                "name": arg.arg,
                "annotation": _ann(arg.annotation),
                "default": default_val,
                "required": required,
            }
        )
    returns = _ann(node.returns)
    return_arity = _return_arity_from_annotation(returns)
    if return_arity is None:
        return_arity = _return_arity_from_body(node)
    return {
        "kind": "function",
        "params": params,
        "returns": returns,
        "return_arity": return_arity,
        "async": isinstance(node, ast.AsyncFunctionDef),
    }


def _python_primary_export_name(content):
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                try:
                    all_names = ast.literal_eval(node.value)
                except Exception:
                    continue
                if isinstance(all_names, (list, tuple)) and len(all_names) == 1:
                    name = all_names[0]
                    if isinstance(name, str) and name.strip():
                        return name.strip()

    public_names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                public_names.append(node.name)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            public_names.append(node.name)

    if len(public_names) == 1:
        return public_names[0]
    return None


def _typescript_primary_export_name(exports):
    if not exports:
        return None
    if "default" in exports:
        return "default"
    callable_names = [
        name
        for name, spec in exports.items()
        if name != "__types__" and isinstance(spec, dict) and spec.get("kind") == "function"
    ]
    if len(callable_names) == 1:
        return callable_names[0]
    return None


def _filter_exports(exports, primary_name):
    if not primary_name or primary_name not in exports:
        return exports
    return {primary_name: exports[primary_name]}


def _extract_python(content):
    tree = ast.parse(content)
    exports = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            exports[node.name] = _py_func_sig(node)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            methods = {}
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                    methods[item.name] = _py_func_sig(item, skip_self=True)
            exports[node.name] = {"kind": "class", "methods": methods}
    return exports


_TS_FUNC = re.compile(
    r"export\s+(async\s+)?function\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)\s*(?::\s*([^{]+?))?\s*\{",
    re.MULTILINE,
)
_TS_DEFAULT_FUNC = re.compile(
    r"export\s+default\s+(async\s+)?function(?:\s+([A-Za-z_][\w]*))?\s*\(([^)]*)\)\s*(?::\s*([^{]+?))?\s*\{",
    re.MULTILINE,
)
_TS_CONST_ARROW = re.compile(
    r"export\s+const\s+([A-Za-z_][\w]*)\s*=\s*(async\s*)?\(([^)]*)\)\s*(?::\s*([^=]+?))?\s*=>",
    re.MULTILINE,
)
_TS_TYPE = re.compile(
    r"export\s+(?:type|interface)\s+([A-Za-z_][\w]*)",
    re.MULTILINE,
)


def _ts_params(raw):
    params = []
    for part in (raw or "").split(","):
        piece = part.strip()
        if not piece:
            continue
        name, _, rest = piece.partition(":")
        params.append({"name": name.strip().lstrip("{[").split("=")[0].strip(), "annotation": rest.strip() or None})
    return params


def _extract_typescript(content):
    exports = {}
    for match in _TS_FUNC.finditer(content):
        exports[match.group(2)] = {
            "kind": "function",
            "params": _ts_params(match.group(3)),
            "returns": (match.group(4) or "").strip() or None,
            "async": bool(match.group(1)),
        }
    for match in _TS_DEFAULT_FUNC.finditer(content):
        name = match.group(2) or "default"
        exports[name] = {
            "kind": "function",
            "params": _ts_params(match.group(3)),
            "returns": (match.group(4) or "").strip() or None,
            "async": bool(match.group(1)),
            "default": True,
        }
    for match in _TS_CONST_ARROW.finditer(content):
        exports[match.group(1)] = {
            "kind": "function",
            "params": _ts_params(match.group(3)),
            "returns": (match.group(4) or "").strip() or None,
            "async": bool(match.group(2)),
        }
    types = _TS_TYPE.findall(content)
    if types:
        exports["__types__"] = {"kind": "types", "names": sorted(set(types))}
    return exports


def _output_view(exports):
    view = {}
    for name, spec in (exports or {}).items():
        if not isinstance(spec, dict):
            continue
        if spec.get("kind") == "class":
            methods = {}
            for method, body in (spec.get("methods") or {}).items():
                methods[method] = body.get("returns")
            view[name] = {"kind": "class", "returns": methods}
        elif spec.get("kind") == "types":
            view[name] = {"kind": "types", "names": spec.get("names") or []}
        else:
            view[name] = {"kind": spec.get("kind"), "returns": spec.get("returns")}
    return view


def _has_output_types(view):
    for spec in (view or {}).values():
        if spec.get("kind") == "class":
            if any(value for value in (spec.get("returns") or {}).values()):
                return True
        elif spec.get("kind") == "types":
            if spec.get("names"):
                return True
        elif spec.get("returns"):
            return True
    return False


def extract_public_contract(path, content, code_type=None):
    text = "" if content is None else str(content)
    kind = code_type or infer_code_type(path, text)
    result = {
        "path": path,
        "code_type": kind,
        "plausible": False,
        "exports": {},
        "outputs": {},
        "primary_name": None,
        "raw_export_count": 0,
        "error": None,
    }
    if not text.strip():
        result["error"] = "empty source"
        return result
    try:
        if kind == "py":
            exports = _extract_python(text)
            primary_name = _python_primary_export_name(text)
        elif kind in {"ts", "react"}:
            exports = _extract_typescript(text)
            primary_name = _typescript_primary_export_name(exports)
        else:
            result["error"] = f"unsupported code_type: {kind}"
            return result
    except SyntaxError as exc:
        result["error"] = f"syntax error: {exc}"
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result

    if not exports:
        result["error"] = "no public exports found"
        return result

    raw_export_count = len([name for name in exports if name != "__types__"])
    scoped_exports = _filter_exports(exports, primary_name)
    if not scoped_exports:
        result["error"] = "no public exports found"
        return result

    result["primary_name"] = primary_name
    result["raw_export_count"] = raw_export_count
    result["exports"] = scoped_exports
    result["outputs"] = _output_view(scoped_exports)
    result["plausible"] = True
    return result


def _contract_is_ambiguous(contract):
    if not contract.get("plausible"):
        return True
    raw_count = int(contract.get("raw_export_count") or 0)
    primary_name = contract.get("primary_name")
    if raw_count > 1 and not primary_name:
        return True
    if raw_count > 1 and primary_name and primary_name not in (contract.get("exports") or {}):
        return True
    return False


def _multi_export_module(contract):
    return int((contract or {}).get("raw_export_count") or 0) > 1


def _primary_export_sig(contract):
    if not isinstance(contract, dict) or not contract.get("plausible"):
        return None, None
    primary_name = contract.get("primary_name")
    exports = contract.get("exports") or {}
    if primary_name and primary_name in exports:
        return primary_name, exports[primary_name]
    if len(exports) == 1:
        name = next(iter(exports))
        return name, exports[name]
    return primary_name, None


def classify_io_delta(before, after):
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    if not before.get("plausible") or not after.get("plausible"):
        return {
            "action": "skip",
            "reason": "unparseable ast",
            "hints": {},
        }
    if _contract_is_ambiguous(before) or _contract_is_ambiguous(after):
        return {
            "action": "skip",
            "reason": "ambiguous public exports",
            "hints": {},
        }

    primary_name, before_sig = _primary_export_sig(before)
    _, after_sig = _primary_export_sig(after)
    if not primary_name or not isinstance(before_sig, dict) or not isinstance(after_sig, dict):
        return {
            "action": "skip",
            "reason": "no primary export signature",
            "hints": {},
        }

    before_params = before_sig.get("params") or []
    after_params = after_sig.get("params") or []
    before_by_name = {item["name"]: item for item in before_params if item.get("name")}
    after_by_name = {item["name"]: item for item in after_params if item.get("name")}

    removed_params = [name for name in before_by_name if name not in after_by_name]
    added_required_params = [
        item["name"]
        for item in after_params
        if item.get("name") not in before_by_name and item.get("required", True)
    ]
    added_optional_params = [
        item["name"]
        for item in after_params
        if item.get("name") not in before_by_name and not item.get("required", True)
    ]
    became_required = [
        name
        for name, after_item in after_by_name.items()
        if name in before_by_name
        and not before_by_name[name].get("required", True)
        and after_item.get("required", True)
    ]

    return_type_before = before_sig.get("returns")
    return_type_after = after_sig.get("returns")
    return_arity_before = before_sig.get("return_arity")
    return_arity_after = after_sig.get("return_arity")
    return_type_changed = return_type_before != return_type_after
    return_arity_changed = (
        return_arity_before is not None
        and return_arity_after is not None
        and return_arity_before != return_arity_after
    )

    hints = {
        "primary_name": primary_name,
        "added_required_params": added_required_params,
        "added_optional_params": added_optional_params,
        "removed_params": removed_params,
        "became_required": became_required,
        "return_type_before": return_type_before,
        "return_type_after": return_type_after,
        "return_arity_before": return_arity_before,
        "return_arity_after": return_arity_after,
    }

    breaking = bool(
        removed_params
        or added_required_params
        or became_required
        or return_type_changed
        or return_arity_changed
    )
    if breaking:
        reason_parts = []
        if added_required_params:
            reason_parts.append("new required params")
        if became_required:
            reason_parts.append("params became required")
        if removed_params:
            reason_parts.append("removed params")
        if return_type_changed:
            reason_parts.append("return type changed")
        if return_arity_changed:
            reason_parts.append("return arity changed")
        return {
            "action": "update_callers",
            "reason": ", ".join(reason_parts) or "breaking I/O change",
            "hints": hints,
        }

    if set(before_by_name) == set(after_by_name):
        if added_optional_params:
            return {
                "action": "skip",
                "reason": "new optional params only",
                "hints": hints,
            }
        return {
            "action": "skip",
            "reason": "no breaking I/O change",
            "hints": hints,
        }

    return {
        "action": "skip",
        "reason": "non-breaking signature change",
        "hints": hints,
    }


def output_format_changed(before, after):
    """
    Return (changed, reason, needs_llm).

    needs_llm is True when AST output types are not plausible on either side,
    when multiple public exports make the contract ambiguous, or when a
    multi-export module's primary output types appear to have changed.
    """
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    if not before.get("plausible") or not after.get("plausible"):
        return False, "ast not plausible", True
    if _contract_is_ambiguous(before) or _contract_is_ambiguous(after):
        return False, "ambiguous public exports", True
    before_view = before.get("outputs") or {}
    after_view = after.get("outputs") or {}
    if not _has_output_types(before_view) and not _has_output_types(after_view):
        return False, "no output types in ast", True
    if json.dumps(before_view, sort_keys=True) != json.dumps(after_view, sort_keys=True):
        if _multi_export_module(before) or _multi_export_module(after):
            return True, "public output types changed (multi-export module)", True
        return True, "public output types changed", False
    return False, "public output types unchanged", False


if __name__ == "__main__":
    before = extract_public_contract(
        "mod.py",
        "def foo(x: int) -> dict:\n    return {}\n",
        "py",
    )
    after_same = extract_public_contract(
        "mod.py",
        'def foo(x: int) -> dict:\n    return {"a": 1}\n',
        "py",
    )
    after_changed = extract_public_contract(
        "mod.py",
        "def foo(x: int) -> list:\n    return []\n",
        "py",
    )
    assert before["plausible"] and after_same["plausible"]
    changed, reason, needs_llm = output_format_changed(before, after_same)
    assert changed is False and needs_llm is False, (changed, reason, needs_llm)
    changed, reason, needs_llm = output_format_changed(before, after_changed)
    assert changed is True and needs_llm is False, (changed, reason, needs_llm)

    helper_only_before = extract_public_contract(
        "mod.py",
        '__all__ = ["foo"]\n'
        "def helper() -> dict:\n    return {}\n"
        "def foo(x: int) -> dict:\n    return {}\n",
        "py",
    )
    helper_only_after = extract_public_contract(
        "mod.py",
        '__all__ = ["foo"]\n'
        "def helper() -> list:\n    return []\n"
        "def foo(x: int) -> dict:\n    return {}\n",
        "py",
    )
    changed, reason, needs_llm = output_format_changed(helper_only_before, helper_only_after)
    assert changed is False and needs_llm is False, (changed, reason, needs_llm)
    assert helper_only_before["primary_name"] == "foo"
    assert helper_only_before["raw_export_count"] == 2

    primary_changed_before = extract_public_contract(
        "mod.py",
        '__all__ = ["foo"]\n'
        "def helper() -> dict:\n    return {}\n"
        "def foo(x: int) -> dict:\n    return {}\n",
        "py",
    )
    primary_changed_after = extract_public_contract(
        "mod.py",
        '__all__ = ["foo"]\n'
        "def helper() -> dict:\n    return {}\n"
        "def foo(x: int) -> list:\n    return []\n",
        "py",
    )
    changed, reason, needs_llm = output_format_changed(primary_changed_before, primary_changed_after)
    assert changed is True and needs_llm is True, (changed, reason, needs_llm)

    optional_added = extract_public_contract(
        "mod.py",
        "def foo(x: int, y: int = 1) -> dict:\n    return {}\n",
        "py",
    )
    delta_optional = classify_io_delta(before, optional_added)
    assert delta_optional["action"] == "skip", delta_optional

    required_added = extract_public_contract(
        "mod.py",
        "def foo(x: int, y: int) -> dict:\n    return {}\n",
        "py",
    )
    delta_required = classify_io_delta(before, required_added)
    assert delta_required["action"] == "update_callers", delta_required
    assert "y" in delta_required["hints"]["added_required_params"]

    tuple_before = extract_public_contract(
        "mod.py",
        "def foo() -> tuple[int, str]:\n    return 1, 'a'\n",
        "py",
    )
    tuple_after = extract_public_contract(
        "mod.py",
        "def foo() -> tuple[int, str, bool]:\n    return 1, 'a', True\n",
        "py",
    )
    delta_tuple = classify_io_delta(tuple_before, tuple_after)
    assert delta_tuple["action"] == "update_callers", delta_tuple

    print("EXTRACT_PUBLIC_CONTRACT SELF TEST PASSED")
