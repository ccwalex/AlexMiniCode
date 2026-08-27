"""
MODULE_METADATA = {
    "name": "CodeEdit",
    "type": "class",
    "description": "Dependency-free structured edit core operating on source string and block table.",
    "functions": [
        {"name": "__init__", "inputs": {"source": "str", "block_table": "list[dict]"}, "outputs": "None"},
        {"name": "blocks", "inputs": {}, "outputs": "list[dict]"},
        {"name": "get", "inputs": {"block_id": "str"}, "outputs": "str"},
        {"name": "replace", "inputs": {"block_id": "str", "content": "str"}, "outputs": "None"},
        {"name": "replace_text", "inputs": {"block_id": "str", "old": "str", "new": "str", "all": "bool"}, "outputs": "None"},
        {"name": "insert_before", "inputs": {"block_id": "str", "text": "str"}, "outputs": "None"},
        {"name": "insert_after", "inputs": {"block_id": "str", "text": "str"}, "outputs": "None"},
        {"name": "append_inside", "inputs": {"block_id": "str", "text": "str"}, "outputs": "None"},
        {"name": "delete", "inputs": {"block_id": "str"}, "outputs": "None"},
        {"name": "reconstruct", "inputs": {}, "outputs": "str"}
    ]
}

import copy

class CodeEdit:
    def __init__(self, source: str, block_table: list[dict]):
        self.original_source = source
        self.current_source = source
        self._block_table = copy.deepcopy(block_table)
        self.mutation_log = []
        
        self.spans = {}
        lines = source.splitlines(keepends=True)
        line_count = len(lines)
        seen_ids = set()
        
        for block in self._block_table:
            block_id = block.get("id")
            start_line_raw = block.get("start_line")
            end_line_raw = block.get("end_line")
        
            if not block_id:
                raise ValueError(f"Block table entry missing id: {block}")
        
            if block_id in seen_ids:
                raise ValueError(f"Duplicate block id in block table: {block_id}")
        
            seen_ids.add(block_id)
        
            try:
                start_line = int(start_line_raw) - 1
                end_line = int(end_line_raw) - 1
            except Exception:
                raise ValueError(
                    f"Invalid line numbers for block {block_id}: "
                    f"start_line={start_line_raw}, end_line={end_line_raw}"
                )
        
            if start_line < 0 or end_line < 0:
                raise ValueError(
                    f"Invalid negative/zero line range for block {block_id}: "
                    f"start_line={start_line_raw}, end_line={end_line_raw}"
                )
        
            if start_line > end_line:
                raise ValueError(
                    f"Invalid reversed line range for block {block_id}: "
                    f"start_line={start_line_raw}, end_line={end_line_raw}"
                )
        
            if end_line >= line_count:
                raise ValueError(
                    f"Block {block_id} line range exceeds source length: "
                    f"start_line={start_line_raw}, end_line={end_line_raw}, "
                    f"source_line_count={line_count}"
                )
        
            start_idx = sum(len(lines[i]) for i in range(start_line))
            end_idx = start_idx + sum(len(lines[i]) for i in range(start_line, end_line + 1))
        
            self.spans[block_id] = {
                "start": start_idx,
                "end": end_idx,
                "deleted": False,
            }

    def blocks(self):
        return copy.deepcopy(self._block_table)

    def _check_valid(self, block_id: str):
        if block_id not in self.spans:
            raise ValueError(f"Unknown block_id: {block_id}")
        if self.spans[block_id]["deleted"]:
            raise ValueError(f"Block {block_id} already deleted")

    def _apply_mutation(self, action: str, block_id: str, edit_start: int, edit_end: int, content: str):
        delta = len(content) - (edit_end - edit_start)
        self.current_source = self.current_source[:edit_start] + content + self.current_source[edit_end:]
        
        for bid, span in self.spans.items():
            if span["deleted"]:
                continue
            
            if span["start"] >= edit_end:
                span["start"] += delta
                span["end"] += delta
            elif span["start"] <= edit_start and span["end"] >= edit_end:
                span["end"] += delta
            elif span["end"] <= edit_start:
                pass
            else:
                if action in ("replace", "delete") and bid == block_id:
                    span["end"] += delta
                else:
                    raise ValueError(f"Overlapping mutation conflict for block {bid}")
                    
        self.mutation_log.append({
            "action": action,
            "block_id": block_id,
            "edit_start": edit_start,
            "edit_end": edit_end,
            "content": content,
            "delta": delta
        })

    def get(self, block_id: str) -> str:
        self._check_valid(block_id)
        span = self.spans[block_id]
        return self.current_source[span["start"]:span["end"]]

    def replace(self, block_id: str, content: str):
        self._check_valid(block_id)
        span = self.spans[block_id]
        self._apply_mutation("replace", block_id, span["start"], span["end"], content)
    def replace_text(self, block_id: str, old: str, new: str, all: bool = True):
        self._check_valid(block_id)
    
        if not isinstance(old, str) or old == "":
            raise ValueError("old text must be a non-empty string")
    
        if not isinstance(new, str):
            new = str(new)
    
        span = self.spans[block_id]
        block_start = span["start"]
        block_end = span["end"]
        block_text = self.current_source[block_start:block_end]
    
        if old not in block_text:
            raise ValueError(f"Old text '{old}' not found in block {block_id}")
    
        count = block_text.count(old)
    
        if not all and count > 1:
            raise ValueError(
                f"Old text '{old}' appears multiple times in block {block_id} and all=False"
            )
    
        # Collect relative positions first.
        positions = []
    
        if all:
            search_start = 0
            while True:
                idx = block_text.find(old, search_start)
                if idx == -1:
                    break
                positions.append(idx)
                search_start = idx + len(old)
        else:
            positions.append(block_text.find(old))
    
        # Apply from right to left so offsets remain valid.
        for rel_idx in reversed(positions):
            edit_start = block_start + rel_idx
            edit_end = edit_start + len(old)
    
            self._apply_mutation(
                "replace_text",
                block_id,
                edit_start,
                edit_end,
                new,
            )
    def insert_before(self, block_id: str, text: str):
        self._check_valid(block_id)
        span = self.spans[block_id]
        self._apply_mutation("insert_before", block_id, span["start"], span["start"], text)

    def insert_after(self, block_id: str, text: str):
        self._check_valid(block_id)
        span = self.spans[block_id]
        self._apply_mutation("insert_after", block_id, span["end"], span["end"], text)

    def append_inside(self, block_id: str, text: str):
        self._check_valid(block_id)
        span = self.spans[block_id]
        self._apply_mutation("append_inside", block_id, span["end"], span["end"], text)

    def delete(self, block_id: str):
        self._check_valid(block_id)
        span = self.spans[block_id]
        self._apply_mutation("delete", block_id, span["start"], span["end"], "")
        self.spans[block_id]["deleted"] = True

    def reconstruct(self) -> str:
        return self.current_source

"""
MODULE_METADATA = {
    "name": "CodeEdit",
    "type": "class",
    "description": "Template/tree-based structured edit core operating on source string and block table.",
    "functions": [
        {"name": "__init__", "inputs": {"source": "str", "block_table": "list[dict]"}, "outputs": "None"},
        {"name": "blocks", "inputs": {}, "outputs": "list[dict]"},
        {"name": "get", "inputs": {"block_id": "str"}, "outputs": "str"},
        {"name": "replace", "inputs": {"block_id": "str", "content": "str"}, "outputs": "None"},
        {"name": "replace_text", "inputs": {"block_id": "str", "old": "str", "new": "str", "all": "bool"}, "outputs": "None"},
        {"name": "insert_before", "inputs": {"block_id": "str", "text": "str"}, "outputs": "None"},
        {"name": "insert_after", "inputs": {"block_id": "str", "text": "str"}, "outputs": "None"},
        {"name": "append_inside", "inputs": {"block_id": "str", "text": "str"}, "outputs": "None"},
        {"name": "delete", "inputs": {"block_id": "str"}, "outputs": "None"},
        {"name": "reconstruct", "inputs": {}, "outputs": "str"}
    ]
}

import copy


class CodeEdit:
    """
    Template/tree-based CodeEdit.

    Design:
    - The block table is treated as a derived tree/dataframe.
    - edit_fn calls record operations instead of mutating source immediately.
    - reconstruct() renders deepest blocks first, then parents, then final source.
    - This avoids static-span overlap conflicts for nested blocks.

    Important semantics:
    - get(block_id) returns the ORIGINAL content of that block.
    - edit functions should not depend on reading their own previous mutations.
    - replace(block_id, content) replaces the rendered content of that block.
    - replace_text(block_id, old, new) patches text inside the rendered content of that block.
    - insert_before(block_id, text) prefixes the rendered content of that block.
    - insert_after(block_id, text) suffixes the rendered content of that block.
    - append_inside(block_id, text) appends text to the rendered content of that block.
    - delete(block_id) renders that block as empty string.
    """

    SOURCE_ROOT_ID = "__source__"

    def __init__(self, source: str, block_table: list[dict]):
        self.original_source = source if isinstance(source, str) else str(source)
        self.current_source = self.original_source
        self._block_table = copy.deepcopy(block_table or [])
        self.mutation_log = []
        self._ops = []
        self._op_counter = 0

        self._line_spans = self._compute_line_spans(self.original_source)
        self._blocks = self._normalize_blocks(self._block_table)
        self._block_by_id = {b["id"]: b for b in self._blocks}

        self._validate_unique_ids()
        self._validate_line_ranges()
        self._build_tree()
        self._build_templates()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def blocks(self):
        return copy.deepcopy(self._block_table)

    def get(self, block_id: str) -> str:
        self._check_valid(block_id)
        block = self._block_by_id[block_id]
        return self.original_source[block["start_idx"]:block["end_idx"]]

    def replace(self, block_id: str, content: str):
        self._record_op(
            action="replace",
            block_id=block_id,
            content=content,
        )

    def replace_text(self, block_id: str, old: str, new: str, all: bool = True):
        if not isinstance(old, str) or old == "":
            raise ValueError("old text must be a non-empty string")

        if not isinstance(new, str):
            new = str(new)

        self._record_op(
            action="replace_text",
            block_id=block_id,
            old=old,
            new=new,
            all=bool(all),
        )

    def insert_before(self, block_id: str, text: str):
        self._record_op(
            action="insert_before",
            block_id=block_id,
            text=text,
        )

    def insert_after(self, block_id: str, text: str):
        self._record_op(
            action="insert_after",
            block_id=block_id,
            text=text,
        )

    def append_inside(self, block_id: str, text: str):
        self._record_op(
            action="append_inside",
            block_id=block_id,
            text=text,
        )

    def delete(self, block_id: str):
        self._record_op(
            action="delete",
            block_id=block_id,
        )

    def reconstruct(self) -> str:
        rendered = {}
        mutation_log = []

        # Process real blocks deepest first.
        depths = sorted(
            {b["depth"] for b in self._blocks},
            reverse=True,
        )

        for depth in depths:
            blocks_at_depth = [
                b for b in self._blocks
                if b["depth"] == depth
            ]

            # Stable source order within the same depth.
            blocks_at_depth.sort(key=lambda b: (b["start_idx"], b["end_idx"]))

            for block in blocks_at_depth:
                content = self._render_block_template(block, rendered)
                content, logs = self._apply_ops_to_content(
                    block_id=block["id"],
                    content=content,
                )
                rendered[block["id"]] = content
                mutation_log.extend(logs)

        # Render synthetic source root.
        source_root = self._source_root
        final_source = self._render_block_template(source_root, rendered)

        # Ops targeting synthetic root are unusual but supported.
        final_source, logs = self._apply_ops_to_content(
            block_id=self.SOURCE_ROOT_ID,
            content=final_source,
        )
        mutation_log.extend(logs)

        self.current_source = final_source
        self.mutation_log = mutation_log

        return self.current_source

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    def _compute_line_spans(self, source: str):
        lines = source.splitlines(keepends=True)

        spans = []
        cursor = 0

        for line in lines:
            start = cursor
            end = cursor + len(line)
            spans.append((start, end))
            cursor = end

        return spans

    def _normalize_blocks(self, block_table):
        blocks = []

        for raw in block_table:
            if not isinstance(raw, dict):
                continue

            block = copy.deepcopy(raw)

            block_id = block.get("id")
            if not block_id:
                raise ValueError(f"Block table entry missing id: {raw}")

            try:
                start_line = int(block.get("start_line"))
                end_line = int(block.get("end_line"))
            except Exception:
                raise ValueError(
                    f"Invalid line numbers for block {block_id}: "
                    f"start_line={block.get('start_line')}, "
                    f"end_line={block.get('end_line')}"
                )

            block["start_line"] = start_line
            block["end_line"] = end_line
            block["depth"] = int(block.get("depth", 0) or 0)

            blocks.append(block)

        return blocks

    def _validate_unique_ids(self):
        seen = set()

        for block in self._blocks:
            block_id = block["id"]

            if block_id in seen:
                raise ValueError(f"Duplicate block id in block table: {block_id}")

            seen.add(block_id)

    def _validate_line_ranges(self):
        line_count = len(self._line_spans)

        for block in self._blocks:
            block_id = block["id"]
            start_line = block["start_line"]
            end_line = block["end_line"]

            if start_line < 1 or end_line < 1:
                raise ValueError(
                    f"Invalid negative/zero line range for block {block_id}: "
                    f"start_line={start_line}, end_line={end_line}"
                )

            if start_line > end_line:
                raise ValueError(
                    f"Invalid reversed line range for block {block_id}: "
                    f"start_line={start_line}, end_line={end_line}"
                )

            if end_line > line_count:
                raise ValueError(
                    f"Block {block_id} line range exceeds source length: "
                    f"start_line={start_line}, end_line={end_line}, "
                    f"source_line_count={line_count}"
                )

            start_idx = self._line_spans[start_line - 1][0]
            end_idx = self._line_spans[end_line - 1][1]

            block["start_idx"] = start_idx
            block["end_idx"] = end_idx

    def _build_tree(self):
        children = {b["id"]: [] for b in self._blocks}

        # Synthetic root covers entire source and contains all parentless blocks.
        self._source_root = {
            "id": self.SOURCE_ROOT_ID,
            "type": "source",
            "name": "source",
            "start_line": 1,
            "end_line": len(self._line_spans),
            "start_idx": 0,
            "end_idx": len(self.original_source),
            "parent": None,
            "depth": -1,
            "children": [],
        }

        for block in self._blocks:
            parent_id = block.get("parent")

            if parent_id and parent_id in children:
                children[parent_id].append(block["id"])
            else:
                self._source_root["children"].append(block["id"])

        for block in self._blocks:
            block["children"] = children.get(block["id"], [])

        # Sort children by source position.
        for block in self._blocks:
            block["children"].sort(
                key=lambda cid: (
                    self._block_by_id[cid]["start_idx"],
                    self._block_by_id[cid]["end_idx"],
                )
            )

        self._source_root["children"].sort(
            key=lambda cid: (
                self._block_by_id[cid]["start_idx"],
                self._block_by_id[cid]["end_idx"],
            )
        )

    def _build_templates(self):
        for block in self._blocks:
            block["template"] = self._make_template(block)

        self._source_root["template"] = self._make_template(self._source_root)

    def _make_template(self, block):
        start = block["start_idx"]
        end = block["end_idx"]
        children = block.get("children", [])

        if not children:
            return self.original_source[start:end]

        parts = []
        cursor = start

        for child_id in children:
            child = self._block_by_id[child_id]
            child_start = child["start_idx"]
            child_end = child["end_idx"]

            # Only direct children should be represented here.
            # If a child range is malformed or outside parent, fail loudly.
            if child_start < start or child_end > end:
                raise ValueError(
                    f"Child block {child_id} is outside parent block {block['id']}"
                )

            if child_start < cursor:
                raise ValueError(
                    f"Overlapping sibling child blocks under parent {block['id']}: {child_id}"
                )

            parts.append(self.original_source[cursor:child_start])
            parts.append(self._placeholder(child_id))
            cursor = child_end

        parts.append(self.original_source[cursor:end])

        return "".join(parts)

    # ------------------------------------------------------------------
    # Operation application
    # ------------------------------------------------------------------

    def _record_op(self, action, block_id, **kwargs):
        self._check_valid(block_id)

        op = {
            "order": self._op_counter,
            "action": action,
            "block_id": block_id,
        }
        op.update(kwargs)

        self._op_counter += 1
        self._ops.append(op)

    def _apply_ops_to_content(self, block_id, content):
        ops = [
            op for op in self._ops
            if op["block_id"] == block_id
        ]

        if not ops:
            return content, []

        ops.sort(key=lambda op: op["order"])

        logs = []

        for op in ops:
            before_len = len(content)
            action = op["action"]

            if action == "replace":
                new_content = op.get("content", "")
                if not isinstance(new_content, str):
                    new_content = str(new_content)
                content = new_content

            elif action == "replace_text":
                old = op.get("old", "")
                new = op.get("new", "")
                replace_all = bool(op.get("all", True))

                if not isinstance(old, str) or old == "":
                    raise ValueError("old text must be a non-empty string")

                if not isinstance(new, str):
                    new = str(new)

                if old not in content:
                    raise ValueError(
                        f"Old text '{old}' not found in block {block_id}"
                    )

                if not replace_all and content.count(old) > 1:
                    raise ValueError(
                        f"Old text '{old}' appears multiple times in block "
                        f"{block_id} and all=False"
                    )

                if replace_all:
                    content = content.replace(old, new)
                else:
                    content = content.replace(old, new, 1)

            elif action == "insert_before":
                text = op.get("text", "")
                if not isinstance(text, str):
                    text = str(text)
                content = text + content

            elif action == "insert_after":
                text = op.get("text", "")
                if not isinstance(text, str):
                    text = str(text)
                content = content + text

            elif action == "append_inside":
                text = op.get("text", "")
                if not isinstance(text, str):
                    text = str(text)
                content = content + text

            elif action == "delete":
                content = ""

            else:
                raise ValueError(f"Unknown edit action: {action}")

            logs.append({
                "action": action,
                "block_id": block_id,
                "order": op.get("order"),
                "before_len": before_len,
                "after_len": len(content),
                "delta": len(content) - before_len,
            })

        return content, logs

    def _render_block_template(self, block, rendered):
        content = block["template"]

        for child_id in block.get("children", []):
            if child_id in rendered:
                child_content = rendered[child_id]
            else:
                child = self._block_by_id[child_id]
                child_content = self.original_source[child["start_idx"]:child["end_idx"]]

            content = content.replace(
                self._placeholder(child_id),
                child_content,
            )

        return content

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _check_valid(self, block_id: str):
        if block_id == self.SOURCE_ROOT_ID:
            return

        if block_id not in self._block_by_id:
            available = sorted(self._block_by_id.keys())
            raise ValueError(
                f"Unknown block_id: {block_id}. "
                f"Available block ids include: {available[:80]}"
            )

    def _placeholder(self, block_id):
        return f"__CODE_EDIT_BLOCK_PLACEHOLDER_{block_id}__"