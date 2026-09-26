import json

from read_file import read_file
from call_planner import call_planner


MODULE_METADATA = {
    "name": "repair_write_step",
    "type": "function",
    "description": "Repair rejected write_file content by asking the planner model to return only complete repaired file content.",
    "functions": [
        {
            "name": "repair_write_step",
            "inputs": {
                "step": "dict representing a rejected write_file action",
                "rejection_reason": "str reason returned by verifier for rejecting the write_file action",
                "modules_override": "dict/list or None; optional temporary module registry for same-plan module dependencies"
            },
            "outputs": "dict with success bool and either repaired content str or failure reason str"
        }
    ]
}


def _strip_code_fences(text):
    text = str(text).strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_json_object(text):
    text = _strip_code_fences(text)
    if not text:
        return None

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None

    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:
        return None

    return parsed if isinstance(parsed, dict) else None


def _extract_repaired_file_content(llm_response):
    if not isinstance(llm_response, dict):
        return None, f"repair model returned non-dict response: {str(llm_response)[:300]}"

    raw = llm_response.get("content")
    if not isinstance(raw, str) or not raw.strip():
        return None, f"repair model response missing content: {str(llm_response)[:300]}"

    parsed = _parse_json_object(raw)
    if isinstance(parsed, dict) and isinstance(parsed.get("content"), str):
        inner = parsed["content"]
        if not inner:
            return None, "repair model returned empty content"
        return inner, None

    stripped = raw.strip()
    if stripped.lstrip().startswith("{") and '"content"' in stripped[:200]:
        return None, f"could not parse repair JSON: {stripped[:300]}"

    return raw, None


def repair_write_step(path, content, rejection_reason, modules_override=None):
    """
    Repair a rejected write_file step.

    The repair model must return:
    {
      "content": "complete repaired file content"
    }

    Only step["content"] should be replaced by the caller.

    modules_override:
        Optional temporary module registry dict.
        Useful when repairing a file that imports modules planned in the
        same active_plan but not yet written to persistent modules.json.
    """

    def safe_read(path):
        ok, content = read_file(path)
        return content if ok else ""

    principles = safe_read("agent_memory/core/principles.md")
    memory = safe_read("agent_memory/reasoning/llm_memory.json")


    system_repair = f'''
<system>
You are a coding assistant that repairs rejected file content.

Your task:
- Repair ONLY the provided file content.
- Return ONLY valid JSON.
- Do NOT return markdown.
- Do NOT return explanations.
- Do NOT return a plan.
- Do NOT output tool calls.
- Do NOT change the file path.
- Do NOT introduce unrelated changes.

Output contract:
- Return exactly one JSON object.
- The JSON object must contain exactly one key: "content".
- "content" must be the complete repaired file content as a string.

Follow the principles, known modules, and learned lessons strictly.

Important module registry rule:
- The known_modules block may include temporary modules planned in the same task.
- Treat those modules as available for repairing imports and module usage.
- If repairing a module file itself, the provided repaired content is the new source of truth.
- Do not reject or preserve an old module interface only because it appears in known_modules.
</system>

<principles>
{principles}
</principles>

<learned_lessons>
{memory}
</learned_lessons>
'''

    user_repair = f'''
<repair_request>
The following write_file content was rejected by the verifier.

<path>
{path}
</path>

<rejection_reason>
{rejection_reason}
</rejection_reason>

<original_content>
{content}
</original_content>

<repair_constraints>
- Preserve the original file purpose.
- Preserve compatible public APIs unless the rejection requires changing them.
- If repairing imports, ensure imported names match known modules when possible.
- Return the full repaired file content, not a diff.
</repair_constraints>

<output_format>
Return ONLY:

{{
  "content": "complete repaired file content"
}}
</output_format>
</repair_request>
'''

    repaired = call_planner(
        system_repair,
        user_repair,
        max_tokens=8192,
    )

    repaired_content, error = _extract_repaired_file_content(repaired)
    if error:
        return {
            "success": False,
            "reason": error,
        }

    return {
        "success": True,
        "content": repaired_content,
    }


if __name__ == "__main__":
    inner = "def foo():\n    return 1\n"
    wrapped = json.dumps({"content": inner})

    content, error = _extract_repaired_file_content({"content": wrapped})
    assert error is None, error
    assert content == inner, content

    bare, error = _extract_repaired_file_content({"content": inner})
    assert error is None, error
    assert bare == inner, bare

    fenced, error = _extract_repaired_file_content(
        {"content": f"```json\n{wrapped}\n```"}
    )
    assert error is None, error
    assert fenced == inner, fenced

    bad, error = _extract_repaired_file_content(
        {"content": '{"content": "broken json'}
    )
    assert bad is None, bad
    assert error is not None

    print("REPAIR_WRITE_STEP SELF TEST PASSED")