MODULE_METADATA = {
    "name": "extract_task_context",
    "type": "function",
    "description": "Extract structured GUI task context such as attached file paths, file contents, and optional code tables from XML-like task blocks.",
    "functions": [
        {
            "name": "extract_task_context",
            "inputs": {
                "task": "str task prompt containing <file_context> and <file_N path=\"...\"> blocks"
            },
            "outputs": "dict with keys read_cache, attached_paths, code_tables"
        },
        {
            "name": "extract_attached_files_from_task",
            "inputs": {
                "task": "str task prompt containing <file_context> and <file_N path=\"...\"> blocks"
            },
            "outputs": "dict mapping project-relative file paths to attached file content"
        },
        {
            "name": "extract_attached_paths_from_task",
            "inputs": {
                "task": "str task prompt containing <file_context> and <file_N path=\"...\"> blocks"
            },
            "outputs": "list of project-relative attached file paths"
        },
        {
            "name": "extract_file_context_block",
            "inputs": {
                "task": "str task prompt containing <file_context> blocks"
            },
            "outputs": "str raw <file_context>...</file_context> block or empty string"
        },
        {
            "name": "task_without_file_context",
            "inputs": {
                "task": "str task prompt that may contain <file_context> blocks"
            },
            "outputs": "str task text with <file_context> removed"
        },
        {
            "name": "extract_module_registry_block",
            "inputs": {
                "task": "str task prompt containing <module_registry> blocks"
            },
            "outputs": "str raw <module_registry>...</module_registry> block or empty string"
        },
        {
            "name": "extract_registry_context_block",
            "inputs": {
                "task": "str task prompt containing legacy <registry_context> blocks"
            },
            "outputs": "str raw <module_registry>...</module_registry> block or empty string"
        },
        {
            "name": "task_without_attached_context",
            "inputs": {
                "task": "str task prompt that may contain attached context blocks"
            },
            "outputs": "str task text with <file_context> and <module_registry> removed"
        }
    ]
}

import html
import re


FILE_BLOCK_RE = re.compile(
    r'<file_(?P<index>\d+)\s+path="(?P<path>[^"]+)">\s*'
    r'(?P<body>.*?)'
    r'</file_\1>',
    re.DOTALL,
)

CONTENT_RE = re.compile(
    r'<content>\s*(?P<content>.*?)\s*</content>',
    re.DOTALL,
)

CODE_TABLE_RE = re.compile(
    r'<code_table>\s*(?P<code_table>.*?)\s*</code_table>',
    re.DOTALL,
)

FILE_CONTEXT_BLOCK_RE = re.compile(
    r'<file_context>\s*(?P<body>.*?)\s*</file_context>',
    re.DOTALL | re.IGNORECASE,
)

MODULE_REGISTRY_BLOCK_RE = re.compile(
    r'<module_registry>\s*(?P<body>.*?)\s*</module_registry>',
    re.DOTALL | re.IGNORECASE,
)

# Legacy tag kept for backward compatibility with older submitted jobs.
LEGACY_REGISTRY_CONTEXT_BLOCK_RE = re.compile(
    r'<registry_context>\s*(?P<body>.*?)\s*</registry_context>',
    re.DOTALL | re.IGNORECASE,
)


def _normalize_task_text(task):
    if not isinstance(task, str) or not task:
        return ""
    text = task.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _extract_context_block(task, pattern, tag_name):
    if not isinstance(task, str) or not task.strip():
        return ""

    match = pattern.search(task)
    if not match:
        return ""

    body = (match.group("body") or "").strip()
    if not body:
        return ""

    return f"<{tag_name}>\n{body}\n</{tag_name}>"


def _remove_context_blocks(task, *patterns):
    if not isinstance(task, str) or not task:
        return ""

    stripped = task
    for pattern in patterns:
        stripped = pattern.sub("", stripped)

    return _normalize_task_text(stripped)


def _clean_path(path):
    return html.unescape(str(path)).strip()


def _clean_text(text):
    return html.unescape(str(text))


def extract_task_context(task):
    """
    Extract structured GUI context from task text.

    Expected file format:

    <file_context>
    <file_1 path="product/frontend/src/App.tsx">
    <content>
    ...
    </content>
    <code_table>
    ...
    </code_table>
    </file_1>
    </file_context>

    Returns:
        {
            "read_cache": {
                "product/frontend/src/App.tsx": "...file content..."
            },
            "attached_paths": [
                "product/frontend/src/App.tsx"
            ],
            "code_tables": {
                "product/frontend/src/App.tsx": "...code table..."
            }
        }
    """

    result = {
        "read_cache": {},
        "attached_paths": [],
        "code_tables": {},
    }

    if not isinstance(task, str) or not task:
        return result

    for match in FILE_BLOCK_RE.finditer(task):
        path = _clean_path(match.group("path"))
        body = match.group("body") or ""

        if not path:
            continue

        if path not in result["attached_paths"]:
            result["attached_paths"].append(path)

        content_match = CONTENT_RE.search(body)

        if content_match:
            content = _clean_text(content_match.group("content"))
            result["read_cache"][path] = content

        code_table_match = CODE_TABLE_RE.search(body)

        if code_table_match:
            code_table = _clean_text(code_table_match.group("code_table"))
            result["code_tables"][path] = code_table

    return result


def extract_attached_files_from_task(task):
    """
    Compatibility helper.

    Returns only the read_cache mapping:
        path -> content
    """
    return extract_task_context(task).get("read_cache", {})


def extract_attached_paths_from_task(task):
    """
    Returns only attached file paths in GUI order.
    """
    return extract_task_context(task).get("attached_paths", [])


def extract_file_context_block(task):
    """
    Return the raw <file_context>...</file_context> block from a task string.

    Preserves attached file blocks exactly as submitted by the GUI so they can
    be injected separately from a rewritten task.
    """
    return _extract_context_block(task, FILE_CONTEXT_BLOCK_RE, "file_context")


def task_without_file_context(task):
    """
    Return task text with any <file_context> block removed.

    Used as rewrite input so the context rewriter does not drop attachments.
    """
    return _remove_context_blocks(task, FILE_CONTEXT_BLOCK_RE)


def extract_module_registry_block(task):
    """
    Return the raw <module_registry>...</module_registry> block from a task string.

    Preserves selected module registry metadata so it can be injected separately from
    a rewritten task. Accepts legacy <registry_context> blocks and normalizes the
    returned tag name to <module_registry>.
    """
    block = _extract_context_block(task, MODULE_REGISTRY_BLOCK_RE, "module_registry")
    if block:
        return block

    legacy_match = LEGACY_REGISTRY_CONTEXT_BLOCK_RE.search(task)
    if not legacy_match:
        return ""

    body = (legacy_match.group("body") or "").strip()
    if not body:
        return ""

    return f"<module_registry>\n{body}\n</module_registry>"


def extract_registry_context_block(task):
    """Backward-compatible alias for extract_module_registry_block."""
    return extract_module_registry_block(task)


def task_without_attached_context(task):
    """
    Return task text with <file_context> and <module_registry> removed.

    Used as rewrite input so attached context survives task rewrite.
    """
    return _remove_context_blocks(
        task,
        FILE_CONTEXT_BLOCK_RE,
        MODULE_REGISTRY_BLOCK_RE,
        LEGACY_REGISTRY_CONTEXT_BLOCK_RE,
    )


if __name__ == "__main__":
    demo_task = """
<user_request>
Fix the parser.
</user_request>

<file_context>
<file_1 path="code/a.py">
<content>
print("hi")
</content>
</file_1>
</file_context>
""".strip()

    extracted = extract_task_context(demo_task)
    assert extracted["read_cache"]["code/a.py"] == 'print("hi")'

    block = extract_file_context_block(demo_task)
    assert block.startswith("<file_context>")
    assert '<file_1 path="code/a.py">' in block

    stripped_task = task_without_file_context(demo_task)
    assert "<file_context>" not in stripped_task
    assert "Fix the parser." in stripped_task

    registry_task = """
<user_request>
Update imports.
</user_request>

<module_registry>
{"registries":{"code/modules":{"folder_path":"code/modules","code_type":"py","files":{}}}}
</module_registry>
""".strip()

    registry_block = extract_module_registry_block(registry_task)
    assert registry_block.startswith("<module_registry>")
    assert '"folder_path":"code/modules"' in registry_block

    stripped_registry_task = task_without_attached_context(registry_task)
    assert "<module_registry>" not in stripped_registry_task
    assert "Update imports." in stripped_registry_task

    legacy_registry_task = registry_task.replace("<module_registry>", "<registry_context>").replace("</module_registry>", "</registry_context>")
    legacy_block = extract_module_registry_block(legacy_registry_task)
    assert legacy_block.startswith("<module_registry>")
    assert "<registry_context>" not in legacy_block

    combined_task = demo_task + "\n\n" + registry_block
    stripped_combined = task_without_attached_context(combined_task)
    assert "<file_context>" not in stripped_combined
    assert "<module_registry>" not in stripped_combined
    assert "Fix the parser." in stripped_combined

    print("EXTRACT_TASK_CONTEXT SELF TEST PASSED")