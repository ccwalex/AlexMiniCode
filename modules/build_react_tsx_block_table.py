import re

MODULE_METADATA = {
    "name": "build_react_tsx_block_table",
    "type": "function",
    "description": "Deterministic best-effort block table builder for React TSX source code.",
    "functions": [
        {
            "name": "build_react_tsx_block_table",
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

def _find_matching_brace(lines, start_line_idx, start_char_idx):
    open_count = 0
    for i in range(start_line_idx, len(lines)):
        line = lines[i]
        start_j = start_char_idx if i == start_line_idx else 0
        for j in range(start_j, len(line)):
            if line[j] == '{':
                open_count += 1
            elif line[j] == '}':
                open_count -= 1
                if open_count == 0:
                    return i
    return -1

def _extract_direct_vars(block_lines, nested_spans):
    vars_found = set()
    var_regex = re.compile(r"\b(?:const|let|var|function|class)\s+([a-zA-Z0-9_]+)\b")
    import_regex = re.compile(r"\bimport\s+(.*?)\s+from")
    
    for i, line in enumerate(block_lines):
        in_nested = any((span[0] <= i <= span[1]) for span in nested_spans)
        if in_nested:
            continue
            
        for m in var_regex.finditer(line):
            vars_found.add(m.group(1))
            
        for m in import_regex.finditer(line):
            import_str = m.group(1).replace('{', '').replace('}', '')
            for v in import_str.split(','):
                v_clean = v.strip()
                if v_clean:
                    vars_found.add(v_clean)
                
    return sorted(list(vars_found))
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

def build_react_tsx_block_table(source: str, path: str = "", max_depth: int = 4, min_chunk_lines: int = 30) -> list:
    lines = source.splitlines()
    num_lines = len(lines)
    
    blocks = []
    blocks.append({
        "id": "mod_1",
        "type": "module",
        "name": path or "module",
        "start_line": 1,
        "end_line": num_lines,
        "parent": None,
        "depth": 0,
        "vars_defined": [],
        "_raw_start": 0,
        "_raw_end": num_lines - 1
    })
    
    comp_re = re.compile(r"(?:export\s+(?:default\s+)?)?(?:const|let|var|function)\s+([A-Z][a-zA-Z0-9_]*)\b.*(?:=|\()")
    handler_re = re.compile(r"(?:const|let|var)\s+(handle[A-Za-z0-9_]*)\b\s*=")
    hook_re = re.compile(r"\b(use[A-Z][a-zA-Z0-9_]*)\b\s*\(")
    fn_re = re.compile(r"(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+([a-z][a-zA-Z0-9_]*)\b")
    jsx_map_re = re.compile(r"\.(?:map|filter)\s*\(.*=>\s*(?:\{|\()")
    
    potential_blocks = []
    
    for i, line in enumerate(lines):
        if '{' not in line:
            continue
        
        brace_idx = line.rfind('{')
        end_i = _find_matching_brace(lines, i, brace_idx)
        if end_i == -1 or end_i < i:
            continue
            
        b_type, b_name, b_id_prefix = None, None, None
        
        m_comp = comp_re.search(line)
        m_hand = handler_re.search(line)
        m_hook = hook_re.search(line)
        m_fn = fn_re.search(line)
        
        if m_comp:
            b_type = "component"
            b_name = m_comp.group(1)
            b_id_prefix = "comp"
        elif m_hand:
            b_type = "handler"
            b_name = m_hand.group(1)
            b_id_prefix = "handler"
        elif m_hook:
            b_type = "hook"
            b_name = m_hook.group(1)
            b_id_prefix = "hook"
        elif m_fn:
            b_type = "function"
            b_name = m_fn.group(1)
            b_id_prefix = "fn"
        elif "return" in line and ("<" in line or "(" in line):
            b_type = "jsx_branch"
            b_name = "jsx_return"
            b_id_prefix = "jsxbr"
        elif jsx_map_re.search(line):
            b_type = "jsx_branch"
            b_name = "jsx_map"
            b_id_prefix = "jsxbr"
            
        if b_type:
            potential_blocks.append({
                "type": b_type,
                "name": b_name,
                "start_line": i + 1,
                "end_line": end_i + 1,
                "_raw_start": i,
                "_raw_end": end_i,
                "_prefix": b_id_prefix
            })
            
    potential_blocks.sort(key=lambda b: b["end_line"] - b["start_line"])
    
    emitted = []
    counters = { "comp": 1, "handler": 1, "hook": 1, "fn": 1, "jsxbr": 1, "jsx": 1, "block": 1 }
    
    for pb in potential_blocks:
        if (pb["end_line"] - pb["start_line"] + 1) < min_chunk_lines:
            continue
            
        pb["id"] = f"{pb['_prefix']}_{counters[pb['_prefix']]}"
        counters[pb['_prefix']] += 1
        emitted.append(pb)
        
    emitted.sort(key=lambda b: (b["_raw_start"], -(b["_raw_end"] - b["_raw_start"])))
    
    def contains(parent, child):
        return parent["_raw_start"] <= child["_raw_start"] and parent["_raw_end"] >= child["_raw_end"]

    final_blocks = [blocks[0]]
    for b in emitted:
        parent = blocks[0]
        for potential_parent in reversed(final_blocks):
            if contains(potential_parent, b):
                parent = potential_parent
                break
        
        depth = parent["depth"] + 1
        if depth <= max_depth:
            b["parent"] = parent["id"]
            b["depth"] = depth
            final_blocks.append(b)
            
    for b in final_blocks:
        nested_spans = []
        for cb in final_blocks:
            if cb.get("parent") == b["id"]:
                nested_spans.append((cb["_raw_start"] - b["_raw_start"] + 1, cb["_raw_end"] - b["_raw_start"] - 1))
        
        b_lines = lines[b["_raw_start"]:b["_raw_end"]+1]
        b["vars_defined"] = _extract_direct_vars(b_lines, nested_spans)
        
        if "_raw_start" in b: del b["_raw_start"]
        if "_raw_end" in b: del b["_raw_end"]
        if "_prefix" in b: del b["_prefix"]
        
    final_blocks.sort(key=lambda x: x["start_line"])
    final_blocks = limit_block_table_complexity(
         source=source,
         rows=final_blocks,
         min_block_lines=max(10, int(min_chunk_lines or 10)),
         max_blocks_per_level=10,
         max_depth=min(3, int(max_depth or 3)),
     )
    return final_blocks
