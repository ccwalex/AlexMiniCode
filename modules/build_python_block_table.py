import ast

MODULE_METADATA = {
    "name": "build_python_block_table",
    "type": "function",
    "description": "Deterministic Python parser-backed block table builder for structured editing.",
    "functions": [
        {
            "name": "build_python_block_table",
            "inputs": {
                "source": "str",
                "path": "str",
                "max_depth": "int",
                "min_chunk_lines": "int"
            },
            "outputs": "list[dict]"
        }
    ]
}
def limit_block_table_complexity(
    source,
    rows,
    min_block_lines=10,
    max_blocks_per_level=10,
    max_depth=3,
):
    """
    Reduce block-table complexity for structured editing.

    Rules:
    - Keep root block.
    - Non-root blocks must have at least min_block_lines.
    - Expose at most max_blocks_per_level per depth level.
    - If a depth level would exceed max_blocks_per_level, stop exposing
      that level and all deeper levels.
    - Hidden lower-level blocks remain inside their nearest kept parent,
      effectively combining them upward.
    """

    if not isinstance(rows, list) or not rows:
        return rows

    line_count = len(source.splitlines()) or 1

    def span_lines(row):
        try:
            return int(row.get("end_line")) - int(row.get("start_line")) + 1
        except Exception:
            return 0

    def is_root(row):
        return row.get("parent") is None or row.get("id") in ["mod_1", "doc_1"]

    valid = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        block_id = row.get("id")
        if not block_id:
            continue

        try:
            start_line = int(row.get("start_line"))
            end_line = int(row.get("end_line"))
        except Exception:
            continue

        if start_line < 1:
            continue

        if end_line < start_line:
            continue

        if end_line > line_count:
            continue

        new_row = dict(row)
        new_row["start_line"] = start_line
        new_row["end_line"] = end_line
        new_row["depth"] = int(new_row.get("depth", 0) or 0)

        valid.append(new_row)

    if not valid:
        return rows

    roots = [
        r for r in valid
        if r.get("id") in ["mod_1", "doc_1"]
    ]

    if not roots:
        roots = [r for r in valid if is_root(r)]

    if not roots:
        return valid[:1]

    root = dict(roots[0])
    root_id = root["id"]

    root["parent"] = None
    root["depth"] = 0

    candidates = []

    for row in valid:
        if row["id"] == root_id:
            continue

        if span_lines(row) < min_block_lines:
            continue

        if int(row.get("depth", 0) or 0) > max_depth:
            continue

        candidates.append(dict(row))

    candidates.sort(
        key=lambda r: (
            int(r.get("depth", 0)),
            int(r.get("start_line", 0)),
            int(r.get("end_line", 0)),
            str(r.get("id", "")),
        )
    )

    original_by_id = {r["id"]: r for r in [root] + candidates}

    def nearest_kept_parent_id(row, kept_ids):
        parent_id = row.get("parent")

        while parent_id:
            if parent_id in kept_ids:
                return parent_id

            parent = original_by_id.get(parent_id)
            if not parent:
                break

            parent_id = parent.get("parent")

        return root_id

    kept = [root]
    kept_ids = {root_id}

    current_depth = 1

    while current_depth <= max_depth:
        level = []

        for row in candidates:
            if row["id"] in kept_ids:
                continue

            parent_id = nearest_kept_parent_id(row, kept_ids)
            parent = next((k for k in kept if k["id"] == parent_id), None)

            if parent is None:
                parent = root

            exposed_depth = int(parent.get("depth", 0)) + 1

            if exposed_depth == current_depth:
                level.append(row)

        if len(level) > max_blocks_per_level:
            break

        if not level:
            break

        level.sort(
            key=lambda r: (
                int(r.get("start_line", 0)),
                int(r.get("end_line", 0)),
                str(r.get("id", "")),
            )
        )

        for row in level:
            new_row = dict(row)

            parent_id = nearest_kept_parent_id(new_row, kept_ids)
            parent = next((k for k in kept if k["id"] == parent_id), root)

            new_row["parent"] = parent["id"]
            new_row["depth"] = int(parent.get("depth", 0)) + 1

            kept.append(new_row)
            kept_ids.add(new_row["id"])

        current_depth += 1

    kept.sort(
        key=lambda r: (
            int(r.get("start_line", 0)),
            int(r.get("depth", 0)),
            int(r.get("end_line", 0)),
            str(r.get("id", "")),
        )
    )

    return kept
def build_python_block_table(source: str, path: str = "", max_depth: int = 4, min_chunk_lines: int = 30) -> list:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    rows = []
    
    counters = {
        "class": 1,
        "function": 1,
        "method": 1,
        "async_function": 1,
        "async_method": 1,
        "for": 1,
        "while": 1,
        "if": 1,
        "try": 1
    }

    def get_id(typ):
        if typ == "module":
            return "mod_1"
        prefix_map = {
            "class": "cls",
            "function": "fn",
            "method": "meth",
            "async_function": "afn",
            "async_method": "ameth",
            "for": "for",
            "while": "while",
            "if": "if",
            "try": "try"
        }
        prefix = prefix_map.get(typ, "unknown")
        idx = counters[typ]
        counters[typ] += 1
        return f"{prefix}_{idx}"

    def get_type_and_name(node, parent_type):
        if isinstance(node, ast.Module):
            return "module", path or "module"
        elif isinstance(node, ast.ClassDef):
            return "class", node.name
        elif isinstance(node, ast.FunctionDef):
            typ = "method" if parent_type == "class" else "function"
            return typ, node.name
        elif isinstance(node, ast.AsyncFunctionDef):
            typ = "async_method" if parent_type == "class" else "async_function"
            return typ, node.name
        elif isinstance(node, ast.For):
            return "for", "for"
        elif isinstance(node, ast.AsyncFor):
            return "for", "async for"
        elif isinstance(node, ast.While):
            return "while", "while"
        elif isinstance(node, ast.If):
            return "if", "if"
        elif isinstance(node, ast.Try):
            return "try", "try"
        elif getattr(ast, 'TryStar', None) and isinstance(node, getattr(ast, 'TryStar')):
            return "try", "try*"
        return None, None

    def extract_vars(node):
        vars_def = set()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in getattr(args, 'posonlyargs', []) + getattr(args, 'args', []) + getattr(args, 'kwonlyargs', []):
                vars_def.add(arg.arg)
            if getattr(args, 'vararg', None):
                vars_def.add(args.vararg.arg)
            if getattr(args, 'kwarg', None):
                vars_def.add(args.kwarg.arg)

        body = getattr(node, 'body', [])
        if not isinstance(body, list):
            body = [body]
        orelse = getattr(node, 'orelse', [])
        if not isinstance(orelse, list):
            orelse = [orelse]
        
        for child in body + orelse:
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        vars_def.add(target.id)
            elif isinstance(child, ast.AnnAssign):
                if isinstance(child.target, ast.Name):
                    vars_def.add(child.target.id)
            elif isinstance(child, ast.AugAssign):
                if isinstance(child.target, ast.Name):
                    vars_def.add(child.target.id)
            elif isinstance(child, (ast.For, ast.AsyncFor)):
                if isinstance(child.target, ast.Name):
                    vars_def.add(child.target.id)
                elif isinstance(child.target, ast.Tuple):
                    for elt in child.target.elts:
                        if isinstance(elt, ast.Name):
                            vars_def.add(elt.id)
            elif isinstance(child, ast.With):
                for item in child.items:
                    if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                        vars_def.add(item.optional_vars.id)
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    vars_def.add(alias.asname or alias.name.split('.')[0])
            elif isinstance(child, ast.ImportFrom):
                for alias in child.names:
                    vars_def.add(alias.asname or alias.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                vars_def.add(child.name)
        return sorted(list(vars_def))

    def visit(node, parent_id, depth, parent_type):
        typ, name = get_type_and_name(node, parent_type)
        is_emitted = False
        current_id = parent_id
        
        if typ:
            start = getattr(node, 'lineno', 1)
            end = getattr(node, 'end_lineno', len(source.splitlines()))
            span = end - start + 1
            
            should_emit = False
            if typ == "module":
                should_emit = True
            elif depth <= max_depth:
                if typ in ["for", "while", "if", "try"]:
                    if span >= min_chunk_lines:
                        should_emit = True
                else:
                    should_emit = True
                    
            if should_emit:
                node_id = get_id(typ)
                row = {
                    "id": node_id,
                    "type": typ,
                    "name": name,
                    "start_line": start,
                    "end_line": end,
                    "parent": parent_id,
                    "depth": depth,
                    "vars_defined": extract_vars(node)
                }
                rows.append(row)
                current_id = node_id
                is_emitted = True
                
        next_depth = depth + 1 if is_emitted else depth
        next_parent_type = typ if is_emitted else parent_type
        
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.stmt, ast.expr, ast.ExceptHandler)):
                if hasattr(child, 'lineno'):
                    visit(child, current_id, next_depth, next_parent_type)
                    
    visit(tree, None, 0, None)
    rows.sort(key=lambda r: (r['start_line'], r['depth']))

    rows = limit_block_table_complexity(
         source=source,
         rows=rows,
         min_block_lines=max(10, int(min_chunk_lines or 10)),
         max_blocks_per_level=10,
         max_depth=min(3, int(max_depth or 3)),
     )

    
    return rows

if __name__ == '__main__':
    src = '''
import math
import os as myos
from sys import argv

class MyClass:
    class_var = 1
    def __init__(self, x):
        self.x = x
        y = 2
        for i in range(10):
            z = i
            
    async def amethod(self, a, b):
        pass

def my_func(arg1):
    a = 1
    b = 2
    if a == 1:
        c = 3
    return a + b
'''
    rows = build_python_block_table(src, max_depth=4, min_chunk_lines=0)
    for r in rows:
        print(r)
