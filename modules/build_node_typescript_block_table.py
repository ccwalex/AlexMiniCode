import re

MODULE_METADATA = {
    "name": "build_node_typescript_block_table",
    "type": "function",
    "description": "Deterministic best-effort block table builder for Node/TypeScript source code.",
    "functions": [
        {
            "name": "build_node_typescript_block_table",
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
def _mask_code(s: str) -> str:
    out = []
    i = 0
    n = len(s)
    state = 'NORMAL'
    while i < n:
        c = s[i]
        if state == 'NORMAL':
            if c == '/' and i + 1 < n and s[i+1] == '/':
                state = 'LINE_COMMENT'
                out.append(' '); out.append(' '); i += 2; continue
            elif c == '/' and i + 1 < n and s[i+1] == '*':
                state = 'BLOCK_COMMENT'
                out.append(' '); out.append(' '); i += 2; continue
            elif c == "'": state = 'S_QUOTE'; out.append(' '); i += 1; continue
            elif c == '"': state = 'D_QUOTE'; out.append(' '); i += 1; continue
            elif c == '`': state = 'T_QUOTE'; out.append(' '); i += 1; continue
            else: out.append(c); i += 1; continue
        elif state == 'LINE_COMMENT':
            if c == '\n': state = 'NORMAL'; out.append(c)
            else: out.append(' ')
            i += 1
        elif state == 'BLOCK_COMMENT':
            if c == '*' and i + 1 < n and s[i+1] == '/':
                state = 'NORMAL'
                out.append(' '); out.append(' '); i += 2; continue
            else:
                out.append('\n' if c == '\n' else ' ')
                i += 1
        elif state in ['S_QUOTE', 'D_QUOTE', 'T_QUOTE']:
            quote_char = "'" if state == 'S_QUOTE' else '"' if state == 'D_QUOTE' else '`'
            if c == '\\':
                out.append(' ')
                out.append('\n' if (i+1 < n and s[i+1] == '\n') else ' ')
                i += 2; continue
            elif c == quote_char: state = 'NORMAL'; out.append(' '); i += 1; continue
            else: out.append('\n' if c == '\n' else ' '); i += 1
    return "".join(out)

def _get_vars(text: str, header: str = "") -> list:
    v = set()
    m_params = re.search(r'\(([^)]*)\)', header)
    if m_params:
        for p in m_params.group(1).split(','):
            p_clean = p.split('=')[0].split(':')[0].strip()
            if p_clean and re.match(r'^[a-zA-Z_$][0-9a-zA-Z_$]*$', p_clean):
                v.add(p_clean)
    for m in re.finditer(r'\b(?:const|let|var|class|function)\s+([a-zA-Z_$][0-9a-zA-Z_$]*)', text):
        v.add(m.group(1))
    for m in re.finditer(r'\bimport\s+([a-zA-Z_$][0-9a-zA-Z_$]*)\s+from', text):
        v.add(m.group(1))
    for m in re.finditer(r'\bimport\s+\{([^}]+)\}\s+from', text):
        for imp in m.group(1).split(','):
            parts = imp.split(' as ')
            name = parts[-1].strip()
            if name and re.match(r'^[a-zA-Z_$][0-9a-zA-Z_$]*$', name): v.add(name)
    return sorted(list(v))

def _determine_block_type(header: str) -> tuple:
    header = header.strip()
    if re.search(r'\bclass\s+([a-zA-Z_$][0-9a-zA-Z_$]*)', header):
        m = re.search(r'\bclass\s+([a-zA-Z_$][0-9a-zA-Z_$]*)', header)
        return 'class', m.group(1)
    if re.search(r'\basync\s+function\s*([a-zA-Z_$][0-9a-zA-Z_$]*)?\s*\(', header):
        m = re.search(r'\basync\s+function\s*([a-zA-Z_$][0-9a-zA-Z_$]*)?\s*\(', header)
        return 'async_function', m.group(1) or '<anonymous>'
    if re.search(r'\bfunction\s*([a-zA-Z_$][0-9a-zA-Z_$]*)?\s*\(', header):
        m = re.search(r'\bfunction\s*([a-zA-Z_$][0-9a-zA-Z_$]*)?\s*\(', header)
        return 'function', m.group(1) or '<anonymous>'
    m_arrow = re.search(r'(?:const|let|var)\s+([a-zA-Z_$][0-9a-zA-Z_$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[a-zA-Z_$][0-9a-zA-Z_$]*)\s*=>\s*$', header)
    if m_arrow:
        return 'arrow_function', m_arrow.group(1)
    m_method = re.search(r'(?:async\s+)?(?:\*\s*)?([a-zA-Z_$][0-9a-zA-Z_$]*)\s*\([^)]*\)\s*(?::\s*[^{]+)?$', header)
    if m_method and not re.search(r'\b(?:if|for|while|switch|catch)\b', m_method.group(1)):
        return 'method', m_method.group(1)
    return 'block', '<block>'
"""
def build_node_typescript_block_table(source: str, path: str = "", max_depth: int = 4, min_chunk_lines: int = 30) -> list:
    masked = _mask_code(source)
    blocks = []
    stack = []
    for i, c in enumerate(masked):
        if c == '{': stack.append(i)
        elif c == '}':
            if stack:
                start_i = stack.pop()
                blocks.append((start_i, i))
    
    line_starts = [0] + [i+1 for i, c in enumerate(source) if c == '\n']
    def get_line(idx): 
        import bisect
        return bisect.bisect_right(line_starts, idx)
    
    raw_blocks = []
    for start_i, end_i in blocks:
        header_start = max(0, masked.rfind('}', 0, start_i), masked.rfind(';', 0, start_i), masked.rfind('{', 0, start_i))
        if header_start > 0: header_start += 1
        header = masked[header_start:start_i]
        b_type, b_name = _determine_block_type(header)
        raw_blocks.append({
            'start_line': get_line(start_i),
            'end_line': get_line(end_i),
            'type': b_type,
            'name': b_name,
            'header': header,
            '_start_idx': start_i,
            '_end_idx': end_i
        })
    
    raw_blocks.sort(key=lambda x: (x['start_line'], -x['end_line']))
    
    tree = []
    counters = {'class': 1, 'function': 1, 'async_function': 1, 'arrow_function': 1, 'method': 1, 'block': 1, 'object': 1}
    prefix_map = {'class': 'cls', 'function': 'fn', 'async_function': 'afn', 'arrow_function': 'arrow', 'method': 'meth', 'block': 'block', 'object': 'obj'}
    
    total_lines = len(source.splitlines())
    root = {
        'id': 'mod_1',
        'type': 'module',
        'name': path or 'module',
        'start_line': 1,
        'end_line': total_lines,
        'parent': None,
        'depth': 0,
        'vars_defined': [],
        '_children': []
    }
    tree.append(root)
    
    active_stack = [root]
    for b in raw_blocks:
        while active_stack and not (active_stack[-1]['start_line'] <= b['start_line'] and active_stack[-1]['end_line'] >= b['end_line']):
            active_stack.pop()
        
        parent = active_stack[-1]
        span = b['end_line'] - b['start_line'] + 1
        depth = parent['depth'] + 1
        
        if depth <= max_depth and span >= min_chunk_lines:
            t = b['type']
            node_id = f"{prefix_map.get(t, t)}_{counters.get(t, 1)}"
            counters[t] = counters.get(t, 1) + 1
            
            node = {
                'id': node_id,
                'type': t,
                'name': b['name'],
                'start_line': b['start_line'],
                'end_line': b['end_line'],
                'parent': parent['id'],
                'depth': depth,
                'vars_defined': [],
                '_children': [],
                '_start_idx': b['_start_idx'],
                '_end_idx': b['_end_idx'],
                '_header': b['header']
            }
            parent['_children'].append(node)
            tree.append(node)
            active_stack.append(node)
    
    def extract_text_without_children(node): 
        if node['id'] == 'mod_1':
            text = source
            for c in node['_children']:
                lines = source.splitlines()
                for i in range(c['start_line']-1, c['end_line']): lines[i] = ""
                text = "\n".join(lines)
            return text
        
        text = source[node['_start_idx']:node['_end_idx']]
        for c in node['_children']:
            c_text = source[c['_start_idx']:c['_end_idx']]
            text = text.replace(c_text, " ")
        return text

    for node in tree:
        local_text = extract_text_without_children(node)
        header = node.get('_header', '')
        node['vars_defined'] = _get_vars(local_text, header)
        for k in ['_children', '_start_idx', '_end_idx', '_header']:
            if k in node: del node[k]
            
    return tree
"""
def build_node_typescript_block_table(source: str, path: str = "", max_depth: int = 4, min_chunk_lines: int = 30) -> list:
    masked = _mask_code(source)

    # ------------------------------------------------------------
    # 1. Collect brace pairs as raw structural spans.
    # ------------------------------------------------------------
    brace_pairs = []
    stack = []

    for i, c in enumerate(masked):
        if c == "{":
            stack.append(i)
        elif c == "}":
            if stack:
                start_i = stack.pop()
                brace_pairs.append((start_i, i))

    line_starts = [0] + [i + 1 for i, c in enumerate(source) if c == "\n"]

    def get_line(idx):
        import bisect
        return bisect.bisect_right(line_starts, idx)

    raw_blocks = []

    for start_i, end_i in brace_pairs:
        header_start = max(
            0,
            masked.rfind("}", 0, start_i),
            masked.rfind(";", 0, start_i),
            masked.rfind("{", 0, start_i),
        )

        if header_start > 0:
            header_start += 1

        header = masked[header_start:start_i]
        b_type, b_name = _determine_block_type(header)

        raw_blocks.append({
            "start_line": get_line(start_i),
            "end_line": get_line(end_i),
            "type": b_type,
            "name": b_name,
            "header": header,
            "_start_idx": start_i,
            "_end_idx": end_i,
            "_children": [],
            "_parent": None,
        })

    # Sort by source position, wider container first when same start.
    raw_blocks.sort(
        key=lambda x: (
            x["_start_idx"],
            -(x["_end_idx"] - x["_start_idx"]),
        )
    )

    # ------------------------------------------------------------
    # 2. Build full structural tree using character ranges.
    #    Important: even small non-emitted blocks remain structural
    #    containers, preventing incorrect sibling overlaps.
    # ------------------------------------------------------------
    total_lines = len(source.splitlines()) or 1

    root_struct = {
        "start_line": 1,
        "end_line": total_lines,
        "type": "module",
        "name": path or "module",
        "header": "",
        "_start_idx": 0,
        "_end_idx": len(source),
        "_children": [],
        "_parent": None,
        "_struct_depth": 0,
    }

    struct_stack = [root_struct]

    for b in raw_blocks:
        while struct_stack:
            p = struct_stack[-1]

            if (
                p["_start_idx"] <= b["_start_idx"]
                and p["_end_idx"] >= b["_end_idx"]
            ):
                break

            struct_stack.pop()

        if not struct_stack:
            struct_stack = [root_struct]

        parent = struct_stack[-1]
        b["_parent"] = parent
        b["_struct_depth"] = parent.get("_struct_depth", 0) + 1
        parent["_children"].append(b)
        struct_stack.append(b)

    # ------------------------------------------------------------
    # 3. Emit editable block table from structural tree.
    # ------------------------------------------------------------
    tree = []

    counters = {
        "class": 1,
        "function": 1,
        "async_function": 1,
        "arrow_function": 1,
        "method": 1,
        "block": 1,
        "object": 1,
    }

    prefix_map = {
        "class": "cls",
        "function": "fn",
        "async_function": "afn",
        "arrow_function": "arrow",
        "method": "meth",
        "block": "block",
        "object": "obj",
    }

    root = {
        "id": "mod_1",
        "type": "module",
        "name": path or "module",
        "start_line": 1,
        "end_line": total_lines,
        "parent": None,
        "depth": 0,
        "vars_defined": [],
        "_struct": root_struct,
    }

    tree.append(root)

    struct_to_emitted = {
        id(root_struct): root,
    }

    def nearest_emitted_parent(struct_node):
        cur = struct_node.get("_parent")

        while cur is not None:
            emitted = struct_to_emitted.get(id(cur))
            if emitted is not None:
                return emitted
            cur = cur.get("_parent")

        return root

    def walk_struct(node):
        for child in node.get("_children", []):
            parent_emitted = nearest_emitted_parent(child)
            depth = parent_emitted["depth"] + 1
            span = child["end_line"] - child["start_line"] + 1

            should_emit = (
                depth <= max_depth
                and span >= min_chunk_lines
            )

            emitted_node = None

            if should_emit:
                t = child["type"]
                node_id = f"{prefix_map.get(t, t)}_{counters.get(t, 1)}"
                counters[t] = counters.get(t, 1) + 1

                emitted_node = {
                    "id": node_id,
                    "type": t,
                    "name": child["name"],
                    "start_line": child["start_line"],
                    "end_line": child["end_line"],
                    "parent": parent_emitted["id"],
                    "depth": depth,
                    "vars_defined": [],
                    "_struct": child,
                    "_header": child.get("header", ""),
                }

                tree.append(emitted_node)
                struct_to_emitted[id(child)] = emitted_node

            walk_struct(child)

    walk_struct(root_struct)

    # ------------------------------------------------------------
    # 4. Fill vars_defined using emitted direct children.
    # ------------------------------------------------------------
    emitted_by_struct = {
        id(node["_struct"]): node
        for node in tree
        if "_struct" in node
    }

    children_by_emitted_id = {
        node["id"]: []
        for node in tree
    }

    for node in tree:
        parent_id = node.get("parent")
        if parent_id in children_by_emitted_id:
            children_by_emitted_id[parent_id].append(node)

    def extract_text_without_emitted_children(node):
        struct = node["_struct"]

        if node["id"] == "mod_1":
            text = source
            lines = source.splitlines()

            for child in children_by_emitted_id.get(node["id"], []):
                for i in range(child["start_line"] - 1, child["end_line"]):
                    if 0 <= i < len(lines):
                        lines[i] = ""

            return "\n".join(lines)

        start = struct["_start_idx"]
        end = struct["_end_idx"] + 1
        text = source[start:end]

        for child in children_by_emitted_id.get(node["id"], []):
            c_struct = child["_struct"]
            c_text = source[c_struct["_start_idx"]:c_struct["_end_idx"] + 1]
            text = text.replace(c_text, " ")

        return text

    for node in tree:
        local_text = extract_text_without_emitted_children(node)
        header = node.get("_header", "")
        node["vars_defined"] = _get_vars(local_text, header)

    # ------------------------------------------------------------
    # 5. Clean private fields and sort final table.
    # ------------------------------------------------------------
    for node in tree:
        for k in ["_struct", "_header"]:
            if k in node:
                del node[k]

    tree.sort(key=lambda x: (x["start_line"], x["depth"], x["end_line"]))
    tree = limit_block_table_complexity(
         source=source,
         rows=tree,
         min_block_lines=max(10, int(min_chunk_lines or 10)),
         max_blocks_per_level=10,
         max_depth=min(3, int(max_depth or 3)),
     )

    return tree