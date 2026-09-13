MODULE_METADATA = {
    "name": "render_file_context",
    "type": "function",
    "description": "Render read_cache and optional code tables as a unified <file_context> block for planner prompts.",
    "functions": [
        {
            "name": "refresh_read_cache",
            "inputs": {
                "read_cache": "dict mapping project-relative paths to cached file content"
            },
            "outputs": "same read_cache dict with every path re-read from disk; unreadable paths removed"
        },
        {
            "name": "drop_read_cache",
            "inputs": {
                "read_cache": "dict mapping project-relative paths to cached file content",
                "paths": "list[str] project-relative paths to remove from the cache"
            },
            "outputs": "dict with dropped paths, missing paths, and remaining cache size"
        },
        {
            "name": "get_drop_cache_endpoint_doc",
            "inputs": {"loop": "str main or debug"},
            "outputs": "str endpoint documentation block for planner prompts"
        },
        {
            "name": "render_file_context",
            "inputs": {
                "read_cache": "dict mapping project-relative file paths to file content",
                "code_tables": "dict or None mapping paths to optional code table text",
                "path_order": "list or None preferred path ordering before remaining cache keys"
            },
            "outputs": "str <file_context> block or empty string when no files"
        }
    ]
}

import json

from build_block_table import build_block_table
from infer_code_type import infer_code_type
from read_file import read_file


def refresh_read_cache(read_cache):
    """Re-read every cached path from disk before the next planner turn."""
    if not isinstance(read_cache, dict) or not read_cache:
        return read_cache

    for path in list(read_cache):
        success, content_or_error = read_file(path)
        if success:
            read_cache[path] = content_or_error
        else:
            read_cache.pop(path, None)

    return read_cache


def drop_read_cache(read_cache, paths):
    """Remove specific paths from read_cache so they stop appearing in file_context."""
    if read_cache is None:
        read_cache = {}

    dropped = []
    missing = []
    seen = set()

    for raw_path in paths or []:
        path = str(raw_path or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        if path in read_cache:
            read_cache.pop(path, None)
            dropped.append(path)
        else:
            missing.append(path)

    return {
        "dropped": dropped,
        "missing": missing,
        "remaining": len(read_cache),
    }


def get_drop_cache_endpoint_doc(loop: str = "main") -> str:
    loop = str(loop or "main").strip().lower()
    index = 9 if loop == "debug" else 10
    return f"""
{index}. /drop_cache

Remove specific files from <file_context> when they are no longer needed for later turns.

Payload:
{{
  "paths": ["relative/path.py", "other/path.ts"]
}}

Rules:
- Drop files that are large, already inspected, and not needed for remaining work.
- If you still need findings later, copy them to /scratchpad first.
- Do not drop a file you still need to /edit or inspect this turn unless a later /write or /read will restore it.
- Dropped files disappear from <file_context> on the next planner turn.
- Dropped files can be re-read later with /read.
- Does not delete files on disk; it only removes cached prompt context.
- Does not trigger /request_feedback by itself.
""".strip()


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

    code_tables = code_tables or {}
    parts = ["<file_context>"]

    for index, path in enumerate(_ordered_paths(read_cache, path_order), start=1):
        text = str(read_cache.get(path, ""))

        if max_chars_per_file is not None and max_chars_per_file > 0 and len(text) > max_chars_per_file:
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

    stale_path = "test_temp/temp.py"
    success, disk_content = read_file(stale_path)
    assert success, disk_content
    stale_cache = {stale_path: "stale content"}
    refresh_read_cache(stale_cache)
    assert stale_cache[stale_path] == disk_content

    drop_cache = {
        "code/a.py": "a",
        "code/b.py": "b",
        "code/c.py": "c",
    }
    drop_result = drop_read_cache(drop_cache, ["code/b.py", "code/missing.py", "code/b.py"])
    assert drop_result["dropped"] == ["code/b.py"]
    assert drop_result["missing"] == ["code/missing.py"]
    assert drop_result["remaining"] == 2
    assert "code/b.py" not in drop_cache
    assert "/drop_cache" in get_drop_cache_endpoint_doc()
    assert "10. /drop_cache" in get_drop_cache_endpoint_doc("main")
    assert "9. /drop_cache" in get_drop_cache_endpoint_doc("debug")

    print("RENDER_FILE_CONTEXT SELF TEST PASSED")
