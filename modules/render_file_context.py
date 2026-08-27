MODULE_METADATA = {
    "name": "render_file_context",
    "type": "function",
    "description": "Render read_cache and optional code tables as a unified <file_context> block for planner prompts.",
    "functions": [
        {
            "name": "render_file_context",
            "inputs": {
                "read_cache": "dict mapping project-relative file paths to file content",
                "code_tables": "dict or None mapping paths to optional code table text",
                "path_order": "list or None preferred path ordering before remaining cache keys",
                "max_chars_per_file": "int maximum characters per file before truncation"
            },
            "outputs": "str <file_context> block or empty string when no files"
        }
    ]
}

import json

from build_block_table import build_block_table
from cfg import CFG
from infer_code_type import infer_code_type


def _ordered_paths(read_cache, path_order=None):
    paths = []
    seen = set()

    for path in path_order or []:
        key = str(path).strip()
        if key and key in read_cache and key not in seen:
            paths.append(key)
            seen.add(key)

    for path in read_cache:
        if path not in seen:
            paths.append(path)
            seen.add(path)

    return paths


def render_file_context(
    read_cache,
    code_tables=None,
    path_order=None,
    max_chars_per_file=None,
):
    """
    Render all cached file contents as one <file_context> block.

    Includes user-attached files and every file read/written/edited during the job.
    """
    if not read_cache:
        return ""

    if max_chars_per_file is None:
        max_chars_per_file = int(getattr(CFG, "LLM_CONTEXT_MAX_CHARS_PER_FILE", 60000))

    code_tables = code_tables or {}
    parts = ["<file_context>"]

    for index, path in enumerate(_ordered_paths(read_cache, path_order), start=1):
        text = str(read_cache.get(path, ""))

        if len(text) > max_chars_per_file:
            text = text[:max_chars_per_file] + "\n[TRUNCATED]"

        tag = f"file_{index}"
        parts.append(f'<{tag} path="{path}">')
        parts.append("<content>")
        parts.append(text)
        parts.append("</content>")

        code_table = None
        try:
            code_type = infer_code_type(path, text)
            blocks = build_block_table(text, path, code_type, 10, 1)
            if blocks:
                code_table = json.dumps(blocks, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            code_table = None

        if not code_table:
            code_table = code_tables.get(path)
        if code_table and str(code_table).strip():
            parts.append("<code_table>")
            parts.append(str(code_table).strip())
            parts.append("</code_table>")

        parts.append(f"</{tag}>")

    parts.append("</file_context>")
    return "\n".join(parts)


if __name__ == "__main__":
    cache = {
        "code/a.py": 'print("attached")\n',
        "code/b.py": 'print("read later")\n',
    }
    rendered = render_file_context(
        cache,
        code_tables={"code/a.py": "id=1 type=function"},
        path_order=["code/a.py"],
    )

    assert rendered.startswith("<file_context>")
    assert '<file_1 path="code/a.py">' in rendered
    assert '<file_2 path="code/b.py">' in rendered
    assert "print(\"attached\")" in rendered
    assert "print(\"read later\")" in rendered
    assert "<code_table>" in rendered
    assert rendered.endswith("</file_context>")

    assert render_file_context({}) == ""

    print("RENDER_FILE_CONTEXT SELF TEST PASSED")
