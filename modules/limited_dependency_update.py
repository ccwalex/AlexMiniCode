MODULE_METADATA = {
    "name": "limited_dependency_update",
    "type": "function",
    "description": "Apply limited-scope dependency call-site updates using block tables, with escalate-to-full-file support.",
    "functions": [
        {
            "name": "find_call_site_blocks",
            "inputs": {
                "source": "str dependent file source",
                "path": "str dependent file path",
                "symbol_names": "list[str] callable names to locate",
                "code_type": "str or None",
            },
            "outputs": "list of block dicts containing call sites",
        },
        {
            "name": "limited_update_dependent",
            "inputs": {
                "changed_path": "str",
                "dependent_path": "str",
                "dependent_source": "str",
                "delta": "dict classify_io_delta result",
                "before_contract": "dict",
                "after_contract": "dict",
            },
            "outputs": "dict with success, escalate, reason, and optional edit result",
        },
        {
            "name": "full_file_update_dependent",
            "inputs": {
                "changed_path": "str",
                "dependent_path": "str",
                "dependent_source": "str",
                "delta": "dict",
                "reason": "str escalate reason",
            },
            "outputs": "dict with success, escalate, reason, and optional content",
        },
    ],
}

import ast
import json

from build_block_table import build_block_table
from call_llm import call_llm_role
from cfg import CFG
from edit_file import edit_file
from infer_code_type import infer_code_type
from opencode_session import universal_session
from structured_llm_retry import call_llm_role_with_parse_retry
from write_file import write_file


MAX_CALL_SITE_BLOCKS = 2


def _block_contains_line(block, line_no):
    try:
        start = int(block.get("start_line"))
        end = int(block.get("end_line"))
    except Exception:
        return False
    return start <= line_no <= end


def _call_site_lines(source, symbol_names):
    names = {name for name in (symbol_names or []) if name}
    if not names or not source.strip():
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        symbol = None
        if isinstance(node.func, ast.Name):
            symbol = node.func.id
        elif isinstance(node.func, ast.Attribute):
            symbol = node.func.attr
        if symbol in names:
            lines.add(int(node.lineno))
    return sorted(lines)


def find_call_site_blocks(source, path, symbol_names, code_type=None):
    line_numbers = _call_site_lines(source, symbol_names)
    if not line_numbers:
        return []

    c_type = code_type or infer_code_type(path, source)
    block_table = build_block_table(source, path, c_type, 10, 1)
    selected = []
    seen_ids = set()
    for line_no in line_numbers:
        candidates = [
            block
            for block in block_table
            if _block_contains_line(block, line_no) and block.get("id") not in seen_ids
        ]
        if not candidates:
            continue
        block = min(
            candidates,
            key=lambda item: int(item.get("end_line", 0)) - int(item.get("start_line", 0)),
        )
        seen_ids.add(block.get("id"))
        selected.append(block)
    return selected


def _extract_json_obj(resp):
    if isinstance(resp, dict):
        return resp
    if not isinstance(resp, str):
        return None
    text = resp.strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    decoder = json.JSONDecoder()
    for index, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def _build_update_prompt(changed_path, dependent_path, delta, blocks, full_file=False):
    scope = "full file" if full_file else "listed blocks only"
    hints = json.dumps((delta or {}).get("hints") or {}, ensure_ascii=False, indent=2)
    block_payload = json.dumps(blocks, ensure_ascii=False, indent=2)
    return f"""
Update dependent file call sites after a breaking public I/O change.

Changed module: {changed_path}
Dependent file: {dependent_path}
Scope: {scope}

<io_delta>
{json.dumps(delta or {}, ensure_ascii=False, indent=2)}
</io_delta>

<hints>
{hints}
</hints>

<blocks>
{block_payload}
</blocks>

Return JSON only with one of:
1) {{"escalate": true, "reason": "..."}}
2) {{"edit_fns": ["def edit(code):\\n    ..."], "reason": "..."}}

Rules:
- Change only call sites affected by the I/O delta.
- For limited scope, edit only the provided block ids via code.replace(...) or code.replace_text(...).
- Escalate when the fix needs changes outside the provided blocks or across many sites.
- Do not rewrite unrelated code.
""".strip()


def _request_dependency_edit(changed_path, dependent_path, dependent_source, delta, blocks, full_file=False):
    prompt = _build_update_prompt(
        changed_path,
        dependent_path,
        delta,
        blocks,
        full_file=full_file,
    )
    response = call_llm_role_with_parse_retry(
        role="subagent_implement",
        messages=[
            {
                "role": "system",
                "content": "You update dependent call sites after module I/O changes. Return JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        is_valid=lambda obj: isinstance(obj, dict),
        parse_fallback_kind="execution",
        llm_call=call_llm_role,
        max_tokens=2500,
        thinking="low",
        timeout=CFG().get_timeout("subagent_call"),
        session_id=universal_session("subagent_implement", "dependency"),
    )
    return _extract_json_obj(response)


def limited_update_dependent(
    changed_path,
    dependent_path,
    dependent_source,
    delta,
    before_contract=None,
    after_contract=None,
):
    hints = (delta or {}).get("hints") or {}
    symbol_names = [hints.get("primary_name")]
    symbol_names = [name for name in symbol_names if name]
    if not symbol_names:
        primary = (after_contract or {}).get("primary_name") or (before_contract or {}).get("primary_name")
        if primary:
            symbol_names = [primary]

    blocks = find_call_site_blocks(
        dependent_source,
        dependent_path,
        symbol_names,
    )
    if not blocks:
        return {
            "success": True,
            "skipped": True,
            "reason": "no call-site blocks found",
            "escalate": False,
        }
    if len(blocks) > MAX_CALL_SITE_BLOCKS:
        return {
            "success": False,
            "escalate": True,
            "reason": f"too many call-site blocks ({len(blocks)})",
        }

    parsed = _request_dependency_edit(
        changed_path,
        dependent_path,
        dependent_source,
        delta,
        blocks,
        full_file=False,
    )
    if not isinstance(parsed, dict):
        return {
            "success": False,
            "escalate": True,
            "reason": "dependency editor returned unparseable response",
        }
    if parsed.get("escalate"):
        return {
            "success": False,
            "escalate": True,
            "reason": str(parsed.get("reason") or "editor requested escalation"),
        }

    edit_fns = parsed.get("edit_fns") or []
    if not isinstance(edit_fns, list) or not edit_fns:
        return {
            "success": False,
            "escalate": True,
            "reason": "dependency editor returned no edit_fns",
        }

    edit_result = edit_file(dependent_path, edit_fns)
    if edit_result.get("success"):
        return {
            "success": True,
            "escalate": False,
            "reason": str(parsed.get("reason") or "limited block edit applied"),
            "edit_result": edit_result,
        }
    return {
        "success": False,
        "escalate": True,
        "reason": edit_result.get("reason") or "limited block edit failed",
        "edit_result": edit_result,
    }


def full_file_update_dependent(
    changed_path,
    dependent_path,
    dependent_source,
    delta,
    reason="",
):
    parsed = _request_dependency_edit(
        changed_path,
        dependent_path,
        dependent_source,
        delta,
        blocks=[{"id": "full_file", "scope": "entire file"}],
        full_file=True,
    )
    if not isinstance(parsed, dict):
        return {
            "success": False,
            "escalate": True,
            "reason": reason or "full-file editor returned unparseable response",
        }
    if parsed.get("escalate"):
        return {
            "success": False,
            "escalate": True,
            "reason": str(parsed.get("reason") or reason or "full-file edit insufficient"),
        }

    content = parsed.get("content")
    edit_fns = parsed.get("edit_fns") or []
    if isinstance(content, str) and content.strip():
        write_ok, write_msg = write_file(dependent_path, content, None)
        return {
            "success": bool(write_ok),
            "escalate": not write_ok,
            "reason": write_msg if not write_ok else str(parsed.get("reason") or "full-file write applied"),
            "content": content,
        }
    if edit_fns:
        edit_result = edit_file(dependent_path, edit_fns)
        return {
            "success": bool(edit_result.get("success")),
            "escalate": not edit_result.get("success"),
            "reason": edit_result.get("reason") or reason,
            "edit_result": edit_result,
        }
    return {
        "success": False,
        "escalate": True,
        "reason": reason or "full-file editor returned no content or edit_fns",
    }


if __name__ == "__main__":
    source = "from pkg.mod import foo\n\n\ndef use():\n    return foo(1)\n"
    blocks = find_call_site_blocks(source, "app/use.py", ["foo"], "py")
    assert blocks, blocks
    print("LIMITED_DEPENDENCY_UPDATE SELF TEST PASSED")
