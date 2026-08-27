import html.parser
from collections import defaultdict

MODULE_METADATA = {
    "name": "build_html_block_table",
    "type": "function",
    "description": "Deterministic HTML parser-backed block table builder for structured editing.",
    "functions": [
        {
            "name": "build_html_block_table",
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

class BlockHTMLParser(html.parser.HTMLParser):
    def __init__(self, source_lines):
        super().__init__()
        self.source_lines = source_lines
        self.blocks = []
        self.stack = []
        self.counters = defaultdict(int)

    def handle_starttag(self, tag, attrs):
        self._handle_start(tag, attrs, is_self_closing=False)

    def handle_startendtag(self, tag, attrs):
        self._handle_start(tag, attrs, is_self_closing=True)

    def _handle_start(self, tag, attrs, is_self_closing):
        line, _ = self.getpos()
        attr_dict = dict(attrs)
        
        vars_defined = set()
        if 'id' in attr_dict and attr_dict['id']:
            vars_defined.add(attr_dict['id'])
        if 'class' in attr_dict and attr_dict['class']:
            vars_defined.update(attr_dict['class'].split())
        if 'name' in attr_dict and attr_dict['name']:
            vars_defined.add(attr_dict['name'])
        for k in attr_dict.keys():
            if k.startswith('data-'):
                vars_defined.add(k)
                
        name = tag
        if 'id' in attr_dict and attr_dict['id']:
            name = f"{tag}#{attr_dict['id']}"
        elif 'class' in attr_dict and attr_dict['class']:
            classes = attr_dict['class'].split()
            name = tag + "." + ".".join(classes)
            
        btype = "element"
        if tag == "script": btype = "script"
        elif tag == "style": btype = "style"
        elif tag == "template": btype = "template"
        
        self.counters[btype] += 1
        bid = f"{btype}_{self.counters[btype]}"
        
        block = {
            "id": bid,
            "type": btype,
            "name": name,
            "start_line": line,
            "end_line": line,
            "vars_defined": sorted(list(vars_defined)),
            "tag": tag
        }
        
        if is_self_closing:
            self.blocks.append(block)
        else:
            self.stack.append(block)

    def handle_endtag(self, tag):
        line, _ = self.getpos()
        for i in reversed(range(len(self.stack))):
            if self.stack[i]["tag"] == tag:
                block = self.stack.pop(i)
                block["end_line"] = line
                self.blocks.append(block)
                self.stack = self.stack[:i]
                break
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

def build_html_block_table(source, path="", max_depth=4, min_chunk_lines=30):
    lines = source.splitlines()
    doc_end = len(lines)
    
    doc_row = {
        "id": "doc_1",
        "type": "document",
        "name": path if path else "document",
        "start_line": 1,
        "end_line": doc_end,
        "parent": None,
        "depth": 0,
        "vars_defined": []
    }
    
    parser = BlockHTMLParser(lines)
    try:
        parser.feed(source)
        for b in parser.stack:
            b["end_line"] = doc_end
            parser.blocks.append(b)
    except Exception:
        return []
        
    blocks = sorted(parser.blocks, key=lambda x: (x["start_line"], -x["end_line"]))
    
    results = [doc_row]
    active_stack = [doc_row]
    
    for b in blocks:
        if b["end_line"] - b["start_line"] + 1 < min_chunk_lines:
            continue
            
        while active_stack:
            p = active_stack[-1]
            if p["start_line"] <= b["start_line"] and p["end_line"] >= b["end_line"]:
                break
            active_stack.pop()
            
        if not active_stack:
            active_stack = [doc_row]
            
        parent = active_stack[-1]
        depth = parent["depth"] + 1
        
        if depth > max_depth:
            continue
            
        b_final = {
            "id": b["id"],
            "type": b["type"],
            "name": b["name"],
            "start_line": b["start_line"],
            "end_line": b["end_line"],
            "parent": parent["id"],
            "depth": depth,
            "vars_defined": b["vars_defined"]
        }
        results.append(b_final)
        active_stack.append(b_final)
        
    return results
